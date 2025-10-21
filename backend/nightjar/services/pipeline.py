"""Platform-side wrappers around builder B's interfaces (stream, intel, llm, agents, ml, detect).

The platform keeps working when a pipeline component is not running or not yet available: each wrapper turns
`NotImplementedError` (and connection failures, where stated) into a plain fallback, so the API answers with
"offline" states or `503 unavailable` instead of a 500.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import structlog
from pydantic import BaseModel

from nightjar.contracts import PipelineHealth, UntrustedText, to_doc

log = structlog.get_logger(__name__)

_TACTIC_LABELS = {
    "reconnaissance": "Reconnaissance",
    "resource-development": "Resource development",
    "initial-access": "Initial access",
    "execution": "Execution",
    "persistence": "Persistence",
    "privilege-escalation": "Privilege escalation",
    "defense-evasion": "Defence evasion",
    "credential-access": "Credential access",
    "discovery": "Discovery",
    "lateral-movement": "Lateral movement",
    "collection": "Collection",
    "command-and-control": "Command and control",
    "exfiltration": "Exfiltration",
    "impact": "Impact",
}


def tactic_label(tactic: str) -> str:
    try:
        from nightjar.intel import api as intel

        return intel.attack_tactic_label(tactic)
    except Exception:
        return _TACTIC_LABELS.get(tactic, tactic.replace("-", " ").capitalize())


def kev_docs(cve_ids: list[str]) -> list[dict]:
    """`KevHitDoc[]` for an asset's CVEs (entries added after the demo date are hidden by B's intel). Falls back
    to bare CVE ids when the intel bundle is unavailable."""
    if not cve_ids:
        return []
    try:
        from nightjar.intel import api as intel

        hits = {h.cve_id: to_doc(h) for h in intel.kev_for_assets(list(cve_ids))}
    except Exception:
        return [{"cveId": c} for c in cve_ids]
    return [hits.get(c, {"cveId": c}) for c in cve_ids]


def technique_detail(technique_id: str) -> dict | None:
    try:
        from nightjar.intel import api as intel

        detail = getattr(intel, "attack_technique_detail", None)
        if detail is not None:
            found = detail(technique_id)
            if found:
                return dict(found)
        ref = intel.attack_technique(technique_id)
    except Exception:
        return None
    if ref is None:
        return None
    return {
        "techniqueId": ref.technique_id,
        "name": ref.name,
        "tactics": list(ref.tactics),
        "description": "",
        "url": "https://attack.mitre.org/techniques/" + ref.technique_id.replace(".", "/") + "/",
    }


OFFLINE_HEALTH = PipelineHealth(
    mode="memory",
    connected=False,
    status="offline",
    plain_status="Event intake is paused",
    events_per_minute=0.0,
    streams=(),
    dlq_messages=0,
    alert_to_triage_p50_s=None,
    alert_to_triage_p95_s=None,
)


async def pipeline_health() -> PipelineHealth:
    try:
        from nightjar.stream import api as stream

        return await stream.pipeline_health()
    except Exception as exc:
        log.debug("pipeline_health_unavailable", error=type(exc).__name__)
        return OFFLINE_HEALTH


async def generate_or_baseline(
    task: str,
    *,
    instructions: str,
    trusted: dict[str, Any],
    untrusted: dict[str, UntrustedText],
    schema: type[BaseModel],
    baseline: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Generated text through B's `llm.generate` (offline fake by default, which returns `trusted["baseline"]`
    unchanged for these tasks). Falls back to the deterministic baseline if generation is unavailable or the
    output does not validate. Returns (output, generated_by)."""
    try:
        from nightjar.llm import api as llm

        result = await llm.generate(
            task,
            instructions=instructions,
            trusted={**trusted, "baseline": baseline},
            untrusted=untrusted,
            schema=schema,
        )
        output = schema.model_validate(result.output).model_dump()
        return output, ("offline" if result.model == "offline-fake" else "cloud")
    except Exception as exc:
        log.info("generation_fallback", task=task, error=type(exc).__name__)
        return schema.model_validate(baseline).model_dump(), "offline"


def neutralise(value: object, *, field: str, max_len: int = 2000) -> UntrustedText:
    try:
        from nightjar import safety

        return safety.neutralise(value, field=field, max_len=max_len)
    except NotImplementedError:
        text = " ".join(str(value or "").split())
        text = "".join(ch for ch in text if ch.isprintable())
        return UntrustedText(text=text[:max_len], field=field, truncated=len(text) > max_len)


def stable_hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


async def start_investigation(
    org_id: uuid.UUID, incident_id: uuid.UUID, *, trigger: str, requested_by: uuid.UUID | None
) -> uuid.UUID:
    """B's agents; raises `AgentsUnavailableError` when they cannot take the task."""
    try:
        from nightjar.agents import api as agents

        return await agents.start_investigation(
            org_id,
            incident_id,
            trigger=trigger,  # type: ignore[arg-type]
            requested_by=requested_by,
        )
    except Exception as exc:
        raise AgentsUnavailableError(str(exc) or type(exc).__name__) from exc


class AgentsUnavailableError(Exception):
    pass


async def execute_approved_action(org_id: uuid.UUID, approval_id: uuid.UUID) -> bool:
    """Hand the approved action to B's response agent. False (logged, retried by the sweeper) if unavailable."""
    try:
        from nightjar.agents import api as agents

        await agents.execute_approved_action(org_id, approval_id)
        return True
    except Exception as exc:
        log.warning("execute_approved_action_unavailable", approval_id=str(approval_id), error=type(exc).__name__)
        return False
