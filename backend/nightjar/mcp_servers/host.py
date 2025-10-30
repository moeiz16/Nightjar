"""One host process for the five tool servers (ADR 0002: five processes would be too heavy for the laptop):
Streamable HTTP at `/mcp/<server>` on :8034, each server with its own bearer-token verification.

`ToolClient` is what an agent uses: it mints its tenant-bound token and talks Streamable HTTP either to the host
on :8034 (`AGENTS_TRANSPORT=http`) or to the same ASGI app in-process (`inline`, tests and the seed), so the
auth and scope checks are identical in both modes.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from nightjar.mcp_servers.auth import tool_token
from nightjar.mcp_servers.servers import SERVER_NAMES, ScopedServer, build_all


class ToolHost:
    """ASGI app dispatching `/mcp/<name>…` to that server's Streamable HTTP app; runs every session manager."""

    def __init__(self, base_url: str = "http://127.0.0.1:8034") -> None:
        self.base_url = base_url.rstrip("/")
        self.servers: dict[str, ScopedServer] = build_all(self.base_url)
        from urllib.parse import urlparse

        from mcp.server.transport_security import TransportSecuritySettings

        u = urlparse(self.base_url)
        hosts = [u.netloc, u.hostname or "", "127.0.0.1:8034", "localhost:8034", "127.0.0.1", "localhost"]
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=sorted(set(hosts)), allowed_origins=[self.base_url]
        )
        self.apps = {
            name: srv.streamable_http_app(
                streamable_http_path=f"/mcp/{name}",
                stateless_http=True,
                json_response=True,
                transport_security=security,
            )
            for name, srv in self.servers.items()
        }
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        # anyio task groups must be entered and exited by the same task: keep them in this one.
        async with contextlib.AsyncExitStack() as stack:
            for srv in self.servers.values():
                await stack.enter_async_context(srv.session_manager.run())
            self._ready.set()
            await self._stop.wait()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._ready, self._stop = asyncio.Event(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="tool-host")
        await self._ready.wait()

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            with contextlib.suppress(Exception):
                await self._task
            self._task = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await self.start()
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await self.stop()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        path = scope.get("path", "")
        if path in ("/health", "/healthz"):
            body = b'{"status":"ok","servers":["' + b'","'.join(n.encode() for n in SERVER_NAMES) + b'"]}'
            await send(
                {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": body})
            return
        for name, app in self.apps.items():
            if path.startswith(f"/mcp/{name}") or path.endswith(f"/mcp/{name}"):
                await app(scope, receive, send)
                return
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"not found"})


_inline_host: ToolHost | None = None


async def inline_host() -> ToolHost:
    global _inline_host
    if _inline_host is None:
        _inline_host = ToolHost("http://tools.local")
        await _inline_host.start()
    return _inline_host


async def reset_inline_host() -> None:
    global _inline_host
    if _inline_host is not None:
        await _inline_host.stop()
        _inline_host = None


class ToolClient:
    """Calls tools as one agent for one organisation. Returns structured content (dict / list / None)."""

    def __init__(
        self,
        agent: str,
        org_id: uuid.UUID,
        *,
        transport: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        from nightjar.core.config import get_settings

        s = get_settings()
        self.agent = agent
        self.org_id = org_id
        self.transport = transport or s.agents_transport
        self.base_url = (base_url or s.mcp_url).rstrip("/")
        self.token = token or tool_token(agent, org_id)
        self.calls: list[dict[str, Any]] = []

    @contextlib.asynccontextmanager
    async def _session(self, server: str) -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if self.transport == "inline":
            host = await inline_host()
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=host), base_url=host.base_url, headers=headers, timeout=30
            )
            url = f"{host.base_url}/mcp/{server}"
        else:
            http = httpx2.AsyncClient(headers=headers, timeout=30)
            url = f"{self.base_url}/mcp/{server}"
        async with http, Client(streamable_http_client(url, http_client=http)) as client:
            yield client

    async def list_tools(self, server: str) -> list[str]:
        async with self._session(server) as c:
            return [t.name for t in (await c.list_tools()).tools]

    async def call(self, server: str, tool: str, args: dict[str, Any]) -> tuple[Any, bool, str]:
        """(structured result, is_error, error text)."""
        async with self._session(server) as c:
            res = await c.call_tool(tool, args)
        text = " ".join(getattr(x, "text", "") for x in (res.content or []))
        sc = res.structured_content
        if isinstance(sc, dict) and set(sc) == {"result"}:
            sc = sc["result"]
        self.calls.append({"server": server, "tool": tool, "error": bool(res.is_error)})
        return sc, bool(res.is_error), text if res.is_error else ""
