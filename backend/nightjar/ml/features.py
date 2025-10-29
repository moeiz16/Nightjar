"""Features for the two models (ADR 0005). The same code computes them online (scorer, UEBA worker) and offline
(`make train`, eval), so training and serving cannot drift apart.

Triage (one row per detection): what an experienced analyst looks at first — rule fidelity and track record,
kill-chain stage, how important the asset/identity is, whether the person or machine is behaving unusually,
whether the alert is part of a burst across many machines, known-benign software lineage, intel hits, and
whether several stages of an attack show up on the same entities. No protected attributes exist here.

UEBA (one row per entity per 5-minute window): counts and novelty against the entity's own history.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from nightjar.contracts import AssetRecord, Detection, IdentityRecord, NormalizedEvent
from nightjar.intel.api import tactic_stage
from nightjar.workers.replay.generator import BACKUP_PARENT

# ─────────────────────────────────────────────────────────────────────────────
# Triage features
# ─────────────────────────────────────────────────────────────────────────────
TRIAGE_FEATURES: tuple[str, ...] = (
    "rule_level",
    "rule_prior",
    "rule_is_correlation",
    "source_ueba",
    "source_external",
    "tactic_stage",
    "tactic_count",
    "asset_criticality",
    "identity_privileged",
    "service_account",
    "ueba_entity_score",
    "burst_same_rule",
    "host_spread_same_rule",
    "benign_lineage",
    "intel_verdict",
    "kev_on_asset",
    "external_ip",
    "multi_stage_entities",
    "event_count_log",
    "untrusted_text",
)
FEATURE_LABELS: dict[str, str] = {
    "rule_level": "Rule severity",
    "rule_prior": "This rule's track record",
    "rule_is_correlation": "Pattern across several events",
    "source_ueba": "Unusual-behaviour finding",
    "source_external": "Alert from another security tool",
    "tactic_stage": "Stage of the attack chain",
    "tactic_count": "Number of attack stages in the alert",
    "asset_criticality": "Importance of the affected computer",
    "identity_privileged": "Privileged account involved",
    "service_account": "Service account involved",
    "ueba_entity_score": "How unusual the person or computer is behaving",
    "burst_same_rule": "Same alert firing many times",
    "host_spread_same_rule": "Same alert on many computers at once",
    "benign_lineage": "Started by known maintenance software",
    "intel_verdict": "Threat intelligence match",
    "kev_on_asset": "Known exploited vulnerability on the computer",
    "external_ip": "Outside internet address involved",
    "multi_stage_entities": "Several attack stages on the same entities",
    "event_count_log": "Number of events behind the alert",
    "untrusted_text": "Instruction-like text in the evidence",
}
LEVEL_NUM = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
CRIT_NUM = {"low": 0, "medium": 1, "high": 2, "crown_jewel": 3}
BENIGN_LINEAGE = (
    BACKUP_PARENT.lower(),
    "\\veeam\\",
    "\\ccmexec.exe",
    "\\microsoft intune management extension\\",
    "\\sccm\\",
    "\\windows\\ccm\\",
    "\\program files\\tanium\\",
)
_PRIVATE = ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.", "127.")


def is_external_ip(ip: str) -> bool:
    return bool(ip) and ":" not in ip and not ip.startswith(_PRIVATE)


@dataclass
class Inventory:
    """What the featurizer needs from the asset inventory and identity directory (A's `directory` in
    production; the in-memory platform offline)."""

    assets: dict[str, AssetRecord] = field(default_factory=dict)
    identities: dict[str, IdentityRecord] = field(default_factory=dict)

    def asset(self, name: str) -> AssetRecord | None:
        return self.assets.get(name.lower())

    def identity(self, username: str) -> IdentityRecord | None:
        return self.identities.get(username.lower())


class TriageFeaturizer:
    """Stateful per org: burst and host-spread of each rule over 30 minutes, tactics seen per entity over an hour,
    and the latest behaviour-anomaly score per entity (fed by UEBA detections passing through the scorer)."""

    def __init__(
        self,
        inventory: Inventory | Callable[[], Inventory] | None = None,
        rule_priors: dict[str, float] | None = None,
        kev_lookup: Callable[[str], Any] | None = None,
    ) -> None:
        self._inventory = inventory or Inventory()
        self.rule_priors = rule_priors or {}
        self.kev_lookup = kev_lookup
        self._rule_hits: dict[str, deque[tuple[datetime, str]]] = defaultdict(deque)
        self._entity_tactics: dict[str, deque[tuple[datetime, str]]] = defaultdict(deque)
        self._ueba: dict[str, tuple[datetime, float]] = {}

    @property
    def inventory(self) -> Inventory:
        inv = self._inventory
        return inv() if callable(inv) else inv

    def _prior(self, d: Detection) -> float:
        if d.rule_id in self.rule_priors:
            return self.rule_priors[d.rule_id]
        return {"info": 0.1, "low": 0.2, "medium": 0.35, "high": 0.5, "critical": 0.6}[d.severity]

    def featurize(self, d: Detection) -> dict[str, float]:
        """Observe the detection (updates state) and return its feature row."""
        inv = self.inventory
        ts = d.first_seen
        key = f"{d.org_id}|{d.rule_id}"
        hits = self._rule_hits[key]
        while hits and hits[0][0] < ts - timedelta(minutes=30):
            hits.popleft()
        for h in d.entities.hosts[:3] or ("-",):
            hits.append((ts, h))
        burst = len(hits)
        spread = len({h for _, h in hits if h != "-"})

        tactics = {t for a in d.attack for t in a.tactics}
        ents = [f"{d.org_id}|h:{h}" for h in d.entities.hosts] + [f"{d.org_id}|u:{u}" for u in d.entities.users[:5]]
        seen: set[str] = set(tactics)
        for e in ents:
            q = self._entity_tactics[e]
            while q and q[0][0] < ts - timedelta(hours=1):
                q.popleft()
            seen |= {t for _, t in q}
            for t in tactics:
                q.append((ts, t))

        if d.source == "ueba":
            score = float(d.confidence)
            for e in ents:
                self._ueba[e] = (ts, max(score, self._ueba.get(e, (ts, 0.0))[1]))
        ueba = 0.0
        for e in ents:
            hit = self._ueba.get(e)
            if hit and ts - hit[0] <= timedelta(hours=2):
                ueba = max(ueba, hit[1])

        crit = 0
        kev = 0
        for h in d.entities.hosts:
            a = inv.asset(h)
            if a:
                crit = max(crit, CRIT_NUM.get(a.criticality, 0))
                if self.kev_lookup and any(self.kev_lookup(c) for c in a.vulnerabilities):
                    kev = 1
        priv = 0
        svc = 0
        for u in d.entities.users[:5]:
            ident = inv.identity(u)
            if ident and ident.privileged:
                priv = 1
            if (ident and ident.kind == "service") or u.startswith(("svc-", "ci-", "adm-")):
                svc = 1
        lineage = 0
        for snap in d.evidence:
            parent = str((snap.fields or {}).get("process.parent.executable", "")).lower()
            if parent and any(b in parent for b in BENIGN_LINEAGE):
                lineage = 1
        verdicts = {str((s.fields or {}).get("threat.indicator.verdict", "")) for s in d.evidence}
        intel = 2 if "malicious" in verdicts else 1 if "suspicious" in verdicts else 0
        untrusted = int(any(s.summary.flags for s in d.evidence))
        stages = [tactic_stage(t) for t in tactics]
        return {
            "rule_level": float(LEVEL_NUM.get(d.severity, 2)),
            "rule_prior": float(self._prior(d)),
            "rule_is_correlation": float("eventCount" in (d.fields or {}) or len(d.event_ids) > 1),
            "source_ueba": float(d.source == "ueba"),
            "source_external": float(d.source == "external"),
            "tactic_stage": float(max(stages) if stages else -1),
            "tactic_count": float(len(tactics)),
            "asset_criticality": float(crit),
            "identity_privileged": float(priv),
            "service_account": float(svc),
            "ueba_entity_score": round(ueba, 4),
            "burst_same_rule": float(burst),
            "host_spread_same_rule": float(spread),
            "benign_lineage": float(lineage),
            "intel_verdict": float(intel),
            "kev_on_asset": float(kev),
            "external_ip": float(any(is_external_ip(ip) for ip in d.entities.ips)),
            "multi_stage_entities": float(len(seen)),
            "event_count_log": round(math.log1p(len(d.event_ids)), 4),
            "untrusted_text": float(untrusted),
        }


def reason_text(feature: str, value: float, contribution: float) -> str:
    """Plain-language reason for one SHAP contribution (positive = towards "likely real")."""
    up = contribution > 0
    v = value
    match feature:
        case "benign_lineage":
            return "Started by known maintenance software" if v else "Not started by known maintenance software"
        case "burst_same_rule":
            return (
                f"The same alert fired {int(v)} times in 30 minutes"
                if v > 3
                else "An isolated alert, not part of a burst"
            )
        case "host_spread_same_rule":
            return f"The same alert appeared on {int(v)} computers at once" if v > 2 else "Seen on one computer only"
        case "ueba_entity_score":
            return (
                f"The person or computer is behaving very unusually (score {v:.2f})"
                if v >= 0.9
                else "Behaviour is close to this entity's normal pattern"
            )
        case "intel_verdict":
            return {
                2: "Matches known attacker infrastructure",
                1: "Matches a suspicious address in threat intelligence",
            }.get(int(v), "No threat-intelligence match")
        case "asset_criticality":
            return {
                3: "Involves a crown-jewel system",
                2: "Involves a business-critical system",
                1: "Involves a standard server",
            }.get(int(v), "Involves a low-impact computer")
        case "identity_privileged":
            return "Involves a privileged account" if v else "No privileged account involved"
        case "service_account":
            return "Involves a service account" if v else "Involves a personal account"
        case "multi_stage_entities":
            return (
                f"{int(v)} attack stages seen on the same people or computers"
                if v >= 2
                else "Only one attack stage seen"
            )
        case "rule_prior":
            return (
                f"This rule has usually been right before ({v:.0%})"
                if up
                else f"This rule is often noisy ({v:.0%} right before)"
            )
        case "rule_level":
            return "High-severity rule" if v >= 3 else "Lower-severity rule"
        case "kev_on_asset":
            return (
                "The computer has a known exploited vulnerability"
                if v
                else "No known exploited vulnerability on the computer"
            )
        case "external_ip":
            return "An outside internet address is involved" if v else "Only internal addresses involved"
        case "rule_is_correlation":
            return "A pattern across several events" if v else "A single event"
        case "source_external":
            return "Raised by another security tool" if v else "Raised by a detection rule"
        case "source_ueba":
            return "An unusual-behaviour finding" if v else "A rule-based finding"
        case "tactic_stage":
            return "Late in the attack chain (access, movement or impact)" if v >= 7 else "Early in the attack chain"
        case "untrusted_text":
            return "Evidence contained instruction-like text" if v else "No instruction-like text in the evidence"
        case _:
            return FEATURE_LABELS.get(feature, feature)


# ─────────────────────────────────────────────────────────────────────────────
# UEBA windows
# ─────────────────────────────────────────────────────────────────────────────
WINDOW = timedelta(minutes=5)
USER_FEATURES: tuple[str, ...] = (
    "signIns",
    "mfaDenied",
    "distinctSrcIps",
    "newCountry",
    "hostingNetwork",
    "offHours",
    "processStarts",
    "newProcesses",
    "bytesOutMb",
    "distinctDestinations",
    "newShares",
    "mailItemsRead",
)
HOST_FEATURES: tuple[str, ...] = (
    "processStarts",
    "newProcesses",
    "distinctUsers",
    "newUsers",
    "connectionsOut",
    "newDestinations",
    "serviceInstalls",
    "offHours",
)
# A window is only reported when at least one of these is non-zero (novelty or risk), whatever the score.
USER_GATES = ("newCountry", "hostingNetwork", "mfaDenied", "newShares", "newProcesses", "bytesOutMb")
HOST_GATES = ("newProcesses", "newUsers", "serviceInstalls", "newDestinations")
FEATURE_TEXT: dict[str, str] = {
    "signIns": "sign-ins",
    "mfaDenied": "refused sign-in prompts",
    "distinctSrcIps": "different source addresses",
    "newCountry": "sign-ins from a country never used before",
    "hostingNetwork": "sign-ins from a hosting provider network",
    "offHours": "activity at an hour this account is rarely active",
    "processStarts": "programs started",
    "newProcesses": "programs never seen before",
    "bytesOutMb": "MB sent out",
    "distinctDestinations": "outside destinations",
    "newShares": "file shares never opened before",
    "mailItemsRead": "mailbox items read",
    "distinctUsers": "different accounts",
    "newUsers": "accounts never seen on this computer",
    "connectionsOut": "outbound connections",
    "newDestinations": "destinations never contacted before",
    "serviceInstalls": "services installed",
}
HOSTING_WORDS = ("hosting", "vps", "datacenter", "data center", "server", "cloud")


@dataclass
class EntityProfile:
    """What 'normal' means for one entity (learned from history, extended online)."""

    countries: set[str] = field(default_factory=set)
    processes: set[str] = field(default_factory=set)
    shares: set[str] = field(default_factory=set)
    users: set[str] = field(default_factory=set)
    destinations: set[str] = field(default_factory=set)
    hours: list[int] = field(default_factory=lambda: [0] * 24)

    def hour_rarity(self, hour: int) -> float:
        total = sum(self.hours)
        if total < 20:
            return 0.0
        share = self.hours[hour] / total
        return round(max(0.0, 1.0 - share * 24 / 2.0), 3)  # 0 = typical hour, 1 = never seen

    def to_doc(self) -> dict[str, Any]:
        return {
            "countries": sorted(self.countries),
            "processes": sorted(self.processes),
            "shares": sorted(self.shares),
            "users": sorted(self.users),
            "destinations": sorted(self.destinations)[:500],
            "hours": self.hours,
        }

    @classmethod
    def from_doc(cls, d: dict[str, Any]) -> EntityProfile:
        return cls(
            countries=set(d.get("countries", [])),
            processes=set(d.get("processes", [])),
            shares=set(d.get("shares", [])),
            users=set(d.get("users", [])),
            destinations=set(d.get("destinations", [])),
            hours=list(d.get("hours", [0] * 24)),
        )


@dataclass
class Window:
    entity_type: str
    entity_id: str
    start: datetime
    events: list[NormalizedEvent] = field(default_factory=list)


def window_start(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def entity_keys(e: NormalizedEvent) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if e.user and e.user.name and not e.user.name.endswith("$") and e.user.name not in ("system", "-"):
        keys.append(("user", e.user.name.lower()))
    if e.host and e.dataset.startswith("windows."):
        keys.append(("host", e.host.name.lower()))
    return keys


def _bytes_out(e: NormalizedEvent) -> int:
    raw = e.raw or {}
    for k in ("sentbyte", "sent_bytes", "bytes_out"):
        v = raw.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
    return 0


def _share(e: NormalizedEvent) -> str | None:
    if e.action != "share-accessed":
        return None
    return str((e.raw or {}).get("ShareName") or "").lower() or None


def _dest(e: NormalizedEvent) -> str | None:
    if e.destination and e.destination.ip and is_external_ip(e.destination.ip):
        return str(
            (e.raw or {}).get("DestinationHostname") or (e.raw or {}).get("dst_host") or e.destination.ip
        ).lower()
    return None


def window_features(w: Window, profile: EntityProfile | None) -> dict[str, float]:
    p = profile or EntityProfile()
    evs = w.events
    if w.entity_type == "user":
        signins = [e for e in evs if e.action in ("logon", "mfa-push-approved", "sso-logon") and e.outcome == "success"]
        countries = {e.source.geo_country for e in signins if e.source and e.source.geo_country}
        hosting = any(
            e.source and e.source.as_org and any(w_ in e.source.as_org.lower() for w_ in HOSTING_WORDS) for e in signins
        )
        procs = {e.process.name.lower() for e in evs if e.process and e.process.name and e.action == "process-started"}
        shares = {s for e in evs if (s := _share(e))}
        dests = {d for e in evs if (d := _dest(e))}
        mail = sum(int((e.raw or {}).get("OperationCount") or 0) for e in evs if e.action == "mailbox-items-accessed")
        return {
            "signIns": float(len(signins)),
            "mfaDenied": float(sum(1 for e in evs if e.action == "mfa-push-denied")),
            "distinctSrcIps": float(len({e.source.ip for e in evs if e.source and e.source.ip})),
            "newCountry": float(len(countries - p.countries)) if p.countries else 0.0,
            "hostingNetwork": float(hosting),
            "offHours": p.hour_rarity(w.start.hour),
            "processStarts": float(sum(1 for e in evs if e.action == "process-started")),
            "newProcesses": float(len(procs - p.processes)) if p.processes else 0.0,
            "bytesOutMb": round(sum(_bytes_out(e) for e in evs) / 1e6, 3),
            "distinctDestinations": float(len(dests)),
            "newShares": float(len(shares - p.shares)) if p.shares else float(len(shares) > 2),
            "mailItemsRead": float(mail),
        }
    procs = {e.process.name.lower() for e in evs if e.process and e.process.name and e.action == "process-started"}
    users = {e.user.name.lower() for e in evs if e.user and e.user.name and not e.user.name.endswith("$")}
    dests = {d for e in evs if (d := _dest(e))}
    return {
        "processStarts": float(sum(1 for e in evs if e.action == "process-started")),
        "newProcesses": float(len(procs - p.processes)) if p.processes else 0.0,
        "distinctUsers": float(len(users)),
        "newUsers": float(len(users - p.users)) if p.users else 0.0,
        "connectionsOut": float(sum(1 for e in evs if e.action == "network-connection")),
        "newDestinations": float(len(dests - p.destinations)) if p.destinations else 0.0,
        "serviceInstalls": float(sum(1 for e in evs if e.action == "service-installed")),
        "offHours": p.hour_rarity(w.start.hour),
    }


def learn(profile: EntityProfile, w: Window) -> None:
    """Extend an entity's profile with a (normal) window."""
    for e in w.events:
        profile.hours[e.timestamp.hour] += 1
        if e.source and e.source.geo_country and e.action in ("logon", "mfa-push-approved", "sso-logon"):
            profile.countries.add(e.source.geo_country)
        if e.process and e.process.name and e.action == "process-started":
            profile.processes.add(e.process.name.lower())
        if (s := _share(e)) is not None:
            profile.shares.add(s)
        if w.entity_type == "host" and e.user and e.user.name and not e.user.name.endswith("$"):
            profile.users.add(e.user.name.lower())
        if (d := _dest(e)) is not None:
            profile.destinations.add(d)


def windows_of(events: Iterable[NormalizedEvent]) -> list[Window]:
    """Group events into per-entity 5-minute windows (offline helper)."""
    acc: dict[tuple[str, str, str, datetime], Window] = {}
    for e in events:
        for et, eid in entity_keys(e):
            key = (str(e.org_id), et, eid, window_start(e.timestamp))
            w = acc.get(key)
            if w is None:
                w = acc[key] = Window(et, eid, key[3])
            w.events.append(e)
    return sorted(acc.values(), key=lambda w: (w.start, w.entity_type, w.entity_id))
