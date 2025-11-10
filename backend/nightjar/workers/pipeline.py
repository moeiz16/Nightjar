"""The pipeline stages (API contract Part 4, durable consumers) as message handlers, run by
`python -m nightjar.workers run` (or in-process by the seed and the eval).

normalizer → detector + ueba → scorer → correlator → dispatcher → orchestrator → (triage ⇄ enrichment ⇄ response)
responder (approved actions) · jobs (replays) · DLQ mover (max-deliveries advisory)

Every handler is idempotent (ids are content hashes or uuid5s, upserts by correlation key, approvals by nonce), so
redelivery after a crash or a nak is safe. Errors are classified by `stream.runtime.handle_one`: permanent ones
(cannot normalise, invalid schema) are dead-lettered at once, transient ones are retried with the consumer's
back-off and dead-lettered after `max_deliver`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import structlog

from nightjar import clock
from nightjar.contracts import Anomaly, CorrelationUpdate, Detection, IncidentRef, NormalizedEvent, RawEvent
from nightjar.core.metrics import ALERT_TO_TRIAGE, DETECTIONS
from nightjar.correlate.correlator import Correlator
from nightjar.detect.engine import Detector
from nightjar.ml.features import Inventory, TriageFeaturizer, Window, entity_keys, window_start
from nightjar.stream import codec
from nightjar.stream.bus import Bus, Msg
from nightjar.stream.runtime import PermanentError, TransientError, handle_one, note_events, run_consumer
from nightjar.stream.topology import (
    CONTROL_RULES,
    UEBA_DATASETS,
    detections_raw_subject,
    detections_scored_subject,
    events_subject,
    incidents_subject,
)

log = structlog.get_logger("nightjar.workers")
UEBA_GRACE = timedelta(minutes=2)


def _hdr(msg: Msg, **extra: str) -> dict[str, str]:
    keep = {k: v for k, v in msg.headers.items() if k in ("Nj-Org", "Nj-Trace", "Nj-Received-At")}
    return {**keep, **extra}


class Pipeline:
    """All stage handlers for one process. State that must survive restarts lives in the platform (incidents,
    open-incident index) or the `pl_*` tables; the rest (UEBA windows, burst counters) is rebuilt from the stream."""

    def __init__(self, bus: Bus, *, auto_investigate: bool | None = None) -> None:
        self.bus = bus
        self.detector = Detector()
        self.correlator = Correlator(lookup=lambda org: self._inventory.get(org, (0.0, None))[1])
        self._featurizers: dict[uuid.UUID, TriageFeaturizer] = {}
        self._inventory: dict[uuid.UUID, tuple[float, Inventory]] = {}
        self._windows: dict[tuple[uuid.UUID, str, str], dict[datetime, Window]] = defaultdict(dict)
        self._watermark: dict[uuid.UUID, datetime] = {}
        self._restored: set[uuid.UUID] = set()
        self.auto_investigate = auto_investigate
        self.created: list[IncidentRef] = []
        self.runs: list[uuid.UUID] = []
        self.first_detection_at: dict[str, float] = {}  # correlation key → monotonic time of first detection
        self.triage_latency: list[float] = []
        self.counts: dict[str, int] = defaultdict(int)

    # ── normalizer ─────────────────────────────────────────────────────────
    async def on_raw(self, msg: Msg) -> None:
        raw = codec.decode(RawEvent, msg.data)
        try:
            event = self.normalize(raw)
        except ValueError as exc:
            raise PermanentError(str(exc)) from exc
        self.counts["normalized"] += 1
        note_events(1)
        await self.bus.publish(
            events_subject(event.org_id, event.dataset),
            codec.dumps(event),
            msg_id=f"ev-{event.event_id}",
            headers=_hdr(msg, **{"Nj-Schema": "normalized_event.v1"}),
        )

    @staticmethod
    def normalize(raw: RawEvent) -> NormalizedEvent:
        from nightjar.stream.api import normalize

        return normalize(raw)

    # ── detector ───────────────────────────────────────────────────────────
    async def on_event(self, msg: Msg) -> None:
        event = codec.decode(NormalizedEvent, msg.data)
        for d in self.detector.detect(event):
            await self._publish_detection(d, msg)

    async def _publish_detection(self, d: Detection, msg: Msg | None) -> None:
        hdrs = _hdr(msg) if msg else {"Nj-Org": str(d.org_id)}
        hdrs.setdefault("Nj-Detected-At", str(time.monotonic()))
        await self.bus.publish(
            detections_raw_subject(d.org_id, d.source),
            codec.dumps(d),
            msg_id=f"det-{d.id}",
            headers={**hdrs, "Nj-Schema": "detection.v1"},
        )

    # ── UEBA ───────────────────────────────────────────────────────────────
    async def on_ueba_event(self, msg: Msg) -> None:
        subject_dataset = msg.subject.split(".")[-1]
        if subject_dataset not in UEBA_DATASETS:
            return
        from nightjar.ml.runtime import ueba_model

        if ueba_model() is None:
            return
        event = codec.decode(NormalizedEvent, msg.data)
        for et, eid in entity_keys(event):
            ws = window_start(event.timestamp)
            bucket = self._windows[(event.org_id, et, eid)]
            w = bucket.get(ws)
            if w is None:
                w = bucket[ws] = Window(et, eid, ws)
            w.events.append(event)
        mark = self._watermark.get(event.org_id)
        if mark is None or event.timestamp > mark:
            self._watermark[event.org_id] = event.timestamp
            if mark is None or window_start(event.timestamp) != window_start(mark):
                await self.flush_ueba(event.org_id, before=event.timestamp - UEBA_GRACE)

    async def flush_ueba(self, org_id: uuid.UUID | None = None, *, before: datetime | None = None) -> int:
        """Score and drop windows that ended before `before` (all windows when None)."""
        from nightjar.incidents import api as incidents
        from nightjar.ml.runtime import ueba_model
        from nightjar.ml.ueba import anomaly_detection

        model = ueba_model()
        if model is None:
            return 0
        found = 0
        closed: dict[uuid.UUID, list[Window]] = defaultdict(list)
        for key in list(self._windows):
            org, _, _ = key
            if org_id is not None and org != org_id:
                continue
            bucket = self._windows[key]
            for ws in sorted(bucket):
                if before is not None and ws + timedelta(minutes=5) > before:
                    continue
                closed[org].append(bucket.pop(ws))
            if not bucket:
                self._windows.pop(key, None)
        for org, windows in closed.items():
            for w, anomaly in model.evaluate_many(org, windows):
                det = anomaly_detection(anomaly, w)
                await incidents.record_anomalies(org, [replace(anomaly, detection_id=det.id)])
                await self._publish_detection(det, None)
                found += 1
        self.counts["anomalies"] += found
        return found

    # ── scorer ─────────────────────────────────────────────────────────────
    async def _inventory_for(self, org_id: uuid.UUID) -> Inventory:
        hit = self._inventory.get(org_id)
        if hit and time.monotonic() - hit[0] < 300:
            return hit[1]
        from nightjar.directory import api as directory

        inv = Inventory()
        try:
            for a in await directory.search_assets(org_id, "", limit=1000):
                inv.assets[a.name.lower()] = a
            for i in await directory.search_identities(org_id, "", limit=1000):
                inv.identities[i.username.lower()] = i
        except NotImplementedError:
            pass
        except Exception as exc:
            raise TransientError(f"directory unavailable: {exc}") from exc
        self._inventory[org_id] = (time.monotonic(), inv)
        return inv

    def featurizer(self, org_id: uuid.UUID) -> TriageFeaturizer:
        f = self._featurizers.get(org_id)
        if f is None:
            from nightjar.intel.api import kev_lookup
            from nightjar.ml.triage import champion

            model = champion()
            f = TriageFeaturizer(
                lambda: self._inventory.get(org_id, (0.0, Inventory()))[1],
                rule_priors=model.rule_priors if model else {},
                kev_lookup=kev_lookup,
            )
            self._featurizers[org_id] = f
        return f

    async def on_detection(self, msg: Msg) -> None:
        from nightjar.incidents import api as incidents
        from nightjar.ml.triage import champion

        d = codec.decode(Detection, msg.data)
        await self._inventory_for(d.org_id)
        row = self.featurizer(d.org_id).featurize(d)
        model = champion()
        if model is not None:
            d = replace(d, triage=model.score(d, row))
        await incidents.record_detections(d.org_id, [d])
        DETECTIONS.labels(source=d.source, severity=d.severity).inc()
        self.counts["detections"] += 1
        await self.bus.publish(
            detections_scored_subject(d.org_id),
            codec.dumps(d),
            msg_id=f"scored-{d.id}",
            headers={
                **_hdr(msg),
                "Nj-Schema": "detection.v1",
                "Nj-Detected-At": msg.headers.get("Nj-Detected-At", str(time.monotonic())),
            },
        )

    # ── correlator ─────────────────────────────────────────────────────────
    async def _restore(self, org_id: uuid.UUID) -> None:
        if org_id in self._restored:
            return
        from nightjar.incidents import api as incidents

        with contextlib.suppress(NotImplementedError):
            entries = await incidents.open_incident_index(org_id, since=clock.now() - timedelta(hours=12))
            self.correlator.restore(org_id, entries)
        self._restored.add(org_id)

    async def on_scored(self, msg: Msg) -> None:
        from nightjar.incidents import api as incidents

        d = codec.decode(Detection, msg.data)
        await self._restore(d.org_id)
        await self._inventory_for(d.org_id)
        update = self.correlator.add(d)
        if update is None:
            return
        detected_at = float(msg.headers.get("Nj-Detected-At") or time.monotonic())
        ref = await incidents.upsert_incident(update)
        self.correlator.note_incident(d.org_id, update.correlation_key, ref.id)
        self.first_detection_at.setdefault(str(ref.id), detected_at)
        await self._save_state(update, ref)
        what = "opened" if ref.created else "updated"
        if ref.created:
            self.created.append(ref)
        body = {
            "incidentId": str(ref.id),
            "reference": ref.reference,
            "orgId": str(d.org_id),
            "created": ref.created,
            "kind": update.kind,
            "allLikelyNoise": update.all_likely_noise,
        }
        await self.bus.publish(
            incidents_subject(d.org_id, what),  # type: ignore[arg-type]
            codec.dumps(body),
            msg_id=f"inc-{ref.id}-{what}-{len(update.detection_ids) if what == 'updated' else 0}",
            headers={**_hdr(msg), "Nj-Schema": "correlation_update.v1", "Nj-Detected-At": str(detected_at)},
        )

    async def _save_state(self, update: CorrelationUpdate, ref: IncidentRef) -> None:
        try:
            from sqlalchemy import select

            from nightjar.db.models.pipeline import CorrelationState
            from nightjar.db.session import session_scope

            async with session_scope() as db:
                row = (
                    await db.execute(
                        select(CorrelationState).where(
                            CorrelationState.org_id == update.org_id,
                            CorrelationState.correlation_key == update.correlation_key,
                        )
                    )
                ).scalar_one_or_none()
                ents = codec.loads(codec.dumps(update.entities))
                if row is None:
                    db.add(
                        CorrelationState(
                            org_id=update.org_id,
                            correlation_key=update.correlation_key,
                            incident_id=ref.id,
                            entities=ents,
                            state={"detections": len(update.detection_ids)},
                            last_seen=update.last_seen,
                        )
                    )
                else:
                    row.entities, row.last_seen, row.incident_id = ents, update.last_seen, ref.id
                    row.state = {"detections": len(update.detection_ids)}
        except Exception as exc:
            log.debug("correlation_state_skipped", error=str(exc)[:120])

    # ── dispatcher ─────────────────────────────────────────────────────────
    async def _auto(self, org_id: uuid.UUID) -> bool:
        if self.auto_investigate is not None:
            return self.auto_investigate
        try:
            from sqlalchemy import select

            from nightjar.db.models.platform import Organization
            from nightjar.db.session import session_scope

            async with session_scope() as db:
                settings = (
                    await db.execute(select(Organization.settings).where(Organization.id == org_id))
                ).scalar_one_or_none()
            return bool((settings or {}).get("autoInvestigate", True))
        except Exception:
            return True

    async def on_incident_opened(self, msg: Msg) -> None:
        from nightjar.agents import api as agents

        body = codec.loads(msg.data)
        org_id = uuid.UUID(body["orgId"])
        if not await self._auto(org_id):
            return
        run_id = await agents.start_investigation(
            org_id, uuid.UUID(body["incidentId"]), trigger="auto", requested_by=None
        )
        self.runs.append(run_id)

    # ── orchestrator / responder ───────────────────────────────────────────
    async def on_agent_task(self, msg: Msg) -> None:
        from nightjar.agents.service import run_triage_task

        task = codec.loads(msg.data)
        started = time.monotonic()
        await run_triage_task(task)
        detected = self.first_detection_at.get(task["incidentId"])
        if detected is not None:
            seconds = max(0.0, time.monotonic() - detected)
            self.triage_latency.append(seconds)
            ALERT_TO_TRIAGE.observe(seconds)
        self.counts["investigations"] += 1
        log.info("investigation_done", run=task["runId"], seconds=round(time.monotonic() - started, 2))

    async def on_approved_action(self, msg: Msg) -> None:
        from nightjar.agents.a2a import call_agent

        payload = codec.loads(msg.data)
        res = await call_agent("orchestrator", "response", {**payload, "skill": "execute_action"})
        if res["state"] != "TASK_STATE_COMPLETED":
            raise TransientError(f"response agent: {res['state']}")

    # ── jobs ───────────────────────────────────────────────────────────────
    async def on_job(self, msg: Msg) -> None:
        from nightjar.workers.jobs import run_job

        await run_job(codec.loads(msg.data), self)

    # ── control ────────────────────────────────────────────────────────────
    async def reload_rules(self, org_id: uuid.UUID | None = None) -> None:
        from nightjar.detect.api import disabled_rules

        orgs = [org_id] if org_id else list(self.detector._orgs)
        for org in orgs:
            self.detector.set_disabled(org, await disabled_rules(org))


HANDLERS = {
    "normalizer": "on_raw",
    "detector": "on_event",
    "ueba": "on_ueba_event",
    "scorer": "on_detection",
    "correlator": "on_scored",
    "dispatcher": "on_incident_opened",
    "orchestrator": "on_agent_task",
    "responder": "on_approved_action",
    "jobs": "on_job",
}
CONCURRENCY = {"orchestrator": 4, "responder": 4}


async def run_workers(pipeline: Pipeline, *, stop: asyncio.Event, consumers: list[str] | None = None) -> None:
    """Run the given stages until `stop` is set (all stages except the API's store-writer by default)."""
    names = consumers or list(HANDLERS)
    bus = pipeline.bus
    activate = getattr(bus, "activate", None)
    if activate:
        activate(*names)

    async def on_rules(_: bytes) -> None:
        await pipeline.reload_rules()

    with contextlib.suppress(Exception):
        await bus.subscribe_core(CONTROL_RULES, on_rules)
    tasks = [
        asyncio.create_task(
            run_consumer(
                n,
                getattr(pipeline, HANDLERS[n]),
                stop=stop,
                bus=bus,
                batch=200 if n in ("normalizer", "detector", "ueba") else 20,
                fetch_timeout=0.5,
                concurrency=CONCURRENCY.get(n, 1),
            ),
            name=f"consumer-{n}",
        )
        for n in names
    ]

    async def ueba_tick() -> None:
        while not stop.is_set():
            await asyncio.sleep(30)
            for org, mark in list(pipeline._watermark.items()):
                await pipeline.flush_ueba(org, before=mark - UEBA_GRACE)

    tasks.append(asyncio.create_task(ueba_tick(), name="ueba-tick"))
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def drain_one(pipeline: Pipeline, consumer: str, *, batch: int = 500) -> int:
    """Process everything currently pending for one consumer (in-process runs and tests)."""
    n = 0
    handler = getattr(pipeline, HANDLERS[consumer])
    while True:
        msgs = await pipeline.bus.fetch(consumer, batch, 0.01)
        if not msgs:
            return n
        for m in msgs:
            await handle_one(pipeline.bus, consumer, m, handler)
            n += 1


def anomalies_to_doc(items: list[Anomaly]) -> list[dict[str, Any]]:
    return [codec.loads(codec.dumps(a)) for a in items]
