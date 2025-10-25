"""Incidents over HTTP: list order and filters, transitions, optimistic versions, notes, merge, reopen, tickets."""

from __future__ import annotations

import httpx

from nightjar.ticketing import api as ticketing
from tests import factories as f

API = "/api/v1"


async def test_list_orders_by_severity_then_recency_and_filters(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    low = await f.incident(h["org_id"], severity="low", title="Low one")
    crit = await f.incident(h["org_id"], severity="high", hosts=("ant-wms-01",), title="Crown jewel")
    med = await f.incident(h["org_id"], severity="medium", title="Medium one")
    await f.sign_in(client, f.HALDEN["auditor"])
    items = (await client.get(f"{API}/incidents")).json()["items"]
    assert [i["reference"] for i in items] == [crit.reference, med.reference, low.reference]
    assert (
        items[0]["severity"] == "critical" and items[0]["detectionCount"] == 2 and items[0]["assets"] == ["ant-wms-01"]
    )
    assert (await client.get(f"{API}/incidents?severity=low")).json()["total"] == 1
    assert (await client.get(f"{API}/incidents?q=crown")).json()["items"][0]["id"] == str(crit.id)
    page = (await client.get(f"{API}/incidents?pageSize=2&page=2")).json()
    assert page["total"] == 3 and page["totalPages"] == 2 and len(page["items"]) == 1
    assert (await client.get(f"{API}/incidents?status=bogus")).status_code == 422


async def test_get_by_reference_includes_inventory_detail(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], hosts=("mem-citrix-01",), users=("svc-dispatch",))
    await f.sign_in(client, f.HALDEN["analyst"])
    inc = (await client.get(f"{API}/incidents/{ref.reference.lower()}")).json()
    assert inc["reference"] == ref.reference and inc["version"] == 1
    assert (
        inc["assetsDetail"][0]["name"] == "mem-citrix-01"
        and inc["assetsDetail"][0]["vulnerabilities"][0]["cveId"] == "CVE-2023-4966"
    )
    assert inc["identitiesDetail"][0]["privileged"] is True and inc["triage"] is None
    assert inc["attack"][0]["tacticLabels"] == ["Initial access"]
    assert (await client.get(f"{API}/incidents/NJ-9999")).status_code == 404
    assert (await client.get(f"{API}/incidents/not-a-uuid")).status_code == 404


