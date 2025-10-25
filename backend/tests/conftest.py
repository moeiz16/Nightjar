"""Test fixtures: a fresh SQLite database per test, the ASGI app in-process, helpers to sign in."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("BUS", "memory")
os.environ.setdefault("AGENTS_TRANSPORT", "inline")
os.environ.setdefault("LLM_FAKE", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("BACKGROUND_TASKS", "false")

import httpx
from argon2 import PasswordHasher

from nightjar.core import security as _security

# Cheap Argon2 parameters in tests only (production uses the OWASP minimums in core/security.py).
_security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
_security._DUMMY_HASH = _security._hasher.hash("timing-equaliser-not-a-real-password")

DEMO_PASSWORD = "Nightjar-demo-2025!"


@pytest.fixture
async def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[object]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "hunting.duckdb"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from nightjar import clock
    from nightjar.core.config import get_settings
    from nightjar.core.ratelimit import limiter
    from nightjar.db.session import get_engine, get_sessionmaker
    from nightjar.services.auth import reset_lockouts

    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    limiter.reset()
    reset_lockouts()
    clock.uninstall()

    import nightjar.db.models  # noqa: F401
    from nightjar.db.base import Base
    from nightjar.main import create_app

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    application = create_app()
    yield application
    from nightjar.hunting.api import close_store

    close_store()
    await get_engine().dispose()
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    clock.uninstall()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    await client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}


async def make_member(email: str, role: str, org_name: str = "Halden Logistics") -> None:
    """Create (or reuse) an org by name and add a user with the role."""
    from sqlalchemy import select

    from nightjar.db.models.platform import Membership, Organization
    from nightjar.db.session import session_scope
    from nightjar.services import auth as auth_svc

    async with session_scope() as db:
        org = (await db.execute(select(Organization).where(Organization.name == org_name))).scalar_one_or_none()
        user = await auth_svc.create_user(db, email=email, password=DEMO_PASSWORD, full_name=email.split("@")[0])
        if org is None:
            org = await auth_svc.create_org(db, name=org_name, owner=user)
            (await db.execute(select(Membership).where(Membership.user_id == user.id))).scalar_one().role = role
        else:
            db.add(Membership(org_id=org.id, user_id=user.id, role=role))


async def login(client: httpx.AsyncClient, email: str) -> dict:
    headers = await csrf(client)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()
