"""Public interface of the models (builder B). Called by A's model and UEBA endpoints."""

from __future__ import annotations

import uuid

from nightjar.contracts import ModelInfo


def list_models() -> list[ModelInfo]:
    """Champion and challenger per model, with metrics and gate status (from committed model cards)."""
    raise NotImplementedError


def promote(name: str, version: str, *, promoted_by: uuid.UUID) -> ModelInfo:
    """Champion/challenger switch after `models:promote` sign-off (A audits `model.promoted`). Refuses a
    version whose eval gates failed."""
    raise NotImplementedError
