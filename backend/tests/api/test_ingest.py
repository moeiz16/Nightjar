"""Event intake: keys per source, session ingest, limits, backpressure and file uploads (stream faked)."""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from nightjar.contracts import IngestAck, ScenarioInfo
from tests import factories as f

API = "/api/v1"


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list:
    from nightjar.stream import api as stream

    sent: list = []

    async def fake_publish(events):  # type: ignore[no-untyped-def]
        sent.extend(events)
        return IngestAck(len(events), 0, 0, 1, len(events), ())

    monkeypatch.setattr(stream, "publish_raw", fake_publish)
    return sent


async def _key(client: httpx.AsyncClient, source: str = "webhook") -> str:
    headers = await f.sign_in(client, f.HALDEN["admin"])
    r = await client.post(
        f"{API}/org/ingest-keys", json={"name": f"{source} sender", "source": source}, headers=headers
    )
    client.cookies.clear()
    return r.json()["secret"]


async def test_webhook_with_key_publishes_raw_events(client: httpx.AsyncClient, published: list) -> None:
    h = await f.halden()
    secret = await _key(client)
    r = await client.post(
        f"{API}/ingest/events?format=okta",
        headers={"X-Nightjar-Key": secret},
        json={"events": [{"eventType": "user.session.start"}, "not an object", {"a": 1}]},
    )
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 2 and r.json()["rejected"] == 1 and r.json()["errors"] == ["1: not a JSON object"]
    assert {e.org_id for e in published} == {h["org_id"]} and published[0].format == "okta"
    assert published[0].source == "webhook" and published[0].source_ref.startswith("njk_")


async def test_single_object_body_is_accepted(client: httpx.AsyncClient, published: list) -> None:
    await f.halden()
    secret = await _key(client)
    r = await client.post(f"{API}/ingest/events", headers={"X-Nightjar-Key": secret}, json={"message": "one"})
    assert r.status_code == 202 and len(published) == 1


@pytest.mark.parametrize("key", ["njk_wrong", ""])
async def test_bad_keys_are_refused(client: httpx.AsyncClient, published: list, key: str) -> None:
    await f.halden()
    r = await client.post(f"{API}/ingest/events", headers={"X-Nightjar-Key": key}, json={"a": 1})
    assert r.status_code == 401 and r.json()["code"] == "invalid_ingest_key"


async def test_revoked_and_wrong_source_keys_are_refused(client: httpx.AsyncClient, published: list) -> None:
    await f.halden()
    syslog = await _key(client, "syslog")
    r = await client.post(f"{API}/ingest/events", headers={"X-Nightjar-Key": syslog}, json={"a": 1})
    assert r.status_code == 401
    headers = await f.sign_in(client, f.HALDEN["admin"])
    kid = (await client.get(f"{API}/org/ingest-keys")).json()[0]["id"]
    await client.delete(f"{API}/org/ingest-keys/{kid}", headers=headers)
    client.cookies.clear()
    r = await client.post(f"{API}/ingest/syslog", headers={"X-Nightjar-Key": syslog}, content=b'{"a":1}\n')
    assert r.status_code == 401


async def test_session_ingest_needs_permission(client: httpx.AsyncClient, published: list) -> None:
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["responder"])
    assert (await client.post(f"{API}/ingest/events", json={"a": 1}, headers=headers)).status_code == 403
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    r = await client.post(f"{API}/ingest/events", json={"events": [{"a": 1}]}, headers=headers)
    assert r.status_code == 202 and published[0].source_ref.startswith("user:")
    assert (await client.post(f"{API}/ingest/events", json={"a": 1})).status_code == 403  # no CSRF header


