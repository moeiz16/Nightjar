"""Replay scenarios: the bundled `halden-night` (synthetic, committed gzip) and downloaded OTRF datasets
(`data/otrf/*.json`, mapped onto Halden entities with `otrf_map.json`).

Re-timing: bundle offsets are seconds from `night` (00:00 Europe/Brussels). `night_anchor()` follows API contract
Part 6 — the most recent 00:00 Brussels such that now ≥ night + 09:00 — so after 09:00 the replay reads as "last
night". OTRF files are shifted so they end at `now`.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nightjar import clock
from nightjar.contracts import ScenarioInfo
from nightjar.ingest.common import parse_ts
from nightjar.workers.replay.generator import stamp

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "halden-night.jsonl.gz"
OTRF_MAP = HERE / "otrf_map.json"
BRUSSELS = ZoneInfo("Europe/Brussels")
OTRF_TITLES = {
    "lsass_comsvcs": "LSASS memory dump via comsvcs.dll (OTRF)",
    "lsass_dumpert": "LSASS memory dump via direct system calls (OTRF)",
    "lsass_mimikatz": "Mimikatz credential dump (OTRF)",
    "ntds_shadow_copy": "Directory database copied through a shadow copy (OTRF)",
    "psexec_lateral": "PsExec lateral movement (OTRF)",
    "schtask_remote": "Remote scheduled task creation (OTRF)",
    "psh_http_listener": "PowerShell HTTP listener (OTRF)",
    "aws_s3_exfil": "Cloud storage data theft (OTRF)",
}


@dataclass(frozen=True, slots=True)
class ReplayLine:
    seq: int
    segment: str
    t: float
    fmt: str
    payload: dict[str, Any]


def night_anchor(now: datetime | None = None) -> datetime:
    now = (now or clock.now()).astimezone(BRUSSELS)
    night = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now < night + timedelta(hours=9):
        night -= timedelta(days=1)
    return night


@lru_cache(maxsize=1)
def bundle() -> tuple[ReplayLine, ...]:
    if not BUNDLE.exists():
        return ()
    out = []
    with gzip.open(BUNDLE, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            out.append(ReplayLine(d["seq"], d["segment"], float(d["t"]), d["format"], d["payload"]))
    return tuple(out)


def halden_night(
    anchor: datetime | None = None, *, time_order: bool = False
) -> Iterator[tuple[ReplayLine, datetime, dict[str, Any]]]:
    """(line, timestamp, stamped payload) in bundle order (segments in incident-reference order) or time order."""
    anchor = (anchor or night_anchor()).astimezone(UTC)
    lines = sorted(bundle(), key=lambda line: line.t) if time_order else bundle()
    for line in lines:
        when = anchor + timedelta(seconds=line.t)
        yield line, when, stamp(line.fmt, line.payload, when)


def _otrf_dir() -> Path:
    from nightjar.core.config import get_settings

    return get_settings().data_dir / "otrf"


def otrf_available() -> list[str]:
    d = _otrf_dir()
    return [k for k in OTRF_TITLES if (d / f"{k}.json").exists()]


@lru_cache(maxsize=1)
def otrf_map() -> dict[str, Any]:
    return json.loads(OTRF_MAP.read_text()) if OTRF_MAP.exists() else {"hosts": {}, "users": {}}


def _map_value(value: Any, table: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    low = value.lower()
    for src, dst in table.items():
        if src.lower() in low:
            return value.replace(src, dst).replace(src.upper(), dst).replace(src.lower(), dst)
    return value


def otrf_events(
    key: str, *, end: datetime | None = None, limit: int = 20_000
) -> Iterator[tuple[datetime, dict[str, Any]]]:
    """OTRF events mapped onto Halden entities and shifted to end at `end` (originals kept under `_otrf`)."""
    path = _otrf_dir() / f"{key}.json"
    rows = []
    with path.open() as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            if line.strip():
                rows.append(json.loads(line))
    times = []
    for r in rows:
        try:
            times.append(parse_ts(r.get("@timestamp") or r.get("TimeCreated") or r.get("eventTime")))
        except ValueError:
            times.append(None)
    known = [t for t in times if t]
    if not known:
        return
    shift = (end or clock.now()) - max(known)
    m = otrf_map()
    for r, t in zip(rows, times, strict=True):
        if t is None:
            continue
        mapped = {
            k: _map_value(v, m["hosts"])
            if k in ("Hostname", "Computer", "WorkstationName")
            else _map_value(v, m["users"])
            if k in ("SubjectUserName", "TargetUserName", "User")
            else v
            for k, v in r.items()
        }
        mapped["_otrf"] = {k: r[k] for k in ("Hostname", "SubjectUserName", "TargetUserName", "User") if k in r}
        new_t = t + shift
        for key_ in ("@timestamp", "TimeCreated", "eventTime"):
            if key_ in mapped:
                mapped[key_] = new_t.isoformat()
        mapped.pop("TimeCreated", None)
        mapped["@timestamp"] = new_t.isoformat()
        yield new_t, mapped


def scenarios() -> list[ScenarioInfo]:
    lines = bundle()
    out = []
    if lines:
        span = (max(x.t for x in lines) - min(x.t for x in lines)) / 60
        out.append(
            ScenarioInfo(
                "halden-night", "Halden Logistics: one night (sample data)", len(lines), round(span, 1), 6, "synthetic"
            )
        )
    d = _otrf_dir()
    for key in otrf_available():
        n = sum(1 for line in (d / f"{key}.json").open() if line.strip())
        out.append(ScenarioInfo(f"otrf-{key}", OTRF_TITLES[key], n, 0.0, 1, "otrf"))
    return out


def scenario_ids() -> set[str]:
    return {s.id for s in scenarios()}
