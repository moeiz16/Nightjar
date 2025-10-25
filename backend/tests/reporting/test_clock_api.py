"""The incident clock end to end: determinations start clocks, notices are drafted, checked, approved and
submitted (mock), and submissions start follow-on clocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update

from nightjar import clock
from nightjar.db.models.soc import DeadlineRow
from nightjar.db.session import session_scope
from tests import factories as f

API = "/api/v1"


@pytest.fixture
async def reportable(client: httpx.AsyncClient) -> dict:
    h = await f.halden()
    ref = await f.incident(
        h["org_id"],
        hosts=("mem-dispatch-01", "ant-wms-01"),
        users=("svc-dispatch",),
        kind="ransomware_precursor",
        ips=("203.0.113.66",),
    )
    await f.triage(h["org_id"], ref.id, with_facts=f.facts(affected_jurisdictions=("EU", "US", "AU")))
    return {"h": h, "ref": ref}


async def _assess(client, headers, inc_id, regime, **body):  # type: ignore[no-untyped-def]
    payload = {"applies": True, "rationale": "Dispatch platform outage across depots", **body}
    return await client.post(f"{API}/incidents/{inc_id}/reporting/{regime}/assess", json=payload, headers=headers)


async def test_proposals_shown_and_clock_waits_for_a_person(client: httpx.AsyncClient, reportable: dict) -> None:
    await f.sign_in(client, f.HALDEN["auditor"])
    rep = (await client.get(f"{API}/incidents/{reportable['ref'].id}/reporting")).json()
    assert {a["regime"]: a["proposal"]["applies"] for a in rep["assessments"]} == {
        "nis2": "yes",
        "sec_8k": "assess",
        "soci": "yes",
        "gdpr_art33": "no",
    }
    assert rep["deadlines"] == [] and all(a["determination"] is None for a in rep["assessments"])
    clocks = (await client.get(f"{API}/reporting/clocks")).json()
    assert {c["regime"] for c in clocks} == {"nis2", "sec_8k", "soci"} and all(
        c["state"] == "not_started" for c in clocks
    )
    inc = (await client.get(f"{API}/incidents/{reportable['ref'].id}")).json()
    assert inc["reportable"] == "assess"


async def test_nis2_determination_starts_two_clocks(client: httpx.AsyncClient, reportable: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["executive"])
    aware = clock.now() - timedelta(hours=2)
    r = await _assess(client, headers, reportable["ref"].id, "nis2", entityId="halden-eu", awareAt=aware.isoformat())
    assert r.status_code == 200, r.text
    assert (
        r.json()["determination"]["significant"] is True and r.json()["determination"]["by"]["fullName"] == "Ingrid Vos"
    )
    rep = (await client.get(f"{API}/incidents/{reportable['ref'].id}/reporting")).json()
    types = {d["noticeType"]: d for d in rep["deadlines"]}
    assert set(types) == {"nis2.early_warning", "nis2.incident_notification"}
    ew = types["nis2.early_warning"]
    assert ew["state"] == "on_track" and ew["dueAtLocal"]["timezone"] == "Europe/Brussels"
    assert 21 * 3600 < ew["remainingSeconds"] <= 22 * 3600
    inc = (await client.get(f"{API}/incidents/{reportable['ref'].id}")).json()
    assert (
        inc["reportable"] == "yes"
        and inc["stage"] == "report"
        and inc["nextDeadline"]["noticeType"] == "nis2.early_warning"
    )
    audit = (await client.get(f"{API}/audit?action=reporting.")).json()["items"]
    assert {a["action"] for a in audit} == {"reporting.assessed", "reporting.clock_started"}


async def test_determination_validation(client: httpx.AsyncClient, reportable: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["executive"])
    inc = reportable["ref"].id
    assert (await _assess(client, headers, inc, "nis2", entityId="halden-eu")).status_code == 422  # awareAt missing
    future = (clock.now() + timedelta(days=1)).isoformat()
    assert (await _assess(client, headers, inc, "nis2", entityId="halden-eu", awareAt=future)).status_code == 422
    assert (
        await _assess(client, headers, inc, "nis2", entityId="halden-us", awareAt=clock.now().isoformat())
    ).status_code == 422  # entity not in NIS2 scope
    assert (
        await _assess(client, headers, inc, "soci", entityId="halden-au", awareAt=clock.now().isoformat())
    ).status_code == 422  # impact level missing
    short = await client.post(
        f"{API}/incidents/{inc}/reporting/nis2/assess",
        json={"applies": False, "rationale": "no", "entityId": "halden-eu"},
        headers=headers,
    )
    assert short.status_code == 422
    assert (await _assess(client, headers, inc, "dora", entityId="halden-eu")).status_code == 404


async def test_sec_clock_counts_business_days(client: httpx.AsyncClient, reportable: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["executive"])
    determined = clock.now() - timedelta(minutes=10)
    r = await _assess(
        client,
        headers,
        reportable["ref"].id,
        "sec_8k",
        entityId="halden-us",
        materialityDeterminedAt=determined.isoformat(),
    )
    assert r.json()["determination"]["material"] is True
    (d,) = (await client.get(f"{API}/incidents/{reportable['ref'].id}/reporting")).json()["deadlines"]
    assert d["noticeType"] == "sec_8k.item_105" and d["dueAtLocal"]["timezone"] == "America/New_York"
    assert d["dueAtLocal"]["at"][11:16] == "17:30" and 3 <= d["businessDays"] <= 4
    delay = await client.post(
        f"{API}/incidents/{reportable['ref'].id}/reporting/sec_8k/delay",
        json={"grantedUntil": (clock.now() + timedelta(days=30)).isoformat(), "reference": "DOJ-2025-114"},
        headers=headers,
    )
    assert delay.status_code == 200 and "Attorney General" in delay.json()[0]["basis"]
    assert delay.json()[0]["dueAt"] > d["dueAt"]


async def test_delay_without_a_running_clock(client: httpx.AsyncClient, reportable: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["executive"])
    r = await client.post(
        f"{API}/incidents/{reportable['ref'].id}/reporting/sec_8k/delay",
        json={"grantedUntil": clock.now().isoformat(), "reference": "x"},
        headers=headers,
    )
    assert r.status_code == 409 and r.json()["code"] == "clock_not_started"


async def test_notice_lifecycle_with_missing_fields_and_mock_submission(
    client: httpx.AsyncClient, reportable: dict
) -> None:
    inc = reportable["ref"].id
    exec_h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(
        client, exec_h, inc, "nis2", entityId="halden-eu", awareAt=(clock.now() - timedelta(hours=3)).isoformat()
    )
    analyst_h = await f.sign_in(client, f.HALDEN["analyst"])
    r = await client.post(
        f"{API}/incidents/{inc}/notices", json={"regime": "nis2", "type": "early_warning"}, headers=analyst_h
    )
    assert r.status_code == 201, r.text
    n = r.json()
    assert (
        n["type"] == "nis2.early_warning"
        and n["status"] == "draft"
        and n["recipient"]["contact"]["name"] == "Ingrid Vos"
    )
    assert n["dueAt"] is not None and n["generatedBy"] == "offline"
    secs = {s["key"]: s for s in n["sections"]}
    assert secs["suspected_malicious"]["body"].startswith("Yes") and secs["entity_details"]["filled"]
    # analysts may draft but not approve
    assert (await client.post(f"{API}/notices/{n['id']}/approve", headers=analyst_h)).status_code == 403
    # blank a required section: approval is refused with the field named
    edit = await client.patch(
        f"{API}/notices/{n['id']}",
        json={"sections": [{"key": "cross_border", "body": ""}], "version": 1},
        headers=analyst_h,
    )
    assert edit.json()["version"] == 2 and "cross_border" in edit.json()["missing"]
    stale = await client.patch(
        f"{API}/notices/{n['id']}",
        json={"sections": [{"key": "contact", "body": "x"}], "version": 1},
        headers=analyst_h,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"
    unknown = await client.patch(
        f"{API}/notices/{n['id']}", json={"sections": [{"key": "nope", "body": "x"}], "version": 2}, headers=analyst_h
    )
    assert unknown.status_code == 422
    exec_h = await f.sign_in(client, f.HALDEN["executive"])
    refused = await client.post(f"{API}/notices/{n['id']}/approve", headers=exec_h)
    assert refused.status_code == 422 and refused.json()["errors"][0]["field"] == "sections.cross_border"
    not_yet = await client.post(f"{API}/notices/{n['id']}/submit", json={"channel": "portal"}, headers=exec_h)
    assert not_yet.status_code == 409
    await client.patch(
        f"{API}/notices/{n['id']}",
        json={
            "sections": [{"key": "cross_border", "body": "Yes: depots in Belgium and the Netherlands."}],
            "version": 2,
        },
        headers=exec_h,
    )
    ok = await client.post(f"{API}/notices/{n['id']}/approve", headers=exec_h)
    assert ok.json()["status"] == "approved" and ok.json()["approvedBy"]["fullName"] == "Ingrid Vos"
    oral = await client.post(f"{API}/notices/{n['id']}/submit", json={"channel": "oral"}, headers=exec_h)
    assert oral.status_code == 422  # only SOCI reports may be oral
    sub = await client.post(
        f"{API}/notices/{n['id']}/submit", json={"channel": "portal", "note": "Via Safeonweb"}, headers=exec_h
    )
    body = sub.json()
    assert body["status"] == "submitted" and body["submission"]["receipt"].startswith("CCB-MOCK-")
    assert body["submission"]["submittedBy"]["fullName"] == "Ingrid Vos"
    again = await client.post(f"{API}/notices/{n['id']}/submit", json={"channel": "portal"}, headers=exec_h)
    assert again.status_code == 409 and again.json()["code"] == "already_submitted"
    rep = (await client.get(f"{API}/incidents/{inc}/reporting")).json()
    ew = next(d for d in rep["deadlines"] if d["noticeType"] == "nis2.early_warning")
    assert ew["state"] == "submitted" and ew["remainingSeconds"] is None
    clocks = (await client.get(f"{API}/reporting/clocks")).json()
    assert "nis2.early_warning" not in {c["noticeType"] for c in clocks}
    html = await client.get(f"{API}/notices/{n['id']}/document.html")
    assert html.status_code == 200 and "<script" not in html.text and "NIS2 early warning" in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]


async def test_notification_submission_starts_the_one_month_clock(client: httpx.AsyncClient, reportable: dict) -> None:
    inc = reportable["ref"].id
    h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(client, h, inc, "nis2", entityId="halden-eu", awareAt=(clock.now() - timedelta(hours=30)).isoformat())
    n = (
        await client.post(
            f"{API}/incidents/{inc}/notices", json={"regime": "nis2", "type": "incident_notification"}, headers=h
        )
    ).json()
    fill = [{"key": s["key"], "body": s["body"] or "Filled in."} for s in n["sections"]]
    fill = [{"key": s["key"], "body": "Filled in." if "[to be confirmed]" in s["body"] else s["body"]} for s in fill]
    await client.patch(f"{API}/notices/{n['id']}", json={"sections": fill, "version": 1}, headers=h)
    assert (await client.post(f"{API}/notices/{n['id']}/approve", headers=h)).status_code == 200
    await client.post(f"{API}/notices/{n['id']}/submit", json={"channel": "portal"}, headers=h)
    rep = (await client.get(f"{API}/incidents/{inc}/reporting")).json()
    month = next(
        d
        for d in rep["deadlines"]
        if d["startsAt"] and d["noticeType"] in ("nis2.progress_report", "nis2.final_report")
    )
    assert month["noticeType"] == "nis2.progress_report"  # the incident is still open
    start = datetime.fromisoformat(month["startsAt"])
    assert 28 <= (datetime.fromisoformat(month["dueAt"]) - start).days <= 31


async def test_soci_oral_report_starts_written_record(client: httpx.AsyncClient, reportable: dict) -> None:
    inc = reportable["ref"].id
    h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(
        client,
        h,
        inc,
        "soci",
        entityId="halden-au",
        impactLevel="significant",
        awareAt=(clock.now() - timedelta(hours=1)).isoformat(),
    )
    n = (
        await client.post(
            f"{API}/incidents/{inc}/notices", json={"regime": "soci", "type": "soci.critical_report"}, headers=h
        )
    ).json()
    fill = [{"key": s["key"], "body": "Filled in." if not s["filled"] else s["body"]} for s in n["sections"]]
    await client.patch(f"{API}/notices/{n['id']}", json={"sections": fill, "version": 1}, headers=h)
    await client.post(f"{API}/notices/{n['id']}/approve", headers=h)
    sub = await client.post(f"{API}/notices/{n['id']}/submit", json={"channel": "oral"}, headers=h)
    assert sub.json()["submission"]["receipt"].startswith("ASD-MOCK-")
    rep = (await client.get(f"{API}/incidents/{inc}/reporting")).json()
    wr = next(d for d in rep["deadlines"] if d["noticeType"] == "soci.written_record")
    assert (datetime.fromisoformat(wr["dueAt"]) - datetime.fromisoformat(wr["startsAt"])) == timedelta(hours=84)
    written = (
        await client.post(
            f"{API}/incidents/{inc}/notices", json={"regime": "soci", "type": "written_record"}, headers=h
        )
    ).json()
    oral_sec = next(s for s in written["sections"] if s["key"] == "oral_report")
    assert "receipt ASD-MOCK-" in oral_sec["body"] and oral_sec["filled"]


async def test_gdpr_reasons_for_delay_required_once_late(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], users=("j.okafor",), kind="phishing")
    await f.triage(
        h["org_id"],
        ref.id,
        with_facts=f.facts(
            service_disruption="none", personal_data_involved=True, records_affected_estimate=140, cross_border=False
        ),
    )
    headers = await f.sign_in(client, f.HALDEN["executive"])
    r = await _assess(
        client,
        headers,
        ref.id,
        "gdpr_art33",
        entityId="halden-eu",
        awareAt=(clock.now() - timedelta(hours=80)).isoformat(),
    )
    assert r.status_code == 200
    n = (
        await client.post(
            f"{API}/incidents/{ref.id}/notices", json={"regime": "gdpr_art33", "type": "notification"}, headers=headers
        )
    ).json()
    delay = next(s for s in n["sections"] if s["key"] == "delay_reasons")
    assert delay["required"] is True and "delay_reasons" in n["missing"]
    assert "about 140 records" not in n["sections"][0]["body"] and "140" in n["sections"][0]["body"]


async def test_drafting_needs_a_determination_and_known_type(client: httpx.AsyncClient, reportable: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    inc = reportable["ref"].id
    r = await client.post(
        f"{API}/incidents/{inc}/notices", json={"regime": "nis2", "type": "early_warning"}, headers=headers
    )
    assert r.status_code == 409 and r.json()["code"] == "clock_not_started"
    r = await client.post(
        f"{API}/incidents/{inc}/notices", json={"regime": "nis2", "type": "item_105"}, headers=headers
    )
    assert r.status_code == 422


async def test_sec_draft_has_no_technical_indicators(client: httpx.AsyncClient, reportable: dict) -> None:
    inc = reportable["ref"].id
    h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(
        client,
        h,
        inc,
        "sec_8k",
        entityId="halden-us",
        materialityDeterminedAt=(clock.now() - timedelta(hours=1)).isoformat(),
    )
    n = (
        await client.post(f"{API}/incidents/{inc}/notices", json={"regime": "sec_8k", "type": "item_105"}, headers=h)
    ).json()
    text = " ".join(s["body"] for s in n["sections"])
    assert "203.0.113.66" not in text and "T1" not in text and "HLDN" in text
    assert n["recipient"]["channel"] == "EDGAR"


async def test_negative_determination_stops_running_clocks(client: httpx.AsyncClient, reportable: dict) -> None:
    inc = reportable["ref"].id
    h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(
        client,
        h,
        inc,
        "soci",
        entityId="halden-au",
        impactLevel="relevant",
        awareAt=(clock.now() - timedelta(hours=1)).isoformat(),
    )
    r = await client.post(
        f"{API}/incidents/{inc}/reporting/soci/assess",
        json={"applies": False, "rationale": "Depot systems were not affected after all", "entityId": "halden-au"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["determination"]["applies"] is False
    rep = (await client.get(f"{API}/incidents/{inc}/reporting")).json()
    assert [d for d in rep["deadlines"] if d["regime"] == "soci"] == []


async def test_sweeper_announces_due_soon_and_overdue_once(client: httpx.AsyncClient, reportable: dict) -> None:
    from sqlalchemy import func, select

    from nightjar.db.models.platform import UiEvent
    from nightjar.services.sweeper import sweep_once

    inc = reportable["ref"].id
    h = await f.sign_in(client, f.HALDEN["executive"])
    await _assess(
        client, h, inc, "gdpr_art33", entityId="halden-eu", awareAt=(clock.now() - timedelta(hours=70)).isoformat()
    )
    async with session_scope() as db:
        await db.execute(update(DeadlineRow).values(state="on_track"))
    s1 = await sweep_once(announce_pipeline=False)
    s2 = await sweep_once(announce_pipeline=False)
    assert s1["clocks"] == 1 and s2["clocks"] == 0
    async with session_scope() as db:
        await db.execute(update(DeadlineRow).values(due_at=datetime.now(UTC) - timedelta(days=400)))
    assert (await sweep_once(announce_pipeline=False))["clocks"] == 1
    async with session_scope() as db:
        n = (
            await db.execute(select(func.count()).select_from(UiEvent).where(UiEvent.name == "clock.state"))
        ).scalar_one()
        assert n == 2
