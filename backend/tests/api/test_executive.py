"""The Watch, shift log, posture, response times and the weekly and morning briefs."""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import update

from nightjar import clock
from nightjar.db.models.soc import Incident
from nightjar.db.session import session_scope
from nightjar.executive.service import iso_week, last_night, week_bounds
from tests import factories as f

API = "/api/v1"


async def _history(org_id):  # type: ignore[no-untyped-def]
    """Two incidents last week (slow triage) and two this week (fast): the trend reads 'better'."""
    now = clock.now()
    refs = []
    for days, triage_min, contain_min in ((10, 40, 120), (9, 30, 90), (3, 6, 30), (1, 4, 20)):
        ref = await f.incident(org_id, at=now - timedelta(days=days))
        async with session_scope() as db:
            inc = await db.get(Incident, ref.id)
            assert inc is not None
            inc.triaged_at = inc.first_seen + timedelta(minutes=triage_min)
            inc.contained_at = inc.first_seen + timedelta(minutes=contain_min)
            inc.status, inc.verdict = "closed", "true_positive"
        refs.append(ref)
    return refs


async def test_posture_response_times_and_alert_reduction(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    await _history(h["org_id"])
    await f.incident(h["org_id"], severity="high", hosts=("ant-wms-01",))
    await f.incident(h["org_id"], kind="benign_activity", severity="low", detections=20, noise=True)
    await f.sign_in(client, f.HALDEN["executive"])
    p = (await client.get(f"{API}/executive/posture")).json()
    assert p["openBySeverity"]["critical"] == 1 and p["openBySeverity"]["low"] == 1
    assert p["meanTimeToTriage"]["last7dSeconds"] == 300.0 and p["meanTimeToTriage"]["trend"] == "better"
    assert p["meanTimeToContain"]["last7dSeconds"] == 1500.0
    assert (
        p["alerts"]["last7d"] == 26 and p["alerts"]["incidentsLast7d"] == 4 and p["alerts"]["suppressedAsNoise"] == 20
    )
    assert p["alerts"]["reductionPct"] == 85 and p["scoreBand"] in ("steady", "watch", "strained")
    assert {c["key"] for c in p["components"]} == {"exposure", "clocks", "response", "decisions", "intake"}
    assert any("alerts became" in s for s in p["highlights"])


async def test_merged_incidents_are_excluded_from_response_times(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    refs = await _history(h["org_id"])
    async with session_scope() as db:
        await db.execute(update(Incident).where(Incident.id == refs[3].id).values(merged_into_id=refs[2].id))
    await f.sign_in(client, f.HALDEN["executive"])
    p = (await client.get(f"{API}/executive/posture")).json()
    assert p["meanTimeToTriage"]["last7dSeconds"] == 360.0


async def test_weekly_brief_generate_read_and_print(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    await _history(h["org_id"])
    week = iso_week(clock.today() - timedelta(days=7))
    headers = await f.sign_in(client, f.HALDEN["admin"])
    r = await client.post(f"{API}/executive/briefs/generate", json={}, headers=headers)
    assert r.status_code == 202 and r.json()["job"]["status"] == "succeeded"
    assert (
        await client.post(f"{API}/executive/briefs/generate", json={"week": "last week"}, headers=headers)
    ).status_code == 422
    await f.sign_in(client, f.HALDEN["auditor"])
    listing = (await client.get(f"{API}/executive/briefs")).json()
    assert listing[0]["week"] == week
    brief = (await client.get(f"{API}/executive/briefs/{week}")).json()
    assert [s["title"] for s in brief["sections"]][:2] == ["The week in brief", "Incidents that mattered"]
    assert brief["generatedBy"] == "offline" and brief["from"] == week_bounds(week)[0].isoformat()
    html = await client.get(f"{API}/executive/briefs/{week}/document.html")
    assert html.status_code == 200 and brief["headline"].split(":")[0] in html.text and "<script" not in html.text
    assert (await client.get(f"{API}/executive/briefs/2020-W01")).status_code == 404


async def test_watch_shift_log_and_seen_marker(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], at=clock.now() - timedelta(hours=2))
    await f.triage(h["org_id"], ref.id)
    headers = await f.sign_in(client, f.HALDEN["responder"])
    w = (await client.get(f"{API}/watch")).json()
    assert w["incidents"][0]["reference"] == ref.reference and w["pendingApprovals"] == 0
    assert w["pipeline"]["status"] in ("normal", "catching_up", "degraded", "offline")
    kinds = {e["kind"] for e in w["shiftLog"]}
    assert "incident" in kinds and all(e["text"].startswith(ref.reference) for e in w["shiftLog"])
    assert (await client.post(f"{API}/watch/seen", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/watch/shift-log")).json() == []
    since = (clock.now() - timedelta(days=1)).isoformat()
    assert len((await client.get(f"{API}/watch/shift-log", params={"since": since})).json()) >= 2


async def test_morning_brief_covers_last_night(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    start, _end = last_night("Europe/Brussels")
    await f.incident(h["org_id"], at=start + timedelta(hours=3), severity="high")
    await f.incident(h["org_id"], at=start - timedelta(days=2), severity="high")
    await f.sign_in(client, f.HALDEN["executive"])
    m = (await client.get(f"{API}/executive/morning")).json()
    assert len(m["incidents"]) == 1 and m["headline"].startswith("Overnight: 1 incident, 1 serious")
    assert m["night"]["from"] == start.isoformat()


def test_last_night_definition() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    bru = ZoneInfo("Europe/Brussels")
    with clock.travel(datetime(2025, 11, 20, 10, 0, tzinfo=bru)):
        start, end = last_night("Europe/Brussels")
        assert start == datetime(2025, 11, 20, 0, 0, tzinfo=bru) and end.hour == 9
    with clock.travel(datetime(2025, 11, 20, 8, 0, tzinfo=bru)):
        assert last_night("Europe/Brussels")[0].day == 19


def test_iso_weeks() -> None:
    from datetime import date

    assert iso_week(date(2025, 11, 20)) == "2025-W47"
    assert week_bounds("2025-W47") == (date(2025, 11, 17), date(2025, 11, 23))
    assert iso_week(date(2025, 12, 29)) == "2026-W01"
