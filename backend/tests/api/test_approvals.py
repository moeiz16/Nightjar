"""The approval gate (ADR 0006): separation of duties, two-person rule, expiry and single-use tokens."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from nightjar import clock
from nightjar.approvals import api as approvals
from nightjar.contracts import ActionResult, ProposedAction
from nightjar.core.security import verify_approval_token
from nightjar.db.models.platform import AuditLog
from nightjar.db.models.soc import ActionProposal, Asset, Incident
from nightjar.db.session import session_scope
from tests import factories as f

API = "/api/v1"


def action(kind: str, target: str, ttype: str) -> ProposedAction:
    return ProposedAction(kind, target, ttype, "Stop the attacker", "Cuts the host off", True)  # type: ignore[arg-type]


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, uuid.UUID]]:
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []
    from nightjar.agents import api as agents_api

    async def fake(org_id: uuid.UUID, approval_id: uuid.UUID) -> None:
        calls.append((org_id, approval_id))

    monkeypatch.setattr(agents_api, "execute_approved_action", fake)
    return calls


async def _setup(**kw):  # type: ignore[no-untyped-def]
    h = await f.halden()
    ref = await f.incident(h["org_id"], **kw)
    return h, ref


async def _approve(
    client: httpx.AsyncClient, who: str, approval_id: uuid.UUID, note: str | None = None
) -> httpx.Response:
    headers = await f.sign_in(client, f.HALDEN.get(who, who))
    return await client.post(f"{API}/approvals/{approval_id}/approve", json={"note": note}, headers=headers)


async def test_agent_proposal_outside_incident_is_refused(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(hosts=("ant-ws-014",), users=("j.okafor",))
    with pytest.raises(ValueError):
        await approvals.request_approval(h["org_id"], ref.id, None, action("isolate_host", "ant-dc-01", "host"))
    with pytest.raises(ValueError):
        await approvals.request_approval(h["org_id"], ref.id, None, action("isolate_host", "j.okafor", "user"))


async def test_identical_pending_requests_are_deduplicated(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",))
    a = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    b = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    assert a.approval_id == b.approval_id and a.status == "pending" and a.required_roles == ("responder",)


async def test_non_destructive_action_is_auto_approved_without_token(client: httpx.AsyncClient) -> None:
    h, ref = await _setup()
    ref_a = await approvals.request_approval(h["org_id"], ref.id, None, action("open_ticket", ref.reference, "ticket"))
    assert ref_a.status == "auto_approved" and ref_a.required_roles == () and ref_a.token is None


async def test_crown_jewel_and_privileged_targets_need_two_people(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(hosts=("mem-dispatch-01",), users=("svc-dispatch", "j.okafor"))
    host = await approvals.request_approval(
        h["org_id"], ref.id, None, action("isolate_host", "mem-dispatch-01", "host")
    )
    svc = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "svc-dispatch", "user"))
    person = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    assert host.required_roles == svc.required_roles == ("responder", "executive")
    assert person.required_roles == ("responder",)


async def test_responder_approval_issues_bound_single_use_token(client: httpx.AsyncClient, executed: list) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    r = await _approve(client, "responder", p.approval_id, "Confirmed with her manager")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert executed == [(h["org_id"], p.approval_id)]
    got = await approvals.get_approval(p.approval_id)
    assert got.token is not None
    claims = verify_approval_token(got.token, action="disable_account", target="j.okafor", org_id=str(h["org_id"]))
    assert claims is not None
    # misuse: wrong action, wrong target, other org
    assert verify_approval_token(got.token, action="isolate_host", target="j.okafor", org_id=str(h["org_id"])) is None
    assert (
        verify_approval_token(got.token, action="disable_account", target="k.wouters", org_id=str(h["org_id"])) is None
    )
    assert (
        verify_approval_token(got.token, action="disable_account", target="j.okafor", org_id=str(uuid.uuid4())) is None
    )
    # single use
    assert await approvals.consume(h["org_id"], p.approval_id, "not-the-nonce") is False
    assert await approvals.consume(uuid.uuid4(), p.approval_id, claims.nonce) is False
    assert await approvals.consume(h["org_id"], p.approval_id, claims.nonce) is True
    assert await approvals.consume(h["org_id"], p.approval_id, claims.nonce) is False
    assert (await approvals.get_approval(p.approval_id)).token is None


async def test_expired_token_cannot_be_consumed(client: httpx.AsyncClient, executed: list) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("revoke_sessions", "j.okafor", "user"))
    await _approve(client, "responder", p.approval_id)
    token = (await approvals.get_approval(p.approval_id)).token
    claims = verify_approval_token(token or "", action="revoke_sessions", target="j.okafor", org_id=str(h["org_id"]))
    assert claims is not None
    async with session_scope() as db:
        await db.execute(update(ActionProposal).values(token_expires_at=clock.real_now() - timedelta(seconds=1)))
    assert await approvals.consume(h["org_id"], p.approval_id, claims.nonce) is False


async def test_two_person_rule_needs_responder_and_executive(client: httpx.AsyncClient, executed: list) -> None:
    h, ref = await _setup(hosts=("ant-wms-01",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("isolate_host", "ant-wms-01", "host"))
    first = await _approve(client, "responder", p.approval_id)
    assert first.json()["status"] == "pending" and first.json()["awaitingRoles"] == ["executive"]
    assert (await approvals.get_approval(p.approval_id)).token is None and executed == []
    again = await _approve(client, "responder", p.approval_id)
    assert again.status_code == 409 and again.json()["code"] == "already_decided"
    second = await _approve(client, "executive", p.approval_id)
    assert second.json()["status"] == "approved" and len(second.json()["approvals"]) == 2
    assert executed == [(h["org_id"], p.approval_id)]


async def test_executive_alone_cannot_approve_an_ordinary_action(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    r = await _approve(client, "executive", p.approval_id)
    assert r.status_code == 403


@pytest.mark.parametrize("who", ["admin", "analyst", "auditor"])
async def test_roles_without_approval_rights_are_refused(client: httpx.AsyncClient, who: str) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    r = await _approve(client, who, p.approval_id)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


async def test_nobody_is_the_only_approver_of_their_own_request(client: httpx.AsyncClient, executed: list) -> None:
    _h, ref = await _setup(users=("j.okafor",), hosts=("ant-wms-01",))
    headers = await f.sign_in(client, f.HALDEN["responder"])
    r = await client.post(
        f"{API}/incidents/{ref.id}/actions",
        json={"kind": "disable_account", "target": "j.okafor", "rationale": "Account taken over"},
        headers=headers,
    )
    assert r.status_code == 201 and r.json()["proposedBy"] == {"type": "user", "name": "Marcus Bell"}
    assert r.json()["canApprove"] is False
    own = await client.post(f"{API}/approvals/{r.json()['id']}/approve", json={}, headers=headers)
    assert own.status_code == 409 and own.json()["code"] == "self_approval"
    # with the two-person rule the requester may give one of the two approvals
    r2 = await client.post(
        f"{API}/incidents/{ref.id}/actions",
        json={"kind": "isolate_host", "target": "ant-wms-01", "rationale": "Encryption precursor"},
        headers=headers,
    )
    ok = await client.post(f"{API}/approvals/{r2.json()['id']}/approve", json={}, headers=headers)
    assert ok.status_code == 200 and ok.json()["status"] == "pending"


async def test_human_proposal_target_must_be_in_incident(client: httpx.AsyncClient) -> None:
    _h, ref = await _setup(users=("j.okafor",))
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    r = await client.post(
        f"{API}/incidents/{ref.id}/actions",
        json={"kind": "isolate_host", "target": "ant-dc-01", "rationale": "Just in case"},
        headers=headers,
    )
    assert r.status_code == 422 and r.json()["code"] == "target_not_in_incident"


async def test_reject_needs_a_reason_and_ends_the_request(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    headers = await f.sign_in(client, f.HALDEN["responder"])
    assert (await client.post(f"{API}/approvals/{p.approval_id}/reject", json={}, headers=headers)).status_code == 422
    r = await client.post(
        f"{API}/approvals/{p.approval_id}/reject", json={"reason": "She is travelling"}, headers=headers
    )
    assert r.json()["status"] == "rejected" and r.json()["rejection"]["reason"] == "She is travelling"
    assert (await _approve(client, "responder", p.approval_id)).json()["code"] == "already_decided"
    assert (await approvals.get_approval(p.approval_id)).token is None


async def test_pending_requests_expire_after_24_hours(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    async with session_scope() as db:
        await db.execute(update(ActionProposal).values(expires_at=clock.now() - timedelta(minutes=1)))
    r = await _approve(client, "responder", p.approval_id)
    assert r.status_code == 410 and r.json()["code"] == "expired"
    assert (await approvals.get_approval(p.approval_id)).status == "expired"


async def test_record_result_marks_containment_and_is_idempotent(client: httpx.AsyncClient, executed: list) -> None:
    h, ref = await _setup(hosts=("ant-ws-014",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("isolate_host", "ant-ws-014", "host"))
    await _approve(client, "responder", p.approval_id)
    from nightjar.directory import api as directory

    await directory.set_host_isolation(h["org_id"], "ant-ws-014", isolated=True, approval_id=p.approval_id)
    result = ActionResult(
        p.approval_id, "isolate_host", "ant-ws-014", True, "executed", "Isolated", clock.now(), "response.isolate_host"
    )
    await approvals.record_result(h["org_id"], result)
    await approvals.record_result(h["org_id"], result)
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None and inc.contained_at is not None and inc.status == "containing"
        asset = (await db.execute(select(Asset).where(Asset.name == "ant-ws-014"))).scalar_one()
        assert asset.isolated and asset.isolated_by_approval_id == p.approval_id
        n = (
            await db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action == "action.executed"))
        ).scalar_one()
        assert n == 1
    assert (await approvals.get_approval(p.approval_id)).status == "executed"


async def test_refusal_is_audited_without_executing(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",))
    p = await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    await approvals.record_result(
        h["org_id"],
        ActionResult(
            p.approval_id,
            "disable_account",
            "j.okafor",
            False,
            "refused",
            "No valid approval token",
            clock.now(),
            "response.disable_account",
        ),
    )
    assert (await approvals.get_approval(p.approval_id)).status == "pending"
    async with session_scope() as db:
        assert (await db.execute(select(AuditLog).where(AuditLog.action == "action.refused"))).scalar_one()
    with pytest.raises(KeyError):
        await approvals.record_result(
            uuid.uuid4(),
            ActionResult(p.approval_id, "disable_account", "j.okafor", False, "refused", "x", clock.now(), "t"),
        )


async def test_queue_lists_pending_first_and_mine_filters(client: httpx.AsyncClient) -> None:
    h, ref = await _setup(users=("j.okafor",), hosts=("ant-wms-01",))
    await approvals.request_approval(h["org_id"], ref.id, None, action("disable_account", "j.okafor", "user"))
    await approvals.request_approval(h["org_id"], ref.id, None, action("isolate_host", "ant-wms-01", "host"))
    await f.sign_in(client, f.HALDEN["executive"])
    mine = (await client.get(f"{API}/approvals?mine=true")).json()
    assert [d["target"] for d in mine["items"]] == ["ant-wms-01"]
    assert mine["items"][0]["crownJewel"] and mine["items"][0]["kindLabel"] == "Isolate this computer from the network"
    everything = (await client.get(f"{API}/approvals?status=all")).json()
    assert everything["total"] == 2
    acts = (await client.get(f"{API}/incidents/{ref.reference}/actions")).json()
    assert len(acts) == 2
