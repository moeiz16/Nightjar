# Nightjar SOC API contract (v1)

The binding reference for Nightjar SOC's module boundaries. The web app (builder C) builds against Part 1; the
platform (builder A) implements it. Part 2 is the data model, Part 3 the Python interfaces between the platform
(A) and the pipeline (B), Part 4 the event backbone, Part 5 the agents and tool servers, Part 6 the demo seed,
Part 7 module ownership, Part 8 project conventions.

Changing a shape here means changing it in the same commit as the code. `backend/nightjar/contracts.py` and the
`*/api.py` stubs are the code half of Part 3.

---

# Part 1: HTTP API

## 1.1 Conventions

- Base path **`/api/v1`**. The web app (SvelteKit on :3030) proxies `/api` to the API on :8030, so the browser
  sees one origin and cookies just work.
- JSON is **camelCase** on the wire. Maps that keep their keys as written: `raw` (original event payload),
  `fields` (matched detection fields), `detail` (agent step detail), `features` (model feature ids), `params`.
- Ids are UUID strings, except event ids (`eventId`, a 32-hex content hash) and incident references (`NJ-1042`).
  Timestamps are ISO-8601 with offset, always UTC from the API (`2025-11-20T03:12:04+00:00`); the web app shows
  them in the viewer's zone and, for regulator clocks, also in the regime's zone. Calendar dates `YYYY-MM-DD`.
  Countries ISO-3166 alpha-2. Durations in seconds (`…Seconds`) unless named otherwise.
- "Today" and "now" come from the server clock (`clock.now()`), which `NIGHTJAR_DEMO_TODAY` can shift. `GET
  /auth/me` returns `clock.today` so the web app never trusts the browser date for business logic.
- Every response carries `X-Request-ID` (echoed when the client sends a valid one). API responses are
  `Cache-Control: no-store`. The SSE stream sends `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`.
- Anything that came from a log (event messages, command lines, email subjects, file names, user agents, agent
  quotes of them) is **untrusted text**: the API returns it as `{ "text": "...", "flags": [...] }`
  (`UntrustedText`) and the web app renders it as plain text only (no HTML, no markdown, no autolinks). A
  non-empty `flags` list is shown as "This text contained instructions and was treated as data only".

### Errors: RFC 7807
Every error is `application/problem+json`:
```json
{ "type": "https://nightjar-soc.dev/problems/validation_error", "title": "Validation failed", "status": 422,
  "detail": "One or more fields are invalid.", "code": "validation_error",
  "errors": [{ "field": "contacts.0.email", "message": "Enter a valid email address." }], "requestId": "…" }
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | malformed JSON / query |
| 401 | `not_authenticated`, `session_expired`, `invalid_credentials` | no or ended session; wrong email/password (generic) |
| 401 | `invalid_ingest_key` | missing/revoked `X-Nightjar-Key` on ingest endpoints |
| 403 | `forbidden` | permission missing (message never reveals other tenants' data) |
| 403 | `csrf_failed` | unsafe method without a valid `X-CSRF-Token` |
| 403 | `no_active_org` | signed in but no active organisation |
| 404 | `not_found` | unknown id **or another org's id** (never 403 for cross-tenant ids) |
| 409 | `already_decided`, `conflict`, `stale_version`, `clock_not_started`, `already_submitted` | state conflicts (per endpoint) |
| 409 | `self_approval` | the person who proposed/requested an action cannot be its only approver |
| 410 | `expired` | invitation link or approval past its expiry |
| 413 | `payload_too_large` | body over 1 MB (JSON ingest) or `MAX_UPLOAD_MB` (file replay) |
| 415 | `unsupported_media_type` | upload type not allowed |
| 422 | `validation_error`, `weak_password`, `invalid_transition`, `invalid_query`, `target_not_in_incident` | field errors |
| 429 | `rate_limited` | with `Retry-After` seconds |
| 500 | `internal_error` | logged with the request id |
| 503 | `intake_full`, `unavailable`, `agents_unavailable` | event intake full (backpressure) or a dependency down, with `Retry-After` |

### Pagination
List endpoints marked *(paged)* accept `?page=1&pageSize=25` (max 200) and return
`{ "items": [...], "page": 1, "pageSize": 25, "total": 83, "totalPages": 4 }`. Filters are query parameters.
Default order is stated per endpoint. Filters named `status` also accept `all`.

### Rate limits
Sliding per minute, per IP (per ingest key for ingest): `auth` 10 (login, signup, refresh, invitation accept),
`ingest` 600 requests (each up to 500 events), `api` 300. Health, metrics and the SSE stream are exempt. Past the
limit: `429 rate_limited` with `Retry-After`. Sign-in also pauses one email for 15 minutes after 5 failures.

## 1.2 Auth: session cookie + CSRF

- `nj_session`: opaque random token, httpOnly, `SameSite=Lax`, `Secure` in production; stored server-side as an
  HMAC. **Idle expiry 30 min, absolute 12 h** (real wall clock, never the demo clock).
- `nj_csrf`: readable cookie. Echo it in **`X-CSRF-Token`** on every POST/PUT/PATCH/DELETE (also on login and
  signup). `GET /auth/csrf` sets it.
- Flow: `GET /auth/csrf` → `POST /auth/login` → use the app. The client calls `POST /auth/refresh` at most every
  5 minutes while the user is active (rotates the token, slides idle expiry). On `401 session_expired` the app
  goes to sign-in and returns to the current route afterwards.
- Passwords: Argon2id; minimum 12 characters, not common, not containing the email name (`422 weak_password`).
- Login failures are generic (`401 invalid_credentials`). All auth events are audited.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/auth/csrf` | – | `204` + `nj_csrf` cookie |
| POST | `/auth/signup` | `{ email, password, fullName, orgName }` | `201 Me` (creates the org and an `admin` membership) |
| POST | `/auth/login` | `{ email, password }` | `Me` |
| POST | `/auth/logout` | – | `204` |
| POST | `/auth/refresh` | – | `{ expiresAt, absoluteExpiresAt }` |
| GET | `/auth/me` | – | `Me` |
| POST | `/auth/switch-org` | `{ orgId }` | `Me` |
| POST | `/auth/password` | `{ currentPassword, newPassword }` | `204` (other sessions ended) |

```text
User       = { id, email, fullName, title, avatarColor, createdAt }
OrgSummary = { id, name, slug, role, roleLabel }
Org        = { id, name, slug, timezone, profile: OrgProfile, settings: OrgSettings, isSample, createdAt }
Me         = { user: User, activeOrg: Org | null,
               membership: { role, roleLabel, permissions: string[] } | null,
               orgs: OrgSummary[], session: { expiresAt, absoluteExpiresAt },
               clock: { today: "YYYY-MM-DD", demo: boolean } }
```

## 1.3 Roles and permissions

Source of truth: `backend/nightjar/core/permissions.py` (unit-tested). Labels: admin "Administrator", executive
"CISO / executive", responder "Incident responder", analyst "Security analyst", auditor "Auditor".

| Permission | admin | executive | responder | analyst | auditor |
|---|:-:|:-:|:-:|:-:|:-:|
| `watch:read`, `exec:read`, `incidents:read`, `investigations:read`, `actions:read`, `reporting:read`, `settings:read`, `members:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `events:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `detections:read`, `rules:read`, `hunting:read`, `ueba:read` | ✓ | | ✓ | ✓ | ✓ |
| `models:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `events:ingest` | ✓ | | | ✓ | |
| `rules:manage` | | | | ✓ | |
| `incidents:write`, `investigations:run`, `actions:propose`, `hunting:write` | | | ✓ | ✓ | |
| `actions:approve` | | | ✓ | | |
| `actions:approve_crown_jewel` | | ✓ | | | |
| `notices:draft` | | ✓ | ✓ | ✓ | |
| `reporting:assess`, `notices:approve`, `notices:submit`, `models:promote` | | ✓ | | | |
| `reports:generate` | ✓ | ✓ | | | |
| `settings:manage` | ✓ | ✓ | | | |
| `audit:read` | ✓ | ✓ | | | ✓ |
| `ingest_keys:manage`, `members:manage`, `org:admin` | ✓ | | | | |

**Separation of duties.** The admin runs the platform but cannot approve response actions or notices. Destructive
actions need a responder; on a crown-jewel asset or a privileged identity they also need an executive (two-person
rule). Nobody may be the only approver of an action they requested (`409 self_approval`). Regulatory
determinations (significance, materiality, impact level) and notice sign-off belong to the executive. The auditor
reads everything, changes nothing.

## 1.4 Organisation, members, invitations, ingest keys

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/org` | `settings:read` | `Org` |
| PATCH | `/org` | `org:admin` | `{ name?, timezone? }` → `Org` |
| GET | `/org/members` | `members:read` | `{ items: Member[] }` sorted by name |
| PATCH | `/org/members/{id}` | `members:manage` | `{ role }`; the last admin cannot be demoted (`409 conflict`) |
| DELETE | `/org/members/{id}` | `members:manage` | `204`; ends that user's sessions for this org |
| GET | `/org/invitations` | `members:manage` | pending invitations |
| POST | `/org/invitations` | `members:manage` | `{ email, role }` → `201 Invitation` (mock email; the link is shown once in the response as `acceptUrl`) |
| DELETE | `/org/invitations/{id}` | `members:manage` | revoke |
| GET | `/invitations/{token}` | public | `{ orgName, email, role, roleLabel, expiresAt, accountExists }` or `410 expired` |
| POST | `/invitations/{token}/accept` | public (+CSRF) | `{ fullName?, password? }` → `Me` |
| GET | `/org/ingest-keys` | `ingest_keys:manage` | `IngestKey[]` (prefix only) |
| POST | `/org/ingest-keys` | `ingest_keys:manage` | `{ name, source: "webhook"\|"syslog" }` → `201 { key: IngestKey, secret }` (`secret` shown once) |
| DELETE | `/org/ingest-keys/{id}` | `ingest_keys:manage` | revoke |

```text
Member     = { id, user: User, role, roleLabel, joinedAt }
Invitation = { id, email, role, roleLabel, invitedBy: string, expiresAt, acceptUrl? }
IngestKey  = { id, name, prefix, source, createdAt, lastUsedAt, revokedAt }
OrgProfile = { entities: LegalEntity[], crownJewelTags: string[] }
LegalEntity = {
  id: string, name: string, jurisdiction: "EU"|"US"|"AU", country: string, timezone: string,
  nis2?: { applies: boolean, sector: string, subsector?: string, entityType: "essential"|"important",
           memberState: string, authority: string, authorityName: string },
  sec?:  { registrant: boolean, filerType: "domestic", cik?: string, ticker?: string,
           disclosureCommittee: string[] },
  soci?: { responsibleEntity: boolean, assetClasses: string[], assets: string[] },
  gdpr?: { controller: boolean, leadAuthority: string, leadAuthorityName: string }
}
OrgSettings = { regimeContacts: RegimeContacts, crownJewelAssetTags: string[], autoInvestigate: boolean,
                weeklyBriefDay: "monday"|…|"sunday" }
```

