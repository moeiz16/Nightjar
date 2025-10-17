"""Smoke check for agent-to-agent messaging (ADR 0002): the official `a2a-sdk` (protocol 1.0, JSON-RPC binding).

Two agents run as real HTTP servers on free local ports, each publishing an Agent Card at
`/.well-known/agent-card.json`:
  * "triage" receives a task with structured data (a DataPart), delegates enrichment to the
    "enrichment" agent over A2A, and returns a verdict artifact that carries the enrichment result;
  * "enrichment" only accepts calls that present the triage agent's peer token (least privilege
    between agents), and answers with a data artifact.

Verifies: card discovery and fields, task lifecycle to COMPLETED, DataPart round trip, agent → agent
delegation, the peer-token gate (401 without it), and an in-process ASGI transport for tests.

    python scripts/smoke/a2a_smoke.py
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import uvicorn
from a2a.client import A2ACardResolver, ClientConfig, create_client
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
    Role,
    SendMessageRequest,
    TaskState,
)
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

PEER_TOKEN = "smoke-triage-to-enrichment"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def card(name: str, skill_id: str, url: str, description: str) -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[
            AgentSkill(
                id=skill_id,
                name=skill_id.replace("_", " ").title(),
                description=description,
                input_modes=["application/json"],
                output_modes=["application/json"],
                tags=["nightjar", "soc"],
            )
        ],
    )


def _first_data(parts: Any) -> dict[str, Any]:
    data = get_data_parts(parts)
    return data[0] if data else {}


async def call_agent(
    target: AgentCard | str, payload: dict[str, Any], *, http: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Send one data message and return {"state": ..., "artifact": {...}}."""
    client = await create_client(target, client_config=ClientConfig(streaming=False, httpx_client=http))
    try:
        out: dict[str, Any] = {"state": None, "artifact": {}}
        msg = new_data_message(payload, media_type="application/json", role=Role.ROLE_USER)
        async for resp in client.send_message(SendMessageRequest(message=msg)):
            if resp.HasField("task"):
                out["state"] = TaskState.Name(resp.task.status.state)
                for art in resp.task.artifacts:
                    out["artifact"] = _first_data(art.parts)
        return out
    finally:
        await client.close()


class EnrichmentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await up.start_work()
        req = _first_data(context.message.parts)
        ip = req.get("indicator", "")
        result = {"indicator": ip, "kev": [], "intel": "known scanner" if ip.startswith("185.") else "no match"}
        await up.add_artifact(parts=new_data_artifact("enrichment", result).parts, name="enrichment")
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


class TriageExecutor(AgentExecutor):
    def __init__(self, enrichment_url: str) -> None:
        self.enrichment_url = enrichment_url

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await up.start_work()
        alert = _first_data(context.message.parts)
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {PEER_TOKEN}"}, timeout=10) as http:
            peer_card = await A2ACardResolver(httpx_client=http, base_url=self.enrichment_url).get_agent_card()
            enrich = await call_agent(peer_card, {"indicator": alert.get("sourceIp", "")}, http=http)
        verdict = {
            "incidentId": alert.get("incidentId"),
            "verdict": "true_positive" if enrich["artifact"].get("intel") == "known scanner" else "needs_review",
            "handoff": {"to": peer_card.name, "state": enrich["state"]},
            "enrichment": enrich["artifact"],
        }
        await up.add_artifact(parts=new_data_artifact("verdict", verdict).parts, name="verdict")
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


class PeerTokenGate(BaseHTTPMiddleware):
    """Only the agent card is public; the RPC endpoint requires a known caller token."""

    def __init__(self, app: Any, allowed: set[str]) -> None:
        super().__init__(app)
        self.allowed = allowed

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/.well-known/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth.removeprefix("Bearer ") not in self.allowed:
            return JSONResponse({"error": "unknown caller"}, status_code=401)
        return await call_next(request)


def build_app(agent_card: AgentCard, executor: AgentExecutor, allowed: set[str] | None = None) -> Starlette:
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card)
    app = Starlette(routes=[*create_agent_card_routes(agent_card), *create_jsonrpc_routes(handler, "/")])
    if allowed is not None:
        app.add_middleware(PeerTokenGate, allowed=allowed)
    return app


async def main() -> None:
    from importlib.metadata import version

    import a2a

    print(f"a2a-sdk {version('a2a-sdk')} ({a2a.__name__})")
    p_tri, p_enr = _free_port(), _free_port()
    tri_url, enr_url = f"http://127.0.0.1:{p_tri}", f"http://127.0.0.1:{p_enr}"
    enr_card = card("Nightjar Enrichment", "enrich_indicators", enr_url, "Looks up indicators and asset context.")
    tri_card = card("Nightjar Triage", "triage_incident", tri_url, "Decides whether an incident is real or noise.")
    enr_app = build_app(enr_card, EnrichmentExecutor(), allowed={PEER_TOKEN})
    tri_app = build_app(tri_card, TriageExecutor(enr_url))

    servers = [
        uvicorn.Server(uvicorn.Config(enr_app, host="127.0.0.1", port=p_enr, log_level="warning")),
        uvicorn.Server(uvicorn.Config(tri_app, host="127.0.0.1", port=p_tri, log_level="warning")),
    ]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    try:
        while not all(s.started for s in servers):
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(timeout=10) as http:
            fetched = await A2ACardResolver(httpx_client=http, base_url=tri_url).get_agent_card()
            check("agent card served and parsed", fetched.name == "Nightjar Triage", fetched.skills[0].id)
            enr_fetched = await A2ACardResolver(httpx_client=http, base_url=enr_url).get_agent_card()
            check("second agent card served", enr_fetched.supported_interfaces[0].url == enr_url)
            raw = (await http.get(f"{enr_url}/.well-known/agent-card.json")).json()
            check("card JSON is camelCase", "supportedInterfaces" in raw and "defaultInputModes" in raw)
            denied = await http.post(enr_url + "/", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage"})
            check("peer-token gate rejects unknown callers", denied.status_code == 401)

        result = await call_agent(fetched, {"incidentId": "INC-1", "sourceIp": "185.220.101.4", "count": 3})
        art = result["artifact"]
        check("task reached COMPLETED", result["state"] == "TASK_STATE_COMPLETED", str(result["state"]))
        check("triage delegated to enrichment over A2A", art.get("handoff", {}).get("state") == "TASK_STATE_COMPLETED")
        check("structured data round trip", art.get("enrichment", {}).get("intel") == "known scanner", str(art))

        # In-process transport (what the test suite uses: no ports, no sockets)
        transport = httpx.ASGITransport(app=enr_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://enrichment.local", headers={"Authorization": f"Bearer {PEER_TOKEN}"}
        ) as http:
            local_card = card("Nightjar Enrichment", "enrich_indicators", "http://enrichment.local", "In-process")
            local = await call_agent(local_card, {"indicator": "10.0.0.5"}, http=http)
            check("in-process ASGI transport works", local["artifact"].get("intel") == "no match")
        print("A2A smoke: PASS")
    finally:
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
