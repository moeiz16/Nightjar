"""Smoke check for tool servers (ADR 0002): the official `mcp` Python SDK (2.x, `MCPServer`).

Builds one MCP server with a read tool (`lookup_kev`, structured output) and a destructive tool
(`isolate_host`, `destructive_hint=True`) and verifies:
  1. in-process client: list tools (annotations, JSON schemas) and call a tool with structured output
  2. Streamable HTTP with the SDK's bearer `TokenVerifier`: no token → 401; a token scoped `intel:read`
     can call `lookup_kev` but is refused `isolate_host` (scope check inside the server, not the client)
  3. the destructive tool also refuses a responder-scoped token without a valid approval token
  4. stdio transport (the server launched as a subprocess)

    python scripts/smoke/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import socket
import sys
from importlib.metadata import version
from typing import Any

import httpx2
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

APPROVAL_KEY = b"smoke-approval-signing-key"
TOKENS = {
    "tok-triage": AccessToken(token="tok-triage", client_id="agent:triage", scopes=["intel:read"]),
    "tok-response": AccessToken(
        token="tok-response",
        client_id="agent:response",
        scopes=["intel:read", "response:execute"],
    ),
}


class KevResult(BaseModel):
    cve_id: str
    known_exploited: bool
    vendor: str | None = None
    ransomware_use: str | None = None


class ActionResult(BaseModel):
    action: str
    target: str
    executed: bool
    reason: str


class StaticVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return TOKENS.get(token)


def _scopes() -> set[str]:
    tok = get_access_token()
    return set(tok.scopes) if tok else {"*"}  # in-process and stdio runs are trusted local test harnesses


def sign_approval(action: str, target: str) -> str:
    return hmac.new(APPROVAL_KEY, f"{action}:{target}".encode(), hashlib.sha256).hexdigest()


def build_server(*, with_auth: bool, url: str = "http://127.0.0.1:1") -> MCPServer:
    kwargs: dict[str, Any] = {}
    if with_auth:
        kwargs = {
            "token_verifier": StaticVerifier(),
            "auth": AuthSettings(issuer_url=url, resource_server_url=f"{url}/mcp", required_scopes=["intel:read"]),
        }
    server = MCPServer("nightjar-smoke", version="0.1.0", **kwargs)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def lookup_kev(cve_id: str) -> KevResult:
        """Is this CVE in the CISA Known Exploited Vulnerabilities catalogue (bundled offline copy)?"""
        if "intel:read" not in _scopes() and "*" not in _scopes():
            raise ToolError("scope intel:read required")
        known = cve_id.upper() == "CVE-2023-4966"
        return KevResult(
            cve_id=cve_id.upper(),
            known_exploited=known,
            vendor="Citrix" if known else None,
            ransomware_use="Known" if known else None,
        )

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, read_only_hint=False))
    def isolate_host(host: str, approval_token: str = "") -> ActionResult:
        """Network-isolate a host. Requires scope response:execute AND a human approval token."""
        scopes = _scopes()
        if "response:execute" not in scopes and "*" not in scopes:
            raise ToolError("scope response:execute required")
        if not hmac.compare_digest(approval_token, sign_approval("isolate_host", host)):
            return ActionResult(action="isolate_host", target=host, executed=False, reason="approval required")
        return ActionResult(action="isolate_host", target=host, executed=True, reason="approved")

    return server


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def main() -> None:
    print(f"mcp {version('mcp')}")
    # 1. in-process
    async with Client(build_server(with_auth=False)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        check("tools listed", set(tools) == {"lookup_kev", "isolate_host"})
        check("destructive annotation exposed", bool(tools["isolate_host"].annotations.destructive_hint))
        check("input schema generated", "cve_id" in tools["lookup_kev"].input_schema["properties"])
        res = await client.call_tool("lookup_kev", {"cve_id": "cve-2023-4966"})
        check(
            "structured output",
            (res.structured_content or {}).get("known_exploited") is True,
            str(res.structured_content),
        )

    # 2./3. Streamable HTTP + bearer token verification + scopes
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app = build_server(with_auth=True, url=base).streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx2.AsyncClient() as raw:
            r = await raw.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            check("no bearer token → 401", r.status_code == 401, str(r.status_code))

        async def as_agent(token: str, tool: str, args: dict[str, Any]) -> Any:
            from mcp.client.streamable_http import streamable_http_client

            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=10)
            async with http, Client(streamable_http_client(f"{base}/mcp", http_client=http)) as c:
                return await c.call_tool(tool, args)

        ok = await as_agent("tok-triage", "lookup_kev", {"cve_id": "CVE-2023-4966"})
        check("triage token may read intel over HTTP", (ok.structured_content or {}).get("vendor") == "Citrix")
        denied = await as_agent("tok-triage", "isolate_host", {"host": "ant-wms-01"})
        check("triage token refused destructive tool", bool(denied.is_error), _text(denied))
        held = await as_agent("tok-response", "isolate_host", {"host": "ant-wms-01"})
        check(
            "responder without approval → not executed",
            (held.structured_content or {}).get("executed") is False,
            str(held.structured_content),
        )
        approved = await as_agent(
            "tok-response",
            "isolate_host",
            {"host": "ant-wms-01", "approval_token": sign_approval("isolate_host", "ant-wms-01")},
        )
        check("responder with approval → executed", (approved.structured_content or {}).get("executed") is True)
    finally:
        server.should_exit = True
        await task

    # 4. stdio
    params = StdioServerParameters(command=sys.executable, args=[__file__, "--stdio"])
    async with Client(params) as client:
        res = await client.call_tool("lookup_kev", {"cve_id": "CVE-2021-44228"})
        check("stdio transport", (res.structured_content or {}).get("known_exploited") is False)
    print("MCP smoke: PASS")


def _text(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)[:80]


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        build_server(with_auth=False).run("stdio")
    else:
        asyncio.run(main())