## 1.5 Event ingest

Three ways in. All of them publish to the raw stream (Part 4) and return before normalisation; the event appears
in search and the event river once the pipeline has processed it (normally under 2 seconds).

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| POST | `/ingest/events` | `X-Nightjar-Key` (webhook) **or** session + `events:ingest` | `{ events: object[] }` (1–500, ≤ 256 KB each, ≤ 1 MB total) or one object; optional `?format=ecs\|winlog\|sysmon\|cloudtrail\|okta\|m365\|nightjar` | `202 IngestAck` |
| POST | `/ingest/syslog` | `X-Nightjar-Key` (syslog) | `application/x-ndjson`: one JSON object per line (syslog-style JSON: `{ "@timestamp"\|"timestamp", "host", "program", "severity", "message", ... }`), ≤ 1 MB | `202 IngestAck` |
| POST | `/ingest/files` | session + `events:ingest` | multipart `file` (`.json`, `.jsonl`, `.ndjson`, `.csv` CloudTrail/S3 logs; `.zip` with one of those; ≤ `MAX_UPLOAD_MB`), `format?`, `speed?` (1–600, default 60 = one hour per minute), `retime?` (bool, default true: shift timestamps so the file ends "now") | `202 { job: Job, preview: { detectedFormat, sampleEvents: NormalizedEventDoc[3], rejected: number } }` |
| GET | `/ingest/scenarios` | `events:ingest` | – | `ScenarioInfo[]` (bundled `halden-night`, plus downloaded OTRF scenarios when present) |
| POST | `/ingest/scenarios/{id}/replay` | `events:ingest` | `{ speed?: number }` | `202 { job: Job }` |

```text
IngestAck = { accepted, duplicates, rejected, firstSeq, lastSeq, errors: string[] }   # errors: "3: missing timestamp"
ScenarioInfo = { id, title, events, durationMinutes, incidentsExpected, source: "synthetic"|"otrf" }
```
Backpressure: when the raw stream is full or the bus is offline the API answers `503 intake_full` with
`Retry-After` (seconds); senders retry. Duplicates (same `dedupeKey`, or same payload hash within 2 minutes) are
acknowledged as `duplicates`, not stored twice.

## 1.6 Normalised events (ECS-like) and search

`NormalizedEvent` (contracts.py) on the wire:
```text
NormalizedEventDoc = {
  eventId, timestamp, ingestedAt, dataset, kind: "event"|"alert"|"signal",
  category: string[], type: string[], action, outcome: "success"|"failure"|"unknown", severity: number|null,
  message: UntrustedText,
  host: { name, ip: string[], os, id } | null,
  user: { name, domain, email, id } | null,
  source: { ip, port, geoCountry, asOrg } | null, destination: (same) | null,
  process: { name, executable, pid, commandLine, parentName, parentExecutable, hashSha256 } | null,
  cloud: { provider, accountId, region, service, api, accessKeyId } | null,
  email: { sender, recipients, subject, attachmentNames, urls, verdict } | null,
  url, filePath, fileHashSha256, relatedIps, relatedUsers, relatedHosts, labels, untrustedFlags,
  raw?: object            # only on GET /events/{id}
}
UntrustedText = { text, field, flags: string[], truncated: boolean }
```
Datasets: `windows.security`, `windows.sysmon`, `windows.powershell`, `aws.cloudtrail`, `aws.s3access`,
`okta.system`, `m365.audit`, `mail.gateway`, `vpn.syslog`, `edr.alert`, `nightjar.synthetic`. Normaliser field
mapping per format is builder B's (`backend/nightjar/ingest/formats/*.py`); the Sigma field mapping is in ADR 0003.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/events` *(paged)* | `events:read` | `?from=&to=&q=&dataset=&host=&user=&ip=&action=&outcome=` (structured filters only; `q` is a contains match on message/command line) → `NormalizedEventDoc[]` newest first (hunting store) |
| GET | `/events/{eventId}` | `events:read` | full doc with `raw` |
| GET | `/events/river` | `watch:read` | `?after=<eventId>&limit=50` → `{ lines: RiverLine[], eventsPerMinute }`, plain-language lines grouped by minute |

```text
RiverLine = { eventId, at, text, dataset, severity: Severity|null, anomaly: boolean, incidentId: string|null }
```
`text` is templated by the platform from the event's action/outcome/entities ("Sign-in failed for a.ng from
203.0.113.50"), never copied from the untrusted message.

## 1.7 Detections and rules

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/detections` *(paged)* | `detections:read` | `?from=&to=&severity=&source=sigma\|ueba\|external&band=likely_real\|uncertain\|likely_noise&incidentId=&ruleId=` newest first |
| GET | `/detections/{id}` | `detections:read` | `DetectionDoc` with `evidence` |
| GET | `/rules` | `rules:read` | `RuleInfo[]` `?q=&source=&enabled=&tactic=` |
| GET | `/rules/{id}` | `rules:read` | `{ rule: RuleInfo, yaml }` |
| PATCH | `/rules/{id}` | `rules:manage` | `{ enabled }` → `RuleInfo` (audited `rule.toggled`; applied within 5 s) |
| GET | `/attack/techniques/{id}` | `rules:read` | `{ techniqueId, name, tactics, description, url }` (ATT&CK v18.1 offline) |

```text
Severity = "info"|"low"|"medium"|"high"|"critical"
AttackRef = { techniqueId, name, tactics: string[] }
Reason = { feature, value, baseline, contribution, text }
TriageScoreDoc = { probability, band, threshold, reasons: Reason[3], modelVersion, suppressed }
DetectionDoc = { id, source, ruleId, ruleTitle, severity, confidence, attack: AttackRef[],
                 entities: { hosts, users, ips, cloudAccounts, accessKeys, hashes, emails },
                 firstSeen, lastSeen, eventCount, summary, fields, triage: TriageScoreDoc|null,
                 incidentId: string|null, evidence?: EventSnapshotDoc[] }
EventSnapshotDoc = { eventId, timestamp, dataset, action, summary: UntrustedText, host, user, fields }
RuleInfo = { id, title, source: "sigma"|"ueba"|"nightjar", level, status, logsource, attack: AttackRef[],
             enabled, hits7d, precisionEstimate: number|null, origin }
```

## 1.8 Incidents

Stages run **Triage → Investigate → Contain → Report** (`stage`); status is the working state.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/incidents` *(paged)* | `incidents:read` | `?status=open\|all\|<status>&severity=&stage=&kind=&assignee=me\|<userId>&reportable=true&q=` default open, ordered by severity then `lastSeen` desc → `IncidentSummary[]` |
| GET | `/incidents/{id}` | `incidents:read` | `Incident` |
| GET | `/incidents/{id}/timeline` | `incidents:read` | `TimelineEntry[]` oldest first (`?audience=executive` returns the plain-language subset) |
| GET | `/incidents/{id}/detections` | `detections:read` | `DetectionDoc[]` |
| PATCH | `/incidents/{id}` | `incidents:write` | `{ status?, severity?, assigneeId?, title?, resolution? , version }` → `Incident`; `409 stale_version`; `422 invalid_transition` |
| POST | `/incidents/{id}/notes` | `incidents:write` | `{ body }` (≤ 4,000 chars) → `TimelineEntry` |
| POST | `/incidents/{id}/merge` | `incidents:write` | `{ incidentIds: string[] }` → `Incident` (others closed as merged) |
| POST | `/incidents/{id}/reopen` | `incidents:write` | `{ reason }` |

```text
IncidentSummary = { id, reference, title, kind, severity, status, statusLabel, stage, assets: string[], identities: string[],
                    attackTactics: string[], detectionCount, firstSeen, lastSeen, assignee: User|null,
                    verdict: "true_positive"|"benign"|"false_positive"|"needs_review"|null,
                    likelyBenign: boolean, reportable: "yes"|"no"|"assess"|null, nextDeadline: Deadline|null,
                    pendingApprovals: number }
Incident = IncidentSummary & {
  version, summary, attack: (AttackRef & { tacticLabels: string[] })[], entities: Entities,
  assetsDetail: AssetDoc[], identitiesDetail: IdentityDoc[],
  triage: { verdict, confidence, summary, modelProbability, decidedAt, runId } | null,
  timeToTriageSeconds: number|null, timeToContainSeconds: number|null,
  resolution: { reason, note, by, at } | null, mergedInto: string|null,
  regimes: RegimeAssessment[], tickets: Ticket[] }
TimelineEntry = { id, at, kind: "detection"|"anomaly"|"agent"|"handoff"|"approval"|"action"|"status"|"note"|"clock"|"notice",
                  actor: { type: "user"|"agent"|"system", name }, title, body: string|null, refId: string|null,
                  executive: boolean }
AssetDoc = { id, name, kind, ips, os, site, jurisdiction, owner, businessService, criticality, tags, isolated, vulnerabilities: KevHitDoc[] }
IdentityDoc = { id, username, displayName, email, department, title, jurisdiction, manager, privileged, mfa, usualCountries, disabled, kind }
```
Transitions: `new → triage → investigating → containing → monitoring → resolved → closed`; any open status may go
to `resolved` with a `resolution.reason` (`contained`, `benign`, `false_positive`, `duplicate`, `merged`); `resolved
→ closed` after 24 h or by hand; `reopen` returns to `investigating`. Severity changes by hand are audited with
the old/new value.

## 1.9 Investigations (agent runs)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/incidents/{id}/investigations` | `investigations:read` | `RunSummary[]` newest first |
| POST | `/incidents/{id}/investigations` | `investigations:run` | `{ reason? }` → `202 RunSummary` (rerun); `503 agents_unavailable` |
| GET | `/investigations/{runId}` | `investigations:read` | `Run` with all steps |
| GET | `/investigations/{runId}/steps/{seq}` | `investigations:read` | `AgentStepDoc` with full `detail` |
| GET | `/investigations/{runId}/replay` | `investigations:read` | `{ runId, steps: AgentStepDoc[], verification: { inputHashesMatch: boolean, offlineReplayVerdict, recordedVerdict, method: "replayed"\|"recorded" } }` re-runs the offline policy over the recorded inputs and compares (`replayed`, via `agents.replay_verify`); when the agents are unavailable it checks the record is complete instead (`recorded`) |

