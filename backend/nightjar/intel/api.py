"""Public interface of offline intel (builder B). Used by A for display and by the intel tool server.

Bundled data (`intel/data/`, see docs/DATA.md): an ATT&CK v18.1 subset, a CISA KEV subset and a fictional
sample indicator feed. `INTEL_SOURCE=downloaded` reads ATT&CK and KEV from `data/` instead (`make data`).
KEV entries with `dateAdded` after `clock.today()` are hidden, so a demo dated November 2025 never shows
vulnerabilities catalogued later.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from nightjar import clock
from nightjar.contracts import AttackRef, IndicatorFinding, KevHit

DATA = Path(__file__).resolve().parent / "data"
TACTIC_ORDER = (
    "reconnaissance",
    "resource-development",
    "initial-access",
    "execution",
    "persistence",
    "privilege-escalation",
    "defense-evasion",
    "credential-access",
    "discovery",
    "lateral-movement",
    "collection",
    "command-and-control",
    "exfiltration",
    "impact",
)
_TACTIC_PLAIN = {
    "reconnaissance": "Reconnaissance",
    "resource-development": "Resource development",
    "initial-access": "Initial access",
    "execution": "Execution",
    "persistence": "Persistence",
    "privilege-escalation": "Privilege escalation",
    "defense-evasion": "Defence evasion",
    "credential-access": "Credential access",
    "discovery": "Discovery",
    "lateral-movement": "Lateral movement",
    "collection": "Collection",
    "command-and-control": "Command and control",
    "exfiltration": "Exfiltration",
    "impact": "Impact",
}
_TECH_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")


def _source() -> str:
    try:
        from nightjar.core.config import get_settings

        return get_settings().intel_source
    except Exception:  # pragma: no cover - settings unavailable in a bare script
        return "bundled"


@lru_cache(maxsize=2)
def _attack(source: str = "bundled") -> dict[str, Any]:
    """The ATT&CK subset. The full STIX bundle in `data/` only adds fields we do not display, so both sources
    read the bundled subset (rebuild it with `scripts/build_bundles.py attack`)."""
    return json.loads((DATA / "attack_v18_1.json").read_text())


@lru_cache(maxsize=2)
def _kev(source: str = "bundled") -> dict[str, dict[str, Any]]:
    path = DATA / "kev_subset.json"
    items: list[dict[str, Any]]
    if source == "downloaded":
        from nightjar.core.config import get_settings

        full = get_settings().data_dir / "kev" / "known_exploited_vulnerabilities.json"
        if full.exists():
            items = json.loads(full.read_text())["vulnerabilities"]
            return {v["cveID"].upper(): v for v in items}
    items = json.loads(path.read_text())["items"]
    return {v["cveID"].upper(): v for v in items}


@lru_cache(maxsize=1)
def _indicators() -> dict[tuple[str, str], dict[str, Any]]:
    items = json.loads((DATA / "sample_indicators.json").read_text())["items"]
    return {(i["type"], i["value"].lower()): i for i in items}


def normalise_technique_id(value: str) -> str | None:
    """`attack.t1003.001`, `t1003.001`, `T1003.001` → `T1003.001`."""
    v = value.strip().upper().removeprefix("ATTACK.")
    return v if _TECH_RE.match(v) else None


def attack_technique(technique_id: str) -> AttackRef | None:
    tid = normalise_technique_id(technique_id or "")
    if not tid:
        return None
    t = _attack(_source())["techniques"].get(tid)
    if not t:
        return None
    return AttackRef(technique_id=tid, name=t["name"], tactics=tuple(t["tactics"]))


def attack_technique_detail(technique_id: str) -> dict[str, Any] | None:
    """`{ techniqueId, name, tactics, description, url }` for GET /attack/techniques/{id}."""
    tid = normalise_technique_id(technique_id or "")
    if not tid:
        return None
    t = _attack(_source())["techniques"].get(tid)
    if not t:
        return None
    return {
        "techniqueId": tid,
        "name": t["name"],
        "tactics": list(t["tactics"]),
        "description": t["description"],
        "url": t["url"],
    }


def search_attack(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for tid, t in _attack(_source())["techniques"].items():
        hay = f"{tid} {t['name']} {' '.join(t['tactics'])}".lower()
        if all(word in hay for word in q.split()):
            out.append(attack_technique_detail(tid))
            if len(out) >= max(1, min(limit, 10)):
                break
    return [o for o in out if o]


def attack_tactic_label(tactic: str) -> str:
    """ "credential-access" → "Credential access" (plain wording for the UI)."""
    key = (tactic or "").strip().lower().replace("_", "-").replace(" ", "-")
    if key in _TACTIC_PLAIN:
        return _TACTIC_PLAIN[key]
    return key.replace("-", " ").capitalize()


def tactic_stage(tactic: str) -> int:
    """Kill-chain position (0 = reconnaissance … 13 = impact); -1 when unknown."""
    try:
        return TACTIC_ORDER.index(tactic)
    except ValueError:
        return -1


def _kev_hit(v: dict[str, Any]) -> KevHit:
    return KevHit(
        cve_id=v["cveID"].upper(),
        vendor=v["vendorProject"],
        product=v["product"],
        name=v["vulnerabilityName"],
        date_added=v["dateAdded"],
        ransomware_use="Known" if v.get("knownRansomwareCampaignUse") == "Known" else "Unknown",
    )


def kev_lookup(cve_id: str) -> KevHit | None:
    """Entries with `dateAdded` after `clock.today()` are hidden (the demo reads as of its date)."""
    key = (cve_id or "").strip().upper()
    if not _CVE_RE.match(key):
        return None
    v = _kev(_source()).get(key)
    if not v or v["dateAdded"] > clock.today().isoformat():
        return None
    return _kev_hit(v)


def kev_for_assets(cve_ids: list[str]) -> list[KevHit]:
    hits = [kev_lookup(c) for c in dict.fromkeys(cve_ids or [])]
    return [h for h in hits if h is not None]


def lookup_indicator(kind: str, value: str) -> IndicatorFinding:
    """Bundled sample feed (fictional). CVE ids are answered from KEV. Unknown values → verdict `unknown`."""
    kind = (kind or "").strip().lower()
    val = (value or "").strip()
    if kind == "cve":
        hit = kev_lookup(val)
        if hit:
            ransomware = " with known ransomware use" if hit.ransomware_use == "Known" else ""
            return IndicatorFinding(
                type="cve",
                value=hit.cve_id,
                verdict="malicious",
                source="CISA KEV",
                detail=f"Known exploited vulnerability in {hit.vendor} {hit.product}{ransomware}.",
                kev=hit,
            )
        return IndicatorFinding(
            type="cve", value=val.upper(), verdict="unknown", source="CISA KEV", detail="Not listed."
        )
    row = _indicators().get((kind, val.lower()))
    if kind == "url" and row is None:  # fall back to the URL's host
        host = re.sub(r"^[a-z]+://", "", val.lower()).split("/")[0]
        row = _indicators().get(("domain", host))
    if row:
        return IndicatorFinding(
            type=kind if kind in ("ip", "domain", "url", "hash") else "ip",  # type: ignore[arg-type]
            value=val,
            verdict=row["verdict"],
            source="sample threat feed",
            detail=row["detail"],
        )
    return IndicatorFinding(
        type=kind if kind in ("ip", "domain", "url", "hash") else "ip",  # type: ignore[arg-type]
        value=val,
        verdict="unknown",
        source="sample threat feed",
        detail="No match in the bundled feed.",
    )


def is_known_bad(kind: str, value: str) -> bool:
    return lookup_indicator(kind, value).verdict in ("malicious", "suspicious")
