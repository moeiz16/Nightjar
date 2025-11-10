"""Correlation into incidents: time window + entity overlap, storms, merges, kinds and titles."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

from nightjar.contracts import AttackRef, Detection, Entities, OpenIncidentIndexEntry, TriageScore
from nightjar.correlate.correlator import Correlator, classify, link_keys
from tests.pipeline_support import T0

ORG = uuid.uuid4()


def det(
    *, hosts=(), users=(), ips=(), keys=(), sev="high", tech=("T1003.001",), band="likely_real", minutes=0, title="Rule"
) -> Detection:
    d = Detection(
        id=uuid.uuid4(),
        org_id=ORG,
        source="sigma",
        rule_id=title,
        rule_title=title,
        severity=sev,
        confidence=0.8,
        attack=tuple(AttackRef(t, t, ("credential-access",)) for t in tech),
        entities=Entities(hosts=tuple(hosts), users=tuple(users), ips=tuple(ips), access_keys=tuple(keys)),
        first_seen=T0 + timedelta(minutes=minutes),
        last_seen=T0 + timedelta(minutes=minutes),
        event_ids=("e",),
        evidence=(),
        summary="s",
    )
    p = 0.9 if band == "likely_real" else 0.01 if band == "likely_noise" else 0.5
    return replace(d, triage=TriageScore(d.id, p, band, 0.2, (), "1", band == "likely_noise"))  # type: ignore[arg-type]


def test_shared_host_within_window_joins_one_incident() -> None:
    c = Correlator()
    a = c.add(det(hosts=("h1",), users=("u1",)))
    b = c.add(det(hosts=("h1",), minutes=30))
    assert a and b and a.correlation_key == b.correlation_key and len(b.detection_ids) == 2


def test_outside_the_window_starts_a_new_cluster() -> None:
    c = Correlator()
    a = c.add(det(hosts=("h1",)))
    b = c.add(det(hosts=("h1",), minutes=200))
    assert a and b and a.correlation_key != b.correlation_key


def test_no_overlap_means_separate_incidents() -> None:
    c = Correlator()
    assert c.add(det(hosts=("h1",))).correlation_key != c.add(det(hosts=("h2",))).correlation_key


def test_wide_spray_users_do_not_link_unrelated_incidents() -> None:
    spray = det(hosts=("gw",), ips=("203.0.113.50",), users=tuple(f"u{i}" for i in range(40)), tech=("T1110.003",))
    assert not any(k.startswith("u:") for k in link_keys(spray))
    c = Correlator()
    a = c.add(spray)
    b = c.add(det(users=("u3",), hosts=("ws-1",)))
    assert a.correlation_key != b.correlation_key


def test_isolated_noise_never_opens_an_incident() -> None:
    c = Correlator()
    assert c.add(det(hosts=("h",), sev="medium", band="likely_noise")) is None
    assert c.add(det(hosts=("h2",), sev="medium", band="uncertain")) is None  # one medium alone


def test_two_medium_unsuppressed_detections_open() -> None:
    c = Correlator()
    assert c.add(det(hosts=("h",), sev="medium", band="uncertain")) is None
    assert c.add(det(hosts=("h",), sev="medium", band="uncertain", minutes=1)) is not None


def test_storm_opens_one_likely_benign_incident() -> None:
    c = Correlator(storm_size=20)
    out = None
    for i in range(25):
        out = (
            c.add(
                det(
                    hosts=(f"h{i % 12}",),
                    users=("svc-backup",),
                    sev="high",
                    band="likely_noise",
                    tech=("T1059.001",),
                    minutes=i * 0.5,
                )
            )
            or out
        )
    assert out is not None and out.all_likely_noise and out.kind == "benign_activity" and out.severity == "low"
    assert len(out.detection_ids) == 25 and len(out.entities.hosts) == 12


def test_bridge_detection_merges_two_open_incidents() -> None:
    c = Correlator()
    first = c.add(det(hosts=("h1",)))
    second = c.add(det(hosts=("h2",), minutes=5))
    c.note_incident(ORG, first.correlation_key, uuid.UUID(int=1))
    c.note_incident(ORG, second.correlation_key, uuid.UUID(int=2))
    merged = c.add(det(hosts=("h1", "h2"), minutes=10))
    assert merged.correlation_key == first.correlation_key and merged.merge_incident_ids == (uuid.UUID(int=2),)


def test_redelivered_detection_is_idempotent() -> None:
    c = Correlator()
    d = det(hosts=("h",))
    a, b = c.add(d), c.add(d)
    assert a.detection_ids == b.detection_ids == (d.id,)


def test_warm_start_attaches_to_restored_incident() -> None:
    c = Correlator()
    iid = uuid.uuid4()
    c.restore(ORG, [OpenIncidentIndexEntry(iid, "corr:old", Entities(hosts=("h9",)), ("impact",), T0, "investigating")])
    upd = c.add(det(hosts=("h9",), minutes=5))
    assert upd.correlation_key == "corr:old"


def test_kind_classification() -> None:
    assert classify([det(tech=("T1490",))], all_noise=False) == "ransomware_precursor"
    assert classify([det(tech=("T1566.002",)), det(tech=("T1621",))], all_noise=False) == "credential_misuse"
    assert classify([det(tech=("T1530",), keys=("AKIA1",))], all_noise=False) == "cloud_credential_exposure"
    assert classify([det(tech=("T1110.003",))], all_noise=False) == "brute_force"
    assert classify([det(tech=("T1567.002",))], all_noise=False) == "data_exfiltration"
    assert classify([det(tech=("T1110.003",))], all_noise=True) == "benign_activity"
