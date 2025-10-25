"""Organisation administration: members, invitations, event-source keys, settings and the audit trail."""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import update

from nightjar import clock
from nightjar.db.models.platform import Invitation
from nightjar.db.session import session_scope
from tests import factories as f
from tests.conftest import DEMO_PASSWORD

API = "/api/v1"


async def _member_id(client: httpx.AsyncClient, email: str) -> str:
    items = (await client.get(f"{API}/org/members")).json()["items"]
    return next(m["id"] for m in items if m["user"]["email"] == email)


async def test_members_sorted_and_role_change_audited(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    items = (await client.get(f"{API}/org/members")).json()["items"]
    assert [m["user"]["fullName"] for m in items] == sorted(m["user"]["fullName"] for m in items) and len(items) == 6
    mid = await _member_id(client, f.HALDEN["analyst"])
    r = await client.patch(f"{API}/org/members/{mid}", json={"role": "responder"}, headers=headers)
    assert r.json()["role"] == "responder" and r.json()["roleLabel"] == "Incident responder"
    audit = (await client.get(f"{API}/audit?action=member.")).json()["items"]
    assert audit[0]["label"] == "Role of aiko.tanaka@halden.example changed to Incident responder"


async def test_last_admin_cannot_be_demoted_or_removed(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    mid = await _member_id(client, f.HALDEN["admin"])
    assert (
        await client.patch(f"{API}/org/members/{mid}", json={"role": "analyst"}, headers=headers)
    ).status_code == 409
    assert (await client.delete(f"{API}/org/members/{mid}", headers=headers)).status_code == 409
    assert (await client.patch(f"{API}/org/members/{mid}", json={"role": "owner"}, headers=headers)).status_code == 422


async def test_removing_a_member_ends_their_sessions(client: httpx.AsyncClient) -> None:
    await f.halden()
    other = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")  # type: ignore[attr-defined]
    await f.sign_in(other, f.HALDEN["analyst2"])
    assert (await other.get(f"{API}/incidents")).status_code == 200
    headers = await f.sign_in(client, f.HALDEN["admin"])
    mid = await _member_id(client, f.HALDEN["analyst2"])
    assert (await client.delete(f"{API}/org/members/{mid}", headers=headers)).status_code == 204
    assert (await other.get(f"{API}/incidents")).status_code == 401
    await other.aclose()


async def test_invitation_flow_for_a_new_person(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    r = await client.post(
        f"{API}/org/invitations", json={"email": "New.Analyst@halden.example", "role": "analyst"}, headers=headers
    )
    assert r.status_code == 201 and r.json()["email"] == "new.analyst@halden.example"
    token = r.json()["acceptUrl"].rsplit("/", 1)[1]
    assert len((await client.get(f"{API}/org/invitations")).json()["items"]) == 1
    guest = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")  # type: ignore[attr-defined]
    info = (await guest.get(f"{API}/invitations/{token}")).json()
    assert (
        info["orgName"] == "Halden Logistics"
        and info["accountExists"] is False
        and info["roleLabel"] == "Security analyst"
    )
    await guest.get(f"{API}/auth/csrf")
    h = {"X-CSRF-Token": guest.cookies.get("nj_csrf") or ""}
    weak = await guest.post(
        f"{API}/invitations/{token}/accept", json={"fullName": "New", "password": "short"}, headers=h
    )
    assert weak.status_code == 422
    me = await guest.post(
        f"{API}/invitations/{token}/accept",
        json={"fullName": "Nadia Analyst", "password": "Correct-horse-42!"},
        headers=h,
    )
    assert me.status_code == 200 and me.json()["membership"]["role"] == "analyst"
    assert (await guest.get(f"{API}/invitations/{token}")).status_code == 404  # single use
    await guest.aclose()


async def test_invitation_for_existing_account_needs_its_password(client: httpx.AsyncClient) -> None:
    await f.halden()
    await f.brightline()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    r = await client.post(f"{API}/org/invitations", json={"email": f.OLU, "role": "auditor"}, headers=headers)
    token = r.json()["acceptUrl"].rsplit("/", 1)[1]
    guest = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")  # type: ignore[attr-defined]
    assert (await guest.get(f"{API}/invitations/{token}")).json()["accountExists"] is True
    await guest.get(f"{API}/auth/csrf")
    h = {"X-CSRF-Token": guest.cookies.get("nj_csrf") or ""}
    bad = await guest.post(f"{API}/invitations/{token}/accept", json={"password": "Wrong-password-1"}, headers=h)
    assert bad.status_code == 401
    ok = await guest.post(f"{API}/invitations/{token}/accept", json={"password": DEMO_PASSWORD}, headers=h)
    assert ok.status_code == 200 and {o["name"] for o in ok.json()["orgs"]} == {
        "Halden Logistics",
        "Brightline Couriers",
    }
    await guest.aclose()


async def test_expired_and_revoked_invitations(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    a = (
        await client.post(
            f"{API}/org/invitations", json={"email": "a@halden.example", "role": "analyst"}, headers=headers
        )
    ).json()
    b = (
        await client.post(
            f"{API}/org/invitations", json={"email": "b@halden.example", "role": "analyst"}, headers=headers
        )
    ).json()
    async with session_scope() as db:
        await db.execute(
            update(Invitation)
            .where(Invitation.email == "a@halden.example")
            .values(expires_at=clock.real_now() - timedelta(minutes=1))
        )
    assert (await client.get(f"{API}/invitations/{a['acceptUrl'].rsplit('/', 1)[1]}")).status_code == 410
    assert (await client.delete(f"{API}/org/invitations/{b['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/invitations/{b['acceptUrl'].rsplit('/', 1)[1]}")).status_code == 404
    assert (
        await client.post(
            f"{API}/org/invitations", json={"email": f.HALDEN["auditor"], "role": "analyst"}, headers=headers
        )
    ).status_code == 409


async def test_ingest_keys_secret_shown_once(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    r = await client.post(f"{API}/org/ingest-keys", json={"name": "EDR webhook", "source": "webhook"}, headers=headers)
    assert r.status_code == 201 and r.json()["secret"].startswith(r.json()["key"]["prefix"])
    listing = (await client.get(f"{API}/org/ingest-keys")).json()
    assert "secret" not in listing[0] and listing[0]["revokedAt"] is None
    assert (await client.delete(f"{API}/org/ingest-keys/{listing[0]['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/org/ingest-keys")).json()[0]["revokedAt"] is not None


async def test_org_timezone_is_validated(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    assert (await client.patch(f"{API}/org", json={"timezone": "Mars/Olympus"}, headers=headers)).status_code == 422
    assert (await client.patch(f"{API}/org", json={"timezone": "Europe/Brussels"}, headers=headers)).json()[
        "timezone"
    ] == "Europe/Brussels"


async def test_regulator_contacts_round_trip_with_owner(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    headers = await f.sign_in(client, f.HALDEN["executive"])
    ingrid = str(h["users"][f.HALDEN["executive"]])
    body = {
        "nis2": {
            "jurisdiction": "EU",
            "authorityName": "CCB",
            "portalUrl": "https://notif.example/",
            "internalOwner": {"userId": ingrid},
            "contacts": [
                {
                    "name": "Ingrid Vos",
                    "role": "CISO",
                    "email": "ingrid.vos@halden.example",
                    "phone": "+32",
                    "notes": "",
                }
            ],
        }
    }
    r = await client.put(f"{API}/settings/regulators", json=body, headers=headers)
    assert r.status_code == 200 and r.json()["nis2"]["internalOwner"]["name"] == "Ingrid Vos"
    assert (await client.get(f"{API}/settings/regulators")).json()["nis2"]["authorityName"] == "CCB"
    body["nis2"]["internalOwner"] = {"userId": "00000000-0000-0000-0000-000000000000"}
    assert (await client.put(f"{API}/settings/regulators", json=body, headers=headers)).status_code == 422
    body["nis2"]["portalUrl"] = "javascript:alert(1)"
    assert (await client.put(f"{API}/settings/regulators", json=body, headers=headers)).status_code == 422
    assert (await client.get(f"{API}/audit?action=settings.contacts_updated")).json()["total"] == 1


async def test_profile_and_preferences(client: httpx.AsyncClient) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    prof = (await client.get(f"{API}/settings/profile")).json()
    assert {e["id"] for e in prof["entities"]} == {"halden-us", "halden-eu", "halden-au"}
    bad = {"entities": [prof["entities"][0], prof["entities"][0]], "crownJewelTags": []}
    assert (await client.put(f"{API}/settings/profile", json=bad, headers=headers)).status_code == 422
    bad_tz = {"entities": [{**prof["entities"][0], "timezone": "Nowhere/City"}], "crownJewelTags": []}
    assert (await client.put(f"{API}/settings/profile", json=bad_tz, headers=headers)).status_code == 422
    assert (await client.put(f"{API}/settings/profile", json=prof, headers=headers)).status_code == 200
    r = await client.patch(
        f"{API}/settings/preferences", json={"autoInvestigate": False, "weeklyBriefDay": "friday"}, headers=headers
    )
    assert r.json()["autoInvestigate"] is False and r.json()["weeklyBriefDay"] == "friday"
    me = (await client.get(f"{API}/auth/me")).json()
    assert me["activeOrg"]["settings"]["weeklyBriefDay"] == "friday"


async def test_audit_chain_export_and_verify(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    await f.incident(h["org_id"])
    await f.sign_in(client, f.HALDEN["auditor"])
    v = (await client.get(f"{API}/audit/verify")).json()
    assert v["valid"] is True and v["checked"] >= 2
    csv = await client.get(f"{API}/audit/export.csv")
    assert csv.headers["content-type"].startswith("text/csv") and "incident.opened" in csv.text
    assert "attachment" in csv.headers["content-disposition"]
