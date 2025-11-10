"""Fixtures and builders shared by the pipeline, detection, ML, agent and safety tests (builder B)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nightjar.contracts import RawEvent

T0 = datetime(2025, 11, 20, 2, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every B test gets its own (empty) database URL, memory bus, inline agents and the offline model."""
    from nightjar import clock
    from nightjar.core.config import get_settings
    from nightjar.db.session import get_engine, get_sessionmaker
    from nightjar.stream import runtime

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    monkeypatch.setenv("BUS", "memory")
    monkeypatch.setenv("AGENTS_TRANSPORT", "inline")
    monkeypatch.setenv("LLM_FAKE", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    runtime.set_bus(None)
    runtime._memory_dlq.clear()
    clock.uninstall()
    yield
    runtime.set_bus(None)
    clock.uninstall()
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()


@pytest.fixture
def platform() -> Iterator[Any]:
    from nightjar.workers.inmemory_platform import InMemoryPlatform

    p = InMemoryPlatform().install()
    yield p
    p.uninstall()


@pytest.fixture
async def bus() -> AsyncIterator[Any]:
    from nightjar.stream.bus import MemoryBus
    from nightjar.stream.runtime import install_dlq_mover, set_bus

    b = MemoryBus(time_scale=0.001)
    await b.connect()
    await b.setup()
    install_dlq_mover(b)
    set_bus(b)
    yield b
    set_bus(None)


@pytest.fixture
async def tool_host() -> AsyncIterator[Any]:
    from nightjar.mcp_servers.host import reset_inline_host

    yield
    await reset_inline_host()


def raw(
    payload: dict[str, Any], *, org: uuid.UUID | None = None, fmt: str | None = None, source: str = "webhook"
) -> RawEvent:
    from nightjar.workers.inmemory_platform import HALDEN_ORG

    return RawEvent(org or HALDEN_ORG, source, T0, payload, fmt)  # type: ignore[arg-type]


def sysmon_proc(
    image: str,
    cmd: str,
    *,
    host: str = "ant-ws-014",
    user: str = "HALDEN\\j.okafor",
    parent: str = "C:\\Windows\\explorer.exe",
    ts: datetime = T0,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "EventID": 1,
        "Channel": "Microsoft-Windows-Sysmon/Operational",
        "Hostname": f"{host}.halden.example",
        "Image": image,
        "CommandLine": cmd,
        "ParentImage": parent,
        "User": user,
        "@timestamp": ts.isoformat(),
        "OriginalFileName": image.split("\\")[-1],
        **extra,
    }


def vpn_fail(user: str, ip: str, ts: datetime, host: str = "mel-vpn-01") -> dict[str, Any]:
    return {
        "host": host,
        "program": "fortigate",
        "action": "login-failed",
        "user": user,
        "src_ip": ip,
        "message": f"action=login-failed user={user} src={ip}",
        "@timestamp": ts.isoformat(),
    }


def norm(payload: dict[str, Any], **kw: Any) -> Any:
    from nightjar.ingest.normalizer import normalize

    return normalize(raw(payload, **kw))


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
