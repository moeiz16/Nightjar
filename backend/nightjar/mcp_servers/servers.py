"""The five tool servers (API contract Part 5.2) on the official `mcp` SDK (`MCPServer`).

Every tool checks its scope **inside the server** from the verified bearer token (`get_access_token()`), and
`tools/list` only lists the tools the caller may use. The token also fixes the organisation, so tools never take
an org id from arguments. Strings that came from logs, intel or tickets are returned as `UntrustedText`.

The response server's destructive tools execute only when all of these hold, in this order (ADR 0006): scope
`response:execute`; a valid approval token for this exact org + action + target; the approval's status is
`approved`; the token's nonce is consumed for the first time; the target is still one of the incident's
entities (when the platform exposes the approval's incident). Every refusal is recorded through
`approvals.record_result` (audited `action.refused`) and counted in `nightjar_tool_denials_total`.
"""
# ruff: noqa: N803, N815  (tool arguments and outputs use the contract's camelCase names)

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from nightjar import clock
from nightjar.agents.actions import ACTIONS, TOOL_TO_KIND
from nightjar.contracts import ActionResult, AssetRecord, IdentityRecord, ProposedAction, to_doc
from nightjar.core.metrics import TOOL_DENIALS
from nightjar.mcp_servers.auth import AgentTokenVerifier, caller_of
from nightjar.safety import neutralise, untrusted_doc

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


