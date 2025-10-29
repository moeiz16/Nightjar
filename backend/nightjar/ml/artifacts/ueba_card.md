# Model card: behaviour anomaly detector (UEBA) v1.0.0

**Purpose.** Notice when a person or a computer behaves unlike itself (new country or hosting network, refused
prompts, file shares or programs never used before, unusual volumes sent out) and put that finding on the stream as
a detection the triage colleague weighs.

**Method.** Per entity and 5-minute window: user features signIns, mfaDenied, distinctSrcIps, newCountry, hostingNetwork, offHours, processStarts, newProcesses, bytesOutMb, distinctDestinations, newShares, mailItemsRead; host features
processStarts, newProcesses, distinctUsers, newUsers, connectionsOut, newDestinations, serviceInstalls, offHours. Novelty features compare with the entity's own profile. Two signals: an Isolation Forest
per entity type (150 trees), rank-normalised against the baseline windows, and a robust z-score per feature against
the entity's median with a floored MAD (`max(1.4826·MAD, 0.5·std, absolute floor)`). A window is reported when the
rank score ≥ 0.95, some robust z ≥ 3 and a novelty or risk feature is present.

**Data.** 14 days of synthetic Halden activity (21491 events): days 0–6 build profiles, days 7–13
give 7340 user and 6614 host baseline windows.

**Evaluation.** Two held-out normal days with 18 injected attack sequences (phishing take-over, contractor data
transfer, ransomware precursor) touching 219 of 3704 entity windows.
precision@k = **1.0** (top k = 18 windows by score), sequence recall at the
operating threshold = **0.7778** (an injected sequence counts as found when at least one of its windows is
flagged), window precision = **1.0** (74 windows flagged), window recall =
0.3379 (many injected windows hold a single unremarkable event, such as the delivered email).

**Fairness (people are scored).** False-positive rate on normal user windows by group:

| group | false-positive rate |
|---|---|
| kind:contractor | 0.0 |
| kind:employee | 0.0 |
| kind:service | 0.0 |
| site:antwerp | 0.0 |
| site:melbourne | 0.0 |
| site:memphis | 0.0 |
| site:other | 0.0 |

Largest gap between sites: 0.0 (gate ≤ 0.05).

**Gates.** precision@k ≥ 0.8, recall ≥ 0.8, site gap ≤
0.05, window precision ≥ 0.7. Passed: **no**.

**Limitations.** Cold start: a new person has no profile, so novelty features stay at zero until a week of history
exists. Night-shift workers look unusual at first. Findings are evidence for a person to review, never a verdict.
