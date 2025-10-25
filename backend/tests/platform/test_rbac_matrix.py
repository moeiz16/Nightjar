"""Role checks across the API: each write path refuses the roles the matrix in §1.3 excludes."""

from __future__ import annotations

import httpx
import pytest

from tests import factories as f

API = "/api/v1"

# (method, path, body, roles allowed past the permission check)
MATRIX = [
    ("PATCH", "/org", {"name": "Halden"}, {"admin"}),
    ("POST", "/org/invitations", {"email": "new.person@halden.example", "role": "analyst"}, {"admin"}),
    ("POST", "/org/ingest-keys", {"name": "EDR", "source": "webhook"}, {"admin"}),
    ("PUT", "/settings/regulators", {}, {"admin", "executive"}),
    ("PATCH", "/settings/preferences", {"autoInvestigate": True}, {"admin", "executive"}),
    ("POST", "/hunting/saved", {"name": "Spray", "query": {"filters": []}}, {"responder", "analyst"}),
    ("POST", "/hunting/search", {"filters": []}, {"admin", "responder", "analyst", "auditor"}),
    ("POST", "/executive/briefs/generate", {"week": "2025-W46"}, {"admin", "executive"}),
    ("GET", "/audit", None, {"admin", "executive", "auditor"}),
    ("GET", "/detections", None, {"admin", "responder", "analyst", "auditor"}),
    ("GET", "/watch", None, {"admin", "executive", "responder", "analyst", "auditor"}),
    ("GET", "/executive/posture", None, {"admin", "executive", "responder", "analyst", "auditor"}),
    ("GET", "/org/ingest-keys", None, {"admin"}),
    ("GET", "/org/invitations", None, {"admin"}),
    ("GET", "/reporting/clocks", None, {"admin", "executive", "responder", "analyst", "auditor"}),
]
ROLES = ["admin", "executive", "responder", "analyst", "auditor"]


@pytest.mark.parametrize("role", ROLES)
async def test_permission_matrix(client: httpx.AsyncClient, role: str) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN[role])
    for method, path, body, allowed in MATRIX:
        r = await client.request(method, API + path, json=body, headers=headers)
        if role in allowed:
            assert r.status_code != 403, (role, path, r.text)
        else:
            assert r.status_code == 403 and r.json()["code"] == "forbidden", (role, path, r.status_code)


@pytest.mark.parametrize("role", ["admin", "responder", "analyst", "auditor"])
async def test_only_the_executive_determines_and_signs_off(client: httpx.AsyncClient, role: str) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN[role])
    r = await client.post(
        f"{API}/incidents/{ref.id}/reporting/nis2/assess",
        json={
            "applies": True,
            "rationale": "Significant disruption",
            "entityId": "halden-eu",
            "awareAt": "2025-11-19T03:52:00+00:00",
        },
        headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["admin", "executive", "auditor"])
async def test_only_the_security_team_changes_incidents(client: httpx.AsyncClient, role: str) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"])
    headers = await f.sign_in(client, f.HALDEN[role])
    r = await client.post(f"{API}/incidents/{ref.id}/notes", json={"body": "note"}, headers=headers)
    assert r.status_code == 403


async def test_no_active_org_is_refused(client: httpx.AsyncClient) -> None:
    from nightjar.db.session import session_scope
    from nightjar.services import auth as auth_svc
    from tests.conftest import DEMO_PASSWORD

    async with session_scope() as db:
        await auth_svc.create_user(db, email="loner@example.com", password=DEMO_PASSWORD, full_name="Loner")
    await f.sign_in(client, "loner@example.com")
    r = await client.get(f"{API}/incidents")
    assert r.status_code == 403 and r.json()["code"] == "no_active_org"
