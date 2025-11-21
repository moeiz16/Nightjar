"""Behaviour anomaly detection (UEBA, ADR 0005): an Isolation Forest per entity type, rank-normalised against the
baseline period, plus a robust z-score per feature against the entity's own median and MAD (with a floor:
without it binary and sparse features have MAD 0 and every non-zero value looks infinitely unusual).

A window is reported when (a) its rank-normalised forest score is at least `threshold`, (b) some feature is at
least `z_min` robust deviations above the entity's normal, and (c) a novelty or risk feature is non-zero (new
country, hosting network, refused prompts, new shares or programs, data sent out). Reasons are the largest
robust deviations, worded plainly ("3,200 MB sent out; usually 4 MB").
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nightjar.contracts import Anomaly, Detection, Entities, Reason
from nightjar.ml.features import (
    FEATURE_TEXT,
    HOST_FEATURES,
    HOST_GATES,
    USER_FEATURES,
    USER_GATES,
    EntityProfile,
    Window,
    window_features,
)

NS_ANOMALY = uuid.UUID("2b8d7a3e-9c1f-4e55-8a0b-6c7d8e9f0a1b")
ABS_FLOOR = {"offHours": 0.25, "bytesOutMb": 5.0, "mailItemsRead": 5.0}
GATE_MIN = {"bytesOutMb": 100.0}  # a novelty/risk feature must reach this to open the gate (default: > 0)


def features_for(entity_type: str) -> tuple[str, ...]:
    return USER_FEATURES if entity_type == "user" else HOST_FEATURES


def gates_for(entity_type: str) -> tuple[str, ...]:
    return USER_GATES if entity_type == "user" else HOST_GATES


def gate_open(entity_type: str, feats: dict[str, float]) -> bool:
    return any(feats.get(g, 0.0) > GATE_MIN.get(g, 0.0) for g in gates_for(entity_type))


@dataclass
class Baseline:
    median: float
    mad: float  # floored scale used for z
    p95: float
    n: int


def robust_baseline(values: np.ndarray, feature: str) -> Baseline:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return Baseline(0.0, max(ABS_FLOOR.get(feature, 0.5), 0.5), 0.0, 0)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)) * 1.4826)
    floor = max(0.5 * float(v.std()), ABS_FLOOR.get(feature, 0.5))
    return Baseline(med, max(mad, floor), float(np.percentile(v, 95)), int(v.size))


@dataclass
class UebaModel:
    version: str
    forests: dict[str, Any]  # entity type → fitted IsolationForest
    score_dist: dict[str, list[float]]  # sorted raw scores of baseline windows (rank normalisation)
    baselines: dict[str, dict[str, dict[str, Baseline]]]  # type → entity → feature → baseline
    population: dict[str, dict[str, Baseline]]  # type → feature → baseline (cold-start fallback)
    profiles: dict[str, dict[str, EntityProfile]] = field(default_factory=dict)
    threshold: float = 0.95
    z_min: float = 3.0

    # ── scoring ────────────────────────────────────────────────────────────
    def vector(self, w: Window) -> tuple[dict[str, float], np.ndarray]:
        prof = self.profiles.get(w.entity_type, {}).get(w.entity_id)
        feats = window_features(w, prof)
        names = features_for(w.entity_type)
        return feats, np.array([[feats[n] for n in names]], dtype=float)

    def rank(self, entity_type: str, raw: float) -> float:
        dist = self.score_dist.get(entity_type) or [0.0]
        return float(np.searchsorted(np.asarray(dist), raw, side="right") / len(dist))

    def robust_z(self, entity_type: str, entity_id: str, feats: dict[str, float]) -> dict[str, float]:
        base = self.baselines.get(entity_type, {}).get(entity_id) or self.population.get(entity_type, {})
        out: dict[str, float] = {}
        for f, v in feats.items():
            b = base.get(f) or self.population.get(entity_type, {}).get(f)
            if b is None:
                continue
            out[f] = round((v - b.median) / b.mad, 3) if b.mad > 0 else 0.0
        return out

    def score_window(self, w: Window) -> tuple[float, float, dict[str, float], dict[str, float]]:
        feats, x = self.vector(w)
        forest = self.forests[w.entity_type]
        raw = float(-forest.score_samples(x)[0])
        return self.rank(w.entity_type, raw), raw, self.robust_z(w.entity_type, w.entity_id, feats), feats

    def evaluate_many(self, org_id: uuid.UUID, windows: list[Window]) -> list[tuple[Window, Anomaly]]:
        """Score many windows with one forest call per entity type (a single-row call costs ~5 ms)."""
        out: list[tuple[Window, Anomaly]] = []
        for et in ("user", "host"):
            group = [w for w in windows if w.entity_type == et]
            if not group:
                continue
            vecs = [self.vector(w) for w in group]
            # cheap pre-gate: only windows with a novelty/risk feature can be reported at all
            keep = [i for i, (f, _) in enumerate(vecs) if gate_open(et, f)]
            if not keep:
                continue
            x = np.vstack([vecs[i][1] for i in keep])
            raws = -self.forests[et].score_samples(x)
            for i, raw in zip(keep, raws, strict=True):
                w, (feats, _) = group[i], vecs[i]
                score = self.rank(et, float(raw))
                z = self.robust_z(et, w.entity_id, feats)
                if self.is_anomalous(et, score, z, feats):
                    out.append((w, self.anomaly(org_id, w, score, float(raw), z, feats)))
        return out

    def evaluate(self, org_id: uuid.UUID, w: Window) -> Anomaly | None:
        score, raw, z, feats = self.score_window(w)
        if not self.is_anomalous(w.entity_type, score, z, feats):
            return None
        return self.anomaly(org_id, w, score, raw, z, feats)

    def is_anomalous(self, entity_type: str, score: float, z: dict[str, float], feats: dict[str, float]) -> bool:
        if score < self.threshold:
            return False
        if max(z.values(), default=0.0) < self.z_min:
            return False
        return gate_open(entity_type, feats)

    def reasons(
        self, entity_type: str, entity_id: str, z: dict[str, float], feats: dict[str, float]
    ) -> tuple[Reason, ...]:
        base = self.baselines.get(entity_type, {}).get(entity_id) or self.population.get(entity_type, {})
        top = sorted(((f, s) for f, s in z.items() if s > 0), key=lambda kv: -kv[1])[:3]
        out = []
        for f, s in top:
            b = base.get(f) or self.population.get(entity_type, {}).get(f)
            med = b.median if b else 0.0
            out.append(
                Reason(
                    feature=f,
                    value=round(feats[f], 3),
                    contribution=round(s, 3),
                    text=_reason_text(f, feats[f], med),
                    baseline=round(med, 3),
                )
            )
        return tuple(out)

    def anomaly(
        self, org_id: uuid.UUID, w: Window, score: float, raw: float, z: dict[str, float], feats: dict[str, float]
    ) -> Anomaly:
        from datetime import timedelta

        aid = uuid.uuid5(NS_ANOMALY, f"{org_id}|{w.entity_type}|{w.entity_id}|{w.start.isoformat()}")
        return Anomaly(
            id=aid,
            org_id=org_id,
            entity_type=w.entity_type,  # type: ignore[arg-type]
            entity_id=w.entity_id,
            window_start=w.start,
            window_end=w.start + timedelta(minutes=5),
            score=round(score, 4),
            isolation_score=round(raw, 5),
            robust_z=z,
            features=feats,
            reasons=self.reasons(w.entity_type, w.entity_id, z, feats),
            model_version=self.version,
        )

    # ── persistence ────────────────────────────────────────────────────────
    def save(self, directory: Path) -> None:
        import joblib

        directory.mkdir(parents=True, exist_ok=True)
        for et, forest in self.forests.items():
            joblib.dump(forest, directory / f"ueba_{et}.joblib", compress=3)
        meta = {
            "version": self.version,
            "threshold": self.threshold,
            "zMin": self.z_min,
            "scoreDist": {k: [round(x, 6) for x in v] for k, v in self.score_dist.items()},
            "baselines": {
                et: {e: {f: b.__dict__ for f, b in fb.items()} for e, fb in ents.items()}
                for et, ents in self.baselines.items()
            },
            "population": {et: {f: b.__dict__ for f, b in fb.items()} for et, fb in self.population.items()},
            "profiles": {et: {e: p.to_doc() for e, p in ents.items()} for et, ents in self.profiles.items()},
        }
        (directory / "ueba_meta.json").write_text(json.dumps(meta, indent=0, sort_keys=True) + "\n")

    @classmethod
    def load(cls, directory: Path) -> UebaModel:
        import joblib

        meta = json.loads((directory / "ueba_meta.json").read_text())
        forests = {et: joblib.load(directory / f"ueba_{et}.joblib") for et in ("user", "host")}
        return cls(
            version=meta["version"],
            forests=forests,
            score_dist={k: list(v) for k, v in meta["scoreDist"].items()},
            baselines={
                et: {e: {f: Baseline(**b) for f, b in fb.items()} for e, fb in ents.items()}
                for et, ents in meta["baselines"].items()
            },
            population={et: {f: Baseline(**b) for f, b in fb.items()} for et, fb in meta["population"].items()},
            profiles={
                et: {e: EntityProfile.from_doc(p) for e, p in ents.items()} for et, ents in meta["profiles"].items()
            },
            threshold=float(meta["threshold"]),
            z_min=float(meta["zMin"]),
        )


def _fmt(v: float) -> str:
    if v >= 100:
        return f"{v:,.0f}"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.1f}"


def _reason_text(feature: str, value: float, median: float) -> str:
    label = FEATURE_TEXT.get(feature, feature)
    if feature == "offHours":
        return "Active at an hour this account is rarely active"
    if feature in ("newCountry", "hostingNetwork") and value:
        return label[0].upper() + label[1:]
    if value == 1:  # "1 program started", not "1 programs started"
        label = _SINGULAR.get(feature, label)
    return f"{_fmt(value)} {label}; usually {_fmt(median)}"


_SINGULAR = {
    "signIns": "sign-in",
    "mfaDenied": "refused sign-in prompt",
    "distinctSrcIps": "different source address",
    "processStarts": "program started",
    "newProcesses": "program never seen before",
    "distinctDestinations": "outside destination",
    "newShares": "file share never opened before",
    "mailItemsRead": "mailbox item read",
    "distinctUsers": "different account",
    "newUsers": "account never seen on this computer",
    "connectionsOut": "outbound connection",
    "newDestinations": "destination never contacted before",
    "serviceInstalls": "service installed",
}


# ─────────────────────────────────────────────────────────────────────────────
# Anomaly → detection on the stream (`source="ueba"`)
# ─────────────────────────────────────────────────────────────────────────────
_FEATURE_TECHNIQUE = {
    "newCountry": "T1078.004",
    "hostingNetwork": "T1078.004",
    "mfaDenied": "T1621",
    "bytesOutMb": "T1567.002",
    "distinctDestinations": "T1567.002",
    "newShares": "T1039",
    "mailItemsRead": "T1114.002",
    "newUsers": "T1078",
    "serviceInstalls": "T1543.003",
    "newDestinations": "T1048",
}


def anomaly_detection(anomaly: Anomaly, window: Window) -> Detection:
    """The `source="ueba"` detection for an anomaly (id derived from the anomaly id, so redelivery is safe)."""
    from nightjar.detect.engine import entities_of, snapshot
    from nightjar.intel.api import attack_technique

    top = [r.feature for r in anomaly.reasons] or ["behaviour"]
    techs = []
    for f in top:
        ref = attack_technique(_FEATURE_TECHNIQUE.get(f, "")) if f in _FEATURE_TECHNIQUE else None
        if ref and ref not in techs:
            techs.append(ref)
    ent = entities_of(window.events)
    users = (anomaly.entity_id,) if anomaly.entity_type == "user" else tuple(u for u in ent.users[:3])
    hosts = (anomaly.entity_id,) if anomaly.entity_type == "host" else tuple(ent.hosts[:3])
    from nightjar.ml.features import is_external_ip

    ent = Entities(
        hosts=hosts,
        users=users,
        ips=tuple(ip for ip in ent.ips if is_external_ip(ip))[:5],
        cloud_accounts=ent.cloud_accounts,
        access_keys=ent.access_keys,
        hashes=(),
        emails=(),
    )
    who = anomaly.entity_id
    title = f"Unusual behaviour by {who}" if anomaly.entity_type == "user" else f"Unusual activity on {who}"
    severity = "high" if anomaly.score >= 0.985 else "medium" if anomaly.score >= 0.95 else "low"
    return Detection(
        id=uuid.uuid5(NS_ANOMALY, f"detection|{anomaly.id}"),
        org_id=anomaly.org_id,
        source="ueba",
        rule_id=f"ueba:{anomaly.entity_type}:{top[0]}",
        rule_title=title,
        severity=severity,  # type: ignore[arg-type]
        confidence=float(anomaly.score),
        attack=tuple(techs),
        entities=ent,
        first_seen=anomaly.window_start,
        last_seen=anomaly.window_end,
        event_ids=tuple(e.event_id for e in window.events[:500]),
        evidence=tuple(snapshot(e) for e in window.events[:20]),
        summary=f"{title}: " + "; ".join(r.text for r in anomaly.reasons),
        fields={**{k: round(v, 3) for k, v in anomaly.features.items()}, "score": anomaly.score},
    )
