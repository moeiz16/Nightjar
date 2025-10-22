"""Calendar arithmetic for the incident clock: US business days (Form 8-K) and calendar months (NIS2 final
report), in the regime's own time zone. Hour-based windows (24 h, 72 h, 12 h) are elapsed time and live in
`rules.py`; they are deliberately not wall-clock arithmetic, so a DST change inside a window does not stretch
or shrink it.
"""

from __future__ import annotations

import calendar as _cal
import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

_DATA = Path(__file__).resolve().parent / "data"
NEW_YORK = ZoneInfo("America/New_York")


@lru_cache
def us_federal_holidays() -> dict[date, str]:
    raw = json.loads((_DATA / "us_federal_holidays.json").read_text(encoding="utf-8"))
    return {date.fromisoformat(h["date"]): h["name"] for h in raw["holidays"]}


def holiday_name(d: date) -> str | None:
    return us_federal_holidays().get(d)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in us_federal_holidays()


def add_business_days(start: date, n: int) -> date:
    """The n-th business day after `start` (start itself never counts, whatever day it is)."""
    if n < 0:
        raise ValueError("n must be non-negative")
    d, counted = start, 0
    while counted < n:
        d += timedelta(days=1)
        if is_business_day(d):
            counted += 1
    return d


def business_days_between(start: date, end: date) -> int:
    """Business days in the half-open range (start, end]; 0 when end <= start."""
    if end <= start:
        return 0
    return sum(1 for i in range(1, (end - start).days + 1) if is_business_day(start + timedelta(days=i)))


def skipped_holidays(start: date, end: date) -> list[tuple[date, str]]:
    """Federal holidays falling on weekdays in (start, end] (shown in the 8-K arithmetic)."""
    out = []
    d = start
    while d < end:
        d += timedelta(days=1)
        name = holiday_name(d)
        if name and d.weekday() < 5:
            out.append((d, name))
    return out


def at_local(d: date, t: time, tz: ZoneInfo) -> datetime:
    """A wall-clock time in `tz` (zoneinfo resolves DST for that date)."""
    return datetime.combine(d, t, tzinfo=tz)


def add_months(moment: datetime, months: int, tz: ZoneInfo) -> datetime:
    """Same wall-clock time `months` calendar months later in `tz`, clamped to the month's last day
    (31 January + 1 month = 28/29 February)."""
    local = moment.astimezone(tz)
    month_index = local.month - 1 + months
    year, month = local.year + month_index // 12, month_index % 12 + 1
    day = min(local.day, _cal.monthrange(year, month)[1])
    return datetime(year, month, day, local.hour, local.minute, local.second, tzinfo=tz)
