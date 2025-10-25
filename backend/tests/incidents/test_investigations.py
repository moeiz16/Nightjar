"""Investigation runs: gap-free step log, verdict application, regime proposals and the HTTP views."""

from __future__ import annotations

import asyncio
import uuid

import httpx
from sqlalchemy import select

from nightjar import clock
from nightjar.contracts import AgentStep
from nightjar.db.models.soc import AgentStepRow, Incident, RegimeAssessmentRow
from nightjar.db.session import session_scope
from nightjar.investigations import api as inv
from tests import factories as f

API = "/api/v1"


async def test_steps_are_gap_free_under_concurrency(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    run = await inv.start_run(h["org_id"], ref.id, trigger="auto", requested_by=None)
    step = AgentStep(agent="triage", kind="tool_call", title="Checked", started_at=clock.now(), duration_ms=3)
    seqs = await asyncio.gather(*(inv.append_step(run.run_id, step) for _ in range(12)))
    assert sorted(seqs) == list(range(1, 13))
    async with session_scope() as db:
        rows = (await db.execute(select(AgentStepRow.seq).where(AgentStepRow.run_id == run.run_id))).scalars().all()
        assert sorted(rows) == list(range(1, 13))


async def test_start_run_rejects_other_org(client: httpx.AsyncClient) -> None:
    h, b = await f.halden(), await f.brightline()
    ref = await f.incident(h["org_id"])
    import pytest

    with pytest.raises(KeyError):
        await inv.start_run(b["org_id"], ref.id, trigger="auto", requested_by=None)


async def test_true_positive_moves_to_investigate_and_records_triage_time(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    await f.triage(h["org_id"], ref.id, verdict="true_positive")
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None and inc.status == "investigating" and inc.stage == "investigate"
        assert inc.triaged_at is not None and inc.verdict == "true_positive"


async def test_benign_verdict_resolves_reversibly(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], noise=True, kind="benign_activity", severity="low")
    await f.triage(h["org_id"], ref.id, verdict="benign")
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None and inc.status == "resolved" and inc.resolution["reason"] == "benign"
        assert inc.resolution["reversible"] is True


async def test_needs_review_stays_in_triage(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    await f.triage(h["org_id"], ref.id, verdict="needs_review", confidence=0.58)
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None and inc.status == "triage" and inc.verdict == "needs_review"


async def test_finish_run_is_idempotent(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    run_id = await f.triage(h["org_id"], ref.id)
    await inv.finish_run(run_id, status="failed", verdict=None, enrichment=None, proposals=(), error="late")
    async with session_scope() as db:
        assert (await db.get(Incident, ref.id)).verdict == "true_positive"  # type: ignore[union-attr]


async def test_facts_create_regime_proposals_never_determinations(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], hosts=("mem-dispatch-01", "ant-wms-01"), users=("svc-dispatch",))
    await f.triage(h["org_id"], ref.id, with_facts=f.facts(affected_jurisdictions=("EU", "US", "AU")))
    async with session_scope() as db:
        rows = (
            (await db.execute(select(RegimeAssessmentRow).where(RegimeAssessmentRow.incident_id == ref.id)))
            .scalars()
            .all()
        )
        got = {r.regime: r.proposal["applies"] for r in rows}
        assert got == {"sec_8k": "assess", "nis2": "yes", "gdpr_art33": "no", "soci": "yes"}
        assert all(r.determination is None for r in rows)


async def test_run_views_and_step_detail(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    run_id = await f.triage(h["org_id"], ref.id)
    await f.sign_in(client, f.HALDEN["auditor"])
    runs = (await client.get(f"{API}/incidents/{ref.reference}/investigations")).json()
    assert runs[0]["id"] == str(run_id) and runs[0]["stepCount"] == 3 and runs[0]["verdict"] == "true_positive"
    run = (await client.get(f"{API}/investigations/{run_id}")).json()
    assert [s["seq"] for s in run["steps"]] == [1, 2, 3] and "detail" not in run["steps"][0]
    step = (await client.get(f"{API}/investigations/{run_id}/steps/2")).json()
    assert step["detail"] == {"input": {"i": 1}} and step["kind"] == "handoff_out"
    assert (await client.get(f"{API}/investigations/{run_id}/steps/99")).status_code == 404
    replay = (await client.get(f"{API}/investigations/{run_id}/replay")).json()
    assert replay["verification"]["inputHashesMatch"] is True
    assert replay["verification"]["recordedVerdict"] == "true_positive"
    tl = (await client.get(f"{API}/incidents/{ref.id}/timeline")).json()
    assert any(e["kind"] == "handoff" for e in tl) and any(e["kind"] == "agent" and e["executive"] for e in tl)


async def test_rerun_needs_agents(client: httpx.AsyncClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN["responder"])
    from nightjar.agents import api as agents_api

    async def down(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise ConnectionError("triage colleague not reachable")

    monkeypatch.setattr(agents_api, "start_investigation", down)
    r = await client.post(f"{API}/incidents/{ref.id}/investigations", json={"reason": "new evidence"}, headers=headers)
    assert r.status_code == 503 and r.json()["code"] == "agents_unavailable"

    async def fake_start(org_id, incident_id, *, trigger, requested_by):  # type: ignore[no-untyped-def]
        run = await inv.start_run(org_id, incident_id, trigger=trigger, requested_by=requested_by)
        return run.run_id

    monkeypatch.setattr(agents_api, "start_investigation", fake_start)
    r = await client.post(f"{API}/incidents/{ref.id}/investigations", json={"reason": "new evidence"}, headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert (
        body["trigger"] == "manual"
        and body["requestedBy"]["fullName"] == "Marcus Bell"
        and body["reason"] == "new evidence"
    )


async def test_auditor_cannot_rerun(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN["auditor"])
    assert (await client.post(f"{API}/incidents/{ref.id}/investigations", json={}, headers=headers)).status_code == 403
    _ = uuid
