"""Public interface of detection rules (builder B). Called by A's rules endpoints."""

from __future__ import annotations

import uuid

from nightjar.contracts import RuleInfo


def list_rules(org_id: uuid.UUID) -> list[RuleInfo]:
    raise NotImplementedError


def get_rule(org_id: uuid.UUID, rule_id: str) -> tuple[RuleInfo, str] | None:
    """(info, rule YAML) or None."""
    raise NotImplementedError


async def set_rule_enabled(org_id: uuid.UUID, rule_id: str, *, enabled: bool) -> RuleInfo:
    """Per-org override; the detector reloads within 5 s (subject `nj.control.rules`)."""
    raise NotImplementedError