```text
RunSummary = { id, incidentId, trigger: "auto"|"manual"|"rerun", status: "queued"|"running"|"completed"|"failed"|"cancelled",
               startedAt, finishedAt, verdict, durationSeconds, stepCount, requestedBy: User|null, reason: string|null }
Run = RunSummary & { steps: AgentStepDoc[], verdictDetail: TriageVerdictDoc|null,
                     enrichment: EnrichmentDoc|null, proposals: ProposalDoc[] }
AgentStepDoc = { seq, agent: "triage"|"enrichment"|"response"|"orchestrator", inputHash: string|null,
                 kind: "plan"|"tool_call"|"tool_result"|"handoff_out"|"handoff_in"|"finding"|"verdict"|"proposal"|"guardrail"|"error",
                 title, startedAt, durationMs, tool: string|null, a2aTaskId: string|null, model: string|null,
                 flags: string[], detail?: object }
TriageVerdictDoc = { verdict, confidence, summary, evidence: { kind, ref, text }[], modelProbability, facts: ReportableFactsDoc|null }
ReportableFactsDoc = { maliciousSuspected, affectedJurisdictions, affectedEntities, serviceDisruption, disruptionHours,
                       criticalAssetImpact: "none"|"relevant"|"significant", personalDataInvolved,
                       recordsAffectedEstimate, crossBorder, financialImpactEstimateEur }
EnrichmentDoc = { indicators: { type, value, verdict, source, detail, kev: KevHitDoc|null }[], techniques: AttackRef[],
                  assetNotes: string[], identityNotes: string[] }
```
UI wording: agents are "the triage colleague", "the enrichment colleague", "the response colleague"; hand-offs are
shown as baton passes. Never show tool names, model ids or protocol words in client-facing text; `tool` and
`model` are for the analyst detail drawer, labelled in plain words ("Checked: known exploited vulnerabilities").

## 1.10 Response actions and approvals

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/approvals` *(paged)* | `actions:read` | `?status=pending\|all&incidentId=&mine=true` (mine = I can approve) → `ProposalDoc[]` oldest pending first |
| GET | `/incidents/{id}/actions` | `actions:read` | `ProposalDoc[]` |
| POST | `/incidents/{id}/actions` | `actions:propose` | `{ kind, target, rationale }` → `201 ProposalDoc` (`422 target_not_in_incident`) |
| POST | `/approvals/{id}/approve` | `actions:approve` or `actions:approve_crown_jewel` (per required role) | `{ note? }` → `ProposalDoc`; when the last required role approves, the action is executed (`status: "approved"` then `executed`/`failed` via SSE). `409 already_decided`, `409 self_approval`, `410 expired` |
| POST | `/approvals/{id}/reject` | same | `{ reason }` (required) → `ProposalDoc` |

```text
ProposalDoc = { id, incidentId, incidentReference, runId: string|null, kind, kindLabel, target, targetType,
                rationale, expectedEffect, reversible, destructive, crownJewel: boolean,
                proposedBy: { type: "agent"|"user", name }, proposedByUserId: string|null,
                status: "auto_approved"|"pending"|"approved"|"rejected"|"expired"|"executed"|"failed",
                requiredRoles: string[], awaitingRoles: string[], canApprove: boolean,   # canApprove: for the caller
                approvals: { userId, name, role, decidedAt, note }[],
                rejection: { userId, name, reason, decidedAt }|null,
                result: { executed, status, detail, executedAt }|null, createdAt, expiresAt }
```
`kindLabel` examples: "Isolate this computer from the network", "Turn off this account", "Sign this person out
everywhere", "Withdraw this cloud access key", "Block this internet address". Pending approvals expire after 24 h
(`expired`); the signed execution token lives 60 minutes and is single use (ADR 0006). The web app's "Hold to
authorise" gesture posts `approve` only after a completed 1.2 s hold.

## 1.11 Tickets (built-in service desk)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/incidents/{id}/tickets` | `incidents:read` | `Ticket[]` |
| GET | `/tickets/{id}` | `incidents:read` | `Ticket` with comments |
| POST | `/tickets/{id}/comments` | `incidents:write` | `{ body }` |

```text
Ticket = { id, key: "OPS-311", incidentId, title, body, priority: "p1"|"p2"|"p3"|"p4", assigneeTeam,
           status: "open"|"in_progress"|"done", createdBy: { type, name }, createdAt, comments?: { author, body, at }[] }
```

## 1.12 Incident clock: regimes, deadlines, notices

The clock **never decides on its own** that an incident is reportable. It proposes, a person with
`reporting:assess` determines, and the determination starts the clock(s). All copy is "designed to support" the
obligations below; Nightjar SOC does not file anything with a regulator (submissions are recorded, mocked).

### Regimes (as of late November 2025)

| Regime id | Law | Applies when (proposal logic) | Trigger (clock start) | Notices and deadlines | Recipient (mock) |
|---|---|---|---|---|---|
| `nis2` | Directive (EU) 2022/2555 Art. 23, as transposed (Belgium: law of 26 April 2024, in force since 18 October 2024) | An affected legal entity has `nis2.applies` and facts suggest a **significant incident** (severe operational disruption or financial loss, or considerable damage to others) | `awareAt`: when the entity became aware of the significant incident (set by the determination) | **Early warning** ≤ 24 h after `awareAt` (suspected unlawful/malicious? cross-border impact?) · **Incident notification** ≤ 72 h after `awareAt` (update, initial assessment of severity and impact, indicators of compromise) · **Intermediate report** on authority request · **Final report** ≤ 1 month after the incident notification was submitted (description, severity and impact, threat type or root cause, mitigation, cross-border impact); if the incident is still ongoing then, a **progress report** is due instead and the final report ≤ 1 month after handling ends | CSIRT / competent authority of the member state (Belgium: CCB) |
| `sec_8k` | SEC Form 8-K **Item 1.05** (rule applies since 18 December 2023) | An affected legal entity has `sec.registrant` and the incident affects that registrant's information systems | `materialityDeterminedAt`: when the registrant (disclosure committee) determined the incident is **material**; the determination itself must be made without unreasonable delay after discovery | **Form 8-K Item 1.05** due by **17:30 America/New_York on the 4th business day** after the determination (business days exclude weekends and US federal holidays; Thanksgiving 27 Nov 2025 is not a business day) · **Form 8-K/A** within 4 business days after information that was not available at filing becomes available · optional US Attorney General delay (recorded, extends the due date by the granted period) | SEC EDGAR |
| `soci` | Security of Critical Infrastructure Act 2018 (Cth), Part 2B (s30BC / s30BD) | An affected legal entity is a `soci.responsibleEntity` and an asset in `soci.assets` is impacted | `awareAt` + `impactLevel` | **Critical incident** (`significant` impact on availability): report ≤ **12 h** after awareness; if made orally, written record ≤ 84 h after the oral report · **Other incident** (`relevant` impact): report ≤ **72 h**; if oral, written record ≤ 48 h after the oral report | Australian Signals Directorate (ACSC, ReportCyber) |
| `gdpr_art33` *(extension: build after the three above)* | GDPR Art. 33 | `personalDataInvolved` and a `gdpr.controller` entity is affected | `awareAt` of the personal data breach | **Notification** ≤ 72 h after awareness (reasons required if late) | Lead supervisory authority (Belgium: Data Protection Authority) |

Holidays: `backend/nightjar/regimes/data/us_federal_holidays.json` (2025–2026, observed dates). Business-day
arithmetic is unit-tested around weekends and Thanksgiving.

**Clock states** (shown with a glyph *and* a word, never colour alone): `not_started` (assessment open), `on_track`,
`due_soon` (remaining ≤ max(25 % of the window, 6 h); for 8-K: ≤ 1 business day), `overdue`, `submitted`,
`not_required`.

### Endpoints

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/reporting/clocks` | `reporting:read` | `Deadline[]` for all open reportable incidents, soonest first (the top clock bar) |
| GET | `/incidents/{id}/reporting` | `reporting:read` | `{ assessments: RegimeAssessment[], deadlines: Deadline[], notices: NoticeSummary[] }` |
| POST | `/incidents/{id}/reporting/{regime}/assess` | `reporting:assess` | `RegimeDetermination` → `RegimeAssessment` (starts or stops the clock; audited `reporting.assessed`, `reporting.clock_started`) |
| POST | `/incidents/{id}/reporting/sec_8k/delay` | `reporting:assess` | `{ grantedUntil, reference }` (AG delay) |
| POST | `/incidents/{id}/notices` | `notices:draft` | `{ regime, type }` → `201 Notice` (drafted from incident facts; offline fake text by default) |
| GET | `/notices/{id}` | `reporting:read` | `Notice` with versions |
| PATCH | `/notices/{id}` | `notices:draft` | `{ sections: { key, body }[], version }` → `Notice` (new version; `409 stale_version`) |
| POST | `/notices/{id}/approve` | `notices:approve` | → `Notice` (`status: approved`) |
| POST | `/notices/{id}/submit` | `notices:submit` | `{ channel: "portal"\|"email"\|"oral", note? }` → `Notice` with `submission` (mock receipt); stops that deadline; for SOCI `oral` starts the written-record deadline |
| GET | `/notices/{id}/document.html` | `reporting:read` | printable HTML (inline styles, no scripts) |

```text
RegimeAssessment = { regime, regimeLabel, jurisdiction, entityId, entityName,
                     proposal: { applies: "yes"|"no"|"assess", reasons: string[], proposedAt },
                     determination: { applies: boolean, significant?: boolean, material?: boolean,
                                      impactLevel?: "significant"|"relevant", awareAt?, materialityDeterminedAt?,
                                      rationale, by: User, at } | null }
RegimeDetermination = { applies: boolean, rationale: string (10–2,000 chars),
                        awareAt?: datetime (nis2, soci, gdpr_art33), materialityDeterminedAt?: datetime (sec_8k),
                        impactLevel?: "significant"|"relevant" (soci), entityId: string }
Deadline = { id, incidentId, incidentReference, regime, regimeLabel, noticeType, noticeLabel, jurisdiction, entityId,
             startsAt, dueAt, dueAtLocal: { at, timezone }, businessDays: number|null,
             state: "not_started"|"on_track"|"due_soon"|"overdue"|"submitted"|"not_required",
             remainingSeconds: number|null, noticeId: string|null, submittedAt: string|null, basis: string }
# remainingSeconds is negative when overdue and null once submitted. businessDays (8-K only) counts New York
# business days in (today, due day]. A `not_started` entry is an open assessment (proposal yes/assess, no
# determination yet): id "assessment-<uuid>", noticeType/startsAt/dueAt/dueAtLocal null, noticeLabel
# "Assessment open", basis = the proposal's reasons. /reporting/clocks lists running clocks soonest first, then
# open assessments. The one-month NIS2 deadline reads `nis2.progress_report` while the incident is still open.
NoticeSummary = { id, regime, type, typeLabel, status: "draft"|"approved"|"submitted", version, dueAt, submittedAt }
Notice = NoticeSummary & { incidentId, entity: { id, name },
                           recipient: { name, channel: "portal"|"EDGAR", portalUrl, contact: RegimeContact|null },
                           sections: { key, title, hint, body, required: boolean, filled: boolean }[],
                           missing: string[],   # required section keys not filled ("[to be confirmed]" counts as unfilled)
                           versions: { version, editedBy, editedAt }[], approvedBy: User|null, approvedAt,
                           submission: { channel, receipt: "CCB-MOCK-…", submittedBy: User, submittedAt, note }|null,
                           generatedBy: "offline"|"cloud" }
