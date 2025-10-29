"""Model registry for the API: the committed base (`ml/artifacts/registry.json`, written by `make train`) plus a
local overlay of promotions (`data/model_registry.json`, git-ignored) so a CISO sign-off at runtime never edits a
committed file. MLflow (local, `mlruns/`) keeps the full run history; this file is what the product serves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
BASE = ARTIFACTS / "registry.json"


def _overlay_path() -> Path:
    try:
        from nightjar.core.config import get_settings

        return get_settings().data_dir / "model_registry.json"
    except Exception:  # pragma: no cover
        return Path(__file__).resolve().parents[3] / "data" / "model_registry.json"


def write_base_registry(registry: dict[str, Any]) -> None:
    BASE.write_text(json.dumps(registry, indent=1, sort_keys=True) + "\n")


def load() -> dict[str, Any]:
    base: dict[str, Any] = json.loads(BASE.read_text()) if BASE.exists() else {}
    path = _overlay_path()
    if path.exists():
        try:
            overlay = json.loads(path.read_text())
        except json.JSONDecodeError:
            overlay = {}
        for name, o in overlay.items():
            entry = base.setdefault(name, {"versions": {}})
            if "champion" in o:
                entry["champion"] = o["champion"]
            for ver, v in o.get("versions", {}).items():
                entry["versions"].setdefault(ver, {}).update(v)
    return base


def save_overlay(name: str, champion: str, versions: dict[str, dict[str, Any]]) -> None:
    path = _overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[name] = {"champion": champion, "versions": versions}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def champion_version(name: str) -> str | None:
    return load().get(name, {}).get("champion")
