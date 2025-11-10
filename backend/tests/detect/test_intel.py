"""Offline intel (contract Part 3, `intel.api`): ATT&CK, KEV hidden after the demo date, sample indicators."""

from __future__ import annotations

import pytest

from nightjar import clock
from nightjar.intel import api as intel


@pytest.mark.parametrize("tid", ["T1003.001", "t1003.001", "attack.t1003.001"])
def test_technique_lookup_normalises_ids(tid: str) -> None:
    ref = intel.attack_technique(tid)
    assert ref and ref.technique_id == "T1003.001" and ref.name == "OS Credential Dumping: LSASS Memory"


def test_unknown_or_invalid_technique() -> None:
    assert intel.attack_technique("T9999") is None and intel.attack_technique("drop table") is None


def test_technique_detail_for_the_api() -> None:
    d = intel.attack_technique_detail("T1566.002")
    assert d and d["url"].startswith("https://attack.mitre.org/") and d["description"]


@pytest.mark.parametrize(
    ("tactic", "label"),
    [
        ("credential-access", "Credential access"),
        ("defense-evasion", "Defence evasion"),
        ("command-and-control", "Command and control"),
        ("new-thing", "New thing"),
    ],
)
def test_tactic_labels_are_plain(tactic: str, label: str) -> None:
    assert intel.attack_tactic_label(tactic) == label


def test_kev_lookup_and_assets() -> None:
    hit = intel.kev_lookup("cve-2023-4966")
    assert hit and hit.vendor == "Citrix" and hit.ransomware_use == "Known"
    assert [h.cve_id for h in intel.kev_for_assets(["CVE-2023-4966", "CVE-2024-21762", "CVE-1999-0001"])] == [
        "CVE-2023-4966",
        "CVE-2024-21762",
    ]


def test_kev_entries_after_the_demo_date_are_hidden() -> None:
    clock.install("2023-10-01")  # before CISA added CVE-2023-4966 (2023-10-18)
    assert intel.kev_lookup("CVE-2023-4966") is None


def test_bundle_has_no_entries_after_the_project_window() -> None:
    assert all(v["dateAdded"] <= "2025-11-27" for v in intel._kev("bundled").values())


def test_indicators_from_the_sample_feed() -> None:
    assert intel.lookup_indicator("ip", "203.0.113.66").verdict == "malicious"
    assert intel.lookup_indicator("url", "https://halden-invoices.example/pay/x").verdict == "malicious"
    assert intel.lookup_indicator("ip", "192.0.2.1").verdict == "unknown"
    assert intel.lookup_indicator("cve", "CVE-2023-4966").kev is not None


def test_search_attack_is_bounded() -> None:
    assert 1 <= len(intel.search_attack("credential dumping", limit=50)) <= 10
