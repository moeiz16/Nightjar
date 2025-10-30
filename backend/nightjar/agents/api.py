"""Public interface of the agents (builder B). Called by A's incident and approval endpoints."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import httpx

from nightjar.contracts import AgentCardInfo


async def start_investigation(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    trigger: Literal["auto", "manual", "rerun"],
    requested_by: uuid.UUID | None,
) -> uuid.UUID:
    """Create the run through `investigations.api.start_run`, publish `nj.agents.tasks.<org>` and return the run
    id. The orchestrator hands the task to the Triage agent over A2A; triage → enrichment → response hand-offs
    are A2A tasks too. Auto investigations start when an incident is created (org setting `autoInvestigate`)."""
    from nightjar.investigations import api as investigations
    from nightjar.stream import codec
    from nightjar.stream.runtime import get_bus
    from nightjar.stream.topology import agent_tasks_subject

    ref = await investigations.start_run(org_id, incident_id, trigger=trigger, requested_by=requested_by)
    task = {
        "runId": str(ref.run_id),
        "orgId": str(org_id),
        "incidentId": str(incident_id),
        "trigger": trigger,
        "traceId": uuid.uuid4().hex,
    }
    bus = await get_bus()
    await bus.publish(
        agent_tasks_subject(org_id),
        codec.dumps(task),
        msg_id=f"run-{ref.run_id}",
        headers={"Nj-Org": str(org_id), "Nj-Schema": "agent_task.v1", "Nj-Trace": task["traceId"]},
    )
    return ref.run_id


async def execute_approved_action(org_id: uuid.UUID, approval_id: uuid.UUID) -> None:
    """Called by A right after the last required human approval. Publishes `nj.actions.approved.<org>`; the
    Response agent executes through the response tool server with the approval token. The outcome arrives via
    `approvals.api.record_result`."""
    from nightjar.stream import codec
    from nightjar.stream.runtime import get_bus
    from nightjar.stream.topology import approved_actions_subject

    bus = await get_bus()
    payload = {
        "orgId": str(org_id),
        "approvalId": str(approval_id),
        "traceId": uuid.uuid4().hex,
        "skill": "execute_action",
    }
    await bus.publish(
        approved_actions_subject(org_id),
        codec.dumps(payload),
        msg_id=f"approval-{approval_id}",
        headers={"Nj-Org": str(org_id), "Nj-Schema": "approved_action.v1"},
    )


def agent_cards() -> list[AgentCardInfo]:
    from nightjar.agents.a2a import CARD_INFO, agent_url
    from nightjar.mcp_servers.auth import SCOPES

    return [
        AgentCardInfo(
            agent=name,  # type: ignore[arg-type]
            name=info["name"],
            url=agent_url(name),
            skills=tuple(s[0] for s in info["skills"]),
            tool_scopes=SCOPES[name],
            healthy=True,
        )
        for name, info in CARD_INFO.items()
    ]


async def agents_health() -> list[AgentCardInfo]:
    """Cards with `healthy` from a live card fetch (1 s timeout each)."""
    from dataclasses import replace

    from nightjar.core.config import get_settings

    cards = agent_cards()
    if get_settings().agents_transport == "inline":
        return cards
    out = []
    async with httpx.AsyncClient(timeout=1.0) as http:
        for c in cards:
            try:
                r = await http.get(f"{c.url}/.well-known/agent-card.json")
                ok = r.status_code == 200 and r.json().get("name") == c.name
            except httpx.HTTPError:
                ok = False
            out.append(replace(c, healthy=ok))
    return out


async def replay_verify(
    org_id: uuid.UUID, run_id: uuid.UUID, steps: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Re-run the offline policy over a run's recorded inputs and compare (GET /investigations/{id}/replay).

    `steps` are the run's AgentStepDoc dicts (camelCase, with `detail` and `inputHash`); when omitted they are read
    through `investigations.api.get_run_steps(run_id)` if the platform provides it.
    {inputHashesMatch, offlineReplayVerdict, recordedVerdict, verdictMatches, checkedSteps}"""
    from nightjar.agents.a2a import input_hash
    from nightjar.agents.policy import PolicyInputs, decide

    if steps is None:
        from nightjar.investigations import api as investigations

        getter = getattr(investigations, "get_run_steps", None)
        steps = list(await getter(run_id)) if getter else []
    checked = 0
    hashes_ok = True
    verdict_step = None
    for s in steps:
        detail = s.get("detail") or {}
        h = s.get("inputHash") or s.get("input_hash")
        if s.get("kind") == "verdict":
            verdict_step = s
            continue
        if h and "input" in detail:
            checked += 1
            if input_hash(detail["input"]) != h:
                hashes_ok = False
    offline = recorded = None
    if verdict_step:
        detail = verdict_step.get("detail") or {}
        recorded = detail.get("verdict")
        pi = detail.get("policyInputs")
        if pi:
            offline, _ = decide(PolicyInputs(**pi))
    return {
        "inputHashesMatch": hashes_ok,
        "offlineReplayVerdict": offline,
        "recordedVerdict": recorded,
        "verdictMatches": offline is not None and offline == recorded,
        "checkedSteps": checked,
    }