```
Approve refuses a notice with missing required sections (`422 validation_error`, one error per
`sections.<key>`); GDPR `delay_reasons` becomes required once the 72 h deadline has passed. Editing an approved
notice returns it to `draft`. Only SOCI reports may be submitted `oral`. Receipts: `CCB-MOCK-`, `EDGAR-MOCK-`,
`ASD-MOCK-`, `APD-MOCK-` + 8 hex. Drafting needs a positive determination (`409 clock_not_started`); `type` may be
short (`early_warning`) or full (`nis2.early_warning`); drafting an existing unsubmitted type returns that notice.

Notice types: `nis2.early_warning`, `nis2.incident_notification`, `nis2.intermediate_report`,
`nis2.progress_report`, `nis2.final_report`, `sec_8k.item_105`, `sec_8k.amendment`, `soci.critical_report`,
`soci.other_report`, `soci.written_record`, `gdpr_art33.notification`. Section keys per type are listed in
`backend/nightjar/regimes/data/notice_templates.json` (builder A). SEC drafts describe material aspects of the
nature, scope and timing and the material impact; they never include technical detail that would hinder response.

## 1.13 Threat hunting

Searches are structured (`HuntQuery`); the API compiles them to parameterised DuckDB SQL with an org filter. Raw
SQL from clients is never accepted.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/hunting/fields` | `hunting:read` | `{ field, label, type: "string"\|"number"\|"ip"\|"time", ops: string[] }[]` |
| POST | `/hunting/search` | `hunting:read` | `HuntQueryDoc` → `HuntResultDoc` (`422 invalid_query`; 10 s timeout → `503 unavailable`) |
| GET | `/hunting/saved` | `hunting:read` | `SavedSearch[]` |
| POST | `/hunting/saved` | `hunting:write` | `{ name, description, query: HuntQueryDoc }` → `201 SavedSearch` |
| PATCH | `/hunting/saved/{id}` | `hunting:write` | partial |
| DELETE | `/hunting/saved/{id}` | `hunting:write` | `204` |
| POST | `/hunting/saved/{id}/run` | `hunting:read` | `{ from?, to? }` → `HuntResultDoc` (audited `hunt.run`) |
| GET | `/hunting/suggestions` | `hunting:read` | `Suggestion[]` plain-language ideas from recent techniques, anomalies and known-exploited vulnerabilities on owned assets |

```text
HuntQueryDoc = { from, to, relativeHours?: number, filters: { field, op: "eq"|"ne"|"in"|"contains"|"prefix"|"cidr"|"gt"|"lt", value }[],
                 groupBy: string[] (≤ 3), metric: "count"|"count_distinct:<field>", bucket: "5m"|"1h"|"1d"|null, limit (≤ 5000) }
# from/to default to the 24 h (or `relativeHours`) ending now; saved searches keep `relativeHours` so they stay
# current. Without groupBy/bucket the rows are events: columns timestamp, eventId, dataset, action, outcome,
# host.name, user.name, source.ip, destination.ip, summary (summary = the untrusted message text: plain text only).
# With them: [bucket], …groupBy, then "count" or "distinct <field>". Matching is case-insensitive.
HuntResultDoc = { columns: string[], rows: any[][], totalMatched, elapsedMs, truncated }
SavedSearch = { id, name, description, query: HuntQueryDoc, createdBy: User, createdAt, lastRunAt, lastCount }
Suggestion = { id, title, why, query: HuntQueryDoc, basis: { kind: "technique"|"anomaly"|"vulnerability", ref } }
```
Example suggestion: title "Look for other people who approved a sign-in prompt after several refusals", why "Joy
Okafor's account was taken over this way on Thursday night".

## 1.14 The Watch and the executive view

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/watch` | `watch:read` | `{ clocks: Deadline[], incidents: IncidentSummary[] (open), pendingApprovals: number, shiftLog: ShiftLogEntry[], pipeline: { status, plainStatus, eventsPerMinute } }` |
| GET | `/watch/shift-log` | `watch:read` | `?since=` (default: my `lastSeenAt`) → `ShiftLogEntry[]`; `POST /watch/seen` marks now |
| GET | `/executive/posture` | `exec:read` | `Posture` |
| GET | `/executive/morning` | `exec:read` | `MorningBrief`: what happened last night (the `night` window of Part 6) |
| GET | `/executive/briefs` | `exec:read` | `{ week: "2025-W47", generatedAt, headline }[]` |
| GET | `/executive/briefs/{week}` | `exec:read` | `WeeklyBrief` |
| GET | `/executive/briefs/{week}/document.html` | `exec:read` | printable |
| POST | `/executive/briefs/generate` | `reports:generate` | `{ week? }` → `202 { job: Job }` |

```text
ShiftLogEntry = { at, text, kind: "incident"|"approval"|"clock"|"action"|"notice"|"pipeline", refId, severity: Severity|null }
Posture = { asOf, score: 0..100, scoreBand: "steady"|"watch"|"strained", components: { key, label, score, note }[],
            openBySeverity: { critical, high, medium, low, info }, reportableOpen: number,
            meanTimeToTriage: { last7dSeconds, last30dSeconds, trend: "better"|"worse"|"flat" },
            meanTimeToContain: { last7dSeconds, last30dSeconds, trend },
            alerts: { last7d, incidentsLast7d, suppressedAsNoise, reductionPct },
            clocks: Deadline[], highlights: string[] }
WeeklyBrief = { week, from, to, headline, sections: { title, body }[], metrics: Posture, incidents: IncidentSummary[], generatedBy: "offline"|"cloud", generatedAt }
MorningBrief = { asOf, night: { from, to }, headline, incidents: IncidentSummary[], contained: number,
                 pendingApprovals: number, alerts: { last7d, incidentsLast7d, suppressedAsNoise, reductionPct },
                 clocks: Deadline[], highlights: string[] }
# Posture.components keys: exposure, clocks, response, decisions, intake. POST /executive/briefs/generate runs in
# the API process and returns the job already `succeeded` (week defaults to last ISO week).
```
`meanTimeToTriage` = first detection → recorded triage verdict; `meanTimeToContain` = first detection → first
executed containment action (or resolution as benign). Both exclude merged incidents.

## 1.15 Behaviour anomalies and models

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/ueba/anomalies` *(paged)* | `ueba:read` | `?entityType=user\|host&minScore=&from=&to=` → `AnomalyDoc[]` highest score first |
| GET | `/ueba/entities/{type}/{id}` | `ueba:read` | `{ entity, baseline: { feature, label, median, p95 }[], recent: { windowStart, features, score }[] }` |
| GET | `/models` | `models:read` | `ModelInfo[]` (triage, ueba; champion + challenger) |
| GET | `/models/{name}` | `models:read` | `ModelInfo` with `cardMarkdown`, calibration bins, PSI drift |
| POST | `/models/{name}/promote` | `models:promote` | `{ version, note }` → `ModelInfo` (refused if gates failed: `409 conflict`) |
| GET | `/detections/{id}/triage` | `detections:read` | `TriageScoreDoc` |

```text
AnomalyDoc = { id, entityType, entityId, windowStart, windowEnd, score, reasons: Reason[], features, detectionId, incidentId }
ModelInfo  = { name, version, stage, trainedOn, metrics: { [k]: number }, gatesPassed, promotedBy, cardMarkdown?,
               calibration?: { bin, predicted, observed, count }[], drift?: { feature, psi }[] }
```

## 1.16 Settings: regulator contacts

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/settings/regulators` | `settings:read` | `RegimeContacts` |
| PUT | `/settings/regulators` | `settings:manage` | `RegimeContacts` (audited `settings.contacts_updated`) |
| GET | `/settings/profile` | `settings:read` | `OrgProfile` |
| PUT | `/settings/profile` | `settings:manage` | `OrgProfile` |
| PATCH | `/settings/preferences` | `settings:manage` | `{ autoInvestigate?, crownJewelAssetTags?, weeklyBriefDay? }` |

```text
RegimeContacts = { [regime in "nis2"|"sec_8k"|"soci"|"gdpr_art33"]: {
  jurisdiction, authorityName, portalUrl, internalOwner: { userId, name }, contacts: RegimeContact[] } }
RegimeContact = { name, role, email, phone, notes }
```

## 1.17 Audit, jobs, live updates, health

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/audit` *(paged)* | `audit:read` | `?action=&actorType=user\|agent\|system&resourceType=&resourceId=&from=&to=` newest first (`action=incident.` filters by area) |
| GET | `/audit/verify` | `audit:read` | `{ valid, checked, brokenAt }` |
| GET | `/audit/export.csv` | `audit:read` | CSV |
| GET | `/jobs/{id}` | any member (own org) | `Job` |
| GET | `/pipeline/health` | `watch:read` | `PipelineHealth` (plain status for everyone; stream detail for `detections:read`) |
| GET | `/pipeline/dlq` *(paged)* | `rules:manage` or `org:admin` | dead-lettered messages `{ seq, stream, consumer, subject, deliveries, reason, at }` |
| POST | `/pipeline/dlq/{seq}/retry` | `org:admin` | re-publish to the original subject |
| GET | `/agents` | `investigations:read` | `AgentCardInfo[]` with health |
| GET | `/events/stream` | any member | SSE (below) |
| GET | `/health` · `/health/ready` · `/metrics` (root, no auth) | – | liveness · readiness · Prometheus |

```text
AuditEntry = { id, action, label, actorName, actorType, resourceType, resourceId, metadata, ip, createdAt, hash, prevHash }
Job = { id, kind: "replay"|"train"|"weekly_report"|"reindex", status: "queued"|"running"|"succeeded"|"failed", progress: 0..100, result, error, createdAt, finishedAt }
PipelineHealth = { mode, connected, status: "normal"|"catching_up"|"degraded"|"offline", plainStatus, eventsPerMinute,
                   streams: { name, messages, bytes, consumers: { name, pending, ackPending, redelivered }[] }[],
                   dlqMessages, alertToTriageP50S, alertToTriageP95S }
```

### SSE events
`GET /api/v1/events/stream` (cookie auth). `Last-Event-ID` header or `?lastEventId=` resumes; without either the
stream starts at the newest event. The first frame is `ready` with the current id. The server closes the stream
every 5 minutes and the browser reconnects (`retry: 3000`). A `: heartbeat` comment is sent after 15 s idle.
Frames: `id: <monotonic int>` · `event: <name>` · `data: <json>`. Events are org-scoped and filtered by the
recipient's permissions. Payloads are small; fetch details by id.

