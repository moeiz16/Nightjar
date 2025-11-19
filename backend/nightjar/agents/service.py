"""The three agents (Triage, Enrichment, Response) as A2A executors, and the orchestrator hand-off.

Flow (API contract Part 3, step 3): orchestrator → triage (`triage_incident`) → enrichment (`enrich_incident`) →
triage decides → response (`plan_response`) → triage closes the run. Every agent appends its own steps through
`investigations.append_step` (with its A2A task id and an input hash), so the run log is a replayable record of
who checked what, what came back, what was decided and why. Tool use goes through the MCP tool servers with the
agent's own least-privilege token.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, Field

from nightjar import clock
from nightjar.agents import policy
from nightjar.agents.a2a import BaseExecutor, Recorder, build_app, call_agent, input_hash, register_inline
from nightjar.agents.actions import ACTIONS
from nightjar.contracts import (
    AttackRef,
    EnrichmentResult,
    IndicatorFinding,
    KevHit,
    ProposedAction,
    TriageVerdict,
    UntrustedText,
    to_doc,
)
from nightjar.mcp_servers.host import ToolClient
from nightjar.safety import neutralise
from nightjar.stream.codec import from_doc

log = structlog.get_logger("nightjar.agents")


class TriageNarrative(BaseModel):
    """What the model may write: display text and a ranked list of hypotheses; it cannot add tools or targets."""

    summary: str = Field(max_length=1200)
    hypotheses: list[str] = Field(default_factory=list, max_length=4)


def _uuid(v: Any) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _flags_of(value: Any) -> tuple[str, ...]:
    """Instruction-like flags anywhere in a tool result (nested UntrustedText docs)."""
    found: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if isinstance(x.get("flags"), list) and "text" in x:
                found.update(str(f) for f in x["flags"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(value)
    return tuple(sorted(found))


async def _tool(rec: Recorder, tools: ToolClient, call: policy.ToolCall) -> Any:
    t0 = time.perf_counter()
    name = f"{call.server}.{call.tool}"
    await rec.step("tool_call", call.why, tool=name, detail={"input": call.args}, hash_of=call.args, started=t0)
    out, err, text = await tools.call(call.server, call.tool, call.args)
    flags = _flags_of(out)
    if err:
        await rec.step(
            "error", f"Could not complete: {call.why.lower()}", tool=name, detail={"error": text[:300]}, started=t0
        )
        return None
    await rec.step(
        "tool_result",
        _result_title(call, out),
        tool=name,
        detail={"input": call.args, "output": out},
        hash_of=call.args,
        flags=flags,
        started=t0,
    )
    return out


def _result_title(call: policy.ToolCall, out: Any) -> str:
    if out is None:
        return "Nothing on record"
    if call.tool == "sign_in_profile":
        usual = ", ".join(out.get("usualCountries") or []) or "unknown"
        return f"{call.args['username']} usually signs in from {usual}"
    if call.tool == "asset_exposure":
        a = out.get("asset") or {}
        bits = []
        if out.get("crownJewel"):
            bits.append("a crown-jewel system")
        if out.get("kev"):
            bits.append(f"{len(out['kev'])} known exploited vulnerabilit{'y' if len(out['kev']) == 1 else 'ies'}")
        if out.get("internetFacing"):
            bits.append("reachable from the internet")
        return f"{a.get('name', call.args.get('name'))}: " + (", ".join(bits) if bits else "no special exposure")
    if call.tool == "lookup_indicator":
        return f"{call.args['value']}: {out.get('verdict', 'unknown')}"
    if call.tool == "lookup_kev":
        return f"{call.args['cveId']} is a known exploited vulnerability" if out else f"{call.args['cveId']} not listed"
    if call.tool == "lookup_attack_technique":
        return f"{out.get('techniqueId')}: {out.get('name')}"
    return "Done"


# ─────────────────────────────────────────────────────────────────────────────
# Enrichment
# ─────────────────────────────────────────────────────────────────────────────
class EnrichmentExecutor(BaseExecutor):
    agent = "enrichment"
    artifact_name = "enrichment"

    async def handle(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        from nightjar.incidents import api as incidents

        run_id, org_id, incident_id = _uuid(payload["runId"]), _uuid(payload["orgId"]), _uuid(payload["incidentId"])
        rec = Recorder(run_id, "enrichment", task_id)
        await rec.step(
            "handoff_in",
            "Received the incident from the triage colleague",
            detail={"input": {k: payload[k] for k in ("indicators", "techniques") if k in payload}},
        )
        ctx = await incidents.get_incident_context(org_id, incident_id)
        tools = ToolClient("enrichment", org_id)
        indicators = payload.get("indicators") or policy.enrichment_indicators(ctx)
        techniques = payload.get("techniques") or [a.technique_id for a in ctx.attack]
        findings: list[dict[str, Any]] = []
        techs: list[dict[str, Any]] = []
        for call in policy.enrichment_calls(indicators, techniques):
            out = await _tool(rec, tools, call)
            if call.tool == "lookup_indicator" and out:
                findings.append(
                    {
                        "type": out["type"],
                        "value": out["value"],
                        "verdict": out["verdict"],
                        "source": out["source"],
                        "detail": out["detail"]["text"],
                        "kev": None,
                    }
                )
            elif call.tool == "lookup_kev":
                findings.append(
                    {
                        "type": "cve",
                        "value": call.args["cveId"],
                        "verdict": "malicious" if out else "unknown",
                        "source": "CISA KEV",
                        "detail": (out or {}).get("name", "Not listed"),
                        "kev": out,
                    }
                )
            elif call.tool == "lookup_attack_technique" and out:
                techs.append({"techniqueId": out["techniqueId"], "name": out["name"], "tactics": out["tactics"]})
        asset_notes = []
        for h in ctx.entities.hosts[:3]:
            out = await _tool(
                rec,
                tools,
                policy.ToolCall("assets", "asset_exposure", {"name": h}, f"Checked what {h} does for the business"),
            )
            if out and out.get("asset"):
                a = out["asset"]
                note = f"{a['name']} ({a.get('businessService') or a['kind']}, {a['site']})"
                if out.get("crownJewel"):
                    note += " is a crown-jewel system"
                if out.get("kev"):
                    note += "; has known exploited vulnerabilities " + ", ".join(k["cveId"] for k in out["kev"])
                asset_notes.append(note)
        identity_notes = []
        for u in list(ctx.entities.users)[:2] if len(ctx.entities.users) <= 5 else []:
            out = await _tool(
                rec, tools, policy.ToolCall("identity", "get_identity", {"usernameOrEmail": u}, f"Looked up who {u} is")
            )
            if out:
                kind = {"service": "service account", "contractor": "contractor"}.get(out["kind"], "employee")
                priv = ", privileged" if out["privileged"] else ""
                identity_notes.append(f"{u}: {out['displayName']}, {out['title']} ({kind}{priv})")
        hits = [f for f in findings if f["verdict"] in ("malicious", "suspicious")]
        await rec.step(
            "finding",
            f"Enrichment done: {len(hits)} intelligence hit{'s' if len(hits) != 1 else ''}, "
            f"{len(asset_notes)} asset note{'s' if len(asset_notes) != 1 else ''}",
            detail={"output": {"indicators": findings, "techniques": techs}},
        )
        await rec.step("handoff_out", "Handed the findings back to the triage colleague")
        return {"indicators": findings, "techniques": techs, "assetNotes": asset_notes, "identityNotes": identity_notes}


# ─────────────────────────────────────────────────────────────────────────────
# Response
# ─────────────────────────────────────────────────────────────────────────────
def _claims(token: str) -> dict[str, Any]:
    """Read (not verify) the approval token body: action and target. The tool server verifies the signature."""
    try:
        body = token.split(".", 1)[0]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return {}


class ResponseExecutor(BaseExecutor):
    agent = "response"
    artifact_name = "plan"

    def artifact_for(self, payload: dict[str, Any]) -> str:
        return "result" if payload.get("skill") == "execute_action" or "approvalId" in payload else "plan"

    async def handle(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        if self.artifact_for(payload) == "result":
            return await self.execute_action(payload, task_id)
        return await self.plan(payload, task_id)

    async def plan(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        from nightjar.incidents import api as incidents

        run_id, org_id, incident_id = _uuid(payload["runId"]), _uuid(payload["orgId"]), _uuid(payload["incidentId"])
        rec = Recorder(run_id, "response", task_id)
        verdict = payload.get("verdict") or {}
        await rec.step(
            "handoff_in",
            "Received the verdict from the triage colleague",
            detail={"input": {"verdict": verdict.get("verdict")}},
        )
        ctx = await incidents.get_incident_context(org_id, incident_id)
        proposals = policy.response_plan(ctx, verdict.get("verdict", "needs_review"), payload.get("enrichment"))
        await rec.step(
            "plan",
            f"Planned {len(proposals)} action{'s' if len(proposals) != 1 else ''} for people to approve",
            detail={"input": [to_doc(p) for p in proposals]},
            hash_of=[to_doc(p) for p in proposals],
        )
        tools = ToolClient("response", org_id)
        out: list[dict[str, Any]] = []
        for p in proposals:
            spec = ACTIONS[p.kind]
            t0 = time.perf_counter()
            if p.kind == "open_ticket":
                ticket, err, text = await tools.call(
                    "ticketing",
                    "create_ticket",
                    {
                        "incidentId": str(incident_id),
                        "title": f"{ctx.reference}: {ctx.title}"[:200],
                        "body": f"{verdict.get('summary', '')}\n\nOpened by the response colleague.",
                        "priority": "p1" if ctx.severity == "critical" else "p2",
                        "assigneeTeam": "Security operations",
                    },
                )
                await rec.step(
                    "tool_result" if not err else "error",
                    f"Opened ticket {ticket.get('key')}" if ticket else "Could not open a ticket",
                    tool="ticketing.create_ticket",
                    detail={"output": ticket, "error": text or None},
                    started=t0,
                )
            args = {
                "incidentId": str(incident_id),
                "runId": str(run_id),
                "kind": p.kind,
                "target": p.target,
                "rationale": p.rationale,
            }
            ref, err, text = await tools.call("response", "propose_action", args)
            title = f"Proposed: {spec.label} ({p.target})" if p.target_type != "ticket" else f"Recorded: {spec.label}"
            if ref and ref.get("status") == "pending":
                title += " — waiting for approval"
            await rec.step(
                "proposal" if not err else "guardrail",
                title if not err else f"Proposal refused: {text[:120]}",
                tool="response.propose_action",
                detail={"input": args, "output": ref, "error": text or None},
                hash_of=args,
                started=t0,
                flags=("target_not_in_incident",) if err else (),
            )
            if ref and not err:
                out.append(
                    {
                        **to_doc(p),
                        "approvalId": ref["approvalId"],
                        "status": ref["status"],
                        "requiredRoles": ref["requiredRoles"],
                    }
                )
        await rec.step("handoff_out", "Handed the plan back to the triage colleague")
        return {"proposals": out}

    async def execute_action(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        from nightjar.approvals import api as approvals

        org_id, approval_id = _uuid(payload["orgId"]), _uuid(payload["approvalId"])
        ref = await approvals.get_approval(approval_id)
        token = ref.token or ""
        claims = _claims(token)
        kind = payload.get("kind") or claims.get("act")
        target = payload.get("target") or claims.get("tgt")
        if not kind or kind not in ACTIONS or not ACTIONS[kind].tool:
            return {
                "approvalId": str(approval_id),
                "executed": False,
                "status": "refused",
                "detail": "No approved action to carry out.",
            }
        arg = {"host": "host", "user": "username", "access_key": "accessKeyId", "ip": "ip", "email": "messageId"}[
            ACTIONS[kind].target_type
        ]
        tools = ToolClient("response", org_id)
        result, err, text = await tools.call(
            "response", ACTIONS[kind].tool or "", {"approvalId": str(approval_id), arg: target, "approvalToken": token}
        )
        if err:
            return {"approvalId": str(approval_id), "executed": False, "status": "refused", "detail": text[:300]}
        return result or {}


# ─────────────────────────────────────────────────────────────────────────────
# Triage
# ─────────────────────────────────────────────────────────────────────────────
def _verdict_text(ctx: Any, verdict: str, p: policy.PolicyInputs) -> str:
    probs = (
        f" The strongest alert scores {round(p.max_probability * 100)} in 100 for being real."
        if p.max_probability is not None
        else ""
    )
    if verdict == "benign":
        return (
            f"{ctx.detections.__len__()} alerts came from routine activity: every alert was scored as likely noise "
            f"and nothing matched threat intelligence.{probs} Closing as benign; a person can reopen it."
        )
    if verdict == "true_positive":
        bits = []
        if p.intel_malicious:
            bits.append("known attacker infrastructure")
        if p.ueba_max >= 0.95:
            bits.append("strongly unusual behaviour")
        if len(p.tactics) >= 3:
            bits.append(f"{len(p.tactics)} attack stages")
        if p.crown_jewel:
            bits.append("a crown-jewel system")
        extra = (" Evidence: " + ", ".join(bits) + ".") if bits else ""
        return f"This looks like real attacker activity.{probs}{extra}"
    if verdict == "needs_review" and p.insider_shape:
        return (
            "A person with valid access moved an unusual amount of data. The signals say this is out of pattern, "
            f"but intent needs a person's judgement.{probs}"
        )
    if verdict == "false_positive":
        return f"The alerts do not hold up: low model scores and no corroborating evidence.{probs}"
    return f"The evidence points in different directions; a person should review.{probs}"


class TriageExecutor(BaseExecutor):
    agent = "triage"
    artifact_name = "verdict"

    async def handle(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        from nightjar.incidents import api as incidents
        from nightjar.investigations import api as investigations
        from nightjar.llm import api as llm

        run_id, org_id, incident_id = _uuid(payload["runId"]), _uuid(payload["orgId"]), _uuid(payload["incidentId"])
        rec = Recorder(run_id, "triage", task_id)
        started = time.perf_counter()
        try:
            await investigations.mark_running(run_id)
            await rec.step(
                "handoff_in", "Picked up the incident", detail={"input": {"trigger": payload.get("trigger")}}
            )
            ctx = await incidents.get_incident_context(org_id, incident_id)
            checks = policy.triage_checks(ctx)
            await rec.step(
                "plan",
                f"Planned {len(checks)} checks",
                detail={"input": [c.__dict__ for c in checks]},
                hash_of=[c.__dict__ for c in checks],
            )
            from nightjar.ingest.normalizer import INSTRUCTION_FLAGS

            flagged = sorted(
                {f for d in ctx.detections for s in d.evidence for f in s.summary.flags if f in INSTRUCTION_FLAGS}
            )
            if flagged:
                await rec.step(
                    "guardrail",
                    "Some log text was written like instructions; it was treated as evidence only",
                    flags=tuple(flagged),
                    detail={"fields": flagged},
                )
            tools = ToolClient("triage", org_id)
            for c in checks:
                await _tool(rec, tools, c)
            enrich_task = {
                "runId": str(run_id),
                "orgId": str(org_id),
                "incidentId": str(incident_id),
                "indicators": policy.enrichment_indicators(ctx),
                "techniques": [a.technique_id for a in ctx.attack][:6],
            }
            await rec.step(
                "handoff_out",
                "Asked the enrichment colleague for intelligence and asset context",
                detail={"input": enrich_task},
                hash_of=enrich_task,
            )
            enr = await call_agent("triage", "enrichment", enrich_task)
            enrichment = enr["artifact"] if enr["state"] == "TASK_STATE_COMPLETED" else {}
            await rec.step(
                "handoff_in",
                "Enrichment came back" if enrichment else "Enrichment did not complete",
                detail={"output": {"indicators": len(enrichment.get("indicators", []))}},
            )
            inputs = policy.policy_inputs(ctx, enrichment)
            verdict, confidence = policy.decide(inputs)
            evidence = policy.evidence_for(ctx, enrichment)
            facts = policy.facts_for(ctx, verdict, inputs)
            baseline = {"summary": _verdict_text(ctx, verdict, inputs), "hypotheses": [e.text for e in evidence[:3]]}
            untrusted: dict[str, UntrustedText] = {}
            for i, d in enumerate(ctx.detections[:5]):
                for j, s in enumerate(d.evidence[:2]):
                    untrusted[f"evidence.{i}.{j}"] = s.summary
            gen = await llm.generate(
                "triage_summary",
                instructions="Write a two-sentence summary of the verdict for a non-technical reader, citing evidence.",
                trusted={
                    "verdict": verdict,
                    "kind": ctx.kind,
                    "reference": ctx.reference,
                    "baseline": baseline,
                    "policyInputs": inputs.to_dict(),
                },
                untrusted=untrusted,
                schema=TriageNarrative,
            )
            summary = neutralise(gen.output.get("summary", baseline["summary"]), field="model.summary").text
            model_prob = inputs.max_probability
            tv = TriageVerdict(
                verdict=verdict,  # type: ignore[arg-type]
                confidence=confidence,
                summary=summary,
                evidence=evidence,
                model_probability=model_prob,
                facts=facts,
            )
            await rec.step(
                "verdict",
                {
                    "true_positive": "Verdict: real attack activity",
                    "benign": "Verdict: benign",
                    "false_positive": "Verdict: false alarm",
                    "needs_review": "Verdict: needs a person's review",
                }[verdict],
                model=gen.model,
                input_hash_value=gen.input_hash,
                detail={
                    "policyInputs": inputs.to_dict(),
                    "verdict": verdict,
                    "confidence": confidence,
                    "summary": summary,
                    "fallbackUsed": gen.fallback_used,
                },
            )
            proposals: tuple[ProposedAction, ...] = ()
            plan_docs: list[dict[str, Any]] = []
            if verdict in ("true_positive", "needs_review"):
                plan_task = {
                    "runId": str(run_id),
                    "orgId": str(org_id),
                    "incidentId": str(incident_id),
                    "verdict": to_doc(tv),
                    "enrichment": enrichment,
                    "skill": "plan_response",
                }
                await rec.step(
                    "handoff_out",
                    "Asked the response colleague for a containment plan",
                    detail={"input": {"verdict": verdict, "incident": str(incident_id)}},
                    hash_of={"verdict": verdict, "incident": str(incident_id)},
                )
                res = await call_agent("triage", "response", plan_task)
                plan_docs = (
                    (res["artifact"] or {}).get("proposals", []) if res["state"] == "TASK_STATE_COMPLETED" else []
                )
                proposals = tuple(
                    ProposedAction(
                        kind=p["kind"],
                        target=p["target"],
                        target_type=p["targetType"],
                        rationale=p["rationale"],
                        expected_effect=p["expectedEffect"],
                        reversible=bool(p["reversible"]),
                    )
                    for p in plan_docs
                )
                await rec.step(
                    "handoff_in", f"Plan received: {len(proposals)} proposal{'s' if len(proposals) != 1 else ''}"
                )
            enrichment_result = _enrichment_result(enrichment)
            await investigations.finish_run(
                run_id, status="completed", verdict=tv, enrichment=enrichment_result, proposals=proposals
            )
            from nightjar.stream.runtime import note_alert_to_triage

            note_alert_to_triage(time.perf_counter() - started)
            return {
                **to_doc(tv),
                "handoffs": [{"to": "Nightjar Enrichment", "state": enr["state"]}],
                "proposals": plan_docs,
                "durationSeconds": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            log.exception("triage_failed", run=str(run_id))
            try:
                await rec.step("error", "The investigation stopped with an error", detail={"error": str(exc)[:300]})
                await investigations.finish_run(
                    run_id,
                    status="failed",
                    verdict=None,
                    enrichment=None,
                    proposals=(),
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            except Exception:
                log.exception("triage_finish_failed")
            raise


def _enrichment_result(doc: dict[str, Any]) -> EnrichmentResult | None:
    if not doc:
        return None
    inds = []
    for i in doc.get("indicators", []):
        kev = from_doc(KevHit, i["kev"]) if i.get("kev") else None
        typ = i["type"] if i["type"] in ("ip", "domain", "url", "hash", "cve", "user", "access_key") else "ip"
        inds.append(
            IndicatorFinding(
                type=typ,  # type: ignore[arg-type]
                value=str(i["value"]),
                verdict=i["verdict"],
                source=i["source"],
                detail=str(i["detail"]),
                kev=kev,
            )
        )
    techs = tuple(AttackRef(t["techniqueId"], t["name"], tuple(t["tactics"])) for t in doc.get("techniques", []))
    return EnrichmentResult(
        indicators=tuple(inds),
        techniques=techs,
        asset_notes=tuple(doc.get("assetNotes", [])),
        identity_notes=tuple(doc.get("identityNotes", [])),
    )


EXECUTORS = {"triage": TriageExecutor, "enrichment": EnrichmentExecutor, "response": ResponseExecutor}


def build_agent_app(agent: str):
    return build_app(agent, EXECUTORS[agent]())


def ensure_inline_agents() -> None:
    from nightjar.agents.a2a import _inline_apps

    for name in EXECUTORS:
        if name not in _inline_apps:
            register_inline(name, build_agent_app(name))


async def run_triage_task(task: dict[str, Any]) -> dict[str, Any]:
    """Orchestrator side: hand a TriageTask to the triage agent over A2A (raises on failure, for retry)."""
    from nightjar.stream.runtime import TransientError

    rec = Recorder(_uuid(task["runId"]), "orchestrator", None)
    await rec.step("handoff_out", "Handed the incident to the triage colleague", detail={"input": task}, hash_of=task)
    res = await call_agent("orchestrator", "triage", task)
    if res["state"] != "TASK_STATE_COMPLETED":
        raise TransientError(f"triage task ended in {res['state']}")
    return res["artifact"]


def triage_input_hash(task: dict[str, Any]) -> str:
    return input_hash(task)


def now_iso() -> str:
    return clock.now().isoformat()
