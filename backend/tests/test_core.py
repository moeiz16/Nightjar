"""Core helpers: demo clock, approval tokens, SSE outbox."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import httpx

from nightjar import clock
from nightjar.core.security import sign_approval_token, verify_approval_token
from tests.conftest import login, make_member


def test_demo_clock_shifts_today_but_not_real_time() -> None:
    try:
        clock.install("2025-11-20")
        assert clock.today() == date(2025, 11, 20)
        assert clock.real_now().date() != date(2025, 11, 20) or clock.offset() == timedelta(0)
        assert abs((clock.now() - clock.real_now()) - clock.offset()) < timedelta(seconds=1)
        assert clock.is_shifted() or clock.real_now().date() == date(2025, 11, 20)
    finally:
        clock.uninstall()
    assert clock.offset() == timedelta(0)


def test_approval_token_binds_action_target_org_and_expires() -> None:
    org = str(uuid.uuid4())
    token, claims = sign_approval_token(approval_id="a1", org_id=org, action="isolate_host", target="ant-wms-01")
    ok = verify_approval_token(token, action="isolate_host", target="ant-wms-01", org_id=org)
    assert ok is not None and ok.nonce == claims.nonce
    assert verify_approval_token(token, action="isolate_host", target="ant-wms-02", org_id=org) is None
    assert verify_approval_token(token, action="disable_account", target="ant-wms-01", org_id=org) is None
    assert verify_approval_token(token, action="isolate_host", target="ant-wms-01", org_id=str(uuid.uuid4())) is None
    assert verify_approval_token(token[:-2] + "xx", action="isolate_host", target="ant-wms-01", org_id=org) is None
    expired, _ = sign_approval_token(
        approval_id="a2", org_id=org, action="isolate_host", target="h", ttl=timedelta(seconds=-1)
    )
    assert verify_approval_token(expired, action="isolate_host", target="h", org_id=org) is None


async def test_sse_stream_delivers_permitted_events(client: httpx.AsyncClient) -> None:
    from sqlalchemy import select

    from nightjar.core.events import emit, stream
    from nightjar.db.models.platform import Organization, User
    from nightjar.db.session import session_scope

    await make_member("aiko@halden.example", "analyst")
    await login(client, "aiko@halden.example")
    async with session_scope() as db:
        org = (await db.execute(select(Organization))).scalar_one()
        user = (await db.execute(select(User))).scalar_one()
        await emit(db, org.id, "incident.created", {"incidentId": "x"}, permission="incidents:read")
        await emit(db, org.id, "notice.submitted", {"noticeId": "y"}, permission="notices:submit")
    frames = []
    gen = stream(org.id, user.id, {"incidents:read"}, after_id=0, max_seconds=1.5)
    async for frame in gen:
        frames.append(frame)
    names = [f.get("event") for f in frames]
    assert names[0] == "ready" and "incident.created" in names and "notice.submitted" not in names
    await asyncio.sleep(0)
