"""The B-facing incident store: detections, scores, anomalies, correlation upserts and agent context."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from nightjar import clock
from nightjar.contracts import Anomaly, CorrelationUpdate, Entities, Reason, TriageScore
from nightjar.db.models.platform import AuditLog, UiEvent
from nightjar.db.models.soc import DetectionRow, Incident, IncidentEvent, TriageScoreRow
from nightjar.db.session import session_scope
from nightjar.incidents import api as incidents
from tests import factories as f


async def _count(model, *conds) -> int:  # type: ignore[no-untyped-def]
    async with session_scope() as db:
        return int((await db.execute(select(func.count()).select_from(model).where(*conds))).scalar_one())


async def test_record_detections_is_idempotent_and_announces_once(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    det = f.detection(h["org_id"], users=("j.okafor",))
    await incidents.record_detections(h["org_id"], [det])
    await incidents.record_detections(h["org_id"], [det])
    assert await _count(DetectionRow) == 1
    assert await _count(UiEvent, UiEvent.name == "detection.created") == 1
    assert await _count(TriageScoreRow) == 1


async def test_record_detections_refuses_other_org_rows(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    det = f.detection(uuid.uuid4())
    with pytest.raises(ValueError):
        await incidents.record_detections(h["org_id"], [det])


async def test_record_triage_scores_replaces_the_score(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    det = f.detection(h["org_id"], band=None)
    await incidents.record_detections(h["org_id"], [det])
    score = TriageScore(det.id, 0.12, "likely_noise", 0.5, (Reason("hour", 3, -0.2, "Usual time"),), "v2", True)
    await incidents.record_triage_scores(h["org_id"], [score])
    await incidents.record_triage_scores(uuid.uuid4(), [score])  # another org: ignored
    async with session_scope() as db:
        row = await db.get(TriageScoreRow, det.id)
        assert (
            row is not None and row.band == "likely_noise" and row.suppressed and row.reasons[0]["text"] == "Usual time"
        )


async def test_upsert_creates_reference_and_is_idempotent(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], key="corr:abc")
    assert ref.created and ref.reference == "NJ-1001" and ref.status == "new"
    again = await f.incident(h["org_id"], key="corr:abc")
    assert not again.created and again.id == ref.id
    second = await f.incident(h["org_id"], key="corr:def")
    assert second.reference == "NJ-1002"
    assert await _count(AuditLog, AuditLog.action == "incident.opened") == 2
    assert await _count(UiEvent, UiEvent.name == "incident.created") == 2


async def test_references_are_per_organisation(client: httpx.AsyncClient) -> None:
    h, b = await f.halden(), await f.brightline()
    await f.incident(h["org_id"])
    ref_b = await f.incident(b["org_id"], users=("m.devries",))
    assert ref_b.reference == "NJ-1001"


async def test_upsert_extends_entities_attack_and_severity(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], key="corr:x", severity="medium", users=("j.okafor",), techniques=("T1566.002",))
    await f.incident(
        h["org_id"], key="corr:x", severity="high", users=("j.okafor",), ips=("198.51.100.77",), techniques=("T1621",)
    )
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None
        assert inc.severity == "high" and "198.51.100.77" in inc.entities["ips"]
        assert {a["techniqueId"] for a in inc.attack} == {"T1566.002", "T1621"} and inc.version == 2
    assert await _count(UiEvent, UiEvent.name == "incident.updated") >= 1
    assert await _count(IncidentEvent, IncidentEvent.kind == "detection") == 4


async def test_crown_jewel_raises_severity_one_level(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(h["org_id"], severity="high", hosts=("mem-dispatch-01",), users=("svc-dispatch",))
    async with session_scope() as db:
        assert (await db.get(Incident, ref.id)).severity == "critical"  # type: ignore[union-attr]


async def test_all_noise_storm_is_one_likely_benign_incident(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    ref = await f.incident(
        h["org_id"],
        kind="benign_activity",
        severity="low",
        hosts=("ant-backup-01",),
        users=("svc-backup",),
        detections=25,
        noise=True,
    )
    async with session_scope() as db:
        inc = await db.get(Incident, ref.id)
        assert inc is not None and inc.likely_benign and inc.severity == "low"
    assert await _count(DetectionRow, DetectionRow.incident_id == ref.id) == 25


async def test_merge_incident_ids_closes_others(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    a = await f.incident(h["org_id"], users=("j.okafor",))
    b = await f.incident(h["org_id"], users=("j.okafor",), ips=("198.51.100.77",))
    det = f.detection(h["org_id"], users=("j.okafor",))
    await incidents.record_detections(h["org_id"], [det])
    await incidents.upsert_incident(
        CorrelationUpdate(
            org_id=h["org_id"],
            correlation_key="corr:merge",
            detection_ids=(det.id,),
            kind="credential_misuse",
            title="Merged",
            severity="high",
            attack=(),
            entities=Entities(users=("j.okafor",)),
            first_seen=clock.now(),
            last_seen=clock.now(),
            summary="",
            max_triage_probability=0.9,
            all_likely_noise=False,
            merge_incident_ids=(a.id, b.id),
        )
    )
    async with session_scope() as db:
        for ref in (a, b):
            inc = await db.get(Incident, ref.id)
            assert inc is not None and inc.status == "closed" and inc.resolution["reason"] == "merged"
            assert inc.merged_into_id is not None
        moved = (
            await db.execute(
                select(func.count()).select_from(DetectionRow).where(DetectionRow.incident_id == inc.merged_into_id)
            )
        ).scalar_one()
        assert moved == 5


async def test_open_incident_index_for_correlator_warm_start(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    recent = await f.incident(h["org_id"], key="corr:recent", users=("j.okafor",), techniques=("T1621",))
    await f.incident(h["org_id"], key="corr:old", at=clock.now() - timedelta(days=3))
    idx = await incidents.open_incident_index(h["org_id"], since=clock.now() - timedelta(hours=6))
    assert [e.correlation_key for e in idx] == ["corr:recent"]
    assert idx[0].incident_id == recent.id and idx[0].entities.users == ("j.okafor",)
    assert idx[0].attack_tactics == ("credential-access",)


async def test_incident_context_has_inventory_and_is_tenant_checked(client: httpx.AsyncClient) -> None:
    h, b = await f.halden(), await f.brightline()
    ref = await f.incident(h["org_id"], hosts=("ant-ws-014",), users=("j.okafor@halden.example",))
    ctx = await incidents.get_incident_context(h["org_id"], ref.id)
    assert ctx.reference == "NJ-1001" and len(ctx.detections) == 2
    assert [a.name for a in ctx.assets] == ["ant-ws-014"]
    assert [i.username for i in ctx.identities] == ["j.okafor"]
    assert ctx.detections[0].triage is not None and ctx.detections[0].evidence[0].summary.text == "sign-in"
    assert {e["id"] for e in ctx.org.entities} == {"halden-us", "halden-eu", "halden-au"}
    with pytest.raises(KeyError):
        await incidents.get_incident_context(b["org_id"], ref.id)


async def test_record_anomalies_is_idempotent(client: httpx.AsyncClient) -> None:
    h = await f.halden()
    now = clock.now()
    a = Anomaly(
        uuid.uuid4(),
        h["org_id"],
        "user",
        "t.reyes",
        now - timedelta(minutes=5),
        now,
        0.97,
        0.71,
        {"bytes_out": 9.1},
        {"bytes_out": 3.2e9},
        (Reason("bytes_out", 3.2e9, 0.6, "Sent far more data"),),
        "ueba-1",
    )
    await incidents.record_anomalies(h["org_id"], [a])
    await incidents.record_anomalies(h["org_id"], [a])
    assert await _count(UiEvent, UiEvent.name == "anomaly.created") == 1
