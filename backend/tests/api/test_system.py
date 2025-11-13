"""Rules, models, pipeline health, dead letters, agents, jobs and the demo clock (B's modules faked)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from nightjar import clock
from nightjar.contracts import AgentCardInfo, AttackRef, ModelInfo, RuleInfo
from tests import factories as f

API = "/api/v1"
RULE = RuleInfo(
    "r-lsass",
    "LSASS memory read via comsvcs",
    "sigma",
    "high",
    "stable",
    "windows/process_access",
    (AttackRef("T1003.001", "LSASS Memory", ("credential-access",)),),
    True,
    3,
    0.9,
    "SigmaHQ (DRL 1.1)",
)


@pytest.fixture
def fake_detect(monkeypatch: pytest.MonkeyPatch) -> dict:
    from nightjar.detect import api as detect

    state = {"enabled": True}

    def info() -> RuleInfo:
        from dataclasses import replace

        return replace(RULE, enabled=state["enabled"])

    monkeypatch.setattr(detect, "list_rules", lambda org_id: [info()])
    monkeypatch.setattr(detect, "get_rule", lambda org_id, rid: (info(), "title: x") if rid == RULE.id else None)

    async def toggle(org_id, rid, *, enabled):  # type: ignore[no-untyped-def]
        state["enabled"] = enabled
        return info()

    monkeypatch.setattr(detect, "set_rule_enabled", toggle)
    return state


async def test_rules_listing_filters_and_toggle(client: httpx.AsyncClient, fake_detect: dict) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    assert len((await client.get(f"{API}/rules?tactic=credential-access&q=lsass")).json()) == 1
    assert (await client.get(f"{API}/rules?source=ueba")).json() == []
    assert (await client.get(f"{API}/rules/{RULE.id}")).json()["yaml"] == "title: x"
    r = await client.patch(f"{API}/rules/{RULE.id}", json={"enabled": False}, headers=headers)
    assert r.json()["enabled"] is False and fake_detect["enabled"] is False
    assert (await client.patch(f"{API}/rules/nope", json={"enabled": False}, headers=headers)).status_code == 404
    headers = await f.sign_in(client, f.HALDEN["responder"])
    assert (await client.patch(f"{API}/rules/{RULE.id}", json={"enabled": True}, headers=headers)).status_code == 403


async def test_models_and_promotion_sign_off(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.ml import api as ml

    champ = ModelInfo("triage", "3", "champion", "replays", {"pr_auc": 0.91}, True, "# Card")
    chal = ModelInfo("triage", "4", "challenger", "replays", {"pr_auc": 0.93}, False, "# Card")
    monkeypatch.setattr(ml, "list_models", lambda: [champ, chal])

    def promote(name, version, *, promoted_by):  # type: ignore[no-untyped-def]
        if version == "4":
            raise ValueError("Version 4 did not pass its quality gates.")
        return champ

    monkeypatch.setattr(ml, "promote", promote)
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["executive"])
    assert "cardMarkdown" not in (await client.get(f"{API}/models")).json()[0]
    one = (await client.get(f"{API}/models/triage")).json()
    assert one["cardMarkdown"] == "# Card" and len(one["versions"]) == 2
    refused = await client.post(
        f"{API}/models/triage/promote", json={"version": "4", "note": "Better PR-AUC"}, headers=headers
    )
    assert refused.status_code == 409
    ok = await client.post(
        f"{API}/models/triage/promote", json={"version": "3", "note": "Keep champion"}, headers=headers
    )
    assert ok.status_code == 200
    assert (await client.get(f"{API}/audit?action=model.promoted")).json()["total"] == 1


async def test_pipeline_health_offline_and_detail_by_role(client: httpx.AsyncClient) -> None:
    await f.halden()
    await f.sign_in(client, f.HALDEN["executive"])
    h = (await client.get(f"{API}/pipeline/health")).json()
    assert h["status"] in ("normal", "catching_up", "degraded", "offline") and h["streams"] == []
    assert h["plainStatus"]


async def test_dead_letters_and_retry(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.stream import api as stream

    retried: list[int] = []
    monkeypatch.setattr(
        stream, "list_dlq", lambda *, page, page_size: ([{"seq": 7, "stream": "NJ_RAW"}], 1), raising=False
    )

    async def retry(seq: int) -> None:
        if seq != 7:
            raise KeyError(seq)
        retried.append(seq)

    monkeypatch.setattr(stream, "retry_dlq", retry, raising=False)
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    assert (await client.get(f"{API}/pipeline/dlq")).json()["items"][0]["seq"] == 7
    assert (await client.post(f"{API}/pipeline/dlq/7/retry", headers=headers)).status_code == 200 and retried == [7]
    assert (await client.post(f"{API}/pipeline/dlq/8/retry", headers=headers)).status_code == 404
    await f.sign_in(client, f.HALDEN["auditor"])
    assert (await client.get(f"{API}/pipeline/dlq")).status_code == 403


async def test_agents_cards(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.agents import api as agents

    async def health():  # type: ignore[no-untyped-def]
        return [
            AgentCardInfo(
                "triage", "Nightjar Triage", "http://127.0.0.1:8031", ("triage_incident",), ("intel:read",), True
            )
        ]

    monkeypatch.setattr(agents, "agents_health", health)
    await f.halden()
    await f.sign_in(client, f.HALDEN["auditor"])
    assert (await client.get(f"{API}/agents")).json()[0]["toolScopes"] == ["intel:read"]


async def test_jobs_are_private_to_the_org(client: httpx.AsyncClient) -> None:
    await f.halden()
    await f.sign_in(client, f.HALDEN["analyst"])
    assert (await client.get(f"{API}/jobs/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"{API}/jobs/nope")).status_code == 404


async def test_attack_technique_lookup(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.intel import api as intel

    monkeypatch.setattr(
        intel,
        "attack_technique_detail",
        lambda tid: (
            {
                "techniqueId": tid,
                "name": "MFA Request Generation",
                "tactics": ["credential-access"],
                "description": "d",
                "url": "u",
            }
            if tid == "T1621"
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(intel, "attack_technique", lambda tid: None)
    await f.halden()
    await f.sign_in(client, f.HALDEN["analyst"])
    assert (await client.get(f"{API}/attack/techniques/t1621")).json()["name"] == "MFA Request Generation"
    assert (await client.get(f"{API}/attack/techniques/T9999")).status_code == 404


async def test_me_reports_the_demo_clock(client: httpx.AsyncClient) -> None:
    await f.halden()
    await f.sign_in(client, f.HALDEN["analyst"])
    clock.install("2025-11-20")
    try:
        me = (await client.get(f"{API}/auth/me")).json()
        assert me["clock"]["today"] == "2025-11-20" and me["clock"]["demo"] == clock.is_shifted()
        assert me["clock"]["now"].startswith("2025-11-20")
    finally:
        clock.uninstall()


async def test_datetimes_with_offsets_are_stored_as_utc(client: httpx.AsyncClient) -> None:
    from nightjar.db.models.soc import DeadlineRow
    from nightjar.db.session import session_scope

    h = await f.halden()
    ref = await f.incident(h["org_id"])
    ny = datetime(2025, 11, 25, 17, 30, tzinfo=timezone(timedelta(hours=-5)))
    async with session_scope() as db:
        row = DeadlineRow(
            org_id=h["org_id"],
            incident_id=ref.id,
            regime="sec_8k",
            entity_id="halden-us",
            notice_type="sec_8k.item_105",
            starts_at=ny,
            due_at=ny,
            timezone="America/New_York",
        )
        db.add(row)
        await db.flush()
        rid = row.id
    async with session_scope() as db:
        got = await db.get(DeadlineRow, rid)
        assert got is not None and got.due_at == datetime(2025, 11, 25, 22, 30, tzinfo=UTC)


def test_clock_travel_restores_offset() -> None:
    before = clock.offset()
    with clock.travel(datetime(2025, 11, 19, 3, 52, tzinfo=UTC)):
        assert abs(clock.now() - datetime(2025, 11, 19, 3, 52, tzinfo=UTC)) < timedelta(seconds=1)
    assert clock.offset() == before
