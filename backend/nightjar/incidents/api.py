"""Public interface of the incident store (builder A). Called by B's pipeline workers and agents.

All functions open their own unit of work (`session_scope()`), commit, append audit rows where stated and
emit the SSE events listed in API contract §1.17. They are idempotent where noted so NATS redelivery is safe.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from nightjar.contracts import (
    Anomaly,
    CorrelationUpdate,
    Detection,
    IncidentContext,
    IncidentRef,
    OpenIncidentIndexEntry,
    TriageScore,
)


async def record_detections(org_id: uuid.UUID, detections: Sequence[Detection]) -> None:
    """Persist detections with their evidence snapshots and triage scores. Idempotent on `Detection.id`.
    Emits `detection.created` (permission `detections:read`)."""
    raise NotImplementedError


async def record_triage_scores(org_id: uuid.UUID, scores: Sequence[TriageScore]) -> None:
    """Attach (or replace) the triage classifier's score on existing detections. Idempotent."""
    raise NotImplementedError


async def record_anomalies(org_id: uuid.UUID, anomalies: Sequence[Anomaly]) -> None:
    """Persist behaviour anomalies (the UEBA view). Idempotent on `Anomaly.id`. Emits `anomaly.created`."""
    raise NotImplementedError


async def upsert_incident(update: CorrelationUpdate) -> IncidentRef:
    """Create or extend the incident for `update.correlation_key` (idempotent). Recomputes severity (max of
    detections, raised one level when a crown-jewel asset is involved), ATT&CK, entities and the timeline.
    New incident: status `new`, stage `triage`, audit `incident.opened`, SSE `incident.created`.
    Existing: SSE `incident.updated`. `merge_incident_ids` are closed as merged into this one."""
    raise NotImplementedError


async def open_incident_index(org_id: uuid.UUID, *, since: datetime) -> list[OpenIncidentIndexEntry]:
    """Open incidents with activity since `since` (correlator warm start after a restart)."""
    raise NotImplementedError


async def get_incident_context(org_id: uuid.UUID, incident_id: uuid.UUID) -> IncidentContext:
    """Everything an agent may read for an investigation (from SQLite only). Raises KeyError if the incident
    is not in this org (agents never see other tenants)."""
    raise NotImplementedError
