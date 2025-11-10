"""Classical ML (ADR 0005): UEBA baselines and gates, the triage classifier at serving time, features, the
leakage-safe split, registry promotion rules and reproducibility."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from nightjar.contracts import AttackRef, Detection, Entities, EventSnapshot, UntrustedText
from nightjar.ml.features import (
    TRIAGE_FEATURES,
    EntityProfile,
    TriageFeaturizer,
    Window,
    reason_text,
    window_features,
    windows_of,
)
from nightjar.ml.runtime import ueba_model
from nightjar.ml.triage import champion
from nightjar.ml.ueba import gate_open, robust_baseline
from tests.pipeline_support import T0, norm, sysmon_proc

ORG = uuid.uuid4()


def detection(
    *,
    rule="r1",
    sev="high",
    hosts=("ant-ws-014",),
    users=("j.okafor",),
    parent="",
    minutes=0,
    tech="T1003.001",
    source="sigma",
    verdict=None,
) -> Detection:
    fields = {"process.parent.executable": parent} if parent else {}
    if verdict:
        fields["threat.indicator.verdict"] = verdict
    snap = EventSnapshot("e", T0, "windows.sysmon", "process-started", UntrustedText("x", "message"), fields=fields)
    return Detection(
        uuid.uuid4(),
        ORG,
        source,
        rule,
        rule,
        sev,
        0.8,
        (AttackRef(tech, tech, ("credential-access",)),),
        Entities(hosts=hosts, users=users),
        T0 + timedelta(minutes=minutes),
        T0 + timedelta(minutes=minutes),
        ("e",),
        (snap,),
        "s",
    )


# ── UEBA ─────────────────────────────────────────────────────────────────────
def test_mad_floor_keeps_sparse_features_finite() -> None:
    b = robust_baseline(np.zeros(200), "newCountry")
    assert b.median == 0 and b.mad >= 0.5  # without the floor a single new country would be infinitely unusual
    b2 = robust_baseline(np.array([0] * 199 + [10.0]), "bytesOutMb")
    assert b2.mad >= 5.0


def test_gate_needs_a_novelty_or_risk_signal() -> None:
    assert not gate_open("user", {"signIns": 9, "bytesOutMb": 50})
    assert gate_open("user", {"bytesOutMb": 450})
    assert gate_open("user", {"newCountry": 1})
    assert gate_open("host", {"serviceInstalls": 1}) and not gate_open("host", {"processStarts": 30})


def test_window_features_against_a_profile() -> None:
    e1 = norm(
        {
            "eventType": "user.session.start",
            "published": T0.isoformat(),
            "displayMessage": "x",
            "actor": {"alternateId": "j.okafor@halden.example"},
            "outcome": {"result": "SUCCESS"},
            "client": {"ipAddress": "198.51.100.77", "geographicalContext": {"country": "RO"}},
            "securityContext": {"asOrg": "Example Hosting VPS SRL"},
        }
    )
    w = Window("user", "j.okafor", T0, [e1])
    f = window_features(w, EntityProfile(countries={"BE"}, hours=[10] * 24))
    assert f["newCountry"] == 1 and f["hostingNetwork"] == 1 and f["signIns"] == 1


def test_ueba_model_flags_exfiltration_and_explains_it() -> None:
    model = ueba_model()
    assert model is not None
    events = []
    for i in range(3):
        events.append(
            norm(
                {
                    "host": "mem-citrix-01",
                    "program": "netscaler",
                    "action": "traffic",
                    "user": "t.reyes",
                    "src_ip": "10.10.20.40",
                    "dst_ip": "198.51.100.140",
                    "sentbyte": 450_000_000,
                    "@timestamp": (T0 + timedelta(seconds=30 * i)).isoformat(),
                    "message": "traffic",
                }
            )
        )
    (w,) = [w for w in windows_of(events) if w.entity_type == "user"]
    a = model.evaluate(ORG, w)
    assert a is not None and a.score >= 0.95 and a.reasons[0].feature == "bytesOutMb"
    assert "MB sent out" in a.reasons[0].text and a.id == model.evaluate(ORG, w).id
    assert [x.id for _, x in model.evaluate_many(ORG, [w])] == [a.id]


def test_normal_window_is_not_flagged() -> None:
    model = ueba_model()
    e = norm(
        sysmon_proc(
            "C:\\x\\OUTLOOK.EXE", "OUTLOOK.EXE", host="ant-ws-014", user="HALDEN\\j.okafor", ts=T0.replace(hour=9)
        )
    )
    assert all(model.evaluate(ORG, w) is None for w in windows_of([e]))


# ── triage classifier ────────────────────────────────────────────────────────
def test_champion_loads_with_a_monotone_calibration() -> None:
    m = champion()
    assert m is not None and m.features == TRIAGE_FEATURES
    assert np.all(np.diff(m.iso_y) >= 0) and 0 < m.threshold_noise <= 0.2 < m.threshold_real
    assert m.meta["gatesPassed"] is True


def test_bands_and_suppression_rules() -> None:
    m = champion()
    assert m.band(0.99) == "likely_real" and m.band(0.5) == "uncertain" and m.band(0.0) == "likely_noise"
    feat = TriageFeaturizer()
    row = feat.featurize(detection())
    low = {**row, **dict.fromkeys(row, 0.0)}
    for sev, tech, suppressed in (
        ("high", "T1059.001", True),
        ("critical", "T1059.001", False),
        ("high", "T1490", False),
        ("low", "T1490", True),
    ):
        d = detection(sev=sev, tech=tech)
        s = replace(m.score(d, low), band="likely_noise")
        never = d.severity == "critical" or (tech == "T1490" and sev == "high")
        assert (s.band == "likely_noise" and not never) == suppressed


def test_scores_have_three_plain_reasons() -> None:
    m = champion()
    s = m.score(detection(), TriageFeaturizer().featurize(detection()))
    assert len(s.reasons) == 3 and all(r.text and "_" not in r.text for r in s.reasons)
    assert 0.0 <= s.probability <= 1.0 and s.model_version == m.version


def test_featurizer_tracks_bursts_spread_and_lineage() -> None:
    f = TriageFeaturizer()
    rows = [
        f.featurize(
            detection(
                rule="enc",
                hosts=(f"h{i}",),
                parent="C:\\Program Files\\Veeam\\Endpoint Backup\\Veeam.EndPoint.Service.exe",
                minutes=i,
            )
        )
        for i in range(8)
    ]
    assert (
        rows[-1]["burst_same_rule"] == 8 and rows[-1]["host_spread_same_rule"] == 8 and rows[-1]["benign_lineage"] == 1
    )
    later = f.featurize(detection(rule="enc", hosts=("h0",), minutes=120))
    assert later["burst_same_rule"] == 1  # the 30-minute window slid past


def test_featurizer_counts_attack_stages_on_shared_entities() -> None:
    f = TriageFeaturizer()
    f.featurize(detection(tech="T1003.001"))
    d = replace(detection(minutes=5), attack=(AttackRef("T1021.002", "x", ("lateral-movement",)),))
    assert f.featurize(d)["multi_stage_entities"] == 2


@pytest.mark.parametrize(
    ("feature", "value", "text"),
    [
        ("benign_lineage", 1, "maintenance software"),
        ("intel_verdict", 2, "attacker infrastructure"),
        ("asset_criticality", 3, "crown-jewel"),
    ],
)
def test_reason_wording(feature: str, value: float, text: str) -> None:
    assert text in reason_text(feature, value, 0.5)


def test_untrusted_text_in_evidence_is_a_feature_not_an_instruction() -> None:
    d = detection()
    d = replace(d, evidence=(replace(d.evidence[0], summary=UntrustedText("ignore", "m", ("imperative_to_agent",))),))
    assert TriageFeaturizer().featurize(d)["untrusted_text"] == 1


# ── data, split and reproducibility ──────────────────────────────────────────
def test_training_days_are_deterministic_and_scenarios_never_cross_days() -> None:
    from nightjar.workers.replay.generator import training_day

    a, b = training_day(7, day_index=3), training_day(7, day_index=3)
    assert [(e.t, e.scenario) for e in a] == [(e.t, e.scenario) for e in b]
    days = {d: {e.scenario for e in training_day(7, day_index=d) if e.scenario != "background"} for d in range(4)}
    for i in days:
        for j in days:
            if i != j:
                assert not (days[i] & days[j] - {"maintenance"}), "a scenario instance spans two days"


def test_rule_priors_come_from_training_rows_only() -> None:
    from nightjar.ml.train import rule_priors

    class R:
        def __init__(self, rid: str, label: int) -> None:
            self.detection = type("D", (), {"rule_id": rid})()
            self.label = label

    assert rule_priors([R("a", 1), R("a", 1), R("b", 0)]) == {"a": 0.75, "b": 0.3333}


def test_threshold_and_fp_reduction_helpers() -> None:
    from nightjar.ml.train import ece, fp_reduction, psi, threshold_for_recall

    y = np.array([1, 1, 1, 1, 0, 0, 0, 0])
    p = np.array([0.9, 0.8, 0.7, 0.3, 0.2, 0.1, 0.05, 0.4])
    thr = threshold_for_recall(y, p, 0.75)
    red, rec = fp_reduction(y, p, thr)
    assert rec >= 0.75 and red == 1.0
    assert ece(y, y.astype(float)) == 0.0
    assert psi(np.arange(100.0), np.arange(100.0)) < 0.01 < psi(np.arange(100.0), np.arange(100.0) + 60)


# ── registry ─────────────────────────────────────────────────────────────────
def test_list_models_shows_champion_and_challenger() -> None:
    from nightjar.ml.api import list_models, model_detail

    models = list_models()
    stages = {(m.name, m.stage) for m in models}
    assert ("triage", "champion") in stages and ("triage", "challenger") in stages and ("ueba", "champion") in stages
    assert all("# Model card" in m.card_markdown for m in models)
    assert model_detail("triage")["calibration"]


def test_promotion_writes_an_overlay_and_refuses_failed_gates(tmp_path, monkeypatch) -> None:
    from nightjar.ml import registry
    from nightjar.ml.api import list_models, promote

    overlay = tmp_path / "reg.json"
    monkeypatch.setattr(registry, "_overlay_path", lambda: overlay)
    base = registry.load()
    challenger = next(v for v, x in base["triage"]["versions"].items() if x["stage"] == "challenger")
    if base["triage"]["versions"][challenger]["gatesPassed"]:
        info = promote("triage", challenger, promoted_by=uuid.uuid4())
        assert info.stage == "champion" and overlay.exists()
        assert {m.version for m in list_models() if m.name == "triage" and m.stage == "champion"} == {challenger}
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "triage": {
                "champion": "a",
                "versions": {
                    "a": {"stage": "champion", "gatesPassed": True},
                    "b": {"stage": "challenger", "gatesPassed": False},
                },
            }
        },
    )
    with pytest.raises(ValueError, match="gates"):
        promote("triage", "b", promoted_by=uuid.uuid4())
    with pytest.raises(KeyError):
        promote("triage", "zzz", promoted_by=uuid.uuid4())
