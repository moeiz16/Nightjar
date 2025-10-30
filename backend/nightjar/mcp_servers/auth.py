"""Least-privilege identity for agents (ADR 0002): peer tokens for A2A and tenant-bound bearer tokens for the tool
servers.

* Peer token (A2A): `AGENT_TOKEN_<NAME>` or, in development, `HMAC(SECRET_KEY, "agent:<name>")`.
* Tool token (MCP): `<agent>.<org-id>.<sig>` where `sig = HMAC(peer token of agent, org-id)`. The tool servers'
  `TokenVerifier` recomputes the signature, so a token names exactly one agent and one organisation: an agent
  investigating a Halden incident cannot read another tenant's inventory, and a token cannot be widened.
* Scopes come from the server-side table below, never from the token: the token says who, the server says what.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid

from mcp.server.auth.provider import AccessToken

SCOPES: dict[str, tuple[str, ...]] = {
    "triage": ("intel:read", "assets:read", "identity:read"),
    "enrichment": ("intel:read", "assets:read", "identity:read"),
    "response": (
        "assets:read",
        "identity:read",
        "tickets:write",
        "tickets:read",
        "response:propose",
        "response:execute",
    ),
    "orchestrator": (),
}
ALL_SCOPES = sorted({s for v in SCOPES.values() for s in v})


def _secret() -> bytes:
    try:
        from nightjar.core.config import get_settings

        return get_settings().secret_key.get_secret_value().encode()
    except Exception:  # pragma: no cover
        return b"dev-insecure-change-me-0123456789abcdef"


def peer_token(agent: str) -> str:
    env = os.environ.get(f"AGENT_TOKEN_{agent.upper()}")
    if env:
        return env
    return hmac.new(_secret(), f"agent:{agent}".encode(), hashlib.sha256).hexdigest()


def peer_of(token: str) -> str | None:
    """Which agent holds this peer token (constant-time comparison)."""
    for name in SCOPES:
        if hmac.compare_digest(token.encode(), peer_token(name).encode()):
            return name
    return None


def tool_token(agent: str, org_id: uuid.UUID | str) -> str:
    sig = hmac.new(peer_token(agent).encode(), str(org_id).encode(), hashlib.sha256).hexdigest()[:40]
    return f"{agent}.{org_id}.{sig}"


def parse_tool_token(token: str) -> tuple[str, uuid.UUID] | None:
    try:
        agent, org, sig = token.split(".", 2)
        org_id = uuid.UUID(org)
    except ValueError:
        return None
    if agent not in SCOPES:
        return None
    if not hmac.compare_digest(sig, tool_token(agent, org_id).rsplit(".", 1)[1]):
        return None
    return agent, org_id


class AgentTokenVerifier:
    """mcp `TokenVerifier`: the bearer token names the agent and its organisation; scopes from `SCOPES`."""

    async def verify_token(self, token: str) -> AccessToken | None:
        parsed = parse_tool_token(token)
        if parsed is None:
            return None
        agent, org_id = parsed
        return AccessToken(token=token, client_id=f"agent:{agent}@{org_id}", scopes=list(SCOPES[agent]))


def caller_of(access: AccessToken | None) -> tuple[str, uuid.UUID] | None:
    if access is None or not access.client_id.startswith("agent:") or "@" not in access.client_id:
        return None
    name, org = access.client_id.removeprefix("agent:").split("@", 1)
    try:
        return name, uuid.UUID(org)
    except ValueError:
        return None