| Event | Data | Permission |
|---|---|---|
| `ready` | `{ lastEventId }` | – |
| `river.batch` | `{ count, lines: RiverLine[≤20], eventsPerMinute }` (at most every 2 s) | `watch:read` |
| `pipeline.status` | `{ status, plainStatus, eventsPerMinute }` (on change, and every 30 s) | `watch:read` |
| `detection.created` | `{ detectionId, severity, title, band, incidentId }` | `detections:read` |
| `anomaly.created` | `{ anomalyId, entityType, entityId, score }` | `ueba:read` |
| `incident.created` | `{ incidentId, reference, title, severity, kind }` | `incidents:read` |
| `incident.updated` | `{ incidentId, reference, status, stage, severity, changed: string[] }` | `incidents:read` |
| `investigation.started` | `{ runId, incidentId }` | `investigations:read` |
| `investigation.step` | `{ runId, incidentId, seq, agent, kind, title }` | `investigations:read` |
| `investigation.completed` | `{ runId, incidentId, verdict, durationSeconds }` | `investigations:read` |
| `approval.requested` | `{ approvalId, incidentId, kind, kindLabel, target, requiredRoles }` | `actions:read` |
| `approval.decided` | `{ approvalId, incidentId, status }` | `actions:read` |
| `action.executed` | `{ approvalId, incidentId, kind, target, status }` | `actions:read` |
| `clock.started` | `{ incidentId, regime, noticeType, dueAt }` | `reporting:read` |
| `clock.state` | `{ deadlineId, incidentId, regime, state }` (on transition to due_soon / overdue) | `reporting:read` |
| `notice.updated` | `{ noticeId, incidentId, regime, type, status }` | `reporting:read` |
| `job.updated` | `{ jobId, kind, status, progress }` | job creator |
| `brief.ready` | `{ week }` | `exec:read` |

Countdowns tick in the browser from `dueAt` and the server clock offset (`Me.clock` + response `Date` header);
the API does not stream ticks.

---

# Part 2: Data model (SQLAlchemy 2 + Alembic; SQLite default, Postgres-ready)

Every tenant table has `org_id` (indexed) and every query filters on the caller's org. UUID primary keys except
append-only logs (integer ids). `created_at`/`updated_at` on all mutable rows. Portable types only (`JSONType`,
`UUIDType`, `UTCDateTime` from `nightjar/db/base.py`). Builder A owns every table except those prefixed `pl_`
(builder B). The platform tables in `nightjar/db/models/platform.py` already exist (migration `0001`).

| Area | Table | Key fields |
|---|---|---|
| platform (done) | `organizations` | name, slug, timezone, profile (OrgProfile JSON), settings (OrgSettings JSON), is_sample |
| | `users`, `memberships`, `invitations`, `sessions`, `ingest_keys` | per §1.2, §1.4 |
| | `audit_log` | org_id, actor_id, actor_type (user/agent/system), actor_name, action, resource_type, resource_id, metadata, ip, user_agent, prev_hash, hash; unique (org_id, prev_hash) |
| | `ui_events` | org_id, name, data, permission, user_ids (SSE outbox; id = SSE id) |
| | `jobs` | org_id, kind, payload, status, progress, result, error |
| inventory | `assets` | org_id, name (unique per org), kind, ips, os, site, jurisdiction, entity_id, owner_username, business_service, criticality, tags, isolated, isolated_by_approval_id, vulnerabilities (CVE ids) |
| | `identities` | org_id, username (unique per org), display_name, email, department, title, jurisdiction, entity_id, manager, privileged, mfa, usual_countries, disabled, kind |
| | `sign_in_history` | org_id, username, day, country, count (seeded 90 days; directory lookups) |
| | `ip_blocks` | org_id, ip, blocked, approval_id |
| | `access_keys` | org_id, key_id, identity, provider, status (active/revoked), revoked_by_approval_id |
| detection | `detections` | org_id, id (from B), source, rule_id, rule_title, severity, confidence, attack (JSON), entities (JSON), first_seen, last_seen, event_ids (JSON), evidence (JSON ≤ 20 snapshots), summary, fields, incident_id |
| | `triage_scores` | detection_id (unique), probability, band, threshold, reasons, model_version, suppressed |
| | `anomalies` | org_id, id (from B), entity_type, entity_id, window_start, window_end, score, isolation_score, robust_z, features, reasons, model_version, detection_id |
| incidents | `incidents` | org_id, reference (unique per org, `NJ-1001`…), correlation_key (unique per org), version, title, kind, severity, status, stage, summary, attack, entities, first_seen, last_seen, assignee_id, likely_benign, verdict, verdict_confidence, triaged_at, contained_at, resolution (JSON), merged_into_id |
| | `incident_events` | incident timeline rows (`TimelineEntry`), append-only |
| investigations | `investigation_runs` | org_id, incident_id, trigger, status, requested_by, started_at, finished_at, verdict (JSON), enrichment (JSON), error |
| | `agent_steps` | run_id, seq (unique per run), agent, kind, title, started_at, duration_ms, tool, a2a_task_id, model, input_hash, flags, detail (JSON); append-only |
| response | `action_proposals` | org_id, incident_id, run_id, kind, target, target_type, rationale, expected_effect, reversible, crown_jewel, proposed_by_type/name/user_id, status, required_roles, expires_at, token_nonce, token_used_at, result (JSON) |
| | `action_approvals` | proposal_id, user_id, role, decision (approve/reject), note, decided_at; unique (proposal_id, role) |
| | `tickets`, `ticket_comments` | per §1.11 (`key` sequence per org `OPS-301`…) |
| reporting | `regime_assessments` | org_id, incident_id, regime, entity_id, proposal (JSON), determination (JSON), unique (incident_id, regime, entity_id) |
| | `deadlines` | org_id, incident_id, regime, notice_type, starts_at, due_at, state, notice_id, basis |
| | `notices`, `notice_versions` | org_id, incident_id, regime, type, status, version, sections (JSON), approved_by/at, submission (JSON) |
| hunting | `saved_searches` | org_id, name, description, query (JSON), created_by, last_run_at, last_count |
| executive | `weekly_briefs` | org_id, week (unique per org), headline, sections, metrics, generated_by |
| models | `model_promotions` | org_id, model_name, version, stage, promoted_by, note (MLflow holds runs and metrics) |
| pipeline (B) | `pl_rule_overrides` | org_id, rule_id, enabled |
| | `pl_ueba_baselines` | org_id, entity_type, entity_id, feature, median, mad, p95, n, updated_at |
| | `pl_correlation_state` | org_id, correlation_key, incident_id, entities, last_seen (warm start; the incident table remains the source of truth) |

The **hunting store** is DuckDB (`data/hunting.duckdb`), not SQLAlchemy: table `events` with the flattened ECS
columns of §1.6 plus `org_id`, `raw JSON`, partitioned logically by `org_id` and day; owned by A (ADR 0004).

---

# Part 3: Python interfaces between the platform (A) and the pipeline (B)

All shared dataclasses live in **`backend/nightjar/contracts.py`** (frozen, slotted; `to_doc()` gives the
camelCase wire form). Each side exposes typed **`api.py`** modules; callers import only those. Stubs raise
`NotImplementedError` until implemented. Changing a signature means changing this Part in the same commit.

### B calls A (platform side; builder A implements)
```python
# nightjar/incidents/api.py
record_detections(org_id, detections: Sequence[Detection]) -> None            # idempotent on id
record_triage_scores(org_id, scores: Sequence[TriageScore]) -> None
record_anomalies(org_id, anomalies: Sequence[Anomaly]) -> None
upsert_incident(update: CorrelationUpdate) -> IncidentRef                      # idempotent on correlation_key
open_incident_index(org_id, *, since: datetime) -> list[OpenIncidentIndexEntry]
get_incident_context(org_id, incident_id) -> IncidentContext                  # SQLite only, tenant-checked

# nightjar/investigations/api.py
start_run(org_id, incident_id, *, trigger, requested_by) -> RunRef
mark_running(run_id) -> None
append_step(run_id, step: AgentStep) -> int                                    # gap-free seq
finish_run(run_id, *, status, verdict, enrichment, proposals, error=None) -> None

# nightjar/approvals/api.py
request_approval(org_id, incident_id, run_id, action: ProposedAction) -> ApprovalRef   # TargetNotInIncidentError (a ValueError) if target ∉ incident
# non-destructive kinds come back "auto_approved" with no required roles and no token; identical pending
# requests (incident, kind, target) are deduplicated. get_approval(...).token is set only while the approval is
# "approved" and unconsumed. consume() is False on a wrong nonce, reuse, an expired token (real clock), another
# org, or any status other than "approved".
get_approval(approval_id) -> ApprovalRef
consume(org_id, approval_id, nonce) -> bool                                    # single use
record_result(org_id, result: ActionResult) -> None

# nightjar/directory/api.py   (asset inventory + identity directory)
find_asset(org_id, name_or_ip) -> AssetRecord | None
search_assets(org_id, query, *, limit=20) -> list[AssetRecord]
find_identity(org_id, username_or_email) -> IdentityRecord | None
search_identities(org_id, query, *, limit=20) -> list[IdentityRecord]
recent_sign_in_countries(org_id, username, *, days=90) -> list[str]
set_host_isolation(org_id, asset_name, *, isolated, approval_id) -> None      # response tool server only
set_account_disabled(org_id, username, *, disabled, approval_id) -> None      # response tool server only
revoke_access_key(org_id, access_key_id, *, approval_id) -> None              # response tool server only
set_ip_block(org_id, ip, *, blocked, approval_id) -> None                     # response tool server only
revoke_identity_sessions(org_id, username, *, approval_id) -> None             # response tool server only
reset_identity_password(org_id, username, *, approval_id) -> None              # response tool server only
quarantine_message(org_id, message_id, *, approval_id) -> None                 # response tool server only
# effectors raise KeyError when the target does not exist in that org

# nightjar/services/jobs.py
update_job(job_id, *, status=None, progress=None, result=None, error=None)      # workers report job progress; SSE job.updated

# nightjar/hunting/service.py
handle_normalized(events: Sequence[NormalizedEvent]) -> None                   # the store-writer handler (DuckDB + river.batch)

# nightjar/ticketing/api.py
create_ticket(org_id, incident_id, *, title, body, priority, assignee_team) -> dict
add_comment(org_id, ticket_id, *, body, author) -> dict
get_ticket(org_id, ticket_id) -> dict | None

# nightjar/core/events.py and nightjar/services/audit.py (exist)
emit(db, org_id, name, data, *, permission=None, user_ids=None)               # SSE outbox, in the caller's transaction
audit(db, None, org_id=..., action=..., actor_type="agent", actor_name="Response agent", ...)
# nightjar/core/security.py (exists)
sign_approval_token(...) / verify_approval_token(token, *, action, target, org_id) -> ApprovalClaims | None
```

