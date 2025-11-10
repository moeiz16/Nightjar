"""Deterministic agent policy (API contract Part 5.1: "policies are code, narration is the model").

Which tools an agent calls, with which arguments, and which actions it proposes are decided here from the
incident context (entities, techniques, assets) — never from model output and never from log text. The verdict
combines rule fidelity, the triage classifier's calibrated probability (and its SHAP reasons), behaviour-anomaly
scores and enrichment hits; when the evidence disagrees, or when the case is about a legitimate person's own
access (insider-style data movement), the verdict is `needs_review` so a person decides.

`decide()` is a pure function of `PolicyInputs`; its inputs are recorded on the verdict step so a run can be
replayed offline and checked (`agents.api.replay_verify`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from nightjar.agents.actions import ACTIONS
from nightjar.contracts import (
    SEVERITY_ORDER,
    Evidence,
    IncidentContext,
    ProposedAction,
    ReportableFacts,
)

MAX_CHECKS = 6
_GATEWAY_KINDS = ("network", "saas", "cloud_account")


@dataclass(frozen=True)
class ToolCall:
    server: str
    tool: str
    args: dict[str, Any]
    why: str  # plain-language step title


def _external(ip: str) -> bool:
    from nightjar.ml.features import is_external_ip

    return is_external_ip(ip)


def triage_checks(ctx: IncidentContext) -> list[ToolCall]:
    """What the triage colleague checks first (bounded)."""
    calls: list[ToolCall] = []
    for u in (
        [u for u in ctx.entities.users if not u.startswith(("svc-", "ci-"))][:2] if len(ctx.entities.users) <= 5 else []
    ):
        calls.append(
            ToolCall(
                "identity", "sign_in_profile", {"username": u, "days": 90}, f"Checked where {u} usually signs in from"
            )
        )
    for h in ctx.entities.hosts[:3]:
        calls.append(ToolCall("assets", "asset_exposure", {"name": h}, f"Checked how exposed and important {h} is"))
    return calls[:MAX_CHECKS]


def enrichment_indicators(ctx: IncidentContext) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ip in ctx.entities.ips:
        if _external(ip) and len(out) < 6:
            out.append({"type": "ip", "value": ip})
    for d in ctx.detections:
        for s in d.evidence:
            url = (s.fields or {}).get("url.original") or (s.fields or {}).get("destination.domain")
            if url and len(out) < 8 and {"type": "url", "value": str(url)} not in out:
                out.append({"type": "url" if "/" in str(url) else "domain", "value": str(url)})
    for a in ctx.assets:
        for cve in a.vulnerabilities:
            if {"type": "cve", "value": cve} not in out:
                out.append({"type": "cve", "value": cve})
    return out[:10]


def enrichment_calls(indicators: list[dict[str, str]], techniques: list[str]) -> list[ToolCall]:
    calls = []
    for ind in indicators:
        if ind["type"] == "cve":
            calls.append(
                ToolCall(
                    "intel",
                    "lookup_kev",
                    {"cveId": ind["value"]},
                    f"Checked whether {ind['value']} is a known exploited vulnerability",
                )
            )
        else:
            calls.append(
                ToolCall(
                    "intel",
                    "lookup_indicator",
                    {"type": ind["type"], "value": ind["value"]},
                    f"Checked {ind['value']} against threat intelligence",
                )
            )
    for t in techniques[:4]:
        calls.append(
            ToolCall("intel", "lookup_attack_technique", {"techniqueId": t}, f"Looked up attack technique {t}")
        )
    return calls


@dataclass
class PolicyInputs:
    """Everything the verdict depends on (recorded for replay)."""

    kind: str
    severity: str
    detections: int
    unsuppressed: int
    max_rule_confidence: float
    max_probability: float | None
    mean_probability: float | None
    all_likely_noise: bool
    ueba_max: float
    intel_malicious: int
    intel_suspicious: int
    kev_hits: int
    tactics: list[str] = field(default_factory=list)
    crown_jewel: bool = False
    insider_shape: bool = False  # an organisation member moving data with their own valid access, no attacker signs
    flagged_text: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def policy_inputs(ctx: IncidentContext, enrichment: dict[str, Any] | None) -> PolicyInputs:
    scores = [d.triage for d in ctx.detections if d.triage]
    live = [d for d in ctx.detections if not (d.triage and d.triage.suppressed)]
    probs = [s.probability for s in scores]
    indicators = (enrichment or {}).get("indicators", [])
    mal = sum(1 for i in indicators if i.get("verdict") == "malicious" and i.get("type") != "cve")
    sus = sum(1 for i in indicators if i.get("verdict") == "suspicious")
    kev = sum(1 for i in indicators if i.get("kev"))
    tactics = sorted({t for a in ctx.attack for t in a.tactics})
    attacker_signs = mal > 0 or any(
        t in tactics for t in ("credential-access", "lateral-movement", "command-and-control", "initial-access")
    )
    insider = (
        ctx.kind in ("data_exfiltration", "insider")
        and any(i.kind in ("employee", "contractor") for i in ctx.identities)
        and not attacker_signs
    )
    return PolicyInputs(
        kind=ctx.kind,
        severity=ctx.severity,
        detections=len(ctx.detections),
        unsuppressed=len(live),
        max_rule_confidence=round(max((d.confidence for d in ctx.detections if d.source != "ueba"), default=0.0), 4),
        max_probability=round(max(probs), 4) if probs else None,
        mean_probability=round(sum(probs) / len(probs), 4) if probs else None,
        all_likely_noise=bool(scores)
        and len(scores) == len(ctx.detections)
        and all(s.band == "likely_noise" for s in scores),
        ueba_max=round(max((a.score for a in ctx.anomalies), default=0.0), 4),
        intel_malicious=mal,
        intel_suspicious=sus,
        kev_hits=kev,
        tactics=tactics,
        crown_jewel=any(a.criticality == "crown_jewel" for a in ctx.assets),
        insider_shape=insider,
        flagged_text=sum(1 for d in ctx.detections for s in d.evidence if s.summary.flags),
    )


def decide(p: PolicyInputs) -> tuple[str, float]:
    """(verdict, confidence). Pure and deterministic."""
    prob = p.max_probability if p.max_probability is not None else p.max_rule_confidence
    corroboration = sum(
        [
            p.intel_malicious > 0,
            p.intel_suspicious > 0,
            p.ueba_max >= 0.95,
            len(p.tactics) >= 3,
            p.kev_hits > 0,
            p.max_rule_confidence >= 0.8,
        ]
    )
    if p.all_likely_noise and p.intel_malicious == 0 and p.ueba_max < 0.95:
        conf = 1.0 - (p.mean_probability if p.mean_probability is not None else prob)
        return "benign", round(min(0.97, max(0.6, conf)), 2)
    if p.insider_shape:
        # A person with valid access moving data: classifier and behaviour say "unusual", but intent is a human call.
        conf = 0.45 + 0.1 * min(corroboration, 2) + (0.03 if p.ueba_max >= 0.95 else 0.0)
        return "needs_review", round(min(conf, 0.7), 2)
    if prob >= 0.8 and corroboration >= 1:
        return "true_positive", round(min(0.99, 0.6 + 0.3 * prob + 0.03 * corroboration), 2)
    if prob >= 0.95:
        return "true_positive", round(min(0.95, 0.55 + 0.4 * prob), 2)
    if prob < 0.2 and corroboration == 0:
        return "false_positive", round(min(0.9, 0.9 - prob), 2)
    return "needs_review", round(0.4 + 0.3 * prob, 2)


def evidence_for(ctx: IncidentContext, enrichment: dict[str, Any] | None) -> tuple[Evidence, ...]:
    out: list[Evidence] = []
    top = sorted(
        ctx.detections, key=lambda d: (-SEVERITY_ORDER[d.severity], -(d.triage.probability if d.triage else 0))
    )
    for d in top[:4]:
        prob = f"; model {d.triage.probability:.2f}" if d.triage else ""
        out.append(Evidence("detection", str(d.id), f"{d.rule_title} ({d.severity}{prob})"))
        if d.triage and d.triage.reasons:
            out.append(Evidence("model_reason", str(d.id), d.triage.reasons[0].text))
    for a in sorted(ctx.anomalies, key=lambda a: -a.score)[:2]:
        why = a.reasons[0].text if a.reasons else "unusual behaviour"
        out.append(Evidence("anomaly", str(a.id), f"{a.entity_id}: {why} (score {a.score:.2f})"))
    for i in (enrichment or {}).get("indicators", [])[:3]:
        if i.get("verdict") in ("malicious", "suspicious") or i.get("kev"):
            out.append(
                Evidence("intel", str(i.get("value")), f"{i.get('value')}: {i.get('verdict')} ({i.get('source')})")
            )
    for asset in ctx.assets:
        if asset.criticality == "crown_jewel":
            out.append(
                Evidence(
                    "asset",
                    asset.name,
                    f"{asset.name} is a crown-jewel system ({asset.business_service or asset.kind})",
                )
            )
    return tuple(out[:12])


def facts_for(ctx: IncidentContext, verdict: str, p: PolicyInputs) -> ReportableFacts | None:
    if verdict in ("benign", "false_positive"):
        return None
    jur: list[str] = []
    # Where the affected systems are; people's home jurisdiction only counts when their own data or account is
    # the subject (a spray across many accounts affects the gateway, not each person).
    for a in ctx.assets:
        if a.jurisdiction not in jur:
            jur.append(a.jurisdiction)
        served = a.business_service or ""
        for j in ("EU", "US", "AU"):  # shared platforms name the depots they serve ("serves EU, US and AU depots")
            if f" {j}" in f" {served.replace(',', ' ')}" and "serves" in served.lower() and j not in jur:
                jur.append(j)
    if ctx.kind in ("credential_misuse", "phishing", "data_exfiltration", "insider") or not jur:
        for i in ctx.identities:
            if i.jurisdiction not in jur:
                jur.append(i.jurisdiction)
    shared = any("shared_platform" in a.tags or "serves" in (a.business_service or "").lower() for a in ctx.assets)
    if shared and ctx.kind == "ransomware_precursor":
        for j in ("EU", "US", "AU"):
            if j not in jur:
                jur.append(j)
    entities = [e["name"] for e in ctx.org.entities if e.get("jurisdiction") in jur]
    # Containing ransomware on a crown jewel means taking it offline: an outage of the service it runs.
    disruption = (
        "outage" if ctx.kind == "ransomware_precursor" and p.crown_jewel and verdict == "true_positive" else "none"
    )
    soci_hit = any("soci_asset" in a.tags for a in ctx.assets) or ("AU" in jur and disruption == "outage")
    personal = ctx.kind in ("credential_misuse", "data_exfiltration", "cloud_credential_exposure", "phishing")
    records: int | None = None
    for d in ctx.detections:
        n = (d.fields or {}).get("m365.operation_count") or (d.fields or {}).get("eventCount")
        if isinstance(n, int | float) and personal:
            records = int(max(records or 0, n))
    return ReportableFacts(
        malicious_suspected=verdict == "true_positive" or p.intel_malicious > 0,
        affected_jurisdictions=tuple(jur),  # type: ignore[arg-type]
        affected_entities=tuple(entities),
        service_disruption=disruption,  # type: ignore[arg-type]
        disruption_hours=None,
        critical_asset_impact="relevant" if soci_hit and verdict == "true_positive" else "none",
        personal_data_involved=personal,
        records_affected_estimate=records,
        cross_border=len(jur) > 1,
        financial_impact_estimate_eur=None,
    )


def _proposal(kind: str, target: str, rationale: str) -> ProposedAction:
    spec = ACTIONS[kind]
    return ProposedAction(
        kind=kind,  # type: ignore[arg-type]
        target=target,
        target_type=spec.target_type,  # type: ignore[arg-type]
        rationale=rationale,
        expected_effect=spec.effect,
        reversible=spec.reversible,
    )


def response_plan(ctx: IncidentContext, verdict: str, enrichment: dict[str, Any] | None) -> list[ProposedAction]:
    """Containment proposals; every target is drawn from the incident's own entity sets (layer 3 + 4)."""
    out: list[ProposedAction] = []
    bad_ips = [
        str(i["value"])
        for i in (enrichment or {}).get("indicators", [])
        if i.get("type") == "ip"
        and i.get("verdict") in ("malicious", "suspicious")
        and i.get("value") in ctx.entities.ips
    ]
    assets = {a.name: a for a in ctx.assets}
    if verdict == "true_positive":
        if ctx.kind == "ransomware_precursor":
            for h in ctx.entities.hosts:
                a = assets.get(h)
                if a and a.kind in ("server", "workstation") and a.criticality in ("crown_jewel",):
                    out.append(
                        _proposal("isolate_host", h, f"{h} shows attacker activity; isolating stops the spread.")
                    )
            for u in ctx.entities.users:
                ident = next((i for i in ctx.identities if i.username == u), None)
                if ident and ident.kind == "service":
                    out.append(
                        _proposal("disable_account", u, f"The attacker is using {u}; turning it off cuts their access.")
                    )
            for ip in bad_ips:
                out.append(_proposal("block_ip", ip, f"{ip} is the attacker's command server."))
        elif ctx.kind in ("credential_misuse", "phishing"):
            for u in ctx.entities.users[:2]:
                out.append(_proposal("revoke_sessions", u, f"Someone else is signed in as {u}."))
                out.append(_proposal("disable_account", u, f"Stops further use of {u}'s account until it is secured."))
                out.append(_proposal("reset_password", u, f"{u}'s password is known to the attacker."))
            for m in ctx.entities.emails:
                if m.startswith("<"):
                    out.append(_proposal("quarantine_email", m, "Removes the phishing message from every mailbox."))
        elif ctx.kind == "cloud_credential_exposure":
            for k in ctx.entities.access_keys:
                out.append(
                    _proposal("revoke_access_key", k, f"Access key {k} is public and being used by someone else.")
                )
        elif ctx.kind == "brute_force":
            for ip in [ip for ip in ctx.entities.ips if _external(ip)][:5]:
                out.append(_proposal("block_ip", ip, f"{ip} is guessing passwords against the gateway."))
        if out or ctx.kind != "benign_activity":
            out.append(_proposal("open_ticket", "ticket", "Track the containment and follow-up work."))
    elif verdict == "needs_review":
        for h in ctx.entities.hosts[:1]:
            a = assets.get(h)
            if a and a.kind in ("server", "workstation"):
                out.append(_proposal("collect_triage_package", h, "Preserve evidence while a person reviews the case."))
        out.append(_proposal("open_ticket", "ticket", "A person needs to review this case."))
    # layer 4 (defence in depth): drop anything not in the incident's entities
    from nightjar.safety import bounded_target

    return [a for a in out if bounded_target(a.target, a.target_type, ctx.entities)]
