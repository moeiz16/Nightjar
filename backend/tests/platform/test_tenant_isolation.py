"""Tenant isolation: another organisation's ids resolve to 404 (never 403) and never appear in lists; agent-side
functions are tenant-checked too."""

from __future__ import annotations

import uuid

import httpx
import pytest

from nightjar.approvals import api as approvals
from nightjar.contracts import ProposedAction
from nightjar.db.models.soc import SavedSearch
from nightjar.db.session import session_scope
from nightjar.directory import api as directory
from nightjar.ticketing import api as ticketing
from tests import factories as f

API = "/api/v1"


@pytest.fixture
async def two_orgs(client: httpx.AsyncClient) -> dict:
    h, b = await f.halden(), await f.brightline()
    b_inc = await f.incident(b["org_id"], users=("m.devries",), hosts=("bl-ws-003",), title="Brightline sign-in")
    run_id = await f.triage(b["org_id"], b_inc.id)
    appr = await approvals.request_approval(
        b["org_id"], b_inc.id, None, ProposedAction("disable_account", "m.devries", "user", "r", "e", True)
    )
    ticket = await ticketing.create_ticket(
        b["org_id"], b_inc.id, title="Reset", body="b", priority="p3", assignee_team="Service desk"
    )
    async with session_scope() as db:
        s = SavedSearch(org_id=b["org_id"], name="Brightline hunt", query={"filters": [], "relativeHours": 24})
        db.add(s)
        await db.flush()
        saved_id = s.id
    h_inc = await f.incident(h["org_id"])
    return {
        "h": h,
        "b": b,
        "b_inc": b_inc,
        "h_inc": h_inc,
        "run": run_id,
        "approval": appr.approval_id,
        "ticket": ticket["id"],
        "saved": saved_id,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/incidents/{inc}"),
        ("GET", "/incidents/{ref}"),
        ("GET", "/incidents/{inc}/timeline"),
        ("GET", "/incidents/{inc}/detections"),
        ("GET", "/incidents/{inc}/investigations"),
        ("GET", "/incidents/{inc}/actions"),
        ("GET", "/incidents/{inc}/tickets"),
        ("GET", "/incidents/{inc}/reporting"),
        ("GET", "/investigations/{run}"),
        ("GET", "/investigations/{run}/replay"),
        ("GET", "/tickets/{ticket}"),
        ("GET", "/hunting/saved/{saved}"),
    ],
)
async def test_other_org_ids_are_not_found(client: httpx.AsyncClient, two_orgs: dict, method: str, path: str) -> None:
    await f.sign_in(client, f.HALDEN["analyst"])
    url = API + path.format(
        inc=two_orgs["b_inc"].id,
        ref=two_orgs["b_inc"].reference,
        run=two_orgs["run"],
        ticket=two_orgs["ticket"],
        saved=two_orgs["saved"],
    )
    r = await client.request(method, url)
    if path == "/incidents/{ref}":  # same reference exists in both orgs: Halden's own incident is returned
        assert r.status_code == 200 and r.json()["id"] == str(two_orgs["h_inc"].id)
    elif path == "/hunting/saved/{saved}":
        assert r.status_code == 405  # no GET for one saved search; check the write paths below
    else:
        assert r.status_code == 404, (path, r.text)
        assert r.json()["code"] == "not_found"


async def test_other_org_writes_are_not_found(client: httpx.AsyncClient, two_orgs: dict) -> None:
    headers = await f.sign_in(client, f.HALDEN["responder"])
    b = two_orgs
    calls = [
        ("PATCH", f"/incidents/{b['b_inc'].id}", {"status": "investigating", "version": 1}),
        ("POST", f"/incidents/{b['b_inc'].id}/notes", {"body": "hello"}),
        ("POST", f"/approvals/{b['approval']}/approve", {}),
        ("POST", f"/approvals/{b['approval']}/reject", {"reason": "not mine"}),
        ("POST", f"/tickets/{b['ticket']}/comments", {"body": "hi"}),
        ("PATCH", f"/hunting/saved/{b['saved']}", {"name": "mine now"}),
        ("DELETE", f"/hunting/saved/{b['saved']}", None),
        ("POST", f"/hunting/saved/{b['saved']}/run", {}),
        ("POST", f"/incidents/{b['h_inc'].id}/merge", {"incidentIds": [str(b["b_inc"].id)]}),
    ]
    for method, path, body in calls:
        r = await client.request(method, API + path, json=body, headers=headers)
        assert r.status_code == 404, (path, r.status_code, r.text)


async def test_lists_only_show_own_org(client: httpx.AsyncClient, two_orgs: dict) -> None:
    await f.sign_in(client, f.HALDEN["auditor"])
    incs = (await client.get(f"{API}/incidents?status=all")).json()
    assert [i["id"] for i in incs["items"]] == [str(two_orgs["h_inc"].id)]
    assert (await client.get(f"{API}/approvals?status=all")).json()["total"] == 0
    dets = (await client.get(f"{API}/detections")).json()
    assert dets["total"] == 2
    assert (await client.get(f"{API}/hunting/saved")).json() == []
    audit = (await client.get(f"{API}/audit?pageSize=200")).json()["items"]
    assert all("Brightline" not in str(a) for a in audit)


async def test_brightline_admin_sees_nothing_of_halden(client: httpx.AsyncClient, two_orgs: dict) -> None:
    await f.sign_in(client, f.OLU)
    assert (await client.get(f"{API}/incidents/{two_orgs['h_inc'].id}")).status_code == 404
    members = (await client.get(f"{API}/org/members")).json()["items"]
    assert [m["user"]["email"] for m in members] == [f.OLU]


async def test_agent_side_functions_are_tenant_checked(client: httpx.AsyncClient, two_orgs: dict) -> None:
    h = two_orgs["h"]
    assert await directory.find_asset(h["org_id"], "bl-ws-003") is None
    assert await directory.find_identity(h["org_id"], "m.devries") is None
    assert await directory.search_assets(h["org_id"], "bl-") == []
    assert await ticketing.get_ticket(h["org_id"], uuid.UUID(two_orgs["ticket"])) is None
    with pytest.raises(KeyError):
        await directory.set_host_isolation(h["org_id"], "bl-ws-003", isolated=True, approval_id=uuid.uuid4())
    with pytest.raises(ValueError):
        await approvals.request_approval(
            h["org_id"],
            two_orgs["b_inc"].id,
            None,
            ProposedAction("disable_account", "m.devries", "user", "r", "e", True),
        )
    with pytest.raises(KeyError):
        await ticketing.add_comment(h["org_id"], uuid.UUID(two_orgs["ticket"]), body="x", author="y")


async def test_sse_stream_is_org_scoped(client: httpx.AsyncClient, two_orgs: dict) -> None:
    from nightjar.core.events import stream

    h = two_orgs["h"]
    user = next(iter(h["users"].values()))
    frames = [
        fr async for fr in stream(h["org_id"], user, {"incidents:read", "detections:read"}, after_id=0, max_seconds=0.6)
    ]
    payloads = " ".join(fr.get("data", "") for fr in frames)
    assert str(two_orgs["b_inc"].id) not in payloads and str(two_orgs["h_inc"].id) in payloads
