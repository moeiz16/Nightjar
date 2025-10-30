"""A2A plumbing on the official `a2a-sdk` 1.3 (protocol 1.0, JSON-RPC binding; scripts/smoke/a2a_smoke.py):
Agent Cards, the Starlette app per agent with a peer-token gate, the client used for hand-offs, and the step
recorder every agent uses to append to the replayable run log."""
# ruff: noqa: ASYNC109  (explicit per-call timeouts are part of the bus interface)

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_artifact, new_data_message, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    Role,
    SecurityScheme,
    SendMessageRequest,
    TaskState,
)
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nightjar import clock
from nightjar.contracts import AgentStep
from nightjar.core.metrics import AGENT_STEPS
from nightjar.mcp_servers.auth import SCOPES, peer_of, peer_token

AGENT_VERSION = "1.0.0"
CALLERS: dict[str, tuple[str, ...]] = {
    "triage": ("orchestrator",),
    "enrichment": ("triage",),
    "response": ("triage", "orchestrator"),
}
SKILL_CALLERS: dict[str, tuple[str, ...]] = {
    "triage_incident": ("orchestrator",),
    "enrich_incident": ("triage",),
    "plan_response": ("triage",),
    "execute_action": ("orchestrator",),
}
CARD_INFO: dict[str, dict[str, Any]] = {
    "triage": {
        "name": "Nightjar Triage",
        "description": "Decides whether an incident is real, benign or needs a person, citing the evidence.",
        "skills": [
            ("triage_incident", "Triage an incident", "Checks the evidence and returns a verdict with reasons.")
        ],
    },
    "enrichment": {
        "name": "Nightjar Enrichment",
        "description": "Adds threat intelligence, known exploited vulnerabilities and asset context to an incident.",
        "skills": [("enrich_incident", "Enrich an incident", "Looks up indicators, techniques and asset exposure.")],
    },
    "response": {
        "name": "Nightjar Response",
        "description": "Plans containment for people to approve, and carries out approved actions.",
        "skills": [
            ("plan_response", "Plan a response", "Proposes containment actions and opens tickets; executes nothing."),
            (
                "execute_action",
                "Carry out an approved action",
                "Runs one action after the required people approved it.",
            ),
        ],
    },
}


def agent_url(agent: str) -> str:
    from nightjar.core.config import get_settings

    s = get_settings()
    if s.agents_transport == "inline":
        return f"http://{agent}.agents.local"
    return {"triage": s.agent_triage_url, "enrichment": s.agent_enrichment_url, "response": s.agent_response_url}[agent]


def build_card(agent: str, url: str | None = None) -> AgentCard:
    info = CARD_INFO[agent]
    url = url or agent_url(agent)
    return AgentCard(
        name=info["name"],
        description=info["description"],
        version=AGENT_VERSION,
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        security_schemes={
            "peerToken": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="bearer", description="Nightjar peer token; only listed callers are accepted."
                )
            )
        },
        skills=[
            AgentSkill(
                id=sid,
                name=name,
                description=desc,
                tags=["nightjar", "security-operations", agent],
                examples=[json.dumps({"skill": sid, "incidentId": "…"})],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
            for sid, name, desc in info["skills"]
        ],
    )


class PeerTokenGate(BaseHTTPMiddleware):
    """Cards are public; the RPC route needs a known peer token (401) from an allowed caller (403)."""

    def __init__(self, app: Any, agent: str) -> None:
        super().__init__(app)
        self.agent = agent

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/.well-known/") or request.url.path == "/health":
            return await call_next(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        caller = peer_of(token) if token else None
        if caller is None:
            return JSONResponse({"error": "unknown caller"}, status_code=401)
        if caller not in CALLERS[self.agent]:
            return JSONResponse({"error": f"{caller} may not call {self.agent}"}, status_code=403)
        request.state.caller = caller
        return await call_next(request)


def build_app(agent: str, executor: AgentExecutor) -> Starlette:
    card = build_card(agent)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "agent": agent})

    from starlette.routing import Route

    app = Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/"), Route("/health", health)]
    )
    app.add_middleware(PeerTokenGate, agent=agent)
    return app


def first_data(parts: Any) -> dict[str, Any]:
    data = get_data_parts(parts)
    return dict(data[0]) if data else {}


_inline_apps: dict[str, Starlette] = {}


def register_inline(agent: str, app: Starlette) -> None:
    _inline_apps[agent] = app


async def call_agent(caller: str, agent: str, payload: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
    """Send one DataPart message to `agent` as `caller`; returns {state, taskId, artifact}."""
    from nightjar.core.config import get_settings

    headers = {"Authorization": f"Bearer {peer_token(caller)}"}
    if get_settings().agents_transport == "inline":
        if agent not in _inline_apps:
            from nightjar.agents.service import ensure_inline_agents

            ensure_inline_agents()
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_inline_apps[agent]),
            base_url=agent_url(agent),
            headers=headers,
            timeout=timeout,
        )
    else:
        http = httpx.AsyncClient(headers=headers, timeout=timeout)
    card = build_card(agent)
    async with http:
        client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
        try:
            out: dict[str, Any] = {"state": None, "taskId": None, "artifact": {}}
            msg = new_data_message(payload, media_type="application/json", role=Role.ROLE_USER)
            async for resp in client.send_message(SendMessageRequest(message=msg)):
                if resp.HasField("task"):
                    out["state"] = TaskState.Name(resp.task.status.state)
                    out["taskId"] = resp.task.id
                    for art in resp.task.artifacts:
                        out["artifact"] = first_data(art.parts)
            return out
        finally:
            await client.close()


class BaseExecutor(AgentExecutor):
    """Runs `handle(payload, recorder)` inside an A2A task: SUBMITTED → WORKING → COMPLETED (or FAILED)."""

    agent: str = "agent"
    artifact_name: str = "result"

    async def handle(self, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def artifact_for(self, payload: dict[str, Any]) -> str:
        return self.artifact_name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await up.start_work()
        payload = first_data(context.message.parts)
        try:
            result = await self.handle(payload, task.id)
        except Exception as exc:
            await up.add_artifact(
                parts=new_data_artifact("error", {"error": f"{type(exc).__name__}: {exc}"[:500]}).parts, name="error"
            )
            await up.failed()
            return
        name = self.artifact_for(payload)
        await up.add_artifact(parts=new_data_artifact(name, result).parts, name=name)
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # pragma: no cover
        raise NotImplementedError


def input_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Recorder:
    """Appends steps for one agent in one run (`investigations.append_step`)."""

    def __init__(self, run_id: uuid.UUID, agent: str, a2a_task_id: str | None) -> None:
        self.run_id, self.agent, self.task_id = run_id, agent, a2a_task_id

    async def step(
        self,
        kind: str,
        title: str,
        *,
        detail: dict[str, Any] | None = None,
        tool: str | None = None,
        model: str | None = None,
        flags: tuple[str, ...] = (),
        started: float | None = None,
        hash_of: Any = None,
        input_hash_value: str | None = None,
    ) -> int:
        from nightjar.investigations import api as investigations

        now = time.perf_counter()
        step = AgentStep(
            agent=self.agent,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            title=title,
            started_at=clock.now(),
            duration_ms=int(((now - started) if started else 0) * 1000),
            detail=detail or {},
            tool=tool,
            a2a_task_id=self.task_id,
            model=model,
            input_hash=input_hash_value or (input_hash(hash_of) if hash_of is not None else None),
            flags=flags,
        )
        AGENT_STEPS.labels(agent=self.agent, kind=kind).inc()
        return await investigations.append_step(self.run_id, step)


Handler = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
AGENT_SCOPES = SCOPES
