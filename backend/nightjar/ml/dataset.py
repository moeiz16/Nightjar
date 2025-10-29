"""Offline pipeline for training and evaluation: synthetic events → normaliser → Sigma detector → UEBA →
triage features, with ground truth joined from the generator (labels never ride on the events).

Mirrors the online stages (same normaliser, detector, UEBA model and featurizer classes) in a single process."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from nightjar.contracts import Detection, NormalizedEvent, RawEvent
from nightjar.detect.engine import Detector
from nightjar.ingest.normalizer import normalize
from nightjar.ml.features import Inventory, TriageFeaturizer, Window, windows_of
from nightjar.ml.ueba import UebaModel, anomaly_detection
from nightjar.workers.inmemory_platform import HALDEN_ORG, halden_assets, halden_identities
from nightjar.workers.replay.generator import REFERENCE_NIGHT, Ev, stamp


def halden_inventory() -> Inventory:
    return Inventory(
        assets={a.name.lower(): a for a in halden_assets()},
        identities={i.username.lower(): i for i in halden_identities()},
    )


@dataclass
class Normalised:
    events: list[NormalizedEvent] = field(default_factory=list)
    malicious: set[str] = field(default_factory=set)
    scenario_of: dict[str, str] = field(default_factory=dict)
    technique_of: dict[str, str] = field(default_factory=dict)
    source: list[Ev] = field(default_factory=list)


def normalise_all(evs: Sequence[Ev], *, org_id: uuid.UUID = HALDEN_ORG, anchor: datetime | None = None) -> Normalised:
    anchor = (anchor or REFERENCE_NIGHT).astimezone(UTC)
    out = Normalised()
    for e in evs:
        when = anchor + timedelta(seconds=e.t)
        n = normalize(RawEvent(org_id, "replay", when, stamp(e.fmt, e.payload, when), e.fmt, "synthetic"))  # type: ignore[arg-type]
        out.events.append(n)
        out.source.append(e)
        out.scenario_of[n.event_id] = e.scenario
        if e.malicious:
            out.malicious.add(n.event_id)
            if e.technique:
                out.technique_of[n.event_id] = e.technique
    return out


@dataclass
class Row:
    detection: Detection
    features: dict[str, float]
    label: int
    scenario: str
    group: int


def detect_all(norm: Normalised, detector: Detector, ueba: UebaModel | None) -> list[tuple[Detection, Window | None]]:
    dets: list[tuple[Detection, Window | None]] = []
    for e in norm.events:
        for d in detector.detect(e):
            dets.append((d, None))
    if ueba is not None:
        for w, a in ueba.evaluate_many(norm.events[0].org_id, windows_of(norm.events)):
            dets.append((anomaly_detection(a, w), w))
    dets.sort(key=lambda x: (x[0].first_seen, str(x[0].id)))
    return dets


def label_of(d: Detection, norm: Normalised) -> tuple[int, str]:
    hits = [i for i in d.event_ids if i in norm.malicious]
    scen = next((norm.scenario_of[i] for i in d.event_ids if i in norm.scenario_of), "background")
    if hits:
        return 1, norm.scenario_of.get(hits[0], scen)
    return 0, scen


def rows_for(
    norm: Normalised, dets: Iterable[tuple[Detection, Window | None]], featurizer: TriageFeaturizer, group: int
) -> list[Row]:
    rows = []
    for d, _ in dets:
        y, scen = label_of(d, norm)
        rows.append(Row(d, featurizer.featurize(d), y, scen, group))
    return rows
