"""Platform skeleton: health, security headers, auth + CSRF, lockout, RBAC, tenant isolation, audit chain."""

from __future__ import annotations

import httpx
import pytest

from nightjar.core.permissions import ALL_PERMISSIONS, PERMISSIONS, ROLES, permissions_for, role_has
from tests.conftest import DEMO_PASSWORD, csrf, login, make_member


async def test_health_and_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "service": "nightjar-api"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "x-request-id" in r.headers
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200 and ready.json()["databaseOk"] is True


async def test_metrics_exposed(client: httpx.AsyncClient) -> None:
    await client.get("/health")
    r = await client.get("/metrics")
    assert "nightjar_http_requests_total" in r.text


async def test_problem_json_for_unknown_route(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "not_found"


async def test_login_requires_csrf(client: httpx.AsyncClient) -> None:
    await make_member("pieter@halden.example", "admin")
    r = await client.post("/api/v1/auth/login", json={"email": "pieter@halden.example", "password": DEMO_PASSWORD})
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"


async def test_login_me_logout(client: httpx.AsyncClient) -> None:
    await make_member("ingrid@halden.example", "executive")
    me = await login(client, "ingrid@halden.example")
    assert me["membership"]["role"] == "executive"
    assert "reporting:assess" in me["membership"]["permissions"]
    assert "actions:approve" not in me["membership"]["permissions"]
    assert me["session"]["expiresAt"] and me["clock"]["today"]
    assert client.cookies.get("nj_session")
    again = await client.get("/api/v1/auth/me")
    assert again.status_code == 200
    out = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": client.cookies.get("nj_csrf") or ""})
    assert out.status_code == 204
    client.cookies.delete("nj_session")
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_unsafe_request_without_csrf_header_is_refused(client: httpx.AsyncClient) -> None:
    await make_member("pieter@halden.example", "admin")
    await login(client, "pieter@halden.example")
    r = await client.patch("/api/v1/org", json={"name": "Renamed"})
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"


async def test_wrong_password_is_generic_then_locks(client: httpx.AsyncClient) -> None:
    await make_member("aiko@halden.example", "analyst")
    headers = await csrf(client)
    for _ in range(5):
        r = await client.post(
            "/api/v1/auth/login", json={"email": "aiko@halden.example", "password": "wrong-password-1"}, headers=headers
        )
        assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
    r = await client.post(
        "/api/v1/auth/login", json={"email": "aiko@halden.example", "password": DEMO_PASSWORD}, headers=headers
    )
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_rbac_admin_can_rename_org_auditor_cannot(client: httpx.AsyncClient) -> None:
    await make_member("pieter@halden.example", "admin")
    await make_member("samira@halden.example", "auditor")
    await login(client, "samira@halden.example")
    headers = {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}
    assert (await client.patch("/api/v1/org", json={"name": "X"}, headers=headers)).status_code == 403
    await login(client, "pieter@halden.example")
    headers = {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}
    r = await client.patch("/api/v1/org", json={"name": "Halden Logistics Group"}, headers=headers)
    assert r.status_code == 200 and r.json()["name"] == "Halden Logistics Group"


async def test_members_are_tenant_isolated(client: httpx.AsyncClient) -> None:
    await make_member("pieter@halden.example", "admin")
    await make_member("olu@brightline.example", "admin", org_name="Brightline Couriers")
    await login(client, "pieter@halden.example")
    emails = {m["user"]["email"] for m in (await client.get("/api/v1/org/members")).json()["items"]}
    assert emails == {"pieter@halden.example"}


async def test_audit_chain_verifies_and_detects_tampering(client: httpx.AsyncClient) -> None:
    from sqlalchemy import update

    from nightjar.db.models.platform import AuditLog
    from nightjar.db.session import session_scope

    await make_member("pieter@halden.example", "admin")
    await login(client, "pieter@halden.example")
    await login(client, "pieter@halden.example")
    v = (await client.get("/api/v1/audit/verify")).json()
    assert v["valid"] is True and v["checked"] >= 2
    listing = (await client.get("/api/v1/audit?action=auth.")).json()
    assert listing["total"] >= 2 and listing["items"][0]["label"] == "Signed in"
    async with session_scope() as db:
        await db.execute(update(AuditLog).where(AuditLog.id == 1).values(actor_name="Someone else"))
    v = (await client.get("/api/v1/audit/verify")).json()
    assert v["valid"] is False and v["brokenAt"] == 1


def test_role_matrix_separation_of_duties() -> None:
    assert set(ROLES) == {"admin", "executive", "responder", "analyst", "auditor"}
    assert all(PERMISSIONS[p] for p in ALL_PERMISSIONS)
    assert role_has("responder", "actions:approve") and not role_has("admin", "actions:approve")
    assert role_has("executive", "notices:submit") and not role_has("analyst", "notices:submit")
    assert not [p for p in permissions_for("auditor") if not p.endswith(":read")]
    assert permissions_for(None) == []


@pytest.mark.parametrize("role", ["admin", "executive", "responder", "analyst", "auditor"])
def test_every_role_can_see_the_watch(role: str) -> None:
    assert role_has(role, "watch:read") and role_has(role, "incidents:read")
