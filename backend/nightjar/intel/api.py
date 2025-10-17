"""Public interface of offline intel (builder B). Used by A for display and by the intel tool server."""

from __future__ import annotations

from nightjar.contracts import AttackRef, KevHit


def attack_technique(technique_id: str) -> AttackRef | None:
    raise NotImplementedError


def attack_tactic_label(tactic: str) -> str:
    """ "credential-access" → "Credential access" (plain wording for the UI)."""
    raise NotImplementedError


def kev_lookup(cve_id: str) -> KevHit | None:
    """Entries with `dateAdded` after `clock.today()` are hidden (the demo reads as of its date)."""
    raise NotImplementedError


def kev_for_assets(cve_ids: list[str]) -> list[KevHit]:
    raise NotImplementedError
