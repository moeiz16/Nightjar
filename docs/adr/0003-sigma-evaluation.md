# ADR 0003: Sigma evaluation with pySigma's native matcher and an ECS field pipeline

**Status:** accepted

## Context
Detections must come from open, reviewable rules (Sigma) mapped to MITRE ATT&CK, evaluated per event in a Python
worker. pySigma's classic backends convert rules into query languages (Splunk, Elastic, SQL) for a search engine;
we evaluate in-stream instead. Options: convert to SQL and query DuckDB per batch (adds latency, couples detection
to the hunting store's single writer), write our own condition evaluator, or use pySigma's matching module.

## Decision
Use **pySigma 2.1** for parsing and its **native in-memory matcher** (`sigma.matching.SigmaMatcher`), which
compiles each rule's condition tree to a Python callable and supports correlation rules (event count, value count,
temporal). Apply a Nightjar `ProcessingPipeline` with `FieldMappingTransformation` that maps Sigma's field names
onto our ECS-like schema (`Image` → `process.executable`, `CommandLine` → `process.command_line`, `ParentImage` →
`process.parent.executable`, `TargetImage` → `target.process.executable`, `EventID` → `event.code`, …). Events
are flattened to dotted keys before matching. ATT&CK ids come from rule tags (`attack.t1003.001` → `T1003.001`).

Verified with `scripts/smoke/sigma_smoke.py`: modifiers `endswith`, `contains|all`, `contains|windash`,
`wide|base64offset|contains`, `re`, `cidr`, numeric equality, `1 of selection_*`, `not filter` all behave as
expected on positive and negative events; a `value_count` correlation fires on a six-account spray within 10
minutes; **31k events/s** over 7 rules. Over the downloaded SigmaHQ r2025-11-01 subset, **745/745 rules parse and
compile**.

## Consequences and limits
- **Throughput across the full subset is low**: about 4.3 ms per event across 557 process-creation rules (mostly
  regex search), i.e. roughly 230 events/s on simple events and ~40 events/s on long encoded command lines. The
  detector therefore runs a **curated active set** (125 SigmaHQ rules matching Halden's sources plus 18 of
  Nightjar's own); eval gate: ≥ 2,000 events/s on the replay mix (measured: about 16,600 events/s). Optional later optimisation: a literal-substring
  pre-filter per rule.
- The matcher looks fields up by exact name in a flat dict and does not iterate list values: the adapter
  flattens nested objects and expands list fields (`related.ip`) into indexed and joined forms.
- Numbers must be typed consistently (`event.code` as int) because equality is type-strict.
- Correlation rules re-fire on every event past the threshold (2 matches for a 6-event spray at `gte: 5`); the
  correlator de-duplicates per group key and window.
- Rule YAML is code: the matcher compiles generated Python with `exec` (restricted builtins, values `repr`'d).
  Only rules from the bundled SigmaHQ release, Nightjar's own set and analyst-reviewed rules are loaded; there is
  no upload of arbitrary rules in v1.
- Bundled SigmaHQ rules keep their DRL 1.1 attribution (author, id, references) and licence text.