async def test_status_transitions_and_versions(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    url = f"{API}/incidents/{ref.id}"
    r = await client.patch(url, json={"status": "investigating", "version": 1}, headers=headers)
    assert r.status_code == 200 and r.json()["stage"] == "investigate" and r.json()["version"] == 2
    stale = await client.patch(url, json={"status": "containing", "version": 1}, headers=headers)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"
    back = await client.patch(url, json={"status": "triage", "version": 2}, headers=headers)
    assert back.status_code == 422 and back.json()["code"] == "invalid_transition"
    no_reason = await client.patch(url, json={"status": "resolved", "version": 2}, headers=headers)
    assert no_reason.status_code == 422
    closed_early = await client.patch(url, json={"status": "closed", "version": 2}, headers=headers)
    assert closed_early.status_code == 422
    done = await client.patch(
        url,
        json={"status": "resolved", "resolution": {"reason": "contained", "note": "Done"}, "version": 2},
        headers=headers,
    )
    assert done.json()["status"] == "resolved" and done.json()["resolution"]["by"] == "Aiko Tanaka"
    again = await client.post(f"{url}/reopen", json={"reason": "New sign-ins seen"}, headers=headers)
    assert again.json()["status"] == "investigating" and again.json()["resolution"] is None
    assert (await client.post(f"{url}/reopen", json={"reason": "again"}, headers=headers)).status_code == 422


async def test_severity_and_assignee_changes_are_audited(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN["analyst2"])
    aiko = str(h["users"][f.HALDEN["analyst"]])
    r = await client.patch(
        f"{API}/incidents/{ref.id}", json={"severity": "critical", "assigneeId": aiko, "version": 1}, headers=headers
    )
    assert r.json()["severity"] == "critical" and r.json()["assignee"]["fullName"] == "Aiko Tanaka"
    bad = await client.patch(
        f"{API}/incidents/{ref.id}",
        json={"assigneeId": "00000000-0000-0000-0000-000000000001", "version": 2},
        headers=headers,
    )
    assert bad.status_code == 422
    await f.sign_in(client, f.HALDEN["auditor"])
    rows = (await client.get(f"{API}/audit?action=incident.severity_changed")).json()["items"]
    assert rows[0]["metadata"]["from"] == "high" and rows[0]["metadata"]["to"] == "critical"
    mine = await f.sign_in(client, f.HALDEN["analyst"])
    assert (await client.get(f"{API}/incidents?assignee=me")).json()["total"] == 1
    _ = mine


async def test_notes_and_executive_timeline(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN["responder"])
    r = await client.post(
        f"{API}/incidents/{ref.id}/notes", json={"body": "Called Joy; she did not travel."}, headers=headers
    )
    assert r.status_code == 201 and r.json()["kind"] == "note" and r.json()["actor"]["name"] == "Marcus Bell"
    assert (
        await client.post(f"{API}/incidents/{ref.id}/notes", json={"body": "x" * 4001}, headers=headers)
    ).status_code == 422
    full = (await client.get(f"{API}/incidents/{ref.id}/timeline")).json()
    exe = (await client.get(f"{API}/incidents/{ref.id}/timeline?audience=executive")).json()
    assert len(exe) < len(full) and all(e["executive"] for e in exe)
    assert [e["at"] for e in full] == sorted(e["at"] for e in full)


async def test_merge_by_hand(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    a = await f.incident(h["org_id"], users=("j.okafor",))
    b = await f.incident(h["org_id"], users=("k.wouters",))
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    self_merge = await client.post(f"{API}/incidents/{a.id}/merge", json={"incidentIds": [str(a.id)]}, headers=headers)
    assert self_merge.status_code == 422
    r = await client.post(f"{API}/incidents/{a.id}/merge", json={"incidentIds": [b.reference]}, headers=headers)
    assert r.status_code == 200 and set(r.json()["identities"]) == {"j.okafor", "k.wouters"}
    assert r.json()["detectionCount"] == 4
    other = (await client.get(f"{API}/incidents/{b.id}")).json()
    assert other["status"] == "closed" and other["mergedInto"] == a.reference


async def test_tickets_keys_idempotence_and_comments(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    t1 = await ticketing.create_ticket(
        h["org_id"], ref.id, title="Reset Joy's password", body="b", priority="p2", assignee_team="Service desk"
    )
    t1b = await ticketing.create_ticket(
        h["org_id"], ref.id, title="Reset Joy's password", body="b", priority="p2", assignee_team="Service desk"
    )
    t2 = await ticketing.create_ticket(
        h["org_id"], ref.id, title="Check mailbox rules", body="b", priority="p3", assignee_team="Messaging"
    )
    assert t1["key"] == "OPS-301" and t1b["id"] == t1["id"] and t2["key"] == "OPS-302"
    agent = await ticketing.add_comment(
        h["org_id"], __import__("uuid").UUID(t1["id"]), body="Done", author="Response colleague"
    )
    assert agent["comments"][0]["author"] == "Response colleague"
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    assert len((await client.get(f"{API}/incidents/{ref.id}/tickets")).json()) == 2
    r = await client.post(f"{API}/tickets/OPS-301/comments", json={"body": "Confirmed with Joy"}, headers=headers)
    assert r.status_code == 201 and [c["author"] for c in r.json()["comments"]] == ["Response colleague", "Aiko Tanaka"]
    assert (await client.get(f"{API}/tickets/{t2['id']}")).json()["assigneeTeam"] == "Messaging"
    import pytest

    with pytest.raises(ValueError):
        await ticketing.create_ticket(h["org_id"], ref.id, title="x", body="", priority="p9", assignee_team="x")  # type: ignore[arg-type]


async def test_detections_listing_and_detail(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], users=("j.okafor",))
    await f.sign_in(client, f.HALDEN["analyst"])
    lst = (await client.get(f"{API}/detections?band=likely_real")).json()
    assert lst["total"] == 2 and lst["items"][0]["incidentReference"] == ref.reference
    did = lst["items"][0]["id"]
    one = (await client.get(f"{API}/detections/{did}")).json()
    assert one["evidence"][0]["summary"]["text"] == "sign-in" and one["triage"]["band"] == "likely_real"
    assert (await client.get(f"{API}/detections/{did}/triage")).json()["probability"] == 0.9
    by_inc = (await client.get(f"{API}/detections?incidentId={ref.reference}")).json()
    assert by_inc["total"] == 2
    await f.sign_in(client, f.HALDEN["executive"])
    assert (await client.get(f"{API}/detections")).status_code == 403
