"""Hunting around the store: query documents, saved searches, plain-language suggestions, the event river and
the `store-writer` consumer that the API runs."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import NormalizedEvent
from nightjar.core.events import emit
from nightjar.db.models.platform import Organization, User
from nightjar.db.models.soc import AnomalyRow, Asset, DetectionRow, Identity, Incident, SavedSearch
from nightjar.db.session import session_scope
from nightjar.hunting import api as store_api
from nightjar.hunting.store import FIELDS, OPS
from nightjar.schemas.base import CamelIn

log = structlog.get_logger(__name__)


class FilterIn(CamelIn):
    field: str = Field(max_length=60)
    op: str = Field(max_length=10)
    value: Any


class HuntQueryIn(CamelIn):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    metric: str = "count"
    bucket: str | None = None
    limit: int = Field(default=200, ge=1, le=5000)
    relative_hours: int | None = Field(default=None, ge=1, le=24 * 90)  # window ending now when from/to are absent


def query_from_doc(org_id: uuid.UUID, doc: HuntQueryIn, *, default_hours: int = 24) -> store_api.HuntQuery:
    now = clock.now()
    to = doc.to or now
    frm = doc.from_ or (to - timedelta(hours=doc.relative_hours or default_hours))
    if frm.tzinfo is None or to.tzinfo is None:
        raise store_api.InvalidQueryError("Include a time zone in the time range.", "from")
    return store_api.HuntQuery(
        org_id=org_id,
        time_from=frm,
        time_to=to,
        filters=tuple((f.field, f.op, f.value) for f in doc.filters),
        group_by=tuple(doc.group_by),
        metric=doc.metric,
        bucket=doc.bucket,
        limit=doc.limit,
    )


def query_doc(
    *,
    filters: list[tuple[str, str, Any]],
    hours: int = 24 * 7,
    group_by: Sequence[str] = (),
    metric: str = "count",
    bucket: str | None = None,
    frm: datetime | None = None,
    to: datetime | None = None,
) -> dict:
    """A HuntQueryDoc (wire form) for saved searches and suggestions; time range relative to now unless given."""
    return {
        "from": frm.isoformat() if frm else None,
        "to": to.isoformat() if to else None,
        "relativeHours": None if frm else hours,
        "filters": [{"field": f, "op": o, "value": v} for f, o, v in filters],
        "groupBy": list(group_by),
        "metric": metric,
        "bucket": bucket,
        "limit": 200,
    }


def stored_query_in(doc: dict, *, frm: datetime | None = None, to: datetime | None = None) -> HuntQueryIn:
    """Saved queries usually keep a relative window (`relativeHours`) so they stay useful as days pass; a run may
    override the window."""
    data = dict(doc)
    if frm or to:
        data["from"], data["to"] = (frm.isoformat() if frm else None), (to.isoformat() if to else None)
    return HuntQueryIn.model_validate(data)


def result_doc(r: store_api.HuntResult) -> dict:
    return {
        "columns": list(r.columns),
        "rows": [list(row) for row in r.rows],
        "totalMatched": r.total_matched,
        "elapsedMs": r.elapsed_ms,
        "truncated": r.truncated,
    }


def fields_doc() -> list[dict]:
    return [
        {"field": name, "label": spec.label, "type": spec.type, "ops": [o for o in OPS if o in spec.ops]}
        for name, spec in FIELDS.items()
    ]


ALERT_MINUTES = 15


def saved_doc(s: SavedSearch, user: User | None) -> dict:
    from nightjar.services.auth import user_out

    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "query": s.query,
        "createdBy": user_out(user) if user else None,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "lastRunAt": s.last_run_at.isoformat() if s.last_run_at else None,
        "lastCount": s.last_count,
        "alert": bool(s.alert),
        "schedule": f"every {ALERT_MINUTES} min" if s.alert else "on demand",
    }


# ── The event river (plain-language lines; never copies untrusted message text) ──
def river_text(r: dict[str, Any]) -> str:
    user, host, src = r.get("user_name"), r.get("host_name"), r.get("source_ip")
    action = (r.get("action") or "event").lower()
    outcome = r.get("outcome")
    category = r.get("category") or ""
    geo = f" in {r['source_geo']}" if r.get("source_geo") else ""
    frm = f" from {src}{geo}" if src else ""
    on = f" on {host}" if host else ""
    if "authentication" in category or "logon" in action or "sign" in action or "login" in action:
        if "mfa" in action and ("denied" in action or outcome == "failure"):
            return f"{user or 'Someone'} refused a sign-in prompt{frm}"
        if "mfa" in action:
            return f"{user or 'Someone'} approved a sign-in prompt{frm}"
        if outcome == "failure" or "fail" in action:
            return f"Sign-in failed for {user or 'an unknown account'}{frm}"
        return f"{user or 'Someone'} signed in{frm}{on}"
    if r.get("cloud_api"):
        key = f" with key {r['cloud_access_key_id']}" if r.get("cloud_access_key_id") else ""
        verb = "was refused" if outcome == "failure" else "ran"
        return f"Cloud: {user or 'an account'} {verb} {r['cloud_api']}{key}{frm}"
    if "process" in category or "process" in action:
        prog = r.get("process_name") or "a program"
        return f"{prog} started{on}" + (f" as {user}" if user else "")
    if "network" in category:
        dst = f" to {r['destination_ip']}" if r.get("destination_ip") else ""
        return f"Network connection{on or frm}{dst}"
    words = action.replace("-", " ").replace("_", " ")
    who = f" by {user}" if user else ""
    return f"{words[:1].upper()}{words[1:]}{who}{on}"


def _severity(n: int | None) -> str | None:
    if n is None:
        return None
    return "critical" if n >= 90 else "high" if n >= 70 else "medium" if n >= 40 else "low" if n >= 20 else "info"


async def _event_links(db: AsyncSession, org_id: uuid.UUID, rows: list[dict]) -> tuple[dict[str, str], set[str]]:
    """event id → incident id, and the event ids behind behaviour anomalies (for the river's markers)."""
    if not rows:
        return {}, set()
    lo = min(r["ts"] for r in rows) - timedelta(minutes=30)
    hi = max(r["ts"] for r in rows) + timedelta(minutes=30)
    dets = (
        await db.execute(
            select(DetectionRow.event_ids, DetectionRow.incident_id, DetectionRow.source).where(
                DetectionRow.org_id == org_id, DetectionRow.last_seen >= lo, DetectionRow.first_seen <= hi
            )
        )
    ).all()
    wanted = {r["event_id"] for r in rows}
    incident: dict[str, str] = {}
    anomalous: set[str] = set()
    for event_ids, inc_id, source in dets:
        for eid in set(event_ids or ()) & wanted:
            if inc_id:
                incident[eid] = str(inc_id)
            if source == "ueba":
                anomalous.add(eid)
    return incident, anomalous


async def river_lines(db: AsyncSession, org_id: uuid.UUID, rows: list[dict]) -> list[dict]:
    incident, anomalous = await _event_links(db, org_id, rows)
    return [
        {
            "eventId": r["event_id"],
            "at": r["ts"].isoformat() if isinstance(r["ts"], datetime) else r["ts"],
            "text": river_text(r),
            "dataset": r.get("dataset"),
            "severity": _severity(r.get("severity")),
            "anomaly": r["event_id"] in anomalous,
            "incidentId": incident.get(r["event_id"]),
        }
        for r in rows
    ]


async def river(db: AsyncSession, org_id: uuid.UUID, *, after: str | None, limit: int) -> dict:
    pos = await store_api.event_position(org_id, after) if after else None
    rows = await store_api.recent_rows(org_id, after=pos, limit=limit)
    epm = await store_api.events_per_minute(org_id, clock.now() - timedelta(minutes=5), 5)
    minutes: list[dict] = []
    if rows:  # totals for every minute the lines cover (the lines are a sample; the totals are exact)
        lo = min(r["ts"] for r in rows).replace(second=0, microsecond=0)
        hi = max(r["ts"] for r in rows).replace(second=0, microsecond=0) + timedelta(minutes=1)
        minutes = [{"minute": m.isoformat(), "count": n} for m, n in await store_api.minute_totals(org_id, lo, hi)]
    return {"lines": await river_lines(db, org_id, rows), "eventsPerMinute": epm, "minutes": minutes}


# ── store-writer: the API's durable consumer on normalised events ────────────
_last_river: dict[uuid.UUID, float] = {}
RIVER_EVERY_S = 2.0


_held: dict[uuid.UUID, list[NormalizedEvent]] = {}
_held_count: dict[uuid.UUID, int] = {}


async def handle_normalized(events: Sequence[NormalizedEvent]) -> None:
    """Append a batch to DuckDB and announce up to 20 river lines per org (at most every 2 s per org). Events that
    arrive between announcements are held, so each `river.batch` counts everything since the previous one."""
    from nightjar.stream.runtime import note_events

    await store_api.append_events(events)
    note_events(len(events))  # the API's own intake rate, for pipeline health on the watch
    by_org: dict[uuid.UUID, list[NormalizedEvent]] = {}
    for e in events:
        by_org.setdefault(e.org_id, []).append(e)
    loop = asyncio.get_running_loop()
    for org_id, fresh in by_org.items():
        held = _held.setdefault(org_id, [])
        held.extend(fresh)
        if len(held) > 2000:  # keep the newest; the count below still covers every event
            del held[: len(held) - 2000]
        _held_count[org_id] = _held_count.get(org_id, 0) + len(fresh)
        if loop.time() - _last_river.get(org_id, -1e9) < RIVER_EVERY_S:
            continue
        _last_river[org_id] = loop.time()
        evs, count = _held.pop(org_id), _held_count.pop(org_id, 0)
        newest = sorted(evs, key=lambda e: e.timestamp, reverse=True)[:20]
        rows = [
            {
                "event_id": e.event_id,
                "ts": e.timestamp,
                "dataset": e.dataset,
                "action": e.action,
                "outcome": e.outcome,
                "category": "|" + "|".join(e.category) + "|",
                "severity": e.severity,
                "host_name": e.host.name if e.host else None,
                "user_name": e.user.name if e.user else None,
                "source_ip": e.source.ip if e.source else None,
                "source_geo": e.source.geo_country if e.source else None,
                "destination_ip": e.destination.ip if e.destination else None,
                "process_name": e.process.name if e.process else None,
                "cloud_api": e.cloud.api if e.cloud else None,
                "cloud_access_key_id": e.cloud.access_key_id if e.cloud else None,
            }
            for e in newest
        ]
        async with session_scope() as db:
            epm = await store_api.events_per_minute(org_id, clock.now() - timedelta(minutes=5), 5)
            await emit(
                db,
                org_id,
                "river.batch",
                {"count": count, "lines": await river_lines(db, org_id, rows), "eventsPerMinute": epm},
                permission="watch:read",
            )


# ── Suggestions ──────────────────────────────────────────────────────────────
TECHNIQUE_HUNTS: dict[str, tuple[str, list[tuple[str, str, Any]], list[str]]] = {
    "T1621": (
        "Look for other people who approved a sign-in prompt after several refusals",
        [("action", "in", ["mfa-push-denied", "mfa-push-approved"])],
        ["user.name", "action"],
    ),
    "T1110.003": (
        "Look for other addresses guessing passwords across many accounts",
        [("outcome", "eq", "failure"), ("category", "eq", "authentication")],
        ["source.ip"],
    ),
    "T1003.001": (
        "Look for other computers where a program read sign-in secrets from memory",
        [("process.commandLine", "contains", "minidump")],
        ["host.name"],
    ),
    "T1021.002": (
        "Look for remote program launches between computers",
        [("process.name", "in", ["psexec.exe", "psexec64.exe", "psexesvc.exe"])],
        ["host.name", "user.name"],
    ),
    "T1569.002": (
        "Look for services installed remotely",
        [("action", "eq", "service-installed")],
        ["host.name"],
    ),
    "T1053.005": ("Look for new scheduled tasks", [("action", "eq", "scheduled-task-created")], ["host.name"]),
    "T1218.011": (
        "Look for signed Windows programs used to run code",
        [("process.name", "eq", "rundll32.exe")],
        ["host.name", "process.parentName"],
    ),
    "T1490": (
        "Look for attempts to delete backup copies on other computers",
        [("process.name", "eq", "vssadmin.exe")],
        ["host.name"],
    ),
    "T1114.003": (
        "Look for other mailboxes forwarding email outside the company",
        [("action", "eq", "inbox-rule-created")],
        ["user.name"],
    ),
    "T1078.004": (
        "Look for other cloud keys or accounts used from unfamiliar networks",
        [("dataset", "in", ["aws.cloudtrail", "okta.system", "m365.audit"]), ("outcome", "eq", "success")],
        ["user.name", "source.asOrg"],
    ),
    "T1530": (
        "Look for unusually many reads from cloud storage",
        [("action", "eq", "s3-get-object")],
        ["user.name", "cloud.accessKeyId"],
    ),
    "T1567.002": (
        "Look for uploads to personal cloud storage",
        [("process.name", "eq", "rclone.exe")],
        ["user.name", "host.name"],
    ),
    "T1087.002": (
        "Look for directory look-ups from unusual computers",
        [("process.name", "in", ["net.exe", "nltest.exe", "adfind.exe"])],
        ["host.name"],
    ),
}


def _sid(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


async def suggestions(db: AsyncSession, org_id: uuid.UUID, *, limit: int = 8) -> list[dict]:
    from nightjar.services.pipeline import kev_docs

    org = await db.get(Organization, org_id)
    tz = ZoneInfo(org.timezone if org and org.timezone else "UTC")
    since = clock.now() - timedelta(days=7)
    incidents = list(
        (
            await db.execute(
                select(Incident)
                .where(Incident.org_id == org_id, Incident.last_seen >= since, Incident.merged_into_id.is_(None))
                .order_by(Incident.last_seen.desc())
            )
        ).scalars()
    )
    idents = {i.username: i for i in (await db.execute(select(Identity).where(Identity.org_id == org_id))).scalars()}
    out: list[dict] = []
    seen: set[str] = set()
    for inc in incidents:
        if inc.verdict in ("benign", "false_positive") or inc.likely_benign:
            continue
        for ref in inc.attack or []:
            tid = ref["techniqueId"]
            hunt = TECHNIQUE_HUNTS.get(tid) or TECHNIQUE_HUNTS.get(tid.split(".")[0])
            if hunt is None or tid in seen:
                continue
            seen.add(tid)
            title, filters, group = hunt
            users = (inc.entities or {}).get("users") or []
            ident = idents.get(users[0]) if users else None
            who = ident.display_name if ident else None
            local = inc.first_seen.astimezone(tz)
            when = f"{local:%A}" + (" night" if local.hour < 6 or local.hour >= 22 else "")
            hosts = ", ".join(((inc.entities or {}).get("hosts") or [])[:2])
            if who:
                why = f"{who}'s account was taken over this way on {when} ({inc.reference})."
            elif hosts:
                why = f"Seen on {hosts} on {when} ({inc.reference})."
            else:
                why = f"Seen on {when} ({inc.reference})."
            out.append(
                {
                    "id": _sid("technique", tid),
                    "title": title,
                    "why": why,
                    "query": query_doc(filters=filters, group_by=group, hours=24 * 7),
                    "basis": {"kind": "technique", "ref": tid},
                }
            )
    anomalies = list(
        (
            await db.execute(
                select(AnomalyRow)
                .where(AnomalyRow.org_id == org_id, AnomalyRow.window_end >= since)
                .order_by(AnomalyRow.score.desc())
                .limit(3)
            )
        ).scalars()
    )
    for a in anomalies:
        field = "user.name" if a.entity_type == "user" else "host.name"
        name = idents[a.entity_id].display_name if a.entity_id in idents else a.entity_id
        out.append(
            {
                "id": _sid("anomaly", a.id),
                "title": f"Look at everything {name} did around the unusual activity",
                "why": f"Behaviour of {a.entity_id} was more unusual than {round(a.score * 100)}% of its history.",
                "query": query_doc(
                    filters=[(field, "eq", a.entity_id)],
                    group_by=["action"],
                    frm=a.window_start - timedelta(minutes=30),
                    to=a.window_end + timedelta(minutes=30),
                ),
                "basis": {"kind": "anomaly", "ref": str(a.id)},
            }
        )
    assets = list((await db.execute(select(Asset).where(Asset.org_id == org_id).order_by(Asset.name))).scalars())
    for asset in assets:
        if not asset.vulnerabilities:
            continue
        for hit in kev_docs(list(asset.vulnerabilities)):
            if "vendor" not in hit:  # unknown to the known-exploited catalogue (or hidden as of the demo date)
                continue
            kev_filters: list[tuple[str, str, Any]] = (
                [("destination.ip", "in", list(asset.ips))] if asset.ips else [("host.name", "eq", asset.name)]
            )
            out.append(
                {
                    "id": _sid("kev", asset.name, hit["cveId"]),
                    "title": f"Look for unusual connections to {asset.name}",
                    "why": (
                        f"{asset.name} runs {hit.get('product', 'software')} with a flaw attackers are known to use "
                        f"({hit['cveId']})."
                    ),
                    "query": query_doc(filters=kev_filters, group_by=["source.ip"], hours=24 * 7),
                    "basis": {"kind": "vulnerability", "ref": hit["cveId"]},
                }
            )
    return out[:limit]


async def start_store_writer() -> Any:
    """Subscribe the API's `store-writer` durable consumer; None when the event stream is unavailable."""
    try:
        from nightjar.stream import api as stream

        return await stream.subscribe_normalized("store-writer", handle_normalized, batch=500, max_wait_s=1.0)
    except Exception as exc:
        log.warning("store_writer_unavailable", error=type(exc).__name__)
        return None