### A calls B (pipeline side; builder B implements)
```python
# nightjar/stream/api.py
publish_raw(events: Sequence[RawEvent]) -> IngestAck                           # raises BackpressureError → 503
normalize(raw: RawEvent) -> NormalizedEvent                                    # pure; ValueError with a plain reason
subscribe_normalized(durable, handler, *, batch=500, max_wait_s=1.0) -> Subscription   # A's hunting writer
pipeline_health() -> PipelineHealth
list_dlq(*, page, page_size) -> (list[dict], total);  retry_dlq(seq) -> None (KeyError if unknown)
list_scenarios() -> list[ScenarioInfo]
start_replay(org_id, scenario_id, *, speed, job_id) -> None
enqueue_job(kind, job_id, payload) -> None

# nightjar/agents/api.py
start_investigation(org_id, incident_id, *, trigger, requested_by) -> uuid.UUID   # run id
execute_approved_action(org_id, approval_id) -> None
agent_cards() -> list[AgentCardInfo];  agents_health() -> list[AgentCardInfo]
replay_verify(org_id, run_id) -> { inputHashesMatch, offlineReplayVerdict }

# nightjar/detect/api.py
list_rules(org_id) -> list[RuleInfo];  get_rule(org_id, rule_id) -> (RuleInfo, yaml) | None
set_rule_enabled(org_id, rule_id, *, enabled) -> RuleInfo

# nightjar/intel/api.py
attack_technique(technique_id) -> AttackRef | None;  attack_tactic_label(tactic) -> str
attack_technique_detail(technique_id) -> { techniqueId, name, tactics, description, url } | None
kev_lookup(cve_id) -> KevHit | None;  kev_for_assets(cve_ids) -> list[KevHit]   # hides dateAdded > clock.today()

# nightjar/ml/api.py
list_models() -> list[ModelInfo];  promote(name, version, *, promoted_by) -> ModelInfo   # ValueError if gates failed
entity_baseline(org_id, entity_type, entity_id) -> list[{ feature, label, median, p95 }]

# nightjar/llm/api.py
generate(task, *, instructions, trusted, untrusted: dict[str, UntrustedText], schema) -> LlmResult   # offline fake default
# the offline fake returns trusted["baseline"] unchanged when present (notice drafts, weekly briefs)

# nightjar/safety.py
neutralise(value, *, field, max_len=2000) -> UntrustedText;  render_for_prompt(items) -> str
```

### Who triggers what
1. **Ingest** (A's endpoint) → `stream.publish_raw` → B's normaliser → `nj.events.normalized.*` → A's
   `store-writer` (DuckDB + `river.batch` SSE) and B's detector/UEBA.
2. B's scorer → `incidents.record_detections` (+ `record_anomalies`) → B's correlator → `incidents.upsert_incident`.
3. B's dispatcher, on a new incident with `autoInvestigate` → `agents.start_investigation` (which calls
   `investigations.start_run`) → A2A triage → enrichment → response; every step `investigations.append_step`;
   proposals `approvals.request_approval`; end `investigations.finish_run` (A applies the verdict, records
   time-to-triage and proposes regime applicability).
