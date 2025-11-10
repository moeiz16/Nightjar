"""A2A agents end to end: Agent Cards, the peer-token gate, the halden-night replay through the real stages into
six incidents with agent verdicts, the replayable step log, approved-action execution and the alert-to-triage NFR."""

from __future__ import annotations

import uuid

import httpx
import pytest

from nightjar import clock
from nightjar.agents.a2a import build_card
from nightjar.agents.service import build_agent_app
from nightjar.mcp_servers.auth import peer_token
from nightjar.workers.inmemory_platform import HALDEN_ORG

pytestmark = pytest.mark.usefixtures("tool_host")


@pytest.mark.parametrize("agent", ["triage", "enrichment", "response"])
async def test_agent_cards_are_public_camel_case_and_describe_auth(agent: str) -> None:
    app = build_agent_app(agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
    assert card["name"] == build_card(agent).name and "supportedInterfaces" in card and "defaultInputModes" in card
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC" and card["securitySchemes"]
    assert card["capabilities"]["streaming"] is True and card["skills"]


@pytest.mark.parametrize(
    ("agent", "caller", "status"),
    [
        ("enrichment", None, 401),
        ("enrichment", "response", 403),
        ("triage", "enrichment", 403),
        ("response", "enrichment", 403),
    ],
)
async def test_peer_token_gate(agent: str, caller: str | None, status: int) -> None:
    headers = {"Authorization": f"Bearer {peer_token(caller)}"} if caller else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_agent_app(agent)), base_url="http://a", headers=headers
    ) as http:
        r = await http.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}})
    assert r.status_code == status


@pytest.fixture
async def night(platform):
    from nightjar.workers.inline import run_scenario_detailed

    clock.install("2025-11-20")
    run = await run_scenario_detailed(HALDEN_ORG, "halden-night", store_writer=None)
    return platform, run


async def test_halden_night_produces_the_six_seed_incidents(night) -> None:
    platform, _run = night
    incs = platform.incidents_sorted()
    assert [i.reference for i in incs] == [f"NJ-{n}" for n in range(1036, 1042)]
    kinds = [i.update.kind for i in incs]
    assert kinds == [
        "ransomware_precursor",
        "credential_misuse",
        "cloud_credential_exposure",
        "benign_activity",
        "brute_force",
        "data_exfiltration",
    ]
    storm = incs[3].update
    assert storm.all_likely_noise and storm.severity == "low" and len(storm.detection_ids) == 146
    assert storm.title == "Backup software update caused 146 alerts"


async def test_agent_verdicts_and_bounded_proposals(night) -> None:
    platform, _ = night
    incs = platform.incidents_sorted()
    runs = {r.ref.incident_id: r for r in platform.runs.values()}
    verdicts = [runs[i.id].verdict.verdict for i in incs]
    assert verdicts == ["true_positive", "true_positive", "true_positive", "benign", "true_positive", "needs_review"]
    for inc in incs:
        run = runs[inc.id]
        ent = inc.update.entities
        pools = {
            "host": ent.hosts,
            "user": ent.users,
            "ip": ent.ips,
            "access_key": ent.access_keys,
            "email": ent.emails,
        }
        for p in run.proposals:
            assert p.target_type == "ticket" or p.target in pools[p.target_type]
    ransomware = runs[incs[0].id]
    assert {(p.kind, p.target) for p in ransomware.proposals} >= {
        ("isolate_host", "mem-dispatch-01"),
        ("disable_account", "svc-dispatch"),
        ("block_ip", "203.0.113.66"),
    }
    facts = ransomware.verdict.facts
    assert set(facts.affected_jurisdictions) == {"EU", "US", "AU"} and facts.service_disruption == "outage"
    assert runs[incs[3].id].proposals == () and runs[incs[3].id].verdict.facts is None
    assert all(a.status in ("pending", "auto_approved") for a in platform.approvals.values())  # nothing ran itself


async def test_step_log_has_handoffs_tools_and_a_guardrail(night) -> None:
    platform, _ = night
    phishing = platform.incidents_sorted()[1]
    run = next(r for r in platform.runs.values() if r.ref.incident_id == phishing.id)
    agents = {s.agent for s in run.steps}
    kinds = [s.kind for s in run.steps]
    assert agents == {"orchestrator", "triage", "enrichment", "response"}
    assert kinds.count("handoff_out") >= 3 and "verdict" in kinds and "tool_call" in kinds
    guard = [s for s in run.steps if s.kind == "guardrail"]
    assert guard and "imperative_to_agent" in guard[0].flags  # the subject line told the AI to close it
    assert all(s.a2a_task_id for s in run.steps if s.agent != "orchestrator")


async def test_replay_verification_matches(night) -> None:
    from nightjar.agents.api import replay_verify

    platform, _ = night
    for run in platform.runs.values():
        res = await replay_verify(HALDEN_ORG, run.ref.run_id)
        assert res["inputHashesMatch"] and res["verdictMatches"] and res["checkedSteps"] > 3


async def test_alert_to_triage_under_60_seconds(night) -> None:
    platform, _run = night
    times = platform.alert_to_triage_seconds()
    assert len(times) == 6 and max(times) < 60


async def test_approved_action_runs_through_the_responder(night) -> None:
    from nightjar.agents.api import execute_approved_action
    from nightjar.stream.bus import MemoryBus
    from nightjar.stream.runtime import set_bus
    from nightjar.workers.inline import _drain
    from nightjar.workers.pipeline import Pipeline

    platform, _ = night
    key = next(a for a in platform.approvals.values() if a.action.kind == "revoke_access_key")
    platform.approve(key.id, "responder")
    bus = MemoryBus()
    await bus.connect()
    await bus.setup()
    set_bus(bus)
    clock_settings = __import__("nightjar.core.config", fromlist=["get_settings"]).get_settings()
    clock_settings.agents_transport = "inline"
    await execute_approved_action(HALDEN_ORG, key.id)
    await _drain(Pipeline(bus), ("responder",), store=None)
    assert platform.approvals[key.id].status == "executed"
    assert platform.effects[-1] == ("revoke_access_key", "AKIAEXAMPLEHALDEN01", key.id)


async def test_agent_cards_api() -> None:
    from nightjar.agents.api import agent_cards, agents_health

    cards = agent_cards()
    assert [c.agent for c in cards] == ["triage", "enrichment", "response"]
    assert "response:execute" in next(c for c in cards if c.agent == "response").tool_scopes
    assert all(c.healthy for c in await agents_health())


async def test_start_investigation_queues_an_agent_task(platform, bus) -> None:
    from nightjar.agents.api import start_investigation
    from nightjar.contracts import CorrelationUpdate, Entities
    from tests.pipeline_support import T0

    ref = await platform.upsert_incident(
        CorrelationUpdate(
            HALDEN_ORG, "corr:x", (), "other", "t", "low", (), Entities(hosts=("ant-ws-014",)), T0, T0, "s", None, False
        )
    )
    run_id = await start_investigation(HALDEN_ORG, ref.id, trigger="manual", requested_by=uuid.uuid4())
    (_, subject, data, headers) = bus.stream_messages("NJ_WORK")[0]
    assert subject == f"nj.agents.tasks.{HALDEN_ORG}" and str(run_id).encode() in data
    assert headers["Nj-Schema"] == "agent_task.v1"