class ScopedServer(MCPServer):
    """MCPServer whose tool list is filtered by the caller's scopes."""

    def __init__(self, name: str, *, base_url: str, **kwargs: Any) -> None:
        super().__init__(
            name,
            version="1.0.0",
            token_verifier=AgentTokenVerifier(),
            # The verifier binds each token to one agent and one organisation itself (validate audience there).
            auth=AuthSettings(
                issuer_url=base_url,  # type: ignore[arg-type]
                resource_server_url=f"{base_url}/mcp/{name}",  # type: ignore[arg-type]
                required_scopes=[],
                validate_token_resource=False,
            ),
            **kwargs,
        )
        self.tool_scopes: dict[str, str] = {}

    def scoped_tool(
        self, scope: str, annotations: ToolAnnotations
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tool_scopes[fn.__name__] = scope
            return self.tool(annotations=annotations)(fn)

        return deco

    async def list_tools(self):
        tools = await super().list_tools()
        access = get_access_token()
        scopes = set(access.scopes) if access else set()
        return [t for t in tools if self.tool_scopes.get(t.name) in scopes]


def require(scope: str, tool: str) -> tuple[str, uuid.UUID]:
    """(agent, org) of the caller; ToolError if the scope is missing (checked inside the server)."""
    access = get_access_token()
    who = caller_of(access)
    if access is None or who is None or scope not in access.scopes:
        agent = who[0] if who else "unknown"
        TOOL_DENIALS.labels(agent=agent, tool=tool).inc()
        raise ToolError(f"scope {scope} required")
    return who


# ── output models (structured content) ──────────────────────────────────────
class Technique(BaseModel):
    techniqueId: str
    name: str
    tactics: list[str]
    description: str
    url: str


class KevHitModel(BaseModel):
    cveId: str
    vendor: str
    product: str
    name: str
    dateAdded: str
    ransomwareUse: str


class Untrusted(BaseModel):
    text: str
    field: str
    flags: list[str] = Field(default_factory=list)
    truncated: bool = False


class Indicator(BaseModel):
    type: str
    value: str
    verdict: str
    source: str
    detail: Untrusted
    kev: KevHitModel | None = None


class Asset(BaseModel):
    id: str
    name: str
    kind: str
    ips: list[str]
    os: str | None
    site: str
    jurisdiction: str
    owner: str | None
    businessService: str | None
    criticality: str
    tags: list[str]
    isolated: bool
    vulnerabilities: list[str]


class Exposure(BaseModel):
    asset: Asset | None
    kev: list[KevHitModel]
    internetFacing: bool
    crownJewel: bool


class Identity(BaseModel):
    id: str
    username: str
    displayName: str
    email: str
    department: str
    title: str
    jurisdiction: str
    manager: str | None
    privileged: bool
    mfa: str
    usualCountries: list[str]
    disabled: bool
    kind: str


class SignInProfile(BaseModel):
    usualCountries: list[str]
    recentCountries: list[str]
    mfa: str | None
    privileged: bool


class Ticket(BaseModel):
    id: str
    key: str
    incidentId: str
    title: Untrusted
    body: Untrusted
    priority: str
    assigneeTeam: str
    status: str
    createdAt: str
    comments: list[dict[str, Any]] = Field(default_factory=list)


class Approval(BaseModel):
    approvalId: str
    status: str
    requiredRoles: list[str]


class Result(BaseModel):
    approvalId: str
    kind: str
    target: str
    executed: bool
    status: str
    detail: str
    executedAt: str
    toolCall: str


def _kev(h: Any) -> KevHitModel:
    return KevHitModel(**to_doc(h))


def _asset(a: AssetRecord) -> Asset:
    return Asset(**to_doc(a))


def _identity(i: IdentityRecord) -> Identity:
    return Identity(**to_doc(i))


def _ticket(t: dict[str, Any]) -> Ticket:
    return Ticket(
        id=str(t["id"]),
        key=t["key"],
        incidentId=str(t["incidentId"]),
        title=Untrusted(**untrusted_doc(neutralise(t.get("title", ""), field="ticket.title"))),
        body=Untrusted(**untrusted_doc(neutralise(t.get("body", ""), field="ticket.body"))),
        priority=t.get("priority", "p3"),
        assigneeTeam=t.get("assigneeTeam", ""),
        status=t.get("status", "open"),
        createdAt=str(t.get("createdAt", "")),
        comments=[
            {**c, "body": untrusted_doc(neutralise(c.get("body", ""), field="ticket.comment"))}
            for c in t.get("comments", [])
        ],
    )


# ── servers ─────────────────────────────────────────────────────────────────
def intel_server(base_url: str) -> ScopedServer:
    from nightjar.intel import api as intel

    s = ScopedServer(
        "intel", base_url=base_url, instructions="Offline threat intelligence: ATT&CK, CISA KEV, sample feed."
    )

    @s.scoped_tool("intel:read", READ)
    def lookup_attack_technique(techniqueId: str) -> Technique | None:
        """MITRE ATT&CK technique by id (v18.1, offline)."""
        require("intel:read", "lookup_attack_technique")
        d = intel.attack_technique_detail(techniqueId)
        return Technique(**d) if d else None

    @s.scoped_tool("intel:read", READ)
    def search_attack(query: str, limit: int = 5) -> list[Technique]:
        """Search ATT&CK techniques by words in id, name or tactic (at most 10)."""
        require("intel:read", "search_attack")
        return [Technique(**d) for d in intel.search_attack(query, limit=min(max(limit, 1), 10))]

    @s.scoped_tool("intel:read", READ)
    def lookup_kev(cveId: str) -> KevHitModel | None:
        """Is this CVE in CISA's Known Exploited Vulnerabilities catalogue (as of the demo date)?"""
        require("intel:read", "lookup_kev")
        h = intel.kev_lookup(cveId)
        return _kev(h) if h else None

    @s.scoped_tool("intel:read", READ)
    def lookup_indicator(type: str, value: str) -> Indicator:
        """Look up an IP, domain, URL or file hash in the bundled sample feed (fictional, labelled sample)."""
        require("intel:read", "lookup_indicator")
        f = intel.lookup_indicator(type, value)
        return Indicator(
            type=f.type,
            value=f.value,
            verdict=f.verdict,
            source=f.source,
            detail=Untrusted(**untrusted_doc(neutralise(f.detail, field="intel.detail"))),
            kev=_kev(f.kev) if f.kev else None,
        )

    return s


def assets_server(base_url: str) -> ScopedServer:
    from nightjar.directory import api as directory
    from nightjar.intel import api as intel

    s = ScopedServer("assets", base_url=base_url, instructions="Asset inventory of the caller's organisation.")

    @s.scoped_tool("assets:read", READ)
    async def get_asset(nameOrIp: str) -> Asset | None:
        """One asset by name or IP address."""
        _, org = require("assets:read", "get_asset")
        a = await directory.find_asset(org, nameOrIp)
        return _asset(a) if a else None

    @s.scoped_tool("assets:read", READ)
    async def search_assets(query: str, limit: int = 20) -> list[Asset]:
        """Search assets by name or business service."""
        _, org = require("assets:read", "search_assets")
        return [_asset(a) for a in await directory.search_assets(org, query, limit=min(max(limit, 1), 20))]

    @s.scoped_tool("assets:read", READ)
    async def asset_exposure(name: str) -> Exposure:
        """Known exploited vulnerabilities on an asset, whether it faces the internet, whether it is a crown jewel."""
        _, org = require("assets:read", "asset_exposure")
        a = await directory.find_asset(org, name)
        if a is None:
            return Exposure(asset=None, kev=[], internetFacing=False, crownJewel=False)
        return Exposure(
            asset=_asset(a),
            kev=[_kev(h) for h in intel.kev_for_assets(list(a.vulnerabilities))],
            internetFacing="internet_facing" in a.tags or a.kind == "network",
            crownJewel=a.criticality == "crown_jewel",
        )

    return s


def identity_server(base_url: str) -> ScopedServer:
    from nightjar.directory import api as directory

    s = ScopedServer("identity", base_url=base_url, instructions="Identity directory of the caller's organisation.")

    @s.scoped_tool("identity:read", READ)
    async def get_identity(usernameOrEmail: str) -> Identity | None:
        """One person or service account by username or email."""
        _, org = require("identity:read", "get_identity")
        i = await directory.find_identity(org, usernameOrEmail)
        return _identity(i) if i else None

    @s.scoped_tool("identity:read", READ)
    async def sign_in_profile(username: str, days: int = 90) -> SignInProfile:
        """Usual and recent sign-in countries, MFA method and privilege for an account."""
        _, org = require("identity:read", "sign_in_profile")
        i = await directory.find_identity(org, username)
        recent = await directory.recent_sign_in_countries(org, username, days=min(max(days, 1), 90))
        return SignInProfile(
            usualCountries=list(i.usual_countries) if i else [],
            recentCountries=list(recent),
            mfa=i.mfa if i else None,
            privileged=bool(i and i.privileged),
        )

    return s


def ticketing_server(base_url: str) -> ScopedServer:
    from nightjar.ticketing import api as ticketing

    s = ScopedServer("ticketing", base_url=base_url, instructions="The built-in service desk.")

    @s.scoped_tool("tickets:write", WRITE)
    async def create_ticket(
        incidentId: str,
        title: str,
        body: str,
        priority: str = "p2",
        assigneeTeam: str = "Security operations",
    ) -> Ticket:
        """Open (or return the existing) ticket for an incident."""
        _, org = require("tickets:write", "create_ticket")
        if priority not in ("p1", "p2", "p3", "p4"):
            raise ToolError("priority must be p1..p4")
        t = await ticketing.create_ticket(
            org,
            uuid.UUID(incidentId),
            title=title[:200],
            body=body[:4000],
            priority=priority,  # type: ignore[arg-type]
            assignee_team=assigneeTeam[:80],
        )
        return _ticket(t)

    @s.scoped_tool("tickets:write", WRITE)
    async def add_comment(ticketId: str, body: str) -> Ticket:
        """Comment on a ticket as the response agent."""
        _, org = require("tickets:write", "add_comment")
        return _ticket(await ticketing.add_comment(org, uuid.UUID(ticketId), body=body[:4000], author="Response agent"))

    @s.scoped_tool("tickets:read", READ)
    async def get_ticket(ticketId: str) -> Ticket | None:
        """A ticket with its comments."""
        _, org = require("tickets:read", "get_ticket")
        t = await ticketing.get_ticket(org, uuid.UUID(ticketId))
        return _ticket(t) if t else None

    return s


async def _execute(tool: str, approval_id: str, target: str, approval_token: str) -> Result:
    """The approval gate for every destructive tool (order matters; see module docstring)."""
    from nightjar.approvals import api as approvals
    from nightjar.core.security import verify_approval_token
    from nightjar.directory import api as directory

    kind = TOOL_TO_KIND[tool]
    spec = ACTIONS[kind]
    agent, org = require("response:execute", tool)
    try:
        aid = uuid.UUID(approval_id)
    except ValueError:
        aid = uuid.UUID(int=0)

    async def refuse(reason: str) -> Result:
        TOOL_DENIALS.labels(agent=agent, tool=tool).inc()
        res = ActionResult(aid, kind, target, False, "refused", reason, clock.now(), f"response.{tool}")  # type: ignore[arg-type]
        with contextlib.suppress(Exception):  # unknown approval id: still refuse
            await approvals.record_result(org, res)
        return Result(**to_doc(res))

    claims = verify_approval_token(approval_token or "", action=kind, target=target, org_id=str(org))
    if claims is None or claims.approval_id != str(aid):
        return await refuse("No valid human approval for this exact action and target.")
    ref = await approvals.get_approval(aid)
    if ref.status != "approved":
        return await refuse(f"The approval is {ref.status}, not approved.")
    if not await approvals.consume(org, aid, claims.nonce):
        return await refuse("This approval has already been used or has expired.")
    incident_check = getattr(approvals, "approval_target_in_incident", None)
    if incident_check is not None and not await incident_check(org, aid, target):
        return await refuse("The target is no longer part of the incident.")
    effector = getattr(directory, spec.effector or "")
    try:
        if kind in ("isolate_host", "release_host"):
            await effector(org, target, isolated=kind == "isolate_host", approval_id=aid)
        elif kind in ("disable_account", "enable_account"):
            await effector(org, target, disabled=kind == "disable_account", approval_id=aid)
        elif kind in ("block_ip", "unblock_ip"):
            await effector(org, target, blocked=kind == "block_ip", approval_id=aid)
        else:
            await effector(org, target, approval_id=aid)
    except KeyError:
        res = ActionResult(
            aid,
            kind,  # type: ignore[arg-type]
            target,
            False,
            "failed",
            "The target was not found in the inventory.",
            clock.now(),
            f"response.{tool}",
        )
        await approvals.record_result(org, res)
        return Result(**to_doc(res))
    res = ActionResult(aid, kind, target, True, "executed", f"{spec.label}: done.", clock.now(), f"response.{tool}")  # type: ignore[arg-type]
    await approvals.record_result(org, res)
    return Result(**to_doc(res))


def response_server(base_url: str) -> ScopedServer:
    from nightjar.approvals import api as approvals

    s = ScopedServer(
        "response",
        base_url=base_url,
        instructions="Containment actions. Destructive tools need a human approval token.",
    )

    @s.scoped_tool("response:propose", WRITE)
    async def propose_action(incidentId: str, runId: str, kind: str, target: str, rationale: str) -> Approval:
        """Propose a response action for human approval (never executes anything)."""
        _, org = require("response:propose", "propose_action")
        spec = ACTIONS.get(kind)
        if spec is None:
            raise ToolError(f"unknown action kind {kind}")
        action = ProposedAction(
            kind=kind,  # type: ignore[arg-type]
            target=target,
            target_type=spec.target_type,  # type: ignore[arg-type]
            rationale=rationale[:1000],
            expected_effect=spec.effect,
            reversible=spec.reversible,
        )
        try:
            ref = await approvals.request_approval(
                org, uuid.UUID(incidentId), uuid.UUID(runId) if runId else None, action
            )
        except ValueError as exc:
            raise ToolError(f"target_not_in_incident: {exc}") from exc
        return Approval(approvalId=str(ref.approval_id), status=ref.status, requiredRoles=list(ref.required_roles))

    def describe(fn: Callable[..., Any], tool: str) -> Callable[..., Any]:
        fn.__name__ = tool
        fn.__doc__ = (
            f"{ACTIONS[TOOL_TO_KIND[tool]].label}. Destructive: requires a valid single-use human approval token."
        )
        return s.scoped_tool("response:execute", DESTRUCTIVE)(fn)

    # Argument names follow API contract Part 5.2 (host / username / accessKeyId / ip / messageId).
    for tool in ("isolate_host", "release_host"):

        async def by_host(approvalId: str, host: str, approvalToken: str = "", _t: str = tool) -> Result:
            return await _execute(_t, approvalId, host, approvalToken)

        describe(_strip_default(by_host), tool)
    for tool in ("disable_account", "enable_account", "revoke_sessions", "reset_password"):

        async def by_user(approvalId: str, username: str, approvalToken: str = "", _t: str = tool) -> Result:
            return await _execute(_t, approvalId, username, approvalToken)

        describe(_strip_default(by_user), tool)

    async def revoke_access_key(approvalId: str, accessKeyId: str, approvalToken: str = "") -> Result:
        return await _execute("revoke_access_key", approvalId, accessKeyId, approvalToken)

    describe(revoke_access_key, "revoke_access_key")
    for tool in ("block_ip", "unblock_ip"):

        async def by_ip(approvalId: str, ip: str, approvalToken: str = "", _t: str = tool) -> Result:
            return await _execute(_t, approvalId, ip, approvalToken)

        describe(_strip_default(by_ip), tool)

    async def quarantine_email(approvalId: str, messageId: str, approvalToken: str = "") -> Result:
        return await _execute("quarantine_email", approvalId, messageId, approvalToken)

    describe(quarantine_email, "quarantine_email")
    return s


def _strip_default(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Bind the loop variable `_t` and hide it from the tool's input schema."""
    import functools
    import inspect

    tool = inspect.signature(fn).parameters["_t"].default

    @functools.wraps(fn)
    async def wrapper(approvalId: str, approvalToken: str = "", **kw: Any) -> Result:
        return await fn(approvalId=approvalId, approvalToken=approvalToken, _t=tool, **kw)

    import typing

    hints = typing.get_type_hints(fn)  # resolve the string annotations (`from __future__ import annotations`)
    sig = inspect.signature(fn)
    params = [p.replace(annotation=hints.get(n, p.annotation)) for n, p in sig.parameters.items() if n != "_t"]
    wrapper.__signature__ = sig.replace(parameters=params, return_annotation=Result)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {n: hints[n] for n in hints if n != "_t"}
    return wrapper


SERVER_NAMES = ("intel", "assets", "identity", "ticketing", "response")
BUILDERS = {
    "intel": intel_server,
    "assets": assets_server,
    "identity": identity_server,
    "ticketing": ticketing_server,
    "response": response_server,
}


def build_all(base_url: str) -> dict[str, ScopedServer]:
    return {name: BUILDERS[name](base_url) for name in SERVER_NAMES}
