"""Demo seed (builder A): Halden Logistics and Brightline Couriers per API contract Part 6.

`run()` is idempotent (it does nothing when the sample organisations exist); `reset()` drops the local stores and
seeds again. Everything is relative to the demo clock (`NIGHTJAR_DEMO_TODAY`).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.engine import make_url

from nightjar import clock
from nightjar.core.config import get_settings

log = structlog.get_logger(__name__)
HALDEN_SLUG = "halden-logistics"
BRIGHTLINE_SLUG = "brightline-couriers"


async def run(*, use_pipeline: bool = True) -> dict:
    """Seed the demo. Returns timing and counts. Phase 2 uses builder B's in-process pipeline when available."""
    from sqlalchemy import func, select

    from nightjar.db.models.platform import AuditLog
    from nightjar.db.models.soc import DetectionRow, Incident
    from nightjar.db.session import session_scope
    from nightjar.executive.service import build_brief, iso_week
    from nightjar.hunting.api import append_events
    from nightjar.seed import data
    from nightjar.seed.builders import add_inventory, add_sign_in_history, create_org, org_by_slug
    from nightjar.seed.events import background
    from nightjar.seed.history import seed_history
    from nightjar.seed.tonight import People, fixture_night, human_decisions, night_start
    from nightjar.services.audit import audit

    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    async with session_scope() as db:
        if await org_by_slug(db, HALDEN_SLUG):
            log.info("seed_skipped", reason="already seeded")
            return {"skipped": True}
    night = night_start()
    today_start = datetime.combine(
        clock.today(ZoneInfo("Europe/Brussels")), datetime.min.time(), tzinfo=ZoneInfo("Europe/Brussels")
    )

    # ── phase 1: platform ──
    with clock.travel(night - timedelta(days=60)):
        async with session_scope() as db:
            halden, people = await create_org(
                db,
                name="Halden Logistics",
                slug=HALDEN_SLUG,
                timezone="Europe/Brussels",
                profile=data.HALDEN_PROFILE,
                users=data.HALDEN_USERS,
            )
            ingrid = people["ingrid.vos@halden.example"]
            halden.settings = {
                **halden.settings,
                "regimeContacts": data.regime_contacts({"userId": str(ingrid.id), "name": ingrid.full_name}),
            }
            await add_inventory(
                db,
                halden.id,
                assets=data.HALDEN_ASSETS,
                identities=data.HALDEN_IDENTITIES,
                domain="halden.example",
                access_keys=data.HALDEN_ACCESS_KEYS,
            )
            bright, bright_people = await create_org(
                db,
                name="Brightline Couriers",
                slug=BRIGHTLINE_SLUG,
                timezone="Europe/Amsterdam",
                profile=data.BRIGHTLINE_PROFILE,
                users=data.BRIGHTLINE_USERS,
            )
            await add_inventory(
                db,
                bright.id,
                assets=data.BRIGHTLINE_ASSETS,
                identities=data.BRIGHTLINE_IDENTITIES,
                domain="brightline.example",
            )
            for org, who in (
                (halden, people["pieter.claes@halden.example"]),
                (bright, bright_people["olu.adebayo@brightline.example"]),
            ):
                await audit(
                    db,
                    None,
                    org_id=org.id,
                    action="org.created",
                    resource_type="org",
                    resource_id=org.id,
                    actor_id=who.id,
                    actor_name=who.full_name,
                    metadata={"name": org.name},
                )
            await _ingest_keys(db, halden.id, people["pieter.claes@halden.example"].id)
            halden_id, bright_id = halden.id, bright.id
    async with session_scope() as db:  # sign-in history runs up to the (demo) day before today
        await add_sign_in_history(db, halden_id, data.HALDEN_IDENTITIES)
        await add_sign_in_history(db, bright_id, data.BRIGHTLINE_IDENTITIES, seed=11)
    timings["platform"] = time.perf_counter() - t0

    # ── phase 1b: history ──
    t = time.perf_counter()
    history = await seed_history(halden_id, people, today_start=today_start)
    await append_events(background(halden_id))
    timings["history"] = time.perf_counter() - t

    # ── phase 2: last night ──
    t = time.perf_counter()
    mode = "fixtures"
    if use_pipeline:
        try:
            from nightjar.workers.inline import run_scenario

            await run_scenario(halden_id, "halden-night", end_at=None, auto_investigate=True)
            mode = "pipeline"
        except (ImportError, NotImplementedError, AttributeError) as exc:
            log.info("seed_pipeline_unavailable", error=type(exc).__name__)
    if mode == "fixtures":
        await fixture_night(halden_id, night)
    timings["night"] = time.perf_counter() - t

    # ── phase 3: people's decisions ──
    t = time.perf_counter()
    await human_decisions(halden_id, night, People(people))
    await _brightline_incident(bright_id, night)
    await _saved_searches(halden_id, people)
    for weeks_back in range(4, 0, -1):
        week = iso_week(clock.today() - timedelta(days=7 * weeks_back))
        monday_after = today_start - timedelta(days=today_start.weekday() + 7 * (weeks_back - 1)) + timedelta(hours=7)
        with clock.travel(monday_after):
            async with session_scope() as db:
                from nightjar.db.models.platform import Organization

                org = await db.get(Organization, halden_id)  # type: ignore[assignment]
                assert org is not None
                await build_brief(db, org, week)
    timings["decisions"] = time.perf_counter() - t

    async with session_scope() as db:
        counts = {
            "incidents": (
                await db.execute(select(func.count()).select_from(Incident).where(Incident.org_id == halden_id))
            ).scalar_one(),
            "detections": (
                await db.execute(select(func.count()).select_from(DetectionRow).where(DetectionRow.org_id == halden_id))
            ).scalar_one(),
            "auditRows": (
                await db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.org_id == halden_id))
            ).scalar_one(),
        }
    out = {
        "mode": mode,
        "history": history,
        **counts,
        "seconds": round(time.perf_counter() - t0, 1),
        "phases": {k: round(v, 1) for k, v in timings.items()},
        "today": clock.today().isoformat(),
    }
    log.info("seed_done", **out)
    return out


