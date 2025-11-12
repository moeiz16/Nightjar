# Data sources

Nightjar SOC runs fully offline on **bundled, synthetic or derived** data. The public downloads below add real
attack telemetry for evaluation and the full intel/rule catalogues. They go to git-ignored `data/` and are never
committed. Reachability, sizes and licences were checked against the live sources while setting up the project.

```bash
make data                                  # python scripts/fetch_data.py (idempotent, checksum-pinned)
python scripts/fetch_data.py --check       # HEAD-check every URL, download nothing
python scripts/fetch_data.py otrf kev      # selected groups
```

**Budget:** 56 MB transferred, ~152 MB on disk after extraction (cap 300 MB; per-file cap 50 MB, enforced by the
script). Pinned artefacts have SHA-256s in `scripts/data_pins.json`.

## Sources

| Source | What we take | Pin | Size | Licence | Used for |
|---|---|---|---|---|---|
| [OTRF Security Datasets](https://github.com/OTRF/Security-Datasets) | 9 small "atomic" scenarios (below), JSON lines / CSV | `master` (static since 2024) + SHA-256 | 2.3 MB zipped · 94 MB extracted | MIT | Real attack telemetry replayed through the pipeline (`make replay SCENARIO=otrf-<key>`); normaliser format coverage (Windows Security, Sysmon, PowerShell, CloudTrail, S3 access logs). Not part of the eval gates |
| [SigmaHQ rules](https://github.com/SigmaHQ/sigma) | `sigma_core.zip` from release **r2025-11-01**, filtered to 8 folders: 745 rules | release tag + SHA-256 | 1.4 MB zipped · 3.1 MB kept | Detection Rule License (DRL) 1.1 | Detection rules (curated active subset bundled, full subset optional) |
| [MITRE ATT&CK](https://github.com/mitre-attack/attack-stix-data) | `enterprise-attack-18.1.json` (STIX 2.1) | v18.1 + SHA-256 | 48 MB | ATT&CK Terms of Use (royalty-free; reproduce MITRE's copyright notice) | Technique and tactic names, descriptions, links; offline subset bundled |
| [CISA Known Exploited Vulnerabilities](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) | `known_exploited_vulnerabilities.json` | live feed (not pinned) | 1.8 MB | CC0 1.0 | KEV lookups for assets and indicators; offline subset bundled |

### OTRF scenarios (labels from OTRF metadata)

| Key | Scenario | ATT&CK | Events | Extracted |
|---|---|---|---|---|
| `lsass_mimikatz` | Empire Mimikatz LogonPasswords | T1003.001 | 6,026 | 43.5 MB |
| `lsass_comsvcs` | LSASS memory dump via comsvcs.dll | T1003.001 | 184 | 0.3 MB |
| `lsass_dumpert` | LSASS memory dump via direct syscalls (Dumpert) | T1003.001 | 118 | 0.2 MB |
| `ntds_shadow_copy` | NTDS.dit dump through a volume shadow copy | T1003.003 | 16,843 | 35.5 MB |
| `psexec_lateral` | Empire Invoke-PsExec (DCERPC svcctl) | T1021.002 | 4,348 | 12.7 MB |
| `schtask_remote` | Remote scheduled task creation | T1053.005 | 1,042 | 2.0 MB |
| `psh_http_listener` | PowerShell HTTP listener | T1059.001 | 110 | 0.2 MB |
| `aws_s3_exfil` | AWS cloud bank breach (EC2 proxy → S3) | T1078.004, T1530 | 103 | 0.1 MB |
| `aws_honeybucket` | AWS S3 honey-bucket access logs (CSV) | T1580 | 302 | 0.1 MB |

OTRF events carry the original lab hostnames and accounts (e.g. `WORKSTATION5`, `wardog`). The replay tool maps
them onto Halden Logistics entities with a published mapping (`backend/nightjar/workers/replay/otrf_map.json`) and
keeps the original values in `raw`. Ground-truth labels are per scenario window and stored in `eval/`, never on
events.

### Sigma subset (r2025-11-01)
Kept folders: `windows/process_creation` (557), `windows/builtin/security` (77), `windows/powershell/powershell_script`
(53), `windows/network_connection` (23), `windows/process_access` (16), `cloud/aws` (12), `cloud/azure/signin_logs`
(6), `cloud/m365` (1). All 745 parse and compile with pySigma 2.1's native matcher and the Nightjar field pipeline
(`scripts/smoke/sigma_smoke.py`). Throughput over the full subset is low (see ADR 0003), so the detector runs a
**curated active set** (125 rules relevant to Halden's log sources, bundled with attribution) and the rest is
opt-in.

## Bundled offline data (committed, small)

| Path | Content | Derived from | Licence handling |
|---|---|---|---|
| `backend/nightjar/detect/rules/sigmahq/*.yml` | 125 curated SigmaHQ rules, unmodified | SigmaHQ r2025-11-01 | DRL 1.1: original `author`, `id` and `references` kept; `LICENSE.DRL-1.1.txt` and a `README` naming the release alongside |
| `backend/nightjar/detect/rules/nightjar/*.yml` | 18 Nightjar Sigma rules (MFA push denials, external inbox forwarding, cloud key misuse, file-sync transfers, gateway session reuse, instruction-like log text and more) | original | MIT (this repository) |
| `backend/nightjar/intel/data/attack_v18_1.json` | 14 tactics and 691 techniques/sub-techniques: id, name, tactics, short description, URL (~215 KB) | ATT&CK v18.1 | MITRE copyright notice and licence text reproduced in `intel/data/ATTACK_LICENSE.txt` |
| `backend/nightjar/intel/data/kev_subset.json` | 479 KEV entries with `dateAdded` ≤ 2025-11-27: the vendors/products in the sample inventory plus the most recent (~220 KB) | CISA KEV | CC0 1.0 |
| `backend/nightjar/intel/data/sample_indicators.json` | Fictional indicator list ("sample feed"): documentation-range IPs, `.example` domains | synthetic | MIT |
| `backend/nightjar/workers/replay/halden-night.jsonl.gz` | Synthetic replay (5,798 events, 6 scenarios + background), Halden entities, relative timestamps | synthetic (patterns modelled on the OTRF scenarios above) | MIT |
| `backend/nightjar/regimes/data/us_federal_holidays.json` | US federal holidays (observed) 2025–2026 | public calendar | facts |
| `eval/scenarios/halden-night.labels.json`, `eval/scenarios/injection_corpus.json` | Ground truth for the replay; prompt-injection corpus (held-out days and injected UEBA anomalies are generated from fixed seeds by the training and eval scripts) | synthetic | MIT |

`INTEL_SOURCE=downloaded` / `RULES_SOURCE=downloaded` switch to the `data/` copies. The KEV lookups always hide
entries added after the demo date (`dateAdded > clock.today()`), so a demo set in November 2025 does not show
vulnerabilities catalogued later.

## Not used
Real customer logs, any personal data, paid threat-intel feeds, VirusTotal/Shodan lookups (they need accounts), and
large OTRF compound datasets (APT29/APT3 evaluations are hundreds of MB).
