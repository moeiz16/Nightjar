"""Notice drafting: section templates (`data/notice_templates.json`) and the deterministic baseline wording built
from incident facts. The baseline is what the offline text generator returns; a cloud generator may rephrase
it within the same schema. Anything the platform does not know is written as `[to be confirmed]`, which the
missing-fields check treats as unfilled.

SEC Item 1.05 drafts describe the material aspects of the nature, scope and timing and the material impact; they
never include indicators, technique ids or other technical detail that could hinder response or remediation.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from nightjar.regimes.rules import regime_zone

TBC = "[to be confirmed]"
_DATA = Path(__file__).resolve().parent / "data"

INSTRUCTIONS = (
    "Draft the sections of a regulatory incident notice for a person to review. Use only the facts provided. "
    "Plain, factual language; no speculation; write [to be confirmed] where a fact is not known. Keep every "
    "section key. For SEC filings describe material aspects only and never include technical indicators."
)


class DraftSection(BaseModel):
    key: str = Field(max_length=60)
    body: str = Field(max_length=8000)


class NoticeDraft(BaseModel):
    sections: list[DraftSection]


@lru_cache
def templates() -> dict[str, Any]:
    return json.loads((_DATA / "notice_templates.json").read_text(encoding="utf-8"))["types"]


def template(notice_type: str) -> dict[str, Any]:
    try:
        return templates()[notice_type]
    except KeyError as exc:
        raise ValueError(f"unknown notice type {notice_type!r}") from exc


def is_filled(body: str | None) -> bool:
    return bool(body and body.strip() and TBC not in body)


def _when(dt: datetime | str | None, tz: ZoneInfo) -> str:
    if dt is None:
        return TBC
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    local = dt.astimezone(tz)
    return f"{local:%d %B %Y at %H:%M} {local.tzname()}"


def _join(items: list[str], empty: str = TBC) -> str:
    items = [i for i in items if i]
    if not items:
        return empty
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def baseline(notice_type: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """{"sections": [{key, body}]} for the template's sections. `ctx` keys: org, entity, incident, facts,
    determination, actions, contact, iocs, oralReport."""
    regime = notice_type.split(".", 1)[0]
    tz = regime_zone(regime, ctx.get("entity"))
    inc = ctx["incident"]
    ent = ctx.get("entity") or {}
    facts = ctx.get("facts") or {}
    det = ctx.get("determination") or {}
    actions: list[str] = ctx.get("actions") or []
    contact = ctx.get("contact")
    aware = det.get("awareAt") or det.get("materialityDeterminedAt")
    services = _join(inc.get("services") or [], "the affected services")
    disruption = facts.get("serviceDisruption", "none")
    hours = facts.get("disruptionHours")
    disruption_text = (
        "No disruption of services has been identified."
        if disruption == "none"
        else f"Services were {'unavailable' if disruption == 'outage' else 'degraded'}"
        + (f" for about {hours:g} hours" if hours else "")
        + f" ({services})."
    )
    measures = (
        "Measures taken so far: " + "; ".join(actions) + "."
        if actions
        else "Containment measures are being decided " + TBC + "."
    )
    contact_text = (
        f"{contact.get('name')}, {contact.get('role')}, {contact.get('email')}"
        + (f", {contact['phone']}" if contact.get("phone") else "")
        if contact
        else TBC
    )
    malicious = facts.get("maliciousSuspected")
    cross = facts.get("crossBorder")
    first_seen = _when(inc.get("firstSeen"), tz)
    what = (
        f"On {first_seen}, {ent.get('name', ctx.get('org', 'the entity'))} detected {inc['title'].lower()} "
        f"(internal reference {inc['reference']}). {inc.get('verdictSummary') or ''}"
    ).strip()
    iocs = ctx.get("iocs") or []
    ioc_text = "\n".join(f"- {i}" for i in iocs) if iocs else "No indicators of compromise to share yet " + TBC + "."
    bodies: dict[str, str] = {
        "incident_summary": what,
        "suspected_malicious": (
            "Yes. The activity is suspected to be the result of unlawful or malicious acts."
            if malicious
            else ("No. Current evidence does not point to a malicious act." if malicious is False else TBC)
        ),
        "cross_border": (
            "Yes. The affected services also support operations in other EU member states."
            if cross
            else ("No cross-border impact identified so far." if cross is False else TBC)
        ),
        "entity_details": (
            f"{ent.get('name', TBC)}, {(ent.get('nis2') or {}).get('sector', TBC)} sector, "
            f"{(ent.get('nis2') or {}).get('entityType', TBC)} entity under NIS2."
        ),
        "contact": contact_text,
        "update": (
            f"Since the early warning, the incident ({inc['reference']}) has been investigated and contained. "
            + measures
        ),
        "severity_impact": f"Severity assessed as {inc.get('severity', TBC)}. {disruption_text}",
        "indicators": ioc_text,
        "measures": measures,
        "status_update": f"Current status: {inc.get('statusLabel', TBC)}. {measures}",
        "progress": (
            f"Handling of the incident is ongoing (status: {inc.get('statusLabel', TBC)}). {measures} "
            "A final report will follow within one month after handling ends."
        ),
        "description": what,
        "root_cause": (
            f"Threat type: {inc.get('kindLabel', TBC)}. Techniques observed: {_join(inc.get('techniques') or [])}."
        ),
        "mitigation": measures,
        # SEC Item 1.05: material aspects only, no technical detail
        "nature": (
            f"On {_when(inc.get('firstSeen'), tz)}, the Company identified unauthorized activity affecting certain "
            f"of its information systems ({inc.get('kindLabel', 'a cybersecurity incident').lower()})."
        ),
        "scope": (
            f"The activity affected systems supporting {services}. " + (disruption_text if disruption != "none" else "")
        ).strip(),
        "timing": (
            f"The activity occurred between {_when(inc.get('firstSeen'), tz)} and {_when(inc.get('lastSeen'), tz)} "
            f"and was contained on {_when(inc.get('containedAt'), tz) if inc.get('containedAt') else TBC}."
        ),
        "material_impact": (
            "The disclosure committee determined on "
            f"{_when(det.get('materialityDeterminedAt'), tz)} that the incident is material, including because of "
            f"the disruption to operations. The full financial impact has not yet been determined {TBC}."
        ),
        "not_determined": "The full scope and financial impact of the incident have not yet been determined.",
        "signature": f"{ent.get('name', TBC)} (ticker {(ent.get('sec') or {}).get('ticker', TBC)}). Signatory {TBC}.",
        "amended_information": TBC,
        "asset": _join((ent.get("soci") or {}).get("assets") or []),
        "incident": what,
        "impact": (
            f"{'Significant' if det.get('impactLevel') == 'significant' else 'Relevant'} impact on the availability "
            f"of the asset. {disruption_text}"
        ),
        "awareness": _when(aware, tz),
        "oral_report": ctx.get("oralReport") or TBC,
        "nature_gdpr": "",
        "dpo": contact_text,
        "consequences": (
            "Possible misuse of the personal data concerned, such as targeted phishing of the people affected "
            + TBC
            + "."
        ),
        "delay_reasons": "",
    }
    if regime == "gdpr_art33":
        n = facts.get("recordsAffectedEstimate")
        bodies["nature"] = (
            f"Unauthorised access to personal data held by {ent.get('name', TBC)} ({inc['reference']}). "
            f"Approximate number of records concerned: {f'{n:,}' if n else TBC}. Categories of people and data: {TBC}."
        )
    sections = [{"key": s["key"], "body": bodies.get(s["key"], TBC)} for s in template(notice_type)["sections"]]
    return {"sections": sections}
