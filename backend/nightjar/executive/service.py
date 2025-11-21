"""Executive view (API contract §1.14): posture score, mean time to triage / contain, alert reduction, the weekly
brief, the morning brief and the shift log behind the Watch.

Definitions: time to triage = first detection → recorded triage verdict; time to contain = first detection →
first executed containment action (or resolution as benign/false positive). Both exclude merged incidents.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import SEVERITY_ORDER
from nightjar.db.models.platform import Organization
from nightjar.db.models.soc import (
    ActionProposal,
    DetectionRow,
    Incident,
    IncidentEvent,
    TriageScoreRow,
    WeeklyBrief,
)
from nightjar.incidents.service import OPEN_STATUSES, summaries
from nightjar.regimes import rules
from nightjar.regimes.service import clocks as running_clocks
from nightjar.services.pipeline import generate_or_baseline, pipeline_health

SEVERITIES = ("critical", "high", "medium", "low", "info")


def _mins(seconds: float | None) -> str:
    if seconds is None:
        return "no data"
    if seconds < 90:
        return f"{round(seconds)} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{seconds / 3600:.1f} hours"


def _trend(recent: float | None, previous: float | None) -> str:
    if recent is None or previous is None or previous == 0:
        return "flat"
    if recent < previous * 0.9:
        return "better"
    if recent > previous * 1.1:
        return "worse"
    return "flat"


async def _durations(db: AsyncSession, org_id: uuid.UUID, start: datetime, end: datetime, *, which: str) -> list[float]:
    stmt = select(Incident).where(Incident.org_id == org_id, Incident.merged_into_id.is_(None))
    if which == "triage":
        stmt = stmt.where(Incident.triaged_at >= start, Incident.triaged_at < end)
        return [
            (i.triaged_at - i.first_seen).total_seconds() for i in (await db.execute(stmt)).scalars() if i.triaged_at
        ]
    out = []
    for i in (await db.execute(stmt.where(Incident.first_seen >= start - timedelta(days=7)))).scalars():
        done = i.contained_at
        if done is None and i.resolution and i.resolution.get("reason") in ("benign", "false_positive"):
            done = i.resolved_at
        if done is not None and start <= done < end:
            out.append((done - i.first_seen).total_seconds())
    return out


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


async def response_times(db: AsyncSession, org_id: uuid.UUID, as_of: datetime) -> dict[str, dict[str, Any]]:
    out = {}
    for key, which in (("meanTimeToTriage", "triage"), ("meanTimeToContain", "contain")):
        d7 = _avg(await _durations(db, org_id, as_of - timedelta(days=7), as_of, which=which))
        prev = _avg(await _durations(db, org_id, as_of - timedelta(days=14), as_of - timedelta(days=7), which=which))
        d30 = _avg(await _durations(db, org_id, as_of - timedelta(days=30), as_of, which=which))
        out[key] = {"last7dSeconds": d7, "last30dSeconds": d30, "previous7dSeconds": prev, "trend": _trend(d7, prev)}
    return out


async def alert_reduction(db: AsyncSession, org_id: uuid.UUID, start: datetime, end: datetime) -> dict[str, Any]:
    alerts = (
        await db.execute(
            select(func.count())
            .select_from(DetectionRow)
            .where(DetectionRow.org_id == org_id, DetectionRow.first_seen >= start, DetectionRow.first_seen < end)
        )
    ).scalar_one()
    incidents = (
        await db.execute(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.org_id == org_id,
                Incident.first_seen >= start,
                Incident.first_seen < end,
                Incident.merged_into_id.is_(None),
            )
        )
    ).scalar_one()
    suppressed = (
        await db.execute(
            select(func.count())
            .select_from(TriageScoreRow)
            .join(DetectionRow, DetectionRow.id == TriageScoreRow.detection_id)
            .where(
                DetectionRow.org_id == org_id,
                DetectionRow.first_seen >= start,
                DetectionRow.first_seen < end,
                TriageScoreRow.suppressed.is_(True),
            )
        )
    ).scalar_one()
    reduction = round(100 * (1 - incidents / alerts)) if alerts else 0
    return {
        "last7d": int(alerts),
        "incidentsLast7d": int(incidents),
        "suppressedAsNoise": int(suppressed),
        "reductionPct": max(0, reduction),
    }


async def posture(db: AsyncSession, org_id: uuid.UUID, *, as_of: datetime | None = None) -> dict[str, Any]:
    now = as_of or clock.now()
    live = as_of is None
    open_rows = list(
        (
            await db.execute(
                select(Incident).where(
                    Incident.org_id == org_id, Incident.status.in_(OPEN_STATUSES), Incident.first_seen <= now
                )
            )
        ).scalars()
    )
    by_sev = dict.fromkeys(SEVERITIES, 0)
    for i in open_rows:
        by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
    clocks = [c for c in await running_clocks(db, org_id) if c["state"] != "not_started"] if live else []
    sums = await summaries(db, org_id, open_rows)
    reportable_open = sum(1 for s in sums if s["reportable"] == "yes")
    times = await response_times(db, org_id, now)
    alerts = await alert_reduction(db, org_id, now - timedelta(days=7), now)
    pending = (
        (
            await db.execute(
                select(func.count())
                .select_from(ActionProposal)
                .where(ActionProposal.org_id == org_id, ActionProposal.status == "pending")
            )
        ).scalar_one()
        if live
        else 0
    )
    health = await pipeline_health() if live else None
    overdue = sum(1 for c in clocks if c["state"] == "overdue")
    due_soon = sum(1 for c in clocks if c["state"] == "due_soon")
    mttt = times["meanTimeToTriage"]["last7dSeconds"]
    exposure = max(0, 100 - 25 * by_sev["critical"] - 10 * by_sev["high"] - 3 * by_sev["medium"])
    clock_score = max(0, 100 - 40 * overdue - 15 * due_soon)
    speed = 100 if mttt is None else 100 if mttt <= 300 else 80 if mttt <= 1800 else 60 if mttt <= 7200 else 40
    approvals = max(0, 100 - 10 * int(pending))
    intake_scores = {"normal": 100, "catching_up": 75, "degraded": 50, "offline": 60}
    intake = intake_scores.get(health.status, 60) if health else 100
    components = [
        {
            "key": "exposure",
            "label": "Open serious incidents",
            "score": exposure,
            "note": f"{by_sev['critical']} critical and {by_sev['high']} high open",
        },
        {
            "key": "clocks",
            "label": "Regulatory clocks",
            "score": clock_score,
            "note": f"{len(clocks)} running, {due_soon} due soon, {overdue} overdue" if clocks else "No clocks running",
        },
        {
            "key": "response",
            "label": "Speed of first look",
            "score": speed,
            "note": f"Average time to triage this week: {_mins(mttt)}",
        },
        {
            "key": "decisions",
            "label": "Decisions waiting",
            "score": approvals,
            "note": f"{pending} response action{'s' if pending != 1 else ''} waiting for approval",
        },
        {
            "key": "intake",
            "label": "Event intake",
            "score": intake,
            "note": health.plain_status if health else "Taking in events normally",
        },
    ]
    score = round(mean([exposure, clock_score, speed, approvals, intake]))
    band = "steady" if score >= 75 else "watch" if score >= 50 else "strained"
    highlights = []
    for s in sorted(sums, key=lambda s: -SEVERITY_ORDER.get(s["severity"], 0)):
        if s["reportable"] == "yes" and s["nextDeadline"]:
            d = s["nextDeadline"]
            local = datetime.fromisoformat(d["dueAtLocal"]["at"])
            place = d["dueAtLocal"]["timezone"].split("/")[-1].replace("_", " ")
            highlights.append(
                f"{s['reference']} is reportable; next: {d['noticeLabel']} due "
                f"{local.strftime('%a')} {local.day} {local.strftime('%b %H:%M')} {place} time."
            )
    if alerts["last7d"]:
        highlights.append(
            f"{alerts['last7d']} alerts became {alerts['incidentsLast7d']} incidents this week "
            f"({alerts['reductionPct']}% fewer things to look at)."
        )
    if mttt is not None:
        trend = times["meanTimeToTriage"]["trend"]
        highlights.append(
            f"Average time to triage this week: {_mins(mttt)}"
            + {"better": " (faster than last week).", "worse": " (slower than last week).", "flat": "."}[trend]
        )
    return {
        "asOf": now.isoformat(),
        "score": score,
        "scoreBand": band,
        "components": components,
        "openBySeverity": by_sev,
        "reportableOpen": reportable_open,
        **{k: {kk: vv for kk, vv in v.items() if kk != "previous7dSeconds"} for k, v in times.items()},
        "alerts": alerts,
        "clocks": clocks,
        "highlights": highlights,
    }


# ── Weekly brief ─────────────────────────────────────────────────────────────
def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week: str) -> tuple[date, date]:
    try:
        y, w = week.split("-W")
        start = date.fromisocalendar(int(y), int(w), 1)
    except ValueError as exc:
        raise ValueError("week must look like 2025-W47") from exc
    return start, start + timedelta(days=6)


class _BriefSection(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=6000)


class BriefDraft(BaseModel):
    headline: str = Field(max_length=300)
    sections: list[_BriefSection]


async def build_brief(db: AsyncSession, org: Organization, week: str) -> WeeklyBrief:
    start_d, end_d = week_bounds(week)
    tz = ZoneInfo(org.timezone or "UTC")
    start = datetime.combine(start_d, time(0), tzinfo=tz)
    end = datetime.combine(end_d + timedelta(days=1), time(0), tzinfo=tz)
    as_of = min(end, clock.now())
    incidents = list(
        (
            await db.execute(
                select(Incident)
                .where(
                    Incident.org_id == org.id,
                    Incident.first_seen >= start,
                    Incident.first_seen < end,
                    Incident.merged_into_id.is_(None),
                )
                .order_by(Incident.first_seen)
            )
        ).scalars()
    )
    metrics = await posture(db, org.id, as_of=as_of)
    alerts = await alert_reduction(db, org.id, start, end)
    metrics["alerts"] = alerts
    serious = [i for i in incidents if SEVERITY_ORDER.get(i.severity, 0) >= 3 and i.verdict != "benign"]
    benign = [i for i in incidents if i.verdict in ("benign", "false_positive") or i.likely_benign]
    sums = await summaries(db, org.id, incidents)
    reportable = [s for s in sums if s["reportable"] == "yes"]
    if serious:
        headline = f"{len(serious)} serious incident{'s' if len(serious) != 1 else ''} handled" + (
            f"; {len(reportable)} reportable to regulators" if reportable else "; none reportable"
        )
    else:
        headline = f"A steady week: {len(incidents)} incident{'s' if len(incidents) != 1 else ''}, none serious"
    mttt = metrics["meanTimeToTriage"]["last7dSeconds"]
    mttc = metrics["meanTimeToContain"]["last7dSeconds"]
    top = sorted(serious, key=lambda i: (-SEVERITY_ORDER.get(i.severity, 0), i.first_seen))[:5]
    sections = [
        {
            "title": "The week in brief",
            "body": (
                f"{alerts['last7d']} alerts were raised and grouped into {len(incidents)} incidents "
                f"({alerts['reductionPct']}% fewer items to review). {len(serious)} were serious and {len(benign)} "
                "turned out to be expected activity or false alarms."
            ),
        },
        {
            "title": "Incidents that mattered",
            "body": "\n".join(f"{i.reference}: {i.title} ({i.severity}, {i.status})." for i in top)
            or "No serious incidents this week.",
        },
        {
            "title": "How fast we responded",
            "body": (
                f"Average time from first alert to a triage conclusion: {_mins(mttt)}. Average time to contain: "
                f"{_mins(mttc)}. Every containment step was approved by a person before it ran."
            ),
        },
        {
            "title": "Regulatory reporting",
            "body": "\n".join(
                f"{s['reference']}: reportable; "
                + (
                    f"next {s['nextDeadline']['noticeLabel']} due {s['nextDeadline']['dueAt'][:16]} UTC."
                    if s["nextDeadline"]
                    else "all notices submitted."
                )
                for s in reportable
            )
            or "No incident this week required a regulatory notice.",
        },
    ]
    baseline = {"headline": headline, "sections": sections}
    output, generated_by = await generate_or_baseline(
        "weekly_brief",
        instructions=(
            "Write a short weekly security brief for executives in plain business language. Keep the given facts "
            "and section titles; no jargon."
        ),
        trusted={"week": week, "metrics": {k: v for k, v in metrics.items() if k != "clocks"}},
        untrusted={},
        schema=BriefDraft,
        baseline=baseline,
    )
    row = (
        await db.execute(select(WeeklyBrief).where(WeeklyBrief.org_id == org.id, WeeklyBrief.week == week))
    ).scalar_one_or_none()
    if row is None:
        row = WeeklyBrief(org_id=org.id, week=week, date_from=start_d, date_to=end_d)
        db.add(row)
    row.headline = output["headline"]
    row.sections = output["sections"]
    row.metrics = metrics
    row.incident_ids = [str(i.id) for i in incidents]
    row.generated_by = generated_by
    row.generated_at = clock.now()
    await db.flush()
    return row


async def brief_doc(db: AsyncSession, b: WeeklyBrief) -> dict:
    ids = [uuid.UUID(i) for i in b.incident_ids or []]
    incs = list((await db.execute(select(Incident).where(Incident.id.in_(ids)))).scalars()) if ids else []
    incs.sort(key=lambda i: (-SEVERITY_ORDER.get(i.severity, 0), i.first_seen))
    return {
        "week": b.week,
        "from": b.date_from.isoformat(),
        "to": b.date_to.isoformat(),
        "headline": b.headline,
        "sections": b.sections,
        "metrics": b.metrics,
        "incidents": await summaries(db, b.org_id, incs),
        "generatedBy": b.generated_by,
        "generatedAt": b.generated_at.isoformat(),
    }


def brief_html(doc: dict, org_name: str) -> str:
    import html

    e = html.escape
    m = doc["metrics"]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Weekly security brief {e(doc['week'])}</title></head>",
        '<body style="font-family:Georgia,serif;max-width:760px;margin:40px auto;color:#111;line-height:1.5">',
        f"<p style='font:12px Helvetica,Arial,sans-serif;color:#555'>{e(org_name)} · week {e(doc['week'])} "
        f"({e(doc['from'])} to {e(doc['to'])})</p>",
        f"<h1 style='font-size:26px;margin:0 0 16px'>{e(doc['headline'])}</h1>",
        f"<p style='font:14px Helvetica,Arial,sans-serif'>Posture {m.get('score')} / 100 "
        f"({e(m.get('scoreBand', ''))})</p>",
    ]
    for s in doc["sections"]:
        body = e(s["body"]).replace("\n", "<br>")
        parts.append(f"<h2 style='font-size:17px;margin:24px 0 6px'>{e(s['title'])}</h2><p style='margin:0'>{body}</p>")
    parts.append("</body></html>")
    return "".join(parts)


# ── Shift log, morning brief and the Watch ───────────────────────────────────
SHIFT_KINDS = {
    "status": "incident",
    "agent": "incident",
    "approval": "approval",
    "clock": "clock",
    "action": "action",
    "notice": "notice",
}


async def shift_log(db: AsyncSession, org_id: uuid.UUID, *, since: datetime, limit: int = 100) -> list[dict]:
    rows = (
        await db.execute(
            select(IncidentEvent, Incident.reference, Incident.severity)
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .where(
                IncidentEvent.org_id == org_id,
                IncidentEvent.at >= since,
                IncidentEvent.at <= clock.now(),
                IncidentEvent.kind.in_(tuple(SHIFT_KINDS)),
            )
            .order_by(IncidentEvent.at.desc(), IncidentEvent.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "at": e.at.isoformat(),
            "text": f"{ref}: {e.title}",
            "kind": SHIFT_KINDS[e.kind],
            "refId": str(e.incident_id),
            "severity": sev if e.kind in ("status", "agent") else None,
        }
        for e, ref, sev in rows
    ]


def last_night(org_tz: str = "Europe/Brussels") -> tuple[datetime, datetime]:
    """The most recent 00:00 (org time) such that now ≥ that midnight + 09:00; the night runs to 09:00."""
    tz = ZoneInfo(org_tz)
    now = clock.now().astimezone(tz)
    night = datetime.combine(now.date(), time(0), tzinfo=tz)
    if now < night + timedelta(hours=9):
        night -= timedelta(days=1)
    return night, night + timedelta(hours=9)


async def morning_brief(db: AsyncSession, org: Organization) -> dict:
    start, end = last_night(org.timezone or "Europe/Brussels")
    opened = list(
        (
            await db.execute(
                select(Incident)
                .where(
                    Incident.org_id == org.id,
                    Incident.first_seen >= start,
                    Incident.first_seen < end,
                    Incident.merged_into_id.is_(None),
                )
                .order_by(Incident.first_seen)
            )
        ).scalars()
    )
    sums = await summaries(db, org.id, opened)
    serious = [s for s in sums if s["severity"] in ("critical", "high") and s["verdict"] != "benign"]
    contained = sum(1 for i in opened if i.contained_at and i.contained_at < end + timedelta(hours=15))
    benign = sum(1 for s in sums if s["verdict"] in ("benign", "false_positive") or s["likelyBenign"])
    pending = (
        await db.execute(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.org_id == org.id, ActionProposal.status == "pending")
        )
    ).scalar_one()
    clocks = [c for c in await running_clocks(db, org.id) if c["state"] != "not_started"]
    alerts = await alert_reduction(db, org.id, start, end)
    headline = f"Overnight: {len(opened)} incident{'s' if len(opened) != 1 else ''}, {len(serious)} serious" + (
        f"; {pending} decision{'s' if pending != 1 else ''} waiting for you" if pending else ""
    )
    highlights = [f"{s['reference']}: {s['title']} ({s['statusLabel'].lower()})." for s in serious]
    if benign:
        highlights.append(f"{benign} turned out to be expected activity and were closed by the triage colleague.")
    if clocks:
        c = clocks[0]
        highlights.append(
            f"Next regulator deadline: {c['noticeLabel']} for {c['incidentReference']}, due "
            f"{c['dueAtLocal']['at'][:16].replace('T', ' ')} ({c['dueAtLocal']['timezone']})."
        )
    return {
        "asOf": clock.now().isoformat(),
        "night": {"from": start.isoformat(), "to": end.isoformat()},
        "headline": headline,
        "incidents": sums,
        "contained": contained,
        "pendingApprovals": int(pending),
        "alerts": alerts,
        "clocks": clocks,
        "highlights": highlights,
    }


def regime_labels() -> dict[str, str]:
    return {r: rules.regime_label(r) for r in rules.REGIME_IDS}