4. A person approves (A's endpoint) → A signs the token → `agents.execute_approved_action` → the response agent
   calls the response tool server, which verifies the token, `approvals.consume`s it, calls the `directory`
   effector, then `approvals.record_result`.
5. A person with `reporting:assess` determines applicability (A) → deadlines start → notices drafted with
   `llm.generate` (A) → approved and submitted (mock) by the executive.

### Process topology and DuckDB ownership
`api` (FastAPI, :8030; owns DuckDB and runs `store-writer`) · `workers` (B: normaliser, detector + UEBA, scorer,
correlator, dispatcher, orchestrator, DLQ mover, jobs) · `agents` (B: triage :8031, enrichment :8032, response
:8033; three processes, or one process hosting all three with `AGENTS_PROCESS=single`) · `mcp` (B: one host
process on :8034 serving the five tool servers) · `nats-server` (:4230, monitor :8230). SQLite is shared by
`api`, `workers` and `agents` in WAL mode; DuckDB is opened only by `api`.

---

# Part 4: Event backbone (NATS JetStream; ADR 0001)

`.tools/nats-server -js -sd data/jetstream -p 4230 -m 8230` (one ~20 MB process). `BUS=memory` swaps in the
in-process implementation of the same interface (subjects, durable cursors, ack/nak/redelivery, DLQ) for unit
tests and single-process demos. Streams and consumers are created idempotently by `python -m nightjar.workers
setup` (and on worker start).

### Messages
JSON (orjson) of `to_doc(<contract dataclass>)`. Headers: `Nats-Msg-Id` (dedupe), `Nj-Org` (org id), `Nj-Schema`
(`raw_event.v1`, `normalized_event.v1`, `detection.v1`, `correlation_update.v1`, `agent_task.v1`,
`approved_action.v1`, `job.v1`), `Nj-Trace` (trace id from ingest, carried end to end for latency metrics),
`Nj-Received-At`.

### Subjects and streams

| Stream | Subjects | Retention / limits | Purpose |
|---|---|---|---|
| `NJ_RAW` | `nj.raw.<org>.<source>` | limits; max_age 24 h; max_bytes 256 MB; **discard=new** (backpressure); duplicate_window 2 min; file storage | Accepted, unparsed events |
| `NJ_EVENTS` | `nj.events.normalized.<org>.<dataset>` | limits; max_age 7 d; max_bytes 1 GB; discard=old | Normalised events (hunting, detection, UEBA) |
| `NJ_DETECTIONS` | `nj.detections.raw.<org>.<source>`, `nj.detections.scored.<org>` | limits; max_age 30 d | Rule and anomaly detections before/after triage scoring |
| `NJ_INCIDENTS` | `nj.incidents.<org>.opened`, `nj.incidents.<org>.updated` | limits; max_age 30 d | Incident lifecycle for the dispatcher |
| `NJ_WORK` | `nj.agents.tasks.<org>`, `nj.actions.approved.<org>`, `nj.jobs.<kind>` | **work queue**; max_age 24 h | Agent investigations, approved actions, long jobs |
| `NJ_DLQ` | `nj.dlq.<stream>.<consumer>` | limits; max_age 30 d | Dead letters with `Nj-Original-Subject`, `Nj-Original-Seq`, `Nj-Consumer`, `Nj-Deliveries`, `Nj-Error` |
| (core NATS) | `nj.control.rules`, `nj.control.models` | – | Reload signals |

### Durable pull consumers

| Consumer | Process | Stream / filter | ack_wait | max_deliver | max_ack_pending | Backoff | Output |
|---|---|---|---|---|---|---|---|
| `normalizer` | workers | `NJ_RAW` `nj.raw.>` | 30 s | 5 | 1000 | 1 s, 5 s, 30 s, 2 min | `nj.events.normalized.*` |
| `store-writer` | **api** | `NJ_EVENTS` | 60 s | 10 | 5000 | 1 s, 5 s, 30 s | DuckDB batches (500 / 1 s) + `river.batch` SSE |
| `detector` | workers | `NJ_EVENTS` | 30 s | 5 | 2000 | 1 s, 5 s, 30 s, 2 min | `nj.detections.raw.*` (Sigma) |
| `ueba` | workers | `NJ_EVENTS` (auth, process, network datasets) | 60 s | 5 | 2000 | 5 s, 30 s | per-entity windows (5 min); anomalies → `nj.detections.raw.<org>.ueba` |
| `scorer` | workers | `NJ_DETECTIONS` `nj.detections.raw.>` | 30 s | 5 | 500 | 1 s, 5 s, 30 s | `record_detections`; `nj.detections.scored.<org>` |
| `correlator` | workers | `NJ_DETECTIONS` `nj.detections.scored.>` | 30 s | 5 | 200 | 1 s, 5 s, 30 s | `upsert_incident`; `nj.incidents.*` |
| `dispatcher` | workers | `NJ_INCIDENTS` `*.opened` | 30 s | 5 | 50 | 5 s, 30 s | `start_investigation` |
| `orchestrator` | workers | `NJ_WORK` `nj.agents.tasks.>` | 180 s | 3 | **4** (≤ 4 investigations in flight) | 10 s, 60 s | A2A task to triage |
| `responder` | workers | `NJ_WORK` `nj.actions.approved.>` | 60 s | 3 | 10 | 5 s, 30 s | A2A `execute_action` to response |
| `jobs` | workers | `NJ_WORK` `nj.jobs.>` | 600 s | 2 | 1 | 30 s | replays, training, briefs |

**Backpressure.** (1) `NJ_RAW` is byte-limited with discard=new: when full, `publish_raw` raises
`BackpressureError` and the API answers `503 intake_full` + `Retry-After` (verified in the smoke check: the 14th
256-byte publish into a 4 KB stream is refused). (2) `publish_raw` awaits JetStream acks with at most 256 in
flight and a 2 s budget. (3) Every consumer bounds in-flight work with `max_ack_pending` and pulls in batches; a
slow stage lets its stream absorb the burst instead of growing memory. (4) When any consumer's `num_pending` >
10,000 or its oldest unacked message > 60 s, `pipeline_health().status` is `catching_up` ("Catching up: about
2 minutes behind"); intake keeps accepting until `NJ_RAW` is full. (5) Agents are throttled by the orchestrator's
`max_ack_pending=4`.

**Retries and dead letters.** Handlers classify errors: *transient* (database busy, agent unavailable, timeout)
→ `nak(delay=backoff[num_delivered-1])`; *permanent* (cannot normalise, schema invalid) → copy to `NJ_DLQ` with
`Nj-Error` and `term()` (no retry). Messages that exhaust `max_deliver` raise the
`$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>` advisory; the DLQ mover (workers) fetches the
original by sequence and copies it to `nj.dlq.<stream>.<consumer>` (verified in the smoke check). Every handler is
idempotent (dedupe by event id, detection id, correlation key, approval nonce) so redelivery is safe.

**Latency.** `Nj-Trace` + `Nj-Received-At` let the scorer and `finish_run` record per-stage times
(`nightjar_stage_seconds`) and alert-to-triage (`nightjar_alert_to_triage_seconds`, NFR < 60 s at demo scale).

---

# Part 5: Agents (A2A) and tool servers (MCP); ADR 0002, ADR 0006

## 5.1 Agents and Agent Cards
Official `a2a-sdk` 1.3 (protocol 1.0, JSON-RPC binding; verified in `scripts/smoke/a2a_smoke.py`). Each agent is a
Starlette app with `create_agent_card_routes` + `create_jsonrpc_routes`, its card at
`/.well-known/agent-card.json` (public), the RPC endpoint at `/` (peer token required). Inputs and outputs are
**DataParts** (`application/json`) carrying the camelCase contract docs; numbers come back as floats (protobuf
`Value`), so receivers coerce integer fields.

| Agent | Port | Card `name` | Skills (id → input → output artifact) | Accepts calls from |
|---|---|---|---|---|
| Triage | 8031 | `Nightjar Triage` | `triage_incident` → `TriageTask` → `verdict` (`TriageVerdictDoc` + `handoffs`) | orchestrator |
| Enrichment | 8032 | `Nightjar Enrichment` | `enrich_incident` → `EnrichTask` → `enrichment` (`EnrichmentDoc`) | triage |
| Response | 8033 | `Nightjar Response` | `plan_response` → `PlanTask` → `plan` (`{ proposals: ProposalDoc[] }`); `execute_action` → `ExecuteTask` → `result` (`ActionResult`) | triage (`plan_response`); orchestrator/responder consumer (`execute_action`) |

Card fields: `name`, `description` (plain), `version` (package version), `defaultInputModes`/`defaultOutputModes`
`["application/json"]`, `capabilities: { streaming: true }`, `supportedInterfaces: [{ protocolBinding: "JSONRPC",
url, protocolVersion: "1.0" }]`, `skills[]` (id, name, description, tags, examples), and a bearer
`securitySchemes` entry describing the peer-token requirement.

```text
TriageTask  = { runId, orgId, incidentId, trigger: "auto"|"manual"|"rerun", traceId }
EnrichTask  = { runId, orgId, incidentId, indicators: { type, value }[], techniques: string[] }
PlanTask    = { runId, orgId, incidentId, verdict: TriageVerdictDoc }
ExecuteTask = { orgId, approvalId, traceId }
```
Task lifecycle: SUBMITTED → WORKING → COMPLETED (or FAILED with a plain error). Approvals are **not** modelled
as A2A `INPUT_REQUIRED`: `plan_response` completes with pending proposals, and execution is a separate task
started only after humans approve in Nightjar SOC. Each agent records its own steps through
`investigations.append_step` (with `a2aTaskId`), so the timeline shows hand-offs as `handoff_out`/`handoff_in`.

**Peer authentication.** Every agent and the orchestrator hold a peer token (`AGENT_TOKEN_<NAME>`; in dev derived
as `HMAC(SECRET_KEY, "agent:<name>")`). The RPC route rejects unknown callers with 401 and callers not in the
"accepts calls from" column with 403 (verified pattern in the smoke check). Cards are public.

**Policies are code, narration is the model.** Which tools an agent calls, with which arguments, is decided by
deterministic policy code from the incident context (entities, techniques, assets). The model (offline fake by
default) writes the verdict summary and ranks hypotheses into a schema-validated JSON object; it cannot add tool
calls or targets. Verdict rules: the triage agent combines rule fidelity, the triage classifier's calibrated
probability (and its SHAP reasons, which it cites), UEBA scores and enrichment hits; `needs_review` when the
evidence disagrees.

## 5.2 Tool servers (MCP) and least-privilege scopes
Official `mcp` SDK 2.3 (`MCPServer`, verified in `scripts/smoke/mcp_smoke.py`). One host process on :8034 serves
five servers over Streamable HTTP at `/mcp/<server>`; tests connect in-process (`Client(server)`). Each server uses
the SDK's `TokenVerifier`: the bearer token identifies the agent and carries its scopes; every tool checks its
scope **inside the server** (`get_access_token()`), and `tools/list` only lists the tools the caller may use.
Tool results are structured (pydantic models → `structured_content`). Any string a tool returns that came from
logs, intel or tickets is wrapped as `UntrustedText`.

| Server | Tool | Input | Output | Scope | Annotations |
|---|---|---|---|---|---|
| `intel` | `lookup_attack_technique` | `{ techniqueId }` | `{ techniqueId, name, tactics, description, url }` | `intel:read` | read-only |
| | `search_attack` | `{ query, limit≤10 }` | list of the above | `intel:read` | read-only |
| | `lookup_kev` | `{ cveId }` | `KevHit \| null` (hides `dateAdded` > today) | `intel:read` | read-only |
| | `lookup_indicator` | `{ type: ip\|domain\|url\|hash, value }` | `IndicatorFinding` (bundled sample feed, labelled sample) | `intel:read` | read-only |
| `assets` | `get_asset` | `{ nameOrIp }` | `AssetRecord \| null` | `assets:read` | read-only |
| | `search_assets` | `{ query, limit≤20 }` | `AssetRecord[]` | `assets:read` | read-only |
| | `asset_exposure` | `{ name }` | `{ asset, kev: KevHit[], internetFacing, crownJewel }` | `assets:read` | read-only |
| `identity` | `get_identity` | `{ usernameOrEmail }` | `IdentityRecord \| null` | `identity:read` | read-only |
| | `sign_in_profile` | `{ username, days≤90 }` | `{ usualCountries, recentCountries, mfa, privileged }` | `identity:read` | read-only |
| `ticketing` | `create_ticket` | `{ incidentId, title, body, priority, assigneeTeam }` | `Ticket` | `tickets:write` | idempotent |
| | `add_comment` | `{ ticketId, body }` | `Ticket` | `tickets:write` | – |
| | `get_ticket` | `{ ticketId }` | `Ticket \| null` | `tickets:read` | read-only |
| `response` | `propose_action` | `{ incidentId, runId, kind, target, rationale }` | `ApprovalRef` | `response:propose` | not destructive |
| | `isolate_host` / `release_host` | `{ approvalId, host, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |
| | `disable_account` / `enable_account` | `{ approvalId, username, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |
| | `revoke_sessions`, `reset_password` | `{ approvalId, username, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |
| | `revoke_access_key` | `{ approvalId, accessKeyId, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |
| | `block_ip` / `unblock_ip` | `{ approvalId, ip, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |
| | `quarantine_email` | `{ approvalId, messageId, approvalToken }` | `ActionResult` | `response:execute` | **destructive** |

| Caller | Scopes (nothing else) |
|---|---|
| Triage agent | `intel:read`, `assets:read`, `identity:read` |
| Enrichment agent | `intel:read`, `assets:read`, `identity:read` |
| Response agent | `assets:read`, `identity:read`, `tickets:write`, `tickets:read`, `response:propose`, `response:execute` |
| Orchestrator | none (A2A only) |

A destructive tool executes only when **all** hold, checked in this order inside the response server, each refusal
recorded as an `action.refused` audit row and a `guardrail` step: scope `response:execute`; a valid approval token
(`verify_approval_token`: signature, expiry, exact org + action + target); the approval's status is `approved`;
`approvals.consume(nonce)` succeeds (single use); the target is still one of the incident's entities. Only then
does it call the `directory` effector and `approvals.record_result`. Containment is simulated (inventory state
changes; no real endpoints are touched).

## 5.3 Prompt-injection defence (ADR 0006)
1. **Neutralise at intake.** The normaliser passes every free-text field through `safety.neutralise()`; flags go
   to `untrustedFlags`, and an event with flags raises a low-severity detection "Instruction-like text in a log
   field" (it can be an attack on the SOC itself).
2. **Quote, never concatenate.** Prompts have a fixed trusted instruction block; untrusted values are passed only
   through `llm.generate(untrusted=...)`, rendered as escaped `<untrusted field="...">` blocks after a standing
   instruction that they are evidence, not instructions.
3. **Code decides actions.** Tool calls and targets come from policy code and the incident's entity sets; model
   output is schema-validated JSON (enums for verdicts and action kinds) and its free text is display-only.
4. **Targets are bounded.** `request_approval` rejects targets outside the incident (`target_not_in_incident`).
5. **Humans hold the keys.** Destructive tools need a signed, single-use approval token that only the API issues
   after the required people approve; agents never see the signing key.
6. **Render as text.** The web app shows untrusted text as plain text only.
Eval gates (Part 8, `eval/`): 0 destructive executions without approval; 0 tool calls outside scope; 0 proposals
targeting non-incident entities; ≥ 95 % of the injection corpus flagged; verdicts on injected twins match the
clean twins.

---

# Part 6: Demo seed (`make seed`)

`python -m nightjar.cli seed [--fixtures]` (idempotent; `--fixtures` skips the pipeline replay in phase 2; `make reset` drops SQLite, `data/hunting.duckdb` and `data/jetstream`
then re-seeds). All data is synthetic and fictional, labelled "Sample data" in the UI (`organizations.is_sample`).
Domains use the reserved `.example` TLD; IPs use documentation ranges (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24) except the clearly-labelled sample "known bad" range. **Times are relative** to the demo clock:
`night` = the most recent 00:00 Europe/Brussels such that now ≥ night + 09:00 (so screenshots after 09:00 show
"last night"). Recommended screenshot date: `NIGHTJAR_DEMO_TODAY=2025-11-20` (a Thursday), which makes the SEC
clock read "3 business days" and the Thanksgiving holiday visible in the 8-K arithmetic of later incidents.

Seed phases: (1) platform: orgs, users, memberships, inventory, sign-in history, regulator contacts, ingest
keys, 35 historical incidents over the previous 45 days (closed, with triage/contain times for the executive
trends, 4 weekly briefs); (2) **tonight**: the bundled `halden-night` replay is pushed through the real pipeline
in-process (`BUS=memory`, `AGENTS_TRANSPORT=inline`, offline model) with `retime` so detections, incidents and
agent runs are genuine; (3) people: the seed applies the human decisions below at their back-dated times
(approvals, determinations, notices). Until builder B's pipeline lands, phase 2 may insert fixture incidents with
the same keys.

### Organisations and members (password for every demo account: **`Nightjar-demo-2025!`**)
**Halden Logistics** (sample) · timezone Europe/Brussels · ~1,800 staff · regulatory profile:

| Entity id | Legal entity | Jurisdiction | Regimes |
|---|---|---|---|
| `halden-us` | Halden Logistics, Inc. (Delaware; HQ Memphis, TN; fictional ticker HLDN) | US | `sec_8k` (domestic registrant; disclosure committee: CFO Jordan Whitlock, General Counsel, CISO) |
| `halden-eu` | Halden Logistics NV (Antwerp, Belgium) | EU | `nis2` (Annex II, postal and courier services, **important** entity; authority CCB) · `gdpr_art33` (lead authority: Belgian Data Protection Authority) |
| `halden-au` | Halden Logistics Pty Ltd (Melbourne, VIC) | AU | `soci` (responsible entity for a critical freight services asset: "Halden Linehaul East") |

| Name | Email | Role | Title |
|---|---|---|---|
| Pieter Claes | `pieter.claes@halden.example` | admin | IT manager (Antwerp) |
| Ingrid Vos | `ingrid.vos@halden.example` | executive | Chief information security officer |
| Marcus Bell | `marcus.bell@halden.example` | responder | Incident responder (Memphis) |
| Aiko Tanaka | `aiko.tanaka@halden.example` | analyst | Security analyst (Melbourne) |
| Lukas Peeters | `lukas.peeters@halden.example` | analyst | Security analyst (Antwerp) |
| Samira Haddad | `samira.haddad@halden.example` | auditor | Internal audit |

**Brightline Couriers** (sample, EU, 1 entity, `nis2` important) · member `olu.adebayo@brightline.example`
(admin) · 3 assets, 4 identities, 1 low-severity incident. Used for tenant-isolation tests: no Halden user belongs
to it and none of its ids may resolve for Halden users (404).

### Halden inventory (18 assets, 24 identities)
| Asset | Kind | Site / jurisdiction | Service | Criticality | Notes |
|---|---|---|---|---|---|
| `mem-dispatch-01` | server | Memphis hub · US | Dispatch platform (serves EU, US and AU depots) | crown_jewel | |
| `mem-sql-01` | server | Memphis hub · US | Dispatch database | high | |
| `mem-citrix-01` | network | Memphis hub · US | Remote access gateway (NetScaler) | high | CVE-2023-4966 (KEV, known ransomware use) |
| `mem-ws-031` | workstation | Memphis · US | – | low | owner m.bell |
| `mem-ws-040` | workstation | Memphis · US | – | low | owner t.reyes (contractor) |
| `ant-wms-01` | server | Antwerp DC · EU | Warehouse management | crown_jewel | |
| `ant-dc-01` | server | Antwerp DC · EU | Directory | crown_jewel | |
| `ant-fs-01` | server | Antwerp DC · EU | File shares | high | |
| `ant-backup-01` | server | Antwerp DC · EU | Backups | high | backup agent rollout source |
| `ant-vpn-01` | network | Antwerp DC · EU | Remote access (FortiGate) | high | CVE-2024-21762 (KEV) |
| `ant-ws-014` | workstation | Antwerp · EU | – | low | owner j.okafor |
| `ant-ws-022` | workstation | Antwerp · EU | – | low | owner l.peeters |
| `mel-vpn-01` | network | Melbourne depot · AU | Remote access | high | |
| `mel-depot-gw-01` | ot_gateway | Melbourne depot · AU | Fleet telematics (Linehaul East) | high | SOCI asset component |
| `mel-ws-007` | workstation | Melbourne · AU | – | low | owner a.tanaka |
| `aws-prod` | cloud_account | AWS us-east-1 · US | Customer portal, invoices | high | account 111122223333 |
| `m365-tenant` | saas | Global | Email and files | high | |
| `okta` | saas | Global | Sign-in | crown_jewel | |

Identities: `j.okafor` (Joy Okafor, accounts payable lead, Antwerp, push MFA, usual BE), `p.claes`, `i.vos`,
`m.bell`, `a.tanaka`, `l.peeters`, `s.haddad`, `r.dubois` (warehouse supervisor, Antwerp), `e.janssens` (IT admin,
Antwerp), `adm-ejanssens` (privileged admin), `k.wouters` (finance, Antwerp), `g.lambert` (legal and data
protection, Antwerp), `d.harper` (dispatch manager, Memphis), `j.whitlock` (CFO, Memphis), `c.nguyen` (IT
operations, Memphis), `o.fernandez` (HR, Memphis), `t.reyes` (contractor data analyst, Memphis, usual hours
08–17 Central), `svc-dispatch` (privileged service), `svc-backup` (privileged service), `ci-deploy` (AWS CI user,
access key `AKIAEXAMPLEHALDEN01`), `n.kelly` (depot manager, Melbourne), `b.singh` (fleet operations,
Melbourne), `h.morrison` (IT, Melbourne), `w.chen` (finance, Melbourne).

### Incidents
| Ref | When | Incident | Kind / severity | Expected outcome in the seed |
|---|---|---|---|---|
| NJ-1001…1035 | previous 45 days | history (phishing, spray, malware, benign) | mixed | closed; drives MTTT/MTTC trends and 4 weekly briefs |
| **NJ-1036** | night − 1, 04:10–04:53 | **Ransomware precursor on the shared dispatch platform**: gateway session reuse on `mem-citrix-01` (CVE-2023-4966; T1190), sign-in to `mem-dispatch-01` as `svc-dispatch` (T1078.002), signed-binary proxy beacon to the sample bad range (T1218.011, T1071.001), LSASS dump via comsvcs (T1003.001), directory discovery (T1087.002, T1482), PsExec to `ant-wms-01` and `mem-sql-01` (T1021.002, T1569.002), scheduled task (T1053.005), shadow-copy deletion blocked on `ant-wms-01` (T1490) | ransomware_precursor / critical | Triage true_positive at 04:42; isolate `mem-dispatch-01` and `ant-wms-01` (crown jewels: Marcus + Ingrid approve 04:46/04:51, executed 04:52), disable `svc-dispatch` (privileged: both approve), block the beacon IP; dispatch on manual fallback for 9 h (released 13:52). **The reportable incident**: Ingrid determines `nis2` significant (awareAt night − 1 04:52; early warning **submitted** 15:20; incident notification **due night + 2 04:52**, draft awaiting approval), `soci` relevant impact (awareAt 04:52; 72 h report drafted, due night + 2 04:52), `sec_8k` material (determined night − 1 16:00 America/New_York by the disclosure committee; Item 1.05 due 17:30 ET on the 4th business day). Status monitoring, stage report. |
| **NJ-1037** | night 03:05–03:40 | **Phishing → credential misuse** for `j.okafor`: link phish delivered (T1566.002), 03:12:04 sign-in from a new country on a hosting network (T1078.004), 6 MFA pushes, 5 refused (T1621), external forwarding inbox rule (T1114.003), 140 mailbox items read (T1114.002) | credential_misuse / high | True_positive 0.9+; revoke sessions + disable account + reset password approved by Marcus (03:41–03:44) and executed; ticket opened automatically. Regimes: `nis2` proposed "no" (no service disruption), `gdpr_art33` left **assess** (personal data review open; no clock). Status monitoring. |
| **NJ-1038** | night 01:40–02:05 | **Cloud access key used from an unfamiliar network**: CI key exposed in a public repository alert (T1552.001), used from 198.51.100.23 (T1078.004), bucket listing and 38 object reads on `halden-invoices-prod` (T1530), user creation denied (T1136.003) | cloud_credential_exposure / high | True_positive; **`revoke_access_key` pending approval** (the "Hold to authorise" demo); ticket opened. Status containing. |
| **NJ-1039** | night 01:00–01:25 | **Backup software update caused 146 alerts** on 12 hosts (`svc-backup`: shadow-copy listing, encoded PowerShell, WMI) | benign_activity / low | Every detection scored likely_noise; one incident instead of 146; triage verdict benign → resolved (benign), reversible. Shows false-positive reduction. |
| **NJ-1040** | night 02:20–02:50 (12:20 Melbourne) | **Password spray against the Melbourne remote-access gateway**: 412 failed sign-ins for 63 accounts from 3 addresses, no success (T1110.003) | brute_force / medium | True_positive; block 3 IPs (approved by Marcus, executed); resolved (contained); `soci` proposed "no" (no impact on the asset). |
| **NJ-1041** | night 02:40–03:30 (19:40 Memphis) | **Unusual data transfer by a contractor account** `t.reyes`: off-hours, 4 new file shares, file-sync tool to personal cloud storage, 3.2 GB out (T1567.002) | data_exfiltration / high | UEBA anomaly ◇ 0.97 + rule hit; triage **needs_review** (0.58); assigned to Aiko; investigating. |

Also seeded: tickets for NJ-1036/37/38; ≥ 6 saved searches; 1 open DLQ message (a malformed syslog line, for the
pipeline view); champion triage and UEBA models with cards; regulator contacts for all four regimes; ≥ 200 audit
rows forming a valid chain. Ground truth for the replay lives in `eval/scenarios/halden-night.labels.json`
(event id → scenario, malicious flag, expected technique), never on the events.

---

# Part 7: Module ownership

Three builders work in parallel. Only the owner edits a path; cross-boundary changes go through Part 3 and
`contracts.py` (owner: project lead; both builders may propose changes in the same commit as the code that needs
them, updating this document). One heavy process at a time on the machine (`~/Work/_playbook/heavy`).

| Builder | Owns | Paths |
|---|---|---|
| **A: platform + API** | FastAPI app, settings, auth/sessions/RBAC, orgs/members/invitations/ingest keys, audit, SSE outbox, jobs table, every HTTP router; incidents store, timelines, investigations store, approvals gate + token issuing, inventory/directory and effectors, ticketing; **incident clock** (regimes, applicability proposals, deadlines, business days, notices, mock submissions); hunting store wiring (DuckDB writer, structured query compiler, saved searches, suggestions); executive posture and weekly brief; settings; seed and reset; Alembic migrations (except `pl_` tables) | `backend/nightjar/{core,db,api,schemas,services,incidents,investigations,approvals,directory,ticketing,regimes,hunting,executive,seed}/**`, `backend/nightjar/{main,cli,clock}.py`, `backend/alembic/**`, `backend/tests/{platform,api,incidents,reporting,hunting,seed}/**` |
| **B: pipeline + intelligence** | Stream backbone (NATS + memory bus), ingest formats and normaliser, Sigma detection and ATT&CK mapping, rule bundle, correlation, UEBA (Isolation Forest + robust z), triage classifier (baseline, LightGBM, Optuna, calibration, SHAP, MLflow, model cards, drift), offline intel (ATT&CK/KEV bundles), LLM adapter + fake, untrusted-text neutraliser, the three A2A agents and orchestrator, the five MCP servers, workers, replay scenarios, eval harness and gates | `backend/nightjar/{stream,ingest,detect,correlate,ml,agents,mcp_servers,intel,llm,workers}/**`, `backend/nightjar/safety.py`, `backend/nightjar/db/models/pipeline.py` (+ its migration), `backend/tests/{pipeline,detect,ml,agents,safety}/**`, `eval/**`, `scripts/**` (except Makefile-level tooling) |
| **C: web app** | SvelteKit + TypeScript + Tailwind 4.1 console, landing page, sign-in, design system, Playwright screenshots | `frontend/**` |
| Lead | contracts, docs, ADRs, Makefile, CI, README | `backend/nightjar/contracts.py`, `docs/**`, `Makefile`, `.github/**`, `README.md`, `.env.example` |

---

# Part 8: Project conventions

- **Dates in prose.** Write docs, comments, copy and fixtures as of late November 2025 or in timeless terms. NIS2
  applies (transposition deadline 17 October 2024; Belgium's law in force since 18 October 2024); the SEC
  cybersecurity disclosure rule has applied since 18 December 2023; SOCI Part 2B reporting is in force. Do not
  describe later events as having happened, and make no adoption claims about A2A or MCP beyond what was public
  by then.
- **No real run timestamps in committed files.** Eval reports (`eval/RESULTS.md`), model cards, data notes and
  committed artefacts carry versions and metrics, not wall-clock dates. Generated timestamps live only in
  git-ignored outputs (`mlruns/`, `data/`, `eval/out/`). Strip `Create Date` from Alembic revisions (the template
  already omits it).
- **One clock.** Business code uses `nightjar.clock.now()/today()`; security timers use `clock.real_now()`.
- **Seed data is relative** to the demo clock; never hard-code calendar dates for demo records (except statutory
  dates and holiday tables).
- **Eval gates** (`make eval`, CI): detection precision/recall on labelled replays; UEBA precision@k and recall vs
  injected anomalies; triage AUC/PR-AUC, calibration (Brier, ECE) and false-positive reduction vs rules alone;
  triage-agent accuracy vs labels; alert-to-triage time; safety (no destructive tool without approval, no
  out-of-scope tool call, injection corpus). Results tables in `eval/RESULTS.md` and the README.
- **Commit messages** are conventional (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`, `refactor:`, `perf:`),
  in logical batches, with no dates and no tool attribution. Stage explicit paths; never commit `data/`, `.venv`,
  `node_modules`, `.svelte-kit`, `build`, `.tools`, `mlruns/`, `.env`, `*.db` or `*.duckdb`.
- **Client-facing copy** never shows engineering vocabulary (STANDARDS §4 forbidden list). Say "colleague",
  "hand-off", "checked", "pattern check" in the UI; "agent", "A2A", "MCP", "model", "pipeline" only in docs and
  the API.
