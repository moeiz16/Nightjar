# Model card: alert triage classifier v1.0.0

**Purpose.** Score each detection with a calibrated probability that it reflects real attacker activity, so the
triage colleague can prioritise and a burst of known-benign alerts collapses into one likely-benign incident.
It never closes an incident on its own: the triage agent cites the score and its reasons, and people decide.

**Problem framing and metric choice.** Binary classification per detection (real vs noise). False negatives are
costly (a missed intrusion), so the operating point is the lowest score that still keeps at least 98 % of real
detections (chosen on validation); we then report how many false positives that removes compared with alerting on
every rule hit. PR-AUC is reported next to ROC-AUC because real detections are a minority.

**Data.** 2851 / 715 / 762
detections (train / validation / test) from 60 synthetic Halden Logistics days passed through the real normaliser,
Sigma detector and UEBA: randomised attack scenarios modelled on OTRF datasets, benign look-alikes (backup
agent maintenance, administrator deployments, travellers, honest prompt refusals, large legitimate uploads) and
background activity. Label = the detection contains an attacker's event. 3 % of training labels are flipped to
mimic analyst error.

**Leakage-safe split.** By day in time order (40 / 10 / 10 days). Each scenario instance lives inside a single
day, so no scenario contributes to more than one split. Rule track records (`rule_prior`) are computed on the
training days only.

**Models (test split).**

| model | ROC-AUC | PR-AUC | Brier | ECE | FP reduction | recall kept |
|---|---|---|---|---|---|---|
| logistic | 0.9986 | 0.9979 | 0.0198 | 0.0386 | 0.9185 | 1.0 |
| random forest | 0.9994 | 0.9991 | 0.0113 | 0.0417 | 0.9427 | 0.9968 |
| lightgbm uncalibrated | 0.9979 | 0.9962 | 0.0105 | 0.0359 | – | – |
| lightgbm | 0.9974 | 0.9926 | 0.0084 | 0.0043 | 0.9802 | 0.9968 |
| rules alone | – | – | – | – | 0.0 | 1.0 |
| severity cutoff medium | – | – | – | – | 0.5683 | 0.987 |

LightGBM is tuned with Optuna (25 trials, validation PR-AUC
0.9995) and calibrated with isotonic regression on validation. Thresholds: noise below
0.2000, likely real from 0.8500.

**Explanations.** TreeSHAP contributions (LightGBM `pred_contrib`). The three largest per detection become
plain-language reasons ("Started by known maintenance software", "The same alert appeared on 12 computers at once").
Global importance (mean |SHAP|, test):

| feature | mean abs SHAP |
|---|---|
| This rule's track record | 1.851 |
| Started by known maintenance software | 0.5487 |
| Privileged account involved | 0.4688 |
| Several attack stages on the same entities | 0.458 |
| How unusual the person or computer is behaving | 0.2524 |
| Importance of the affected computer | 0.1894 |
| Threat intelligence match | 0.1393 |
| Same alert firing many times | 0.1353 |
| Same alert on many computers at once | 0.1235 |
| Number of events behind the alert | 0.0581 |

**Fairness.** No attribute about people (age, gender, nationality, department) is a feature; entity criticality and
privilege describe systems and access rights. People-level scoring is the UEBA model's job and its false-positive
rate by site and staff type is reported in that model's card.

**Monitoring.** PSI per feature, train vs test (highest: ueba_entity_score 0.1117, multi_stage_entities 0.0704, rule_prior 0.0495, service_account 0.043, benign_lineage 0.0359); the eval re-computes PSI on the halden-night replay.
Champion/challenger promotion requires the gates below and a CISO sign-off (`models:promote`).

**Gates.** ROC-AUC ≥ 0.9, PR-AUC ≥ 0.85, Brier ≤ 0.1,
ECE ≤ 0.08, FP reduction ≥ 0.5
at recall ≥ 0.95.
Passed: **yes**.

**Limitations.** Trained on synthetic scenarios: it learns the patterns we simulated, including how benign
maintenance looks in this estate. Real deployments need labelled history from the customer before the champion is
trusted to suppress; until then it only orders the queue.
