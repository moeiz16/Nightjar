"""US business days (Form 8-K) and calendar months (NIS2), including holidays, year ends and DST."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from nightjar.regimes import calendar as cal

BRUSSELS = ZoneInfo("Europe/Brussels")


@pytest.mark.parametrize(
    ("start", "n", "expected"),
    [
        (date(2025, 11, 19), 4, date(2025, 11, 25)),  # Wed → Tue, no holiday
        (date(2025, 11, 21), 4, date(2025, 11, 28)),  # Fri → skips the weekend and Thanksgiving (27 Nov)
        (date(2025, 11, 22), 4, date(2025, 11, 28)),  # a Saturday start counts from Monday
        (date(2025, 11, 26), 1, date(2025, 11, 28)),  # day before Thanksgiving
        (date(2025, 12, 22), 4, date(2025, 12, 29)),  # Christmas Day skipped
        (date(2025, 12, 31), 1, date(2026, 1, 2)),  # New Year's Day skipped across the year end
        (date(2026, 1, 16), 1, date(2026, 1, 20)),  # Fri → MLK Day Monday skipped
        (date(2026, 7, 2), 1, date(2026, 7, 6)),  # 3 Jul 2026 is the observed Independence Day
        (date(2025, 1, 8), 1, date(2025, 1, 10)),  # federal offices closed 9 Jan 2025
        (date(2025, 11, 20), 0, date(2025, 11, 20)),
    ],
)
def test_add_business_days(start: date, n: int, expected: date) -> None:
    assert cal.add_business_days(start, n) == expected


def test_add_business_days_rejects_negative() -> None:
    with pytest.raises(ValueError):
        cal.add_business_days(date(2025, 11, 20), -1)


@pytest.mark.parametrize(
    ("d", "is_bd"),
    [
        (date(2025, 11, 27), False),  # Thanksgiving
        (date(2025, 11, 28), True),  # day after Thanksgiving is a business day for EDGAR
        (date(2025, 11, 29), False),  # Saturday
        (date(2025, 11, 30), False),  # Sunday
        (date(2025, 11, 11), False),  # Veterans Day
        (date(2025, 11, 20), True),
    ],
)
def test_is_business_day(d: date, is_bd: bool) -> None:
    assert cal.is_business_day(d) is is_bd


def test_business_days_between_counts_half_open_range() -> None:
    assert cal.business_days_between(date(2025, 11, 20), date(2025, 11, 25)) == 3
    assert cal.business_days_between(date(2025, 11, 24), date(2025, 11, 28)) == 3  # 25, 26, 28
    assert cal.business_days_between(date(2025, 11, 25), date(2025, 11, 25)) == 0
    assert cal.business_days_between(date(2025, 11, 26), date(2025, 11, 20)) == 0


def test_holiday_table_covers_2025_and_2026_and_names_skipped_days() -> None:
    hols = cal.us_federal_holidays()
    assert sum(1 for d in hols if d.year == 2025) == 12 and sum(1 for d in hols if d.year == 2026) == 11
    assert hols[date(2025, 11, 27)] == "Thanksgiving Day"
    skipped = cal.skipped_holidays(date(2025, 11, 21), date(2025, 11, 28))
    assert skipped == [(date(2025, 11, 27), "Thanksgiving Day")]


def test_add_months_clamps_to_month_end() -> None:
    start = datetime(2026, 1, 31, 10, 0, tzinfo=BRUSSELS)
    assert cal.add_months(start, 1, BRUSSELS) == datetime(2026, 2, 28, 10, 0, tzinfo=BRUSSELS)
    leap = datetime(2028, 1, 31, 10, 0, tzinfo=BRUSSELS)
    assert cal.add_months(leap, 1, BRUSSELS).day == 29


def test_add_months_keeps_wall_clock_across_dst_end() -> None:
    # 20 Oct 2025 10:00 CEST (08:00 UTC) + 1 month = 20 Nov 2025 10:00 CET (09:00 UTC)
    start = datetime(2025, 10, 20, 8, 0, tzinfo=UTC)
    due = cal.add_months(start, 1, BRUSSELS)
    assert due.astimezone(UTC) == datetime(2025, 11, 20, 9, 0, tzinfo=UTC)
    assert due.astimezone(BRUSSELS).hour == 10


def test_add_months_rolls_over_the_year() -> None:
    start = datetime(2025, 12, 15, 9, 30, tzinfo=BRUSSELS)
    assert cal.add_months(start, 1, BRUSSELS) == datetime(2026, 1, 15, 9, 30, tzinfo=BRUSSELS)


def test_at_local_resolves_dst_by_date() -> None:
    ny = cal.NEW_YORK
    from datetime import time

    summer = cal.at_local(date(2025, 10, 31), time(17, 30), ny).astimezone(UTC)
    winter = cal.at_local(date(2025, 11, 6), time(17, 30), ny).astimezone(UTC)
    assert (summer.hour, winter.hour) == (21, 22)
