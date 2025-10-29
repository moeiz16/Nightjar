"""Lazily loaded models for the serving path (scorer, UEBA worker, agents)."""

from __future__ import annotations

from functools import lru_cache

from nightjar.ml.registry import ARTIFACTS
from nightjar.ml.ueba import UebaModel


@lru_cache(maxsize=1)
def ueba_model() -> UebaModel | None:
    if not (ARTIFACTS / "ueba_meta.json").exists():
        return None
    return UebaModel.load(ARTIFACTS)


def reload() -> None:
    from nightjar.ml import triage

    ueba_model.cache_clear()
    triage.champion.cache_clear()