async def _ingest_keys(db, org_id, created_by) -> None:
    from nightjar.core.security import hash_token, new_ingest_key
    from nightjar.db.models.platform import IngestKey

    for name, source in (("Endpoint protection webhook", "webhook"), ("Remote-access gateways (syslog)", "syslog")):
        secret, prefix = new_ingest_key()  # the secret is not kept: create a new key in Settings to send events
        db.add(
            IngestKey(
                org_id=org_id,
                name=name,
                prefix=prefix,
                key_hash=hash_token(secret),
                source=source,
                created_by=created_by,
            )
        )


async def _brightline_incident(org_id, night: datetime) -> None:
    import uuid

    from nightjar.contracts import CorrelationUpdate, Detection, Entities, EventSnapshot, TriageScore, UntrustedText
    from nightjar.incidents import api as incidents

    t = night + timedelta(hours=5, minutes=12)
    did = uuid.uuid5(uuid.NAMESPACE_URL, f"brightline:{t.date()}")
    ents = Entities(users=("s.bakker",), hosts=("bl-vpn-01",), ips=("203.0.113.90",))
    det = Detection(
        id=did,
        org_id=org_id,
        source="sigma",
        rule_id="nightjar-failed-logins",
        rule_title="Repeated failed sign-ins for a courier account",
        severity="low",
        confidence=0.6,
        attack=(),
        entities=ents,
        first_seen=t,
        last_seen=t + timedelta(minutes=6),
        event_ids=(uuid.uuid4().hex,),
        evidence=(
            EventSnapshot(
                uuid.uuid4().hex, t, "vpn.syslog", "logon-failed", UntrustedText("SSL VPN login failed", "message")
            ),
        ),
        summary="Eight failed sign-ins for s.bakker in six minutes.",
        fields={},
        triage=TriageScore(did, 0.34, "uncertain", 0.5, (), "triage-1.0", False),
    )
    with clock.travel(t):
        await incidents.record_detections(org_id, [det])
        await incidents.upsert_incident(
            CorrelationUpdate(
                org_id=org_id,
                correlation_key="corr:brightline-failed-logins",
                detection_ids=(did,),
                kind="brute_force",
                title="Repeated failed sign-ins for a courier account",
                severity="low",
                attack=(),
                entities=ents,
                first_seen=t,
                last_seen=t + timedelta(minutes=6),
                summary=det.summary,
                max_triage_probability=0.34,
                all_likely_noise=False,
            )
        )