async def test_limits(client: httpx.AsyncClient, published: list) -> None:
    await f.halden()
    secret = await _key(client)
    h = {"X-Nightjar-Key": secret}
    assert (await client.post(f"{API}/ingest/events", headers=h, json={"events": [{"a": 1}] * 501})).status_code == 422
    assert (await client.post(f"{API}/ingest/events", headers=h, json={"events": []})).status_code == 422
    big = {"events": [{"blob": "x" * 300_000}]}
    assert (await client.post(f"{API}/ingest/events", headers=h, json=big)).json()["rejected"] == 1
    huge = {"events": [{"blob": "x" * 200_000}] * 6}
    assert (await client.post(f"{API}/ingest/events", headers=h, json=huge)).status_code == 413
    bad = await client.post(f"{API}/ingest/events", headers={**h, "content-type": "application/json"}, content=b"{nope")
    assert bad.status_code == 400
    assert (await client.post(f"{API}/ingest/events?format=pcap", headers=h, json={"a": 1})).status_code == 422


async def test_backpressure_answers_503_with_retry_after(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nightjar.stream import api as stream

    async def full(events):  # type: ignore[no-untyped-def]
        raise stream.BackpressureError(retry_after_s=7)

    monkeypatch.setattr(stream, "publish_raw", full)
    await f.halden()
    secret = await _key(client)
    r = await client.post(f"{API}/ingest/events", headers={"X-Nightjar-Key": secret}, json={"a": 1})
    assert r.status_code == 503 and r.json()["code"] == "intake_full" and r.headers["retry-after"] == "7"


async def test_syslog_ndjson(client: httpx.AsyncClient, published: list) -> None:
    await f.halden()
    secret = await _key(client, "syslog")
    body = b'{"timestamp":"2025-11-20T02:20:00Z","host":"mel-vpn-01","message":"login failed"}\nnot json\n\n[1]\n'
    r = await client.post(f"{API}/ingest/syslog", headers={"X-Nightjar-Key": secret}, content=body)
    assert r.json()["accepted"] == 1 and r.json()["rejected"] == 2 and published[0].format == "syslog_json"


async def test_file_upload_creates_a_replay_job(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.stream import api as stream

    jobs: list = []

    async def enqueue(kind, job_id, payload):  # type: ignore[no-untyped-def]
        jobs.append((kind, job_id, payload))

    monkeypatch.setattr(stream, "enqueue_job", enqueue)
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["analyst"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "logs/cloudtrail.json", json.dumps({"Records": [{"eventName": "ListBuckets"}, {"eventName": "GetObject"}]})
        )
    r = await client.post(
        f"{API}/ingest/files",
        headers=headers,
        data={"speed": "120", "retime": "true"},
        files={"file": ("trail.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["kind"] == "replay" and jobs[0][0] == "replay_file" and jobs[0][2]["events"] == 2
    assert jobs[0][2]["speed"] == 120.0 and (await client.get(f"{API}/jobs/{job['id']}")).json()["status"] == "queued"
    bad = await client.post(
        f"{API}/ingest/files", headers=headers, files={"file": ("x.exe", b"MZ", "application/octet-stream")}
    )
    assert bad.status_code == 415
    empty = await client.post(f"{API}/ingest/files", headers=headers, files={"file": ("x.jsonl", b"\n", "text/plain")})
    assert empty.status_code == 422


async def test_scenarios_and_replay(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nightjar.stream import api as stream

    started: list = []
    monkeypatch.setattr(
        stream,
        "list_scenarios",
        lambda: [ScenarioInfo("halden-night", "A night at Halden", 4200, 270.0, 6, "synthetic")],
    )

    async def start(org_id, scenario_id, *, speed, job_id):  # type: ignore[no-untyped-def]
        started.append((scenario_id, speed))

    monkeypatch.setattr(stream, "start_replay", start)
    await f.halden()
    headers = await f.sign_in(client, f.HALDEN["admin"])
    assert (await client.get(f"{API}/ingest/scenarios")).json()[0]["incidentsExpected"] == 6
    r = await client.post(f"{API}/ingest/scenarios/halden-night/replay", json={"speed": 300}, headers=headers)
    assert r.status_code == 202 and started == [("halden-night", 300.0)]
    assert (await client.post(f"{API}/ingest/scenarios/nope/replay", json={}, headers=headers)).status_code == 404
