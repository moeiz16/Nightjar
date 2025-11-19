"""The seed in pipeline mode (the night replayed through the real stages and colleagues): every investigation
carries the night's timestamps, not the moment the seed ran."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from nightjar import clock
from tests import factories as f

API = "/api/v1"
BRUSSELS = ZoneInfo("Europe/Brussels")
DEMO_NOW = datetime(2025, 11, 20, 10, 30, tzinfo=BRUSSELS)


@pytest.mark.slow
async def test_replayed_investigations_happen_during_the_night(client: httpx.AsyncClient) -> None:
    from nightjar.seed import run

    with clock.travel(DEMO_NOW):
        out = await run(use_pipeline=True)
    assert out["mode"] == "pipeline"
    await f.sign_in(client, f.HALDEN["auditor"])
    for ref in ("NJ-1036", "NJ-1037", "NJ-1038", "NJ-1040"):
        inc = (await client.get(f"{API}/incidents/{ref}")).json()
        first, last = datetime.fromisoformat(inc["firstSeen"]), datetime.fromisoformat(inc["lastSeen"])
        decided = datetime.fromisoformat(inc["triage"]["decidedAt"])
        assert first < decided <= first + timedelta(minutes=32, seconds=1), ref
        tl = (await client.get(f"{API}/incidents/{inc['id']}/timeline")).json()
        agent_rows = [datetime.fromisoformat(e["at"]) for e in tl if e["actor"]["type"] == "agent"]
        assert agent_rows and max(agent_rows) < DEMO_NOW - timedelta(hours=5), ref
    n36 = (await client.get(f"{API}/incidents/NJ-1036")).json()
    assert datetime.fromisoformat(n36["triage"]["decidedAt"]).astimezone(BRUSSELS).strftime("%H:%M") == "04:42"
    assert n36["timeToTriageSeconds"] == 32 * 60