SAVED = [
    (
        "Refused sign-in prompts by person",
        "People who refused several sign-in prompts (push fatigue).",
        [("action", "eq", "mfa-push-denied")],
        ["user.name"],
        24 * 7,
        "aiko",
    ),
    (
        "Failed sign-ins by source address",
        "Addresses guessing passwords across many accounts.",
        [("action", "eq", "logon-failed")],
        ["source.ip"],
        24,
        "aiko",
    ),
    (
        "Programs reading sign-in secrets",
        "Memory dumps of the sign-in process with built-in tools.",
        [("process.commandLine", "contains", "minidump")],
        ["host.name"],
        24 * 7,
        "lukas",
    ),
    (
        "Remote program launches",
        "PsExec and its service between computers.",
        [("process.name", "in", ["psexec.exe", "psexec64.exe", "psexesvc.exe"])],
        ["host.name", "user.name"],
        24 * 7,
        "lukas",
    ),
    (
        "Cloud storage reads by key",
        "Which access keys read files from cloud storage.",
        [("action", "eq", "s3-get-object")],
        ["cloud.accessKeyId", "source.ip"],
        24 * 7,
        "lukas",
    ),
    (
        "New mailbox forwarding rules",
        "Rules that forward email outside the company.",
        [("action", "eq", "inbox-rule-created")],
        ["user.name"],
        24 * 30,
        "aiko",
    ),
    (
        "Uploads with file-sync tools",
        "File-sync programs that can copy data to personal storage.",
        [("process.name", "eq", "rclone.exe")],
        ["user.name", "host.name"],
        24 * 7,
        "aiko",
    ),
]


async def _saved_searches(org_id, people) -> None:
    from nightjar.db.models.soc import SavedSearch
    from nightjar.db.session import session_scope
    from nightjar.hunting.service import query_doc

    by_first = {e.split(".")[0]: u for e, u in people.items()}
    async with session_scope() as db:
        for name, desc, filters, group, hours, who in SAVED:
            db.add(
                SavedSearch(
                    org_id=org_id,
                    name=name,
                    description=desc,
                    created_by=by_first[who].id,
                    query=query_doc(filters=filters, group_by=group, hours=hours),  # type: ignore[arg-type]
                )
            )


def _sqlite_path() -> Path | None:
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).resolve()


def _drop_local_files(sqlite: Path | None) -> None:
    s = get_settings()
    targets = [Path(str(sqlite) + suffix) for suffix in ("", "-wal", "-shm")] if sqlite else []
    targets += [Path(s.duckdb_path), Path(str(s.duckdb_path) + ".wal")]
    for p in targets:
        p.unlink(missing_ok=True)
    shutil.rmtree(s.nats_store_dir, ignore_errors=True)


async def reset(*, use_pipeline: bool = True) -> dict:
    """Drop the SQLite app DB (or every table on Postgres), data/hunting.duckdb and data/jetstream, then migrate
    and seed."""
    from nightjar.cli import downgrade_base, upgrade_head
    from nightjar.db.session import get_engine
    from nightjar.hunting.api import close_store

    close_store()
    await get_engine().dispose()
    path = _sqlite_path()
    if path is None:
        await asyncio.to_thread(downgrade_base)
    await asyncio.to_thread(_drop_local_files, path)
    await asyncio.to_thread(upgrade_head)
    return await run(use_pipeline=use_pipeline)
