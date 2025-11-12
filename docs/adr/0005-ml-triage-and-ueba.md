# ADR 0005: Classical ML for behaviour anomalies (UEBA) and alert triage

**Status:** accepted

## Context
Two problems are statistical, not rule-shaped: "is this person or machine behaving unlike itself?" and "is this
alert likely real or noise?". Both must run locally on CPU, be explainable to non-specialists, and be evaluated
with gates in CI. Deep models and local language models are out of scope.

## Decision
**UEBA (unsupervised).** Per-entity 5-minute windows of login, process and network features. For people: sign-ins,
refused MFA prompts, distinct source addresses, new country, hosting network, off-hours, process starts, new
processes, MB sent out, distinct destinations, new file shares and mail items read. For computers: process
starts, new processes, distinct and new users, outbound connections, new destinations, service installs and
off-hours. Novelty features compare with the entity's own profile. Two signals: an **Isolation Forest**
(scikit-learn, 150 trees) trained on the baseline period per entity type, rank-normalised to 0..1, and a
**robust z-score** per feature against the entity's own median and MAD. A window is reported when the rank score
is at least 0.95, some robust z is at least 3 and a novelty or risk feature is present. Plain reasons come from the largest z contributions ("3.2 GB sent out; this account usually sends 40 MB").
Evaluation: precision@k and recall against anomalies injected into held-out replay days.

**Triage classifier (supervised).** 20 features per detection: rule level and track record (precision on the
training days only), correlation-rule and source flags, ATT&CK tactic stage and count, asset criticality,
privileged and service accounts, the entity's UEBA score, burst size and host spread of the same rule, known-benign
process lineage, threat-intel and KEV hits, external addresses, several attack stages on the same entities, event
count and instruction-like text in the evidence. Labels come from 60 synthetic Halden days passed through the real
normaliser, detector and UEBA (randomised attack scenarios modelled on OTRF datasets, benign look-alikes and
background), with 3 % of training labels flipped to mimic analyst error. **Time-based split** by day (40 / 10 /
10; each scenario lives inside one day, so none leaks across splits).
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
- The MAD floor (`max(1.4826·MAD, 0.5·std, a per-feature absolute floor)`) is part of the baseline definition
  and unit-tested.
- SHAP for LightGBM binary models returns one array in current versions (older versions returned a list); code
  handles both.
- Training is a single CPU job (`make train`); the committed artefacts in `backend/nightjar/ml/artifacts/` are what
  the workers load, so a fresh checkout scores without retraining.
- The triage agent cites the classifier's probability and reasons; disagreement between agent and model is shown,
  not hidden, and measured in eval.
