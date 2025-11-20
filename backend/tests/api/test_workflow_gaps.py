"""Inventory lists, the executive's evidence view, hunt matches as evidence, step feedback ("Disagree"), deferred
regulatory decisions ("Not yet: decide by…"), rule exceptions ("Lower the noise", "Mark as expected"), rule hit
counts and the river's per-minute totals."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
from sqlalchemy import select

from nightjar import clock
from nightjar.db.models.platform import AuditLog
from nightjar.db.models.soc import StepFeedback
from nightjar.db.session import session_scope
from nightjar.detect.engine import rulebook
from nightjar.hunting.service import handle_normalized
from nightjar.seed import data
from tests import factories as f

API = "/api/v1"


def _sigma_rule() -> str:
    book = rulebook()
    return next(rid for rid in book.meta if rid not in book.hidden)


# ── Inventory ─────────────────────────────────────────────────────────────────
async def test_asset_and_identity_lists_are_tenant_scoped_and_filterable(client: httpx.AsyncClient) -> None:
    await f.halden()
    await f.brightline()
    await f.sign_in(client, f.HALDEN["executive"])
    assets = (await client.get(f"{API}/assets")).json()
    assert len(assets) == 18 and assets[0]["criticality"] == "crown_jewel"
    assert not {a["name"] for a in assets} & {
        a[0] if isinstance(a, tuple) else a["name"] for a in data.BRIGHTLINE_ASSETS
    }
    citrix = next(a for a in assets if a["name"] == "mem-citrix-01")
    assert citrix["vulnerabilities"][0]["cveId"] == "CVE-2023-4966"
    assert [a["name"] for a in (await client.get(f"{API}/assets?q=dispatch")).json()] == [
        "mem-dispatch-01",
        "mem-sql-01",
    ]
    crown = (await client.get(f"{API}/assets?criticality=crown_jewel")).json()
    assert {a["name"] for a in crown} == {"mem-dispatch-01", "ant-wms-01", "ant-dc-01", "okta"}
    people = (await client.get(f"{API}/identities")).json()
    assert len(people) == 24 and any(p["username"] == "j.okafor" for p in people)
    privileged = (await client.get(f"{API}/identities?privileged=true")).json()
    assert {p["username"] for p in privileged} >= {"adm-ejanssens", "svc-dispatch", "svc-backup"}
    assert (await client.get(f"{API}/identities?q=okafor")).json()[0]["displayName"] == "Joy Okafor"


async def test_inventory_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{API}/assets")).status_code == 401


# ── Evidence: the executive's summary, and hunt matches ──────────────────────
async def test_executive_reads_a_summary_of_the_evidence_but_not_the_detections(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], hosts=("ant-ws-014",), detections=3)
    await f.sign_in(client, f.HALDEN["executive"])
    assert (await client.get(f"{API}/incidents/{ref.id}/detections")).status_code == 403
    ev = (await client.get(f"{API}/incidents/{ref.id}/evidence")).json()
    assert len(ev["detections"]) == 3 and ev["huntMatches"] == []
    item = ev["detections"][0]
    assert item["hosts"] == ["ant-ws-014"] and item["users"] == ["j.okafor"] and item["eventCount"] == 1
    assert not {"fields", "evidence", "summary", "entities", "ruleId"} & set(item)


async def test_a_hunt_match_is_added_once_and_audited(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    ev = f.event(h["org_id"], action="mfa-push-denied", outcome="failure", category=("authentication",))
    await handle_normalized([ev])
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    url = f"{API}/incidents/{ref.id}/evidence"
    r = await client.post(url, json={"eventId": ev.event_id, "note": "Same pattern"}, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "detection" and r.json()["title"].startswith("Added from a hunt: j.okafor refused")
    assert r.json()["refId"] == ev.event_id and r.json()["actor"]["name"] == "Aiko Tanaka"
    again = await client.post(url, json={"eventId": ev.event_id}, headers=headers)
    assert again.status_code == 409
    missing = await client.post(url, json={"eventId": uuid.uuid4().hex}, headers=headers)
    assert missing.status_code == 404
    assert (await client.post(url, json={"eventId": "not-hex"}, headers=headers)).status_code == 422
    summary = (await client.get(url)).json()
    assert summary["huntMatches"][0]["refId"] == ev.event_id
    async with session_scope() as db:
        actions = (await db.execute(select(AuditLog.action).where(AuditLog.org_id == h["org_id"]))).scalars().all()
    assert "incident.evidence_added" in actions
    exec_headers = await f.sign_in(client, f.HALDEN["executive"])
    assert (await client.post(url, json={"eventId": ev.event_id}, headers=exec_headers)).status_code == 403


# ── Step feedback ────────────────────────────────────────────────────────────
async def test_disagree_records_a_labelled_verdict_without_changing_the_run(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], detections=2)
    run_id = await f.triage(h["org_id"], ref.id, verdict="true_positive")
    headers = await f.sign_in(client, f.HALDEN["responder"])
    body = {"verdict": "benign", "note": "Joy confirmed she was travelling."}
    r = await client.post(f"{API}/investigations/{run_id}/steps/1/feedback", json=body, headers=headers)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["verdict"] == "benign" and out["recordedVerdict"] == "true_positive" and out["labelledDetections"] == 2
    async with session_scope() as db:
        row = (await db.execute(select(StepFeedback))).scalar_one()
    assert row.user_name == "Marcus Bell" and len(row.label_detection_ids) == 2
    run = (await client.get(f"{API}/investigations/{run_id}")).json()
    assert run["verdict"] == "true_positive"  # the record is never rewritten
    tl = (await client.get(f"{API}/incidents/{ref.id}/timeline")).json()
    assert any(e["title"].startswith("Disagreed with “Step 1”: this is expected activity") for e in tl)
    assert (
        await client.post(f"{API}/investigations/{run_id}/steps/99/feedback", json=body, headers=headers)
    ).status_code == 404
    auditor = await f.sign_in(client, f.HALDEN["auditor"])
    assert (
        await client.post(f"{API}/investigations/{run_id}/steps/1/feedback", json=body, headers=auditor)
    ).status_code == 403


# ── Not yet: decide by… ──────────────────────────────────────────────────────
async def test_sec_decision_can_be_deferred_with_a_reminder(client: httpx.AsyncClient) -> None:
    from nightjar.regimes.service import sweep

    h = await f.halden()
    ref = await f.incident(h["org_id"], hosts=("mem-dispatch-01",), kind="ransomware_precursor")
    await f.triage(h["org_id"], ref.id, with_facts=f.facts(affected_jurisdictions=("EU", "US")))
    url = f"{API}/incidents/{ref.id}/reporting/sec_8k/defer"
    decide_by = (clock.now() + timedelta(days=1)).isoformat()
    body = {"entityId": "halden-us", "decideBy": decide_by, "note": "Waiting for the forensic scope"}
    responder = await f.sign_in(client, f.HALDEN["responder"])
    assert (await client.post(url, json=body, headers=responder)).status_code == 403
    headers = await f.sign_in(client, f.HALDEN["executive"])
    r = await client.post(url, json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["deferral"]["by"] == "Ingrid Vos" and r.json()["determination"] is None
    clocks = (await client.get(f"{API}/reporting/clocks")).json()
    sec = next(c for c in clocks if c["regime"] == "sec_8k")
    assert sec["state"] == "not_started" and sec["noticeLabel"] == "Decision due" and sec["decideBy"]
    assert sec["basis"].startswith("Decision set for")
    past = {**body, "decideBy": (clock.now() - timedelta(hours=1)).isoformat()}
    assert (await client.post(url, json=past, headers=headers)).status_code == 422
    tl = (await client.get(f"{API}/incidents/{ref.id}/timeline?audience=executive")).json()
    assert any("decision set for" in e["title"] for e in tl)
    with clock.travel(clock.now() + timedelta(days=2)):
        async with session_scope() as db:
            assert await sweep(db) >= 1
        async with session_scope() as db:
            assert await sweep(db) == 0  # reminded once
    tl = (await client.get(f"{API}/incidents/{ref.id}/timeline")).json()
    assert any(e["title"].startswith("Reminder: the SEC") for e in tl)
    assess = await client.post(
        f"{API}/incidents/{ref.id}/reporting/sec_8k/assess",
        json={"applies": False, "rationale": "Not material: contained quickly", "entityId": "halden-us"},
        headers=headers,
    )
    assert assess.status_code == 200
    assert (await client.post(url, json=body, headers=headers)).status_code == 409


# ── Rule exceptions and hit counts ───────────────────────────────────────────
async def test_rule_exceptions_are_audited_and_listed_on_the_rule(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    rid = _sigma_rule()
    body = {"ruleId": rid, "field": "user", "value": "svc-backup", "reason": "Nightly backup agent rollout"}
    responder = await f.sign_in(client, f.HALDEN["responder"])
    assert (await client.post(f"{API}/suppressions", json=body, headers=responder)).status_code == 403
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    r = await client.post(f"{API}/suppressions", json={**body, "expiresInDays": 30}, headers=headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["createdBy"] == "Aiko Tanaka" and r.json()["expiresAt"]
    assert (
        await client.post(f"{API}/suppressions", json={**body, "ruleId": "nope"}, headers=headers)
    ).status_code == 404
    short = {**body, "reason": "too short"}
    assert (await client.post(f"{API}/suppressions", json=short, headers=headers)).status_code == 422
    detail = (await client.get(f"{API}/rules/{rid}")).json()
    assert detail["exceptions"][0]["value"] == "svc-backup" and detail["rule"]["exceptions"] == 1
    ueba = {"ruleId": "ueba:user:*", "field": "user", "value": "t.reyes", "reason": "Quarter-end reporting run"}
    assert (await client.post(f"{API}/suppressions", json=ueba, headers=headers)).status_code == 201
    assert len((await client.get(f"{API}/suppressions")).json()) == 2
    assert (await client.delete(f"{API}/suppressions/{sid}", headers=headers)).status_code == 204
    assert (await client.delete(f"{API}/suppressions/{sid}", headers=headers)).status_code == 404
    assert (await client.get(f"{API}/rules/{rid}")).json()["exceptions"] == []
    async with session_scope() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.org_id == h["org_id"]))).scalars().all()
    labels = {r.action for r in rows}
    assert {"rule.exception_added", "rule.exception_removed"} <= labels


async def test_the_scorer_marks_detections_matching_an_exception_as_suppressed(app: object) -> None:
    from nightjar.detect import api as detect
    from nightjar.stream.bus import MemoryBus
    from nightjar.workers.pipeline import Pipeline

    h = await f.halden()
    rid = _sigma_rule()
    await detect.add_suppression(
        h["org_id"],
        rid,
        field="user",
        value="svc-backup",
        reason="Backup rollout",
        created_by_id=None,
        created_by="Aiko",
    )
    pipe = Pipeline(MemoryBus())
    hit = replace(f.detection(h["org_id"], users=("svc-backup",)), rule_id=rid)
    miss = replace(f.detection(h["org_id"], users=("j.okafor",)), rule_id=rid)
    other = replace(f.detection(h["org_id"], users=("svc-backup",)), rule_id="another-rule")
    out = await pipe._apply_suppressions(hit)
    assert out.triage is not None and out.triage.suppressed
    assert out.triage.reasons[0].text == "Marked as expected by Aiko: Backup rollout"
    assert not (await pipe._apply_suppressions(miss)).triage.suppressed  # type: ignore[union-attr]
    assert not (await pipe._apply_suppressions(other)).triage.suppressed  # type: ignore[union-attr]
    ueba = replace(f.detection(h["org_id"], users=("t.reyes",), source="ueba"), rule_id="ueba:user:exfil")
    await detect.add_suppression(
        h["org_id"],
        "ueba:user:*",
        field="user",
        value="t.reyes",
        reason="Quarter end",
        created_by_id=None,
        created_by="A",
    )
    pipe._suppressions.clear()
    assert (await pipe._apply_suppressions(ueba)).triage.suppressed  # type: ignore[union-attr]


async def test_rule_hits_count_the_last_seven_days_per_day(client: httpx.AsyncClient) -> None:
    from nightjar.incidents import api as incidents

    h = await f.halden()
    rid = _sigma_rule()
    now = clock.now()
    dets = [
        replace(f.detection(h["org_id"], at=now - timedelta(days=d, minutes=5)), rule_id=rid) for d in (0, 0, 1, 6, 9)
    ]
    await incidents.record_detections(h["org_id"], dets)
    await f.sign_in(client, f.HALDEN["analyst"])
    rule = next(r for r in (await client.get(f"{API}/rules")).json() if r["id"] == rid)
    assert rule["hits7d"] == 4
    assert len(rule["hits7dDaily"]) == 7 and rule["hits7dDaily"][-1] == 2 and rule["hits7dDaily"][-2] == 1
    assert sum(rule["hits7dDaily"]) == 4


# ── The river's minute totals ────────────────────────────────────────────────
async def test_river_reports_exact_totals_for_the_minutes_it_shows(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    base = clock.now().replace(second=5, microsecond=0) - timedelta(minutes=3)
    events = [f.event(h["org_id"], at=base + timedelta(seconds=i)) for i in range(7)]
    events += [f.event(h["org_id"], at=base + timedelta(minutes=1, seconds=i)) for i in range(4)]
    await handle_normalized(events)
    await f.sign_in(client, f.HALDEN["executive"])
    river = (await client.get(f"{API}/events/river?limit=3")).json()
    assert len(river["lines"]) == 3
    counts = {m["minute"][:16]: m["count"] for m in river["minutes"]}
    assert counts == {(base + timedelta(minutes=1)).isoformat()[:16]: 4}


# ── Demo clock: time of day and the shared anchor ────────────────────────────
def test_demo_clock_pins_the_time_of_day_through_a_shared_anchor(tmp_path) -> None:  # type: ignore[no-untyped-def]
    anchor = tmp_path / "demo-clock.json"
    try:
        clock.install("2025-11-20", "10:00", anchor_file=anchor, fresh=True)
        brussels = clock.now().astimezone(__import__("zoneinfo").ZoneInfo("Europe/Brussels"))
        assert (
            brussels.date() == date(2025, 11, 20)
            and abs((datetime.combine(brussels.date(), time(10), brussels.tzinfo) - brussels).total_seconds()) < 5
        )
        doc = json.loads(anchor.read_text())
        assert doc["demoAt"] == "2025-11-20T09:00:00+00:00"
        first = clock.offset()
        # another process reads the same anchor, later
        doc["realAt"] = (datetime.fromisoformat(doc["realAt"]) - timedelta(minutes=20)).isoformat()
        anchor.write_text(json.dumps(doc))
        clock.install("2025-11-20", "10:00", anchor_file=anchor)
        assert abs((clock.offset() - first).total_seconds() - 20 * 60) < 5
        # a stale anchor is replaced
        doc["realAt"] = (clock.real_now() - timedelta(hours=13)).isoformat()
        anchor.write_text(json.dumps(doc))
        clock.install("2025-11-20", "10:00", anchor_file=anchor)
        assert abs((clock.offset() - first).total_seconds()) < 5
        # without a time, the shift stays a whole number of days
        clock.install("2025-11-20")
        assert clock.offset().seconds == 0 and clock.today() == date(2025, 11, 20)
    finally:
        clock.uninstall()


async def test_every_response_carries_the_business_clock(client: httpx.AsyncClient) -> None:
    await f.halden()
    clock.install("2025-11-20")
    await f.sign_in(client, f.HALDEN["executive"])
    r = await client.get(f"{API}/auth/me")
    now = datetime.fromisoformat(r.headers["x-nightjar-now"])
    assert now.date() in (date(2025, 11, 20), date(2025, 11, 21)) and now.tzinfo is not None
    assert r.json()["clock"]["demo"] is True and r.json()["clock"]["now"].startswith("2025-11-2")
    assert abs((datetime.fromisoformat(r.json()["clock"]["now"]) - now.astimezone(UTC)).total_seconds()) < 5


async def test_session_status_is_quiet_when_signed_out(client: httpx.AsyncClient) -> None:
    await f.halden()
    r = await client.get(f"{API}/auth/session")
    assert r.status_code == 200 and r.json() == {"me": None}
    await f.sign_in(client, f.HALDEN["auditor"])
    me = (await client.get(f"{API}/auth/session")).json()["me"]
    assert me["user"]["email"] == f.HALDEN["auditor"] and me["clock"]["today"]


async def test_saved_search_alerts_are_rerun_by_the_sweeper(client: httpx.AsyncClient) -> None:
    from nightjar.services.sweeper import run_alerting_searches

    h = await f.halden()
    await handle_normalized([f.event(h["org_id"], action="logon-failed", outcome="failure") for _ in range(3)])
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    body = {
        "name": "Failed sign-ins",
        "query": {"filters": [{"field": "action", "op": "eq", "value": "logon-failed"}], "relativeHours": 24},
    }
    saved = (await client.post(f"{API}/hunting/saved", json=body, headers=headers)).json()
    assert saved["alert"] is False and saved["schedule"] == "on demand"
    on = await client.patch(f"{API}/hunting/saved/{saved['id']}", json={"alert": True}, headers=headers)
    assert on.status_code == 200 and on.json()["alert"] is True and on.json()["schedule"] == "every 15 min"
    assert await run_alerting_searches() == 1
    again = next(s for s in (await client.get(f"{API}/hunting/saved")).json() if s["id"] == saved["id"])
    assert again["lastCount"] == 3 and again["lastRunAt"]


async def test_the_live_stream_serves_frames_over_http(client: httpx.AsyncClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Through the real EventSourceResponse (the frame dicts alone do not exercise its field validation)."""
    import functools

    from nightjar.api.v1 import events as events_api
    from nightjar.core.events import stream

    monkeypatch.setattr(events_api, "stream", functools.partial(stream, max_seconds=0.8))
    await f.halden()
    await f.sign_in(client, f.HALDEN["auditor"])
    async with client.stream("GET", f"{API}/events/stream") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = (await r.aread()).decode()
    assert "retry: 3000" in body and "event: ready" in body
