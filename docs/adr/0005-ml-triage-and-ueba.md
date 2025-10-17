# ADR 0005: Classical ML for behaviour anomalies (UEBA) and alert triage

**Status:** accepted

## Context
Two problems are statistical, not rule-shaped: "is this person or machine behaving unlike itself?" and "is this
alert likely real or noise?". Both must run locally on CPU, be explainable to non-specialists, and be evaluated
with gates (STANDARDS §3b). Deep models and local language models are out of scope.

## Decision
**UEBA (unsupervised).** Per-entity 5-minute and daily windows of login, process and network features (sign-in
count, distinct hosts, off-hours share, new-country flag, failed/successful ratio, new process names, bytes out,
distinct destinations). Two signals: an **Isolation Forest** (scikit-learn) trained on the baseline period per
entity type, rank-normalised to 0..1, and a **robust z-score** per feature against the entity's own median and
MAD. Plain reasons come from the largest z contributions ("3.2 GB sent out; this account usually sends 40 MB").
Evaluation: precision@k and recall against anomalies injected into held-out replay days.

**Triage classifier (supervised).** Features per detection: rule level and historical precision, ATT&CK tactic
stage, entity criticality and privilege, UEBA score of the entity, burst size and host spread of the same rule,
time of day, known-benign process lineage, enrichment hits. Labels from the labelled replay scenarios (synthetic
and OTRF windows). **Time-based split** (train on earlier scenario days, test on later; no scenario leaks across).
Baseline **logistic regression**, challengers **random forest** and **LightGBM** tuned with **Optuna**; **isotonic
calibration**; **SHAP** top-3 reasons translated to plain language; thresholds chosen for a fixed recall on real
incidents (≥ 0.98) to measure **false-positive reduction vs rules alone**. **MLflow** local file store (`mlruns/`,
git-ignored) for runs and registry; champion/challenger promotion with CISO sign-off (`models:promote`); PSI drift
on features; model cards committed without run timestamps. No protected attributes exist in the feature set;
fairness by department/site is reported for the UEBA false-positive rate (people are being scored).

Verified with `scripts/smoke/store_ml_smoke.py` on Python 3.13 / macOS arm64 (LightGBM needs Homebrew `libomp`):
IsolationForest precision@20 = 1.00 on injected anomalies; robust z also 1.00 **once MAD has a floor** (without it,
binary and sparse features have MAD 0 and every non-zero value looks infinite, precision fell to 0.05); LightGBM,
logistic baseline, isotonic calibration and SHAP all run; peak memory ~430 MB for the whole smoke.

## Consequences
- The MAD floor (`max(MAD, 0.5·std)`) is part of the baseline definition and unit-tested.
- SHAP for LightGBM binary models returns one array in current versions (older versions returned a list); code
  handles both.
- Training runs one job at a time under the machine-wide heavy lock (`make train`).
- The triage agent cites the classifier's probability and reasons; disagreement between agent and model is shown,
  not hidden, and measured in eval.
