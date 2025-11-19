"""The demo seed (contract Part 6) at the recommended screenshot date: Thursday 20 November 2025, 11:00 Brussels."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from nightjar import clock
from nightjar.seed import data
from tests import factories as f

API = "/api/v1"
DEMO_NOW = datetime(2025, 11, 20, 11, 0, tzinfo=ZoneInfo("Europe/Brussels"))


@pytest.fixture
async def seeded(client: httpx.AsyncClient) -> dict:
    from nightjar.seed import run

    with clock.travel(DEMO_NOW):
        out = await run(use_pipeline=False)
        yield out


async def test_seed_summary_and_idempotence(client: httpx.AsyncClient, seeded: dict) -> None:
    from nightjar.seed import run

    assert seeded["mode"] == "fixtures" and seeded["history"] == 35 and seeded["incidents"] == 41
    assert seeded["auditRows"] >= 200 and seeded["detections"] > 146
    assert seeded["today"] == "2025-11-20"
    assert (await run(use_pipeline=False)) == {"skipped": True}


async def test_every_demo_account_signs_in(client: httpx.AsyncClient, seeded: dict) -> None:
    for email, _name, role, _title in [*data.HALDEN_USERS, *data.BRIGHTLINE_USERS]:
        me = (
            await client.post(
                f"{API}/auth/login", json={"email": email, "password": data.DEMO_PASSWORD}, headers=await _csrf(client)
            )
        ).json()
        assert me["membership"]["role"] == role and me["activeOrg"]["isSample"] is True


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    await client.get(f"{API}/auth/csrf")
    return {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}


async def test_references_and_last_night(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.HALDEN["auditor"])
    allinc = (await client.get(f"{API}/incidents?status=all&pageSize=200")).json()["items"]
    assert sorted(i["reference"] for i in allinc) == [f"NJ-{n}" for n in range(1001, 1042)]
    by = {i["reference"]: i for i in allinc}
    assert all(by[f"NJ-{n}"]["status"] == "closed" for n in range(1001, 1036))
    assert (by["NJ-1036"]["status"], by["NJ-1036"]["stage"], by["NJ-1036"]["severity"]) == (
        "monitoring",
        "report",
        "critical",
    )
    assert by["NJ-1036"]["reportable"] == "yes"
    assert by["NJ-1037"]["status"] == "monitoring" and by["NJ-1037"]["reportable"] == "yes"
    assert by["NJ-1038"]["status"] == "containing" and by["NJ-1038"]["pendingApprovals"] == 1
    assert (
        by["NJ-1039"]["status"] == "resolved"
        and by["NJ-1039"]["detectionCount"] == 146
        and by["NJ-1039"]["likelyBenign"]
    )
    assert by["NJ-1040"]["status"] == "resolved" and by["NJ-1040"]["reportable"] == "no"
    assert by["NJ-1041"]["verdict"] == "needs_review" and by["NJ-1041"]["assignee"]["fullName"] == "Aiko Tanaka"
    open_now = (await client.get(f"{API}/incidents")).json()["items"]
    assert next(i["reference"] for i in open_now) == "NJ-1036"


async def test_regulator_clocks_read_as_of_the_demo_date(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.HALDEN["executive"])
    with clock.travel(DEMO_NOW):
        rep = (await client.get(f"{API}/incidents/NJ-1036/reporting")).json()
    d = {x["noticeType"]: x for x in rep["deadlines"]}
    assert d["nis2.early_warning"]["state"] == "submitted"
    assert d["nis2.incident_notification"]["dueAt"] == "2025-11-22T03:52:00+00:00"
    assert d["soci.other_report"]["dueAt"] == "2025-11-22T03:52:00+00:00"
    sec = d["sec_8k.item_105"]
    assert sec["dueAt"] == "2025-11-25T22:30:00+00:00" and sec["businessDays"] == 3
    assert sec["dueAtLocal"]["at"].startswith("2025-11-25T17:30:00")
    notices = {n["type"]: n["status"] for n in rep["notices"]}
    assert notices == {
        "nis2.early_warning": "submitted",
        "nis2.incident_notification": "draft",
        "soci.other_report": "draft",
    }
    regimes = {(a["regime"], a["determination"]["applies"] if a["determination"] else None) for a in rep["assessments"]}
    assert ("nis2", True) in regimes and ("sec_8k", True) in regimes and ("soci", True) in regimes
    with clock.travel(DEMO_NOW):  # Joy's mailbox (the landing replay): aware 03:13:40, early warning drafted
        joy = (await client.get(f"{API}/incidents/NJ-1037/reporting")).json()
    ew = next(x for x in joy["deadlines"] if x["noticeType"] == "nis2.early_warning")
    assert ew["startsAt"] == "2025-11-20T02:13:40+00:00" and ew["dueAt"] == "2025-11-21T02:13:40+00:00"
    assert ew["state"] == "on_track" and ew["noticeId"]
    assert {n["type"]: n["status"] for n in joy["notices"]} == {"nis2.early_warning": "draft"}
    draft = (await client.get(f"{API}/notices/{ew['noticeId']}")).json()
    assert draft["missing"] == ["cross_border"]
    gdpr = next(a for a in joy["assessments"] if a["regime"] == "gdpr_art33")
    assert gdpr["proposal"]["applies"] == "assess" and gdpr["determination"] is None


async def test_containment_state_and_pending_hold_to_authorise(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.HALDEN["responder"])
    pend = (await client.get(f"{API}/approvals?mine=true")).json()["items"]
    assert [(p["kind"], p["target"]) for p in pend] == [("revoke_access_key", "AKIAEXAMPLEHALDEN01")]
    inc = (await client.get(f"{API}/incidents/NJ-1036")).json()
    assets = {a["name"]: a for a in inc["assetsDetail"]}
    assert assets["mem-dispatch-01"]["isolated"] is False  # released after nine hours
    idents = {i["username"]: i for i in inc["identitiesDetail"]}
    assert idents["svc-dispatch"]["disabled"] is True
    assert inc["timeToTriageSeconds"] == 32 * 60 and inc["timeToContainSeconds"] == 42 * 60
    acts = (await client.get(f"{API}/incidents/NJ-1036/actions")).json()
    iso = [a for a in acts if a["kind"] == "isolate_host"]
    assert all(a["status"] == "executed" and len(a["approvals"]) == 2 for a in iso)
    assert {t["key"] for t in (await client.get(f"{API}/incidents/NJ-1036/tickets")).json()} <= {
        "OPS-301",
        "OPS-302",
        "OPS-303",
    }


async def test_executive_view_history_and_briefs(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.HALDEN["executive"])
    with clock.travel(DEMO_NOW):
        p = (await client.get(f"{API}/executive/posture")).json()
        briefs = (await client.get(f"{API}/executive/briefs")).json()
        morning = (await client.get(f"{API}/executive/morning")).json()
    assert p["meanTimeToTriage"]["trend"] == "better" and p["alerts"]["reductionPct"] >= 80
    assert len(briefs) == 4 and briefs[0]["week"] == "2025-W46"
    assert len(morning["incidents"]) == 5 and morning["pendingApprovals"] == 1


async def test_audit_chain_and_hunting_data(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.HALDEN["auditor"])
    v = (await client.get(f"{API}/audit/verify")).json()
    assert v["valid"] is True and v["checked"] >= 200
    saved = (await client.get(f"{API}/hunting/saved")).json()
    assert len(saved) >= 6
    with clock.travel(DEMO_NOW):
        run = await client.post(
            f"{API}/hunting/saved/{saved[0]['id']}/run",
            json={},
            headers={"X-CSRF-Token": client.cookies.get("nj_csrf") or ""},
        )
        assert run.status_code == 200
        river = (await client.get(f"{API}/events/river?limit=20")).json()
    assert river["lines"]


async def test_brightline_is_separate(client: httpx.AsyncClient, seeded: dict) -> None:
    await f.sign_in(client, f.OLU)
    items = (await client.get(f"{API}/incidents?status=all")).json()["items"]
    assert [(i["reference"], i["severity"]) for i in items] == [("NJ-1001", "low")]
    assets = {a["name"] for a in (await client.get(f"{API}/incidents/NJ-1001")).json()["assetsDetail"]}
    assert assets == {"bl-vpn-01"}
    _ = UTC
