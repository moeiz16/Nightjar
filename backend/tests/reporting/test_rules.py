"""Regime deadline rules: NIS2, SEC Form 8-K Item 1.05, SOCI and GDPR Art. 33, states and proposals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nightjar.regimes import rules
from nightjar.seed.data import HALDEN_ENTITIES

US, EU, AU = (next(e for e in HALDEN_ENTITIES if e["jurisdiction"] == j) for j in ("US", "EU", "AU"))
NY, BRU, MEL = ZoneInfo("America/New_York"), ZoneInfo("Europe/Brussels"), ZoneInfo("Australia/Melbourne")


def u(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# ── Form 8-K Item 1.05 ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("determined", "due"),
    [
        (u(2025, 11, 19, 21, 0), u(2025, 11, 25, 22, 30)),  # Wed 16:00 ET → Tue 25 Nov 17:30 EST
        (u(2025, 11, 20, 3, 0), u(2025, 11, 25, 22, 30)),  # 22:00 ET on the 19th: New York date counts, not UTC
        (u(2025, 11, 20, 5, 30), u(2025, 11, 26, 22, 30)),  # 00:30 ET on the 20th
        (u(2025, 11, 21, 15, 0), u(2025, 11, 28, 22, 30)),  # Fri: weekend + Thanksgiving skipped
        (u(2025, 10, 31, 18, 0), u(2025, 11, 6, 22, 30)),  # DST ends 2 Nov: due at 17:30 EST
        (u(2026, 3, 5, 15, 0), u(2026, 3, 11, 21, 30)),  # DST starts 8 Mar: due at 17:30 EDT
        (u(2025, 11, 22, 16, 0), u(2025, 11, 28, 22, 30)),  # Saturday determination
    ],
)
def test_sec_8k_due(determined: datetime, due: datetime) -> None:
    assert rules.sec_8k_due(determined) == due
    assert rules.sec_8k_due(determined).astimezone(NY).strftime("%H:%M") == "17:30"


def test_sec_basis_names_thanksgiving() -> None:
    d = u(2025, 11, 21, 15, 0)
    assert "Thanksgiving Day" in rules.sec_basis(d, rules.sec_8k_due(d))
    assert "skips" not in rules.sec_basis(u(2025, 11, 19, 21), rules.sec_8k_due(u(2025, 11, 19, 21)))


def test_business_days_left_reads_three_on_the_demo_date() -> None:
    due = rules.sec_8k_due(u(2025, 11, 19, 21, 0))
    assert rules.business_days_left(u(2025, 11, 20, 9, 0), due) == 3
    assert rules.business_days_left(u(2025, 11, 25, 12, 0), due) == 0


def test_sec_determination_starts_one_clock_in_new_york_time() -> None:
    specs = rules.deadlines_for_determination(
        "sec_8k", {"applies": True, "materialityDeterminedAt": u(2025, 11, 19, 21)}, US
    )
    assert [s.notice_type for s in specs] == ["sec_8k.item_105"] and specs[0].timezone == "America/New_York"


def test_sec_amendment_is_four_business_days_after_new_information() -> None:
    spec = rules.sec_amendment(u(2025, 11, 26, 14))
    assert spec.notice_type == "sec_8k.amendment" and spec.due_at == u(2025, 12, 3, 22, 30)  # 28 Nov, 1, 2, 3 Dec


# ── NIS2 ─────────────────────────────────────────────────────────────────────
def test_nis2_early_warning_and_notification_are_elapsed_hours() -> None:
    aware = u(2025, 11, 19, 3, 52)
    specs = rules.deadlines_for_determination("nis2", {"applies": True, "awareAt": aware}, EU)
    assert [(s.notice_type, s.due_at - aware) for s in specs] == [
        ("nis2.early_warning", timedelta(hours=24)),
        ("nis2.incident_notification", timedelta(hours=72)),
    ]
    assert specs[0].timezone == "Europe/Brussels"


def test_nis2_24h_window_across_dst_change_is_24_real_hours() -> None:
    aware = u(2025, 10, 25, 10, 0)  # 12:00 CEST; clocks go back at 03:00 on 26 Oct
    early = rules.deadlines_for_determination("nis2", {"applies": True, "awareAt": aware}, EU)[0]
    assert early.due_at - aware == timedelta(hours=24)
    assert early.due_at.astimezone(BRU).hour == 11  # wall clock shows 11:00 CET


def test_nis2_final_report_one_calendar_month_after_notification() -> None:
    submitted = u(2025, 10, 20, 8, 0)
    (spec,) = rules.deadlines_after_submission("nis2.incident_notification", submitted, channel="portal", entity=EU)
    assert spec.notice_type == "nis2.final_report" and spec.due_at.astimezone(UTC) == u(2025, 11, 20, 9, 0)


def test_nis2_final_report_after_handling_ends_following_a_progress_report() -> None:
    ended = u(2025, 12, 31, 12, 0)
    (spec,) = rules.deadlines_after_submission(
        "nis2.progress_report", u(2025, 12, 20), channel="portal", entity=EU, handling_ended_at=ended
    )
    assert spec.notice_type == "nis2.final_report" and spec.due_at.astimezone(BRU).date().isoformat() == "2026-01-31"
    assert rules.deadlines_after_submission("nis2.progress_report", u(2025, 12, 20), channel="portal", entity=EU) == []


def test_nis2_early_warning_submission_starts_nothing_new() -> None:
    assert rules.deadlines_after_submission("nis2.early_warning", u(2025, 11, 19), channel="portal", entity=EU) == []


# ── SOCI and GDPR ────────────────────────────────────────────────────────────
def test_soci_critical_is_12h_and_other_is_72h() -> None:
    aware = u(2025, 11, 19, 3, 52)
    crit = rules.deadlines_for_determination(
        "soci", {"applies": True, "awareAt": aware, "impactLevel": "significant"}, AU
    )
    other = rules.deadlines_for_determination(
        "soci", {"applies": True, "awareAt": aware, "impactLevel": "relevant"}, AU
    )
    assert (crit[0].notice_type, crit[0].due_at - aware) == ("soci.critical_report", timedelta(hours=12))
    assert (other[0].notice_type, other[0].due_at - aware) == ("soci.other_report", timedelta(hours=72))
    assert crit[0].timezone == "Australia/Melbourne"


def test_soci_12h_across_melbourne_dst_start() -> None:
    aware = u(2025, 10, 4, 20, 0)  # 06:00 AEST; DST starts 02:00 on 5 Oct
    spec = rules.deadlines_for_determination(
        "soci", {"applies": True, "awareAt": aware, "impactLevel": "significant"}, AU
    )[0]
    assert spec.due_at == u(2025, 10, 5, 8, 0) and spec.due_at.astimezone(MEL).hour == 19


@pytest.mark.parametrize(("ntype", "hours"), [("soci.critical_report", 84), ("soci.other_report", 48)])
def test_soci_oral_report_starts_written_record(ntype: str, hours: int) -> None:
    at = u(2025, 11, 19, 10)
    (spec,) = rules.deadlines_after_submission(ntype, at, channel="oral", entity=AU)
    assert spec.notice_type == "soci.written_record" and spec.due_at - at == timedelta(hours=hours)
    assert rules.deadlines_after_submission(ntype, at, channel="portal", entity=AU) == []


def test_gdpr_notification_is_72h() -> None:
    aware = u(2025, 11, 20, 2)
    (spec,) = rules.deadlines_for_determination("gdpr_art33", {"applies": True, "awareAt": aware}, EU)
    assert spec.notice_type == "gdpr_art33.notification" and spec.due_at - aware == timedelta(hours=72)


def test_negative_determination_starts_no_clock() -> None:
    for regime in rules.REGIME_IDS:
        assert rules.deadlines_for_determination(regime, {"applies": False}, EU) == []


def test_unknown_regime_is_rejected() -> None:
    with pytest.raises(ValueError):
        rules.deadlines_for_determination("dora", {"applies": True, "awareAt": u(2025, 1, 1)}, EU)


# ── Clock state ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("remaining_h", "window_h", "state"),
    [
        (30, 72, "on_track"),
        (18, 72, "due_soon"),
        (7, 12, "on_track"),
        (6, 12, "due_soon"),
        (0, 24, "overdue"),
        (-5, 24, "overdue"),
    ],
)
def test_clock_state_due_soon_is_quarter_window_or_six_hours(remaining_h: int, window_h: int, state: str) -> None:
    now = u(2025, 11, 20, 12)
    due = now + timedelta(hours=remaining_h)
    start = due - timedelta(hours=window_h)
    assert rules.clock_state(regime="nis2", starts_at=start, due_at=due, now=now, submitted=False) == state


def test_clock_state_submitted_and_not_required_win() -> None:
    now = u(2025, 11, 20, 12)
    args = {"regime": "nis2", "starts_at": now - timedelta(days=5), "due_at": now - timedelta(days=1), "now": now}
    assert rules.clock_state(**args, submitted=True) == "submitted"
    assert rules.clock_state(**args, submitted=False, required=False) == "not_required"


def test_sec_due_soon_is_one_business_day() -> None:
    due = rules.sec_8k_due(u(2025, 11, 19, 21))  # Tue 25 Nov

    def st(now):
        return rules.clock_state(regime="sec_8k", starts_at=u(2025, 11, 19, 21), due_at=due, now=now, submitted=False)

    assert st(u(2025, 11, 20, 14)) == "on_track"
    assert st(u(2025, 11, 22, 14)) == "on_track"  # Saturday: 24, 25 still ahead
    assert st(u(2025, 11, 24, 14)) == "due_soon"


# ── Proposals ────────────────────────────────────────────────────────────────
OUTAGE = {
    "maliciousSuspected": True,
    "affectedJurisdictions": ["EU", "US", "AU"],
    "serviceDisruption": "outage",
    "disruptionHours": 9,
    "criticalAssetImpact": "relevant",
    "crossBorder": True,
    "financialImpactEstimateEur": 750000,
    "personalDataInvolved": False,
}
PHISH = {
    "maliciousSuspected": True,
    "affectedJurisdictions": ["EU"],
    "serviceDisruption": "none",
    "criticalAssetImpact": "none",
    "personalDataInvolved": True,
    "recordsAffectedEstimate": 140,
    "crossBorder": False,
}


def test_proposals_for_the_ransomware_precursor() -> None:
    assert rules.propose("nis2", EU, OUTAGE, incident={})[0] == "yes"
    assert rules.propose("sec_8k", US, OUTAGE, incident={})[0] == "assess"
    assert rules.propose("soci", AU, OUTAGE, incident={})[0] == "yes"
    assert rules.propose("gdpr_art33", EU, OUTAGE, incident={})[0] == "no"


def test_proposals_for_the_phishing_incident() -> None:
    applies, reasons = rules.propose("nis2", EU, PHISH, incident={})
    assert applies == "no" and "disruption" in reasons[0]
    assert rules.propose("gdpr_art33", EU, PHISH, incident={})[0] == "assess"
    assert rules.propose("sec_8k", US, PHISH, incident={})[0] == "no"
    assert rules.propose("soci", AU, PHISH, incident={})[0] == "no"


def test_proposals_without_facts_ask_for_assessment_only_where_affected() -> None:
    assert rules.propose("nis2", EU, None, incident={"jurisdictions": ["EU"]})[0] == "assess"
    assert rules.propose("nis2", EU, None, incident={"jurisdictions": ["US"]})[0] == "no"


def test_entities_out_of_scope_are_never_proposed() -> None:
    assert rules.propose("nis2", US, OUTAGE, incident={})[0] == "no"
    assert rules.propose("sec_8k", EU, OUTAGE, incident={})[0] == "no"
    assert rules.entity_regimes(EU) == ["nis2", "gdpr_art33"] and rules.entity_regimes(US) == ["sec_8k"]


def test_notice_labels_cover_every_type() -> None:
    from nightjar.regimes.drafting import templates

    assert set(templates()) == set(rules.NOTICE_LABELS)
