"""Regulatory regimes and deadline rules (API contract §1.12), as of late November 2025.

Pure functions only (no database): given a human determination or a submission, which deadlines start and when
are they due. The incident clock never decides on its own that an incident is reportable; `propose()` only
suggests, and a person with `reporting:assess` determines.

* NIS2 (Directive (EU) 2022/2555 Art. 23): early warning 24 h and incident notification 72 h after awareness of
  a significant incident; final report one month after the incident notification was submitted, or a progress
  report then if handling is still ongoing and the final report one month after handling ends.
* SEC Form 8-K Item 1.05: due 17:30 America/New_York on the fourth business day after the materiality
  determination; Form 8-K/A within four business days after new information becomes available; an Attorney
  General delay moves the due date to the granted date.
* SOCI Act 2018 Part 2B: critical incident (significant impact on availability) 12 h, other incident (relevant
  impact) 72 h; after an oral report, a written record within 84 h (critical) or 48 h (other).
* GDPR Art. 33 (extension): notification 72 h after awareness of a personal data breach.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nightjar.regimes import calendar as cal

REGIME_IDS = ("nis2", "sec_8k", "soci", "gdpr_art33")

REGIMES: dict[str, dict[str, Any]] = {
    "nis2": {
        "label": "NIS2",
        "jurisdiction": "EU",
        "timezone": "Europe/Brussels",
        "trigger": "awareAt",
        "recipient": "Centre for Cybersecurity Belgium (CCB)",
        "receipt": "CCB",
    },
    "sec_8k": {
        "label": "SEC Form 8-K Item 1.05",
        "jurisdiction": "US",
        "timezone": "America/New_York",
        "trigger": "materialityDeterminedAt",
        "recipient": "U.S. Securities and Exchange Commission (EDGAR)",
        "receipt": "EDGAR",
    },
    "soci": {
        "label": "SOCI Act",
        "jurisdiction": "AU",
        "timezone": "Australia/Melbourne",
        "trigger": "awareAt",
        "recipient": "Australian Signals Directorate (ReportCyber)",
        "receipt": "ASD",
    },
    "gdpr_art33": {
        "label": "GDPR Article 33",
        "jurisdiction": "EU",
        "timezone": "Europe/Brussels",
        "trigger": "awareAt",
        "recipient": "Belgian Data Protection Authority",
        "receipt": "APD",
    },
}

NOTICE_LABELS: dict[str, str] = {
    "nis2.early_warning": "NIS2 early warning",
    "nis2.incident_notification": "NIS2 incident notification",
    "nis2.intermediate_report": "NIS2 intermediate report",
    "nis2.progress_report": "NIS2 progress report",
    "nis2.final_report": "NIS2 final report",
    "sec_8k.item_105": "Form 8-K Item 1.05",
    "sec_8k.amendment": "Form 8-K/A amendment",
    "soci.critical_report": "SOCI critical incident report",
    "soci.other_report": "SOCI incident report",
    "soci.written_record": "SOCI written record of an oral report",
    "gdpr_art33.notification": "GDPR breach notification",
}

SEC_DUE_TIME = time(17, 30)
SEC_BUSINESS_DAYS = 4
DUE_SOON_FLOOR = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class DeadlineSpec:
    notice_type: str
    starts_at: datetime
    due_at: datetime
    timezone: str
    basis: str


def regime_label(regime: str) -> str:
    return str(REGIMES.get(regime, {}).get("label", regime))


def notice_label(notice_type: str) -> str:
    return NOTICE_LABELS.get(notice_type, notice_type.replace("_", " ").replace(".", " "))


def regime_zone(regime: str, entity: dict | None = None) -> ZoneInfo:
    if regime == "sec_8k":
        return cal.NEW_YORK  # EDGAR filing deadlines are Eastern time whatever the registrant's location
    tz = (entity or {}).get("timezone") or REGIMES[regime]["timezone"]
    return ZoneInfo(tz)


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local:%a %d %b %Y %H:%M} {local.tzname()}"


# ── Form 8-K Item 1.05 ───────────────────────────────────────────────────────
def sec_8k_due(determined_at: datetime, *, business_days: int = SEC_BUSINESS_DAYS) -> datetime:
    """17:30 New York time on the n-th business day after the day (in New York) of the determination."""
    start_day = determined_at.astimezone(cal.NEW_YORK).date()
    due_day = cal.add_business_days(start_day, business_days)
    return cal.at_local(due_day, SEC_DUE_TIME, cal.NEW_YORK)


def sec_basis(determined_at: datetime, due_at: datetime, *, what: str = "materiality determination") -> str:
    start_day = determined_at.astimezone(cal.NEW_YORK).date()
    skipped = cal.skipped_holidays(start_day, due_at.astimezone(cal.NEW_YORK).date())
    text = f"Four business days after the {what} ({_fmt(determined_at, cal.NEW_YORK)}), by 17:30 New York time"
    if skipped:
        text += "; skips " + ", ".join(f"{name} ({d:%d %b})" for d, name in skipped)
    return text + "."


def business_days_left(now: datetime, due_at: datetime) -> int:
    """Business days remaining, counted in New York: (today, due day]."""
    return cal.business_days_between(now.astimezone(cal.NEW_YORK).date(), due_at.astimezone(cal.NEW_YORK).date())


# ── Determination → deadlines ────────────────────────────────────────────────
def deadlines_for_determination(regime: str, determination: dict, entity: dict | None = None) -> list[DeadlineSpec]:
    """The clocks a positive determination starts. `determination` uses wire keys (awareAt, impactLevel,
    materialityDeterminedAt) with datetime values."""
    if not determination.get("applies"):
        return []
    tz = regime_zone(regime, entity)
    tzname = str(tz.key)
    if regime == "nis2":
        aware: datetime = determination["awareAt"]
        return [
            DeadlineSpec(
                "nis2.early_warning",
                aware,
                aware + timedelta(hours=24),
                tzname,
                f"Within 24 hours of becoming aware of the significant incident ({_fmt(aware, tz)}).",
            ),
            DeadlineSpec(
                "nis2.incident_notification",
                aware,
                aware + timedelta(hours=72),
                tzname,
                f"Within 72 hours of becoming aware of the significant incident ({_fmt(aware, tz)}).",
            ),
        ]
    if regime == "sec_8k":
        determined: datetime = determination["materialityDeterminedAt"]
        due = sec_8k_due(determined)
        return [DeadlineSpec("sec_8k.item_105", determined, due, tzname, sec_basis(determined, due))]
    if regime == "soci":
        aware = determination["awareAt"]
        if determination.get("impactLevel") == "significant":
            return [
                DeadlineSpec(
                    "soci.critical_report",
                    aware,
                    aware + timedelta(hours=12),
                    tzname,
                    f"Critical incident (significant impact on availability): within 12 hours of becoming aware "
                    f"({_fmt(aware, tz)}).",
                )
            ]
        return [
            DeadlineSpec(
                "soci.other_report",
                aware,
                aware + timedelta(hours=72),
                tzname,
                f"Incident with a relevant impact: within 72 hours of becoming aware ({_fmt(aware, tz)}).",
            )
        ]
    if regime == "gdpr_art33":
        aware = determination["awareAt"]
        return [
            DeadlineSpec(
                "gdpr_art33.notification",
                aware,
                aware + timedelta(hours=72),
                tzname,
                f"Without undue delay and within 72 hours of becoming aware of the breach ({_fmt(aware, tz)}); "
                "reasons are required if later.",
            )
        ]
    raise ValueError(f"unknown regime {regime!r}")


def deadlines_after_submission(
    notice_type: str,
    submitted_at: datetime,
    *,
    channel: str,
    entity: dict | None = None,
    handling_ended_at: datetime | None = None,
) -> list[DeadlineSpec]:
    """Follow-on clocks a submission starts."""
    regime = notice_type.split(".", 1)[0]
    tz = regime_zone(regime, entity)
    tzname = str(tz.key)
    if notice_type == "nis2.incident_notification":
        due = cal.add_months(submitted_at, 1, tz)
        return [
            DeadlineSpec(
                "nis2.final_report",
                submitted_at,
                due,
                tzname,
                f"One month after the incident notification was submitted ({_fmt(submitted_at, tz)}). If the "
                "incident is still being handled then, a progress report is due instead.",
            )
        ]
    if notice_type == "nis2.progress_report" and handling_ended_at is not None:
        return [nis2_final_after_handling(handling_ended_at, entity)]
    if notice_type in ("soci.critical_report", "soci.other_report") and channel == "oral":
        hours = 84 if notice_type == "soci.critical_report" else 48
        return [
            DeadlineSpec(
                "soci.written_record",
                submitted_at,
                submitted_at + timedelta(hours=hours),
                tzname,
                f"Written record within {hours} hours of the oral report ({_fmt(submitted_at, tz)}).",
            )
        ]
    return []


def nis2_final_after_handling(handling_ended_at: datetime, entity: dict | None = None) -> DeadlineSpec:
    tz = regime_zone("nis2", entity)
    due = cal.add_months(handling_ended_at, 1, tz)
    return DeadlineSpec(
        "nis2.final_report",
        handling_ended_at,
        due,
        str(tz.key),
        f"One month after handling of the incident ended ({_fmt(handling_ended_at, tz)}).",
    )


def sec_amendment(available_at: datetime) -> DeadlineSpec:
    due = sec_8k_due(available_at)
    return DeadlineSpec(
        "sec_8k.amendment",
        available_at,
        due,
        "America/New_York",
        sec_basis(available_at, due, what="new information becoming available"),
    )


def nis2_progress_due(final_due: datetime, *, incident_open: bool) -> str:
    """The NIS2 one-month deadline is a progress report while handling is ongoing."""
    return "nis2.progress_report" if incident_open else "nis2.final_report"


# ── Clock state ──────────────────────────────────────────────────────────────
def clock_state(
    *, regime: str, starts_at: datetime, due_at: datetime, now: datetime, submitted: bool, required: bool = True
) -> str:
    if not required:
        return "not_required"
    if submitted:
        return "submitted"
    if now >= due_at:
        return "overdue"
    if regime == "sec_8k":
        return "due_soon" if business_days_left(now, due_at) <= 1 else "on_track"
    window = due_at - starts_at
    if due_at - now <= max(window * 0.25, DUE_SOON_FLOOR):
        return "due_soon"
    return "on_track"


# ── Applicability proposals (suggestions only) ───────────────────────────────
def propose(regime: str, entity: dict, facts: dict | None, *, incident: dict) -> tuple[str, list[str]]:
    """("yes"|"no"|"assess", plain reasons) for one legal entity. `facts` is a ReportableFactsDoc (camelCase) or
    None; `incident` carries `jurisdictions` (from affected assets/identities) and `kind`."""
    facts = facts or {}
    jurisdictions = set(facts.get("affectedJurisdictions") or ()) | set(incident.get("jurisdictions") or ())
    named = set(facts.get("affectedEntities") or ())
    affected = entity.get("jurisdiction") in jurisdictions or entity.get("name") in named
    disruption = facts.get("serviceDisruption", "none")
    impact = facts.get("criticalAssetImpact", "none")
    money = facts.get("financialImpactEstimateEur") or 0
    malicious = bool(facts.get("maliciousSuspected"))
    name = entity.get("name", "the entity")
    if not facts:
        if not affected:
            return "no", [f"Nothing so far points to {name} being affected."]
        return "assess", [f"{name} may be affected; the investigation has not yet established the facts."]
    if regime == "nis2":
        if not (entity.get("nis2") or {}).get("applies"):
            return "no", [f"{name} is not in scope of NIS2."]
        if not affected:
            return "no", [f"No services of {name} appear to be affected."]
        reasons = []
        if disruption != "none":
            hours = facts.get("disruptionHours")
            reasons.append(
                f"Service disruption ({disruption}{f', about {hours:g} hours' if hours else ''}) affecting {name}."
            )
        if money >= 500_000:
            reasons.append(f"Estimated financial impact of about EUR {money:,.0f}.")
        if facts.get("crossBorder"):
            reasons.append("Impact appears to reach more than one EU member state.")
        if disruption != "none" or money >= 500_000:
            if malicious:
                reasons.append("A malicious act is suspected.")
            return "yes", reasons
        if impact != "none" or facts.get("crossBorder"):
            return "assess", [*reasons, "Impact is not yet clear enough to call this a significant incident."]
        return "no", [f"No operational disruption or financial loss established for {name}."]
    if regime == "sec_8k":
        if not (entity.get("sec") or {}).get("registrant"):
            return "no", [f"{name} is not an SEC registrant."]
        if not affected:
            return "no", ["The registrant's information systems do not appear to be affected."]
        reasons = [f"The incident affects information systems of {name}, an SEC registrant."]
        if disruption != "none":
            reasons.append(f"Operations were disrupted ({disruption}).")
        if money:
            reasons.append(f"Estimated financial impact of about EUR {money:,.0f}.")
        reasons.append("Materiality is for the disclosure committee to determine without unreasonable delay.")
        return "assess", reasons
    if regime == "soci":
        if not (entity.get("soci") or {}).get("responsibleEntity"):
            return "no", [f"{name} is not a responsible entity under the SOCI Act."]
        if not affected or impact == "none":
            return "no", ["No impact on the availability of a critical infrastructure asset was found."]
        assets = ", ".join((entity.get("soci") or {}).get("assets") or []) or "the critical infrastructure asset"
        if impact == "significant":
            return "yes", [f"Significant impact on the availability of {assets}: a critical incident (12 hours)."]
        return "yes", [f"Relevant impact on {assets}: an incident to report within 72 hours."]
    if regime == "gdpr_art33":
        if not (entity.get("gdpr") or {}).get("controller"):
            return "no", [f"{name} is not the controller."]
        if not facts.get("personalDataInvolved"):
            return "no", ["No personal data appears to be involved."]
        n = facts.get("recordsAffectedEstimate")
        reasons = ["Personal data may have been accessed" + (f" (about {n:,} records)." if n else ".")]
        reasons.append("Whether the breach is unlikely to result in a risk to people needs a review.")
        return "assess", reasons
    raise ValueError(f"unknown regime {regime!r}")


def entity_regimes(entity: dict) -> list[str]:
    out = []
    if (entity.get("nis2") or {}).get("applies"):
        out.append("nis2")
    if (entity.get("sec") or {}).get("registrant"):
        out.append("sec_8k")
    if (entity.get("soci") or {}).get("responsibleEntity"):
        out.append("soci")
    if (entity.get("gdpr") or {}).get("controller"):
        out.append("gdpr_art33")
    return out
