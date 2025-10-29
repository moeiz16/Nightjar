"""Alert-triage classifier at serving time (ADR 0005): LightGBM → isotonic calibration → band, with the top-3
SHAP contributions (LightGBM's built-in TreeSHAP, `pred_contrib=True`; no `shap` import on the hot path)
translated into plain-language reasons the triage agent cites.

Bands: `likely_real` at or above `threshold_real`; `likely_noise` below `threshold_noise` (chosen on validation
so that at least 98 % of real detections stay above it); `uncertain` in between. A likely-noise detection is
`suppressed` unless its rule or severity is on the never-suppress list (critical findings, impact techniques,
alerts from other security tools).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from nightjar.contracts import Detection, Reason, TriageScore
from nightjar.ml.features import FEATURE_LABELS, TRIAGE_FEATURES, reason_text

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
NEVER_SUPPRESS_TECHNIQUES = ("T1486", "T1490", "T1003")


@dataclass
class TriageModel:
    version: str
    booster: Any
    features: tuple[str, ...]
    iso_x: np.ndarray
    iso_y: np.ndarray
    threshold_noise: float
    threshold_real: float
    rule_priors: dict[str, float]
    meta: dict[str, Any]

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        return np.clip(np.interp(raw, self.iso_x, self.iso_y), 0.0, 1.0)

    def predict(self, rows: list[dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
        x = np.array([[r[f] for f in self.features] for r in rows], dtype=float)
        raw = self.booster.predict(x)
        contrib = self.booster.predict(x, pred_contrib=True)
        return self.calibrate(np.asarray(raw)), np.asarray(contrib)

    def band(self, p: float) -> str:
        if p >= self.threshold_real:
            return "likely_real"
        if p < self.threshold_noise:
            return "likely_noise"
        return "uncertain"

    def score(self, d: Detection, row: dict[str, float]) -> TriageScore:
        probs, contrib = self.predict([row])
        p = float(probs[0])
        band = self.band(p)
        never = (
            d.severity == "critical"
            or d.source == "external"
            or any(a.technique_id.split(".")[0] in NEVER_SUPPRESS_TECHNIQUES for a in d.attack)
        )
        reasons = self.reasons(row, contrib[0])
        return TriageScore(
            detection_id=d.id,
            probability=round(p, 4),
            band=band,  # type: ignore[arg-type]
            threshold=round(self.threshold_noise, 4),
            reasons=reasons,
            model_version=self.version,
            suppressed=band == "likely_noise" and not never,
        )

    def reasons(self, row: dict[str, float], contrib: np.ndarray) -> tuple[Reason, ...]:
        values = contrib[: len(self.features)]
        order = np.argsort(-np.abs(values))[:3]
        out = []
        for i in order:
            f = self.features[int(i)]
            c = float(values[int(i)])
            out.append(
                Reason(
                    feature=f,
                    value=row[f],
                    contribution=round(c, 4),
                    text=reason_text(f, row[f], c),
                    baseline=None,
                )
            )
        return tuple(out)


def load(directory: Path = ARTIFACTS, *, name: str = "triage") -> TriageModel:
    import lightgbm as lgb

    meta = json.loads((directory / f"{name}_meta.json").read_text())
    booster = lgb.Booster(model_file=str(directory / f"{name}_lgbm.txt"))
    cal = meta["calibration"]
    return TriageModel(
        version=meta["version"],
        booster=booster,
        features=tuple(meta["features"]),
        iso_x=np.asarray(cal["x"], dtype=float),
        iso_y=np.asarray(cal["y"], dtype=float),
        threshold_noise=float(meta["thresholdNoise"]),
        threshold_real=float(meta["thresholdReal"]),
        rule_priors={k: float(v) for k, v in meta.get("rulePriors", {}).items()},
        meta=meta,
    )


@lru_cache(maxsize=1)
def champion() -> TriageModel | None:
    """The promoted triage model (None until `make train` has produced artefacts)."""
    from nightjar.ml.registry import champion_version

    version = champion_version("triage")
    path = ARTIFACTS
    if not (path / "triage_meta.json").exists():
        return None
    model = load(path)
    if version and version != model.version:
        alt = path / "versions" / version
        if (alt / "triage_meta.json").exists():
            return load(alt)
    return model


def feature_labels() -> dict[str, str]:
    return {f: FEATURE_LABELS[f] for f in TRIAGE_FEATURES}
