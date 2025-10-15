"""Demo clock: one switch for "now" across the API, workers, agents and the seed.

`NIGHTJAR_DEMO_TODAY=2025-11-20` shifts the server's idea of today to that date so seeded incidents, regulator
countdowns, business-day arithmetic, the shift log and displayed dates all read as of that day (walkthroughs
and portfolio screenshots). The shift is a whole number of days (demo date minus the real date when the
process starts), so separate processes that read the same setting agree, and the time of day still advances
normally. Unset (the default) means real time.

Rules:
* Business code calls `clock.now()` / `clock.today()`, never `datetime.now()` / `date.today()`.
* Security timers (session idle/absolute expiry, rate limits, approval-token expiry, cookie max-age) use
  `clock.real_now()`; they must not move with the demo date.
* NATS message timestamps are real; event `@timestamp` values produced by replay are `clock.now()`-based.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo

_offset = timedelta(0)
_installed = False


def real_now() -> datetime:
    """Wall-clock UTC time, never shifted."""
    return datetime.now(UTC)


def now() -> datetime:
    """Current UTC time (shifted when the demo clock is installed)."""
    return real_now() + _offset


def today(tz: tzinfo | None = None) -> date:
    """Today's date, in UTC unless a zone is given (e.g. ZoneInfo("Europe/Brussels"))."""
    current = now()
    return (current.astimezone(tz) if tz else current).date()


def offset() -> timedelta:
    return _offset


def is_shifted() -> bool:
    return _offset != timedelta(0)


def install(demo_today: date | str | None) -> timedelta:
    """Shift `now()` so that `today()` is `demo_today`. Idempotent; returns the offset applied."""
    global _offset, _installed
    if not demo_today:
        return timedelta(0)
    target = date.fromisoformat(demo_today) if isinstance(demo_today, str) else demo_today
    _offset = timedelta(days=(target - real_now().date()).days)
    _installed = True
    return _offset


def install_from_settings() -> timedelta:
    """Called once at process start (API lifespan, worker and agent entry points, the seed CLI)."""
    from nightjar.core.config import get_settings

    return install(get_settings().nightjar_demo_today)


def uninstall() -> None:
    """Back to real time (tests)."""
    global _offset, _installed
    _offset, _installed = timedelta(0), False
