"""Shared, frozen dataclasses between builder A (platform, API, incidents, clock, hunting store) and builder B
(stream, detection, correlation, ML, agents, tool servers). API contract Part 3 is the prose version.

Rules:
* Changing a field here means changing docs/API_CONTRACT.md Part 3 in the same commit.
* Datetimes are timezone-aware UTC. Ids are UUIDs except event ids (stable content hashes, `str`).
* Anything that came from a log field is UNTRUSTED (ADR 0006): it may be shown to people, scored by models
  and passed to agents only as data inside `UntrustedText`/`raw`, never as instructions.
* `to_doc()` helpers produce the camelCase JSON used on the wire, in SSE and in A2A DataParts.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Severity = Literal["info", "low", "medium", "high", "critical"]
SEVERITY_ORDER: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
Jurisdiction = Literal["EU", "US", "AU"]
RegimeId = Literal["nis2", "sec_8k", "soci", "gdpr_art33"]
IngestSource = Literal["webhook", "syslog", "file", "replay"]
EventFormat = Literal["ecs", "winlog", "sysmon", "cloudtrail", "okta", "m365", "syslog_json", "nightjar"]
IncidentKind = Literal[
    "phishing",
    "credential_misuse",
    "ransomware_precursor",
    "cloud_credential_exposure",
    "brute_force",
    "data_exfiltration",
    "malware",
    "insider",
    "benign_activity",
    "other",
]
IncidentStatus = Literal["new", "triage", "investigating", "containing", "monitoring", "resolved", "closed"]
IncidentStage = Literal["triage", "investigate", "contain", "report"]
ActionKind = Literal[
    # destructive / business-affecting: always need human approval (ADR 0006)
    "isolate_host",
    "disable_account",
    "revoke_sessions",
    "reset_password",
    "revoke_access_key",
    "block_ip",
    "quarantine_email",
    # reversals (also approved, by a responder)
    "release_host",
    "enable_account",
    "unblock_ip",
    # non-destructive: run without approval, still recorded
    "open_ticket",
    "notify_owner",
    "collect_triage_package",
    "add_to_watchlist",
]
DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "isolate_host",
        "disable_account",
        "revoke_sessions",
        "reset_password",
        "revoke_access_key",
        "block_ip",
        "quarantine_email",
        "release_host",
        "enable_account",
        "unblock_ip",
    }
)
AgentName = Literal["triage", "enrichment", "response", "orchestrator"]
StepKind = Literal[
    "plan",
    "tool_call",
    "tool_result",
    "handoff_out",
    "handoff_in",
    "finding",
    "verdict",
    "proposal",
    "guardrail",
    "error",
]


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def to_doc(obj: Any) -> Any:
    """Dataclass → camelCase JSON-ready dict (UUIDs and datetimes as strings). `raw`/`fields`/`detail` keep keys."""
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    if isinstance(obj, dict):
        keep = {"raw", "fields", "detail", "features", "params", "labels", "input", "output"}
        return {(_camel(k) if isinstance(k, str) else k): (v if k in keep else to_doc(v)) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_doc(v) for v in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Untrusted content
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class UntrustedText:
    """A string that came from a log field, email, file name or tool result. `text` is the neutralised form
    (control/zero-width characters removed, length-capped); `flags` names any instruction-like patterns found
    (e.g. "imperative_to_agent", "role_marker", "tool_syntax", "hidden_text"). Produced by
    `nightjar.safety.neutralise()` (builder B), used by everyone."""

    text: str
    field: str
    flags: tuple[str, ...] = ()
    truncated: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Events (API contract §1.6: the ECS-like normalised schema)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RawEvent:
    org_id: uuid.UUID
    source: IngestSource
    received_at: datetime
    payload: dict[str, Any]
    format: EventFormat | None = None  # hint; the normaliser sniffs when None
    source_ref: str | None = None  # ingest-key prefix, file name or replay scenario id
    dedupe_key: str | None = None  # becomes the JetStream Nats-Msg-Id


@dataclass(frozen=True, slots=True)
class HostRef:
    name: str
    ip: tuple[str, ...] = ()
    os: str | None = None
    id: str | None = None


@dataclass(frozen=True, slots=True)
class UserRef:
    name: str
    domain: str | None = None
    email: str | None = None
    id: str | None = None


@dataclass(frozen=True, slots=True)
class Endpoint:
    ip: str | None = None
    port: int | None = None
    geo_country: str | None = None  # ISO-3166 alpha-2
    as_org: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    name: str | None = None
    executable: str | None = None
    pid: int | None = None
    command_line: str | None = None
    parent_name: str | None = None
    parent_executable: str | None = None
    hash_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CloudInfo:
    provider: Literal["aws", "azure", "gcp", "m365", "okta"] | None = None
    account_id: str | None = None
    region: str | None = None
    service: str | None = None
    api: str | None = None  # e.g. "s3:ListBuckets", "iam:CreateAccessKey"
    access_key_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmailInfo:
    sender: str | None = None
    recipients: tuple[str, ...] = ()
    subject: str | None = None
    attachment_names: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    verdict: str | None = None  # mail gateway verdict if any


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """One event after normalisation. `event_id` is a stable hash (org + dataset + source ids + timestamp) so
    replays and redeliveries dedupe. `raw` is the original payload (untrusted, kept for evidence)."""

    org_id: uuid.UUID
    event_id: str
    timestamp: datetime
    ingested_at: datetime
    # "windows.sysmon", "windows.security", "aws.cloudtrail", "okta.system", "m365.audit", "vpn.syslog",
    # "mail.gateway", "edr.alert"
    dataset: str
    kind: Literal["event", "alert", "signal"]
    # ECS event.category: authentication, process, network, iam, file, email, web, configuration, intrusion_detection
    category: tuple[str, ...]
    # ECS event.type: start, end, info, creation, deletion, change, denied, allowed, access
    type: tuple[str, ...]
    # e.g. "logon", "logon-failed", "process-started", "mfa-push-denied", "inbox-rule-created", "s3-list-buckets"
    action: str
    outcome: Literal["success", "failure", "unknown"]
    message: UntrustedText
    severity: int | None = None  # source severity 0–100 if the sender supplied one
    host: HostRef | None = None
    user: UserRef | None = None
    source: Endpoint | None = None
    destination: Endpoint | None = None
    process: ProcessInfo | None = None
    cloud: CloudInfo | None = None
    email: EmailInfo | None = None
    url: str | None = None
    file_path: str | None = None
    file_hash_sha256: str | None = None
    related_ips: tuple[str, ...] = ()
    related_users: tuple[str, ...] = ()
    related_hosts: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)  # replay scenario id, sample marker; NEVER ground truth
    raw: dict[str, Any] = field(default_factory=dict)
    untrusted_flags: tuple[str, ...] = ()  # fields where neutralise() found instruction-like content


@dataclass(frozen=True, slots=True)
class IngestAck:
    accepted: int
    duplicates: int
    rejected: int
    first_seq: int | None
    last_seq: int | None
    errors: tuple[str, ...] = ()  # per-item reasons for rejected entries (index: reason)


# ─────────────────────────────────────────────────────────────────────────────
# Detections, behaviour anomalies, triage scores
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class AttackRef:
    technique_id: str  # "T1003.001"
    name: str  # "OS Credential Dumping: LSASS Memory"
    tactics: tuple[str, ...]  # ("credential-access",)


@dataclass(frozen=True, slots=True)
class Entities:
    hosts: tuple[str, ...] = ()
    users: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    cloud_accounts: tuple[str, ...] = ()
    access_keys: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Reason:
    """One plain-language reason (SHAP contribution, z-score or rule evidence)."""

    feature: str
    value: float | str | None
    contribution: float  # signed; positive pushes towards "real" / "unusual"
    text: str  # "Sign-in from a country this person has never used"
    baseline: float | str | None = None


@dataclass(frozen=True, slots=True)
class TriageScore:
    detection_id: uuid.UUID
    probability: float  # calibrated P(real)
    band: Literal["likely_real", "uncertain", "likely_noise"]
    threshold: float
    reasons: tuple[Reason, ...]  # top 3 SHAP reasons, plain language
    model_version: str
    suppressed: bool  # band == likely_noise and the rule is not on the never-suppress list


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    """Evidence copy of an event stored with a detection (bounded), so agents and the API never need the
    hunting store from another process."""

    event_id: str
    timestamp: datetime
    dataset: str
    action: str
    summary: UntrustedText
    host: str | None = None
    user: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Detection:
    id: uuid.UUID
    org_id: uuid.UUID
    source: Literal["sigma", "ueba", "correlation", "external"]
    rule_id: str  # Sigma rule UUID, "ueba:user:<feature-set>", or the external alert id
    rule_title: str
    severity: Severity
    confidence: float  # rule fidelity 0..1 (Sigma level/status, or anomaly score)
    attack: tuple[AttackRef, ...]
    entities: Entities
    first_seen: datetime
    last_seen: datetime
    event_ids: tuple[str, ...]
    evidence: tuple[EventSnapshot, ...]  # at most 20
    summary: str  # templated plain language (not model-generated)
    fields: dict[str, Any] = field(default_factory=dict)  # matched values (untrusted)
    triage: TriageScore | None = None


@dataclass(frozen=True, slots=True)
class Anomaly:
    id: uuid.UUID
    org_id: uuid.UUID
    entity_type: Literal["user", "host"]
    entity_id: str
    window_start: datetime
    window_end: datetime
    score: float  # 0..1, higher = more unusual (rank-normalised Isolation Forest)
    isolation_score: float  # raw -score_samples
    robust_z: dict[str, float]  # per feature
    features: dict[str, float]
    reasons: tuple[Reason, ...]
    model_version: str
    detection_id: uuid.UUID | None = None  # the `source="ueba"` Detection emitted onto the stream


# ─────────────────────────────────────────────────────────────────────────────
# Correlation → incidents
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class CorrelationUpdate:
    """B's correlator says: these detections belong together. A upserts the incident by `correlation_key`
    (idempotent), attaches detections, recomputes severity/ATT&CK/assets/identities and the timeline."""

    org_id: uuid.UUID
    correlation_key: str  # stable: hash of the seed entity cluster + opening window
    detection_ids: tuple[uuid.UUID, ...]
    kind: IncidentKind
    title: str  # templated plain language: "Unusual sign-in followed by mailbox rule for j.okafor"
    severity: Severity
    attack: tuple[AttackRef, ...]
    entities: Entities
    first_seen: datetime
    last_seen: datetime
    summary: str
    max_triage_probability: float | None
    all_likely_noise: bool  # every detection scored likely_noise → A marks the incident "likely benign"
    merge_incident_ids: tuple[uuid.UUID, ...] = ()  # existing incidents this update merges into one


@dataclass(frozen=True, slots=True)
class IncidentRef:
    id: uuid.UUID
    reference: str  # "NJ-1042"
    created: bool
    status: IncidentStatus


@dataclass(frozen=True, slots=True)
class OpenIncidentIndexEntry:
    """What the correlator needs to attach new detections to an open incident after a restart."""

    incident_id: uuid.UUID
    correlation_key: str
    entities: Entities
    attack_tactics: tuple[str, ...]
    last_seen: datetime
    status: IncidentStatus


# ─────────────────────────────────────────────────────────────────────────────
# Inventory (asset inventory / identity directory; owned by A, read by B's tool servers)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: uuid.UUID
    name: str  # "ant-wms-01"
    kind: Literal["server", "workstation", "laptop", "network", "cloud_account", "saas", "ot_gateway"]
    ips: tuple[str, ...]
    os: str | None
    site: str  # "Antwerp DC", "Memphis hub", "Melbourne depot", "AWS us-east-1"
    jurisdiction: Jurisdiction
    owner: str | None  # identity username
    business_service: str | None  # "Warehouse management", "Dispatch", "Payroll"
    criticality: Literal["low", "medium", "high", "crown_jewel"]
    tags: tuple[str, ...]
    isolated: bool
    vulnerabilities: tuple[str, ...]  # CVE ids from the (mock) scanner


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: uuid.UUID
    username: str  # "j.okafor"
    display_name: str
    email: str
    department: str
    title: str
    jurisdiction: Jurisdiction
    manager: str | None
    privileged: bool
    mfa: Literal["push", "totp", "fido2", "none"]
    usual_countries: tuple[str, ...]
    disabled: bool
    kind: Literal["employee", "contractor", "service"]


# ─────────────────────────────────────────────────────────────────────────────
# Agent investigations (replayable step log) and response actions
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class OrgProfile:
    org_id: uuid.UUID
    name: str
    timezone: str
    entities: tuple[dict[str, Any], ...]  # LegalEntity docs: API contract §1.4 `profile.entities`
    crown_jewel_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IncidentContext:
    """Everything an agent may read about an incident; built by A from SQLite (no hunting-store access)."""

    org: OrgProfile
    incident_id: uuid.UUID
    reference: str
    title: str
    kind: IncidentKind
    severity: Severity
    status: IncidentStatus
    attack: tuple[AttackRef, ...]
    entities: Entities
    detections: tuple[Detection, ...]
    anomalies: tuple[Anomaly, ...]
    assets: tuple[AssetRecord, ...]
    identities: tuple[IdentityRecord, ...]
    first_seen: datetime
    last_seen: datetime
    prior_runs: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentStep:
    """One recorded step. The ordered list for a run is the replayable decision log (NFR)."""

    agent: AgentName
    kind: StepKind
    title: str  # plain language for the timeline: "Checked the sign-in country against the person's history"
    started_at: datetime
    duration_ms: int
    detail: dict[str, Any] = field(default_factory=dict)  # tool input/output, model output, guardrail findings
    tool: str | None = None  # "<server>.<tool>", e.g. "intel.lookup_kev"
    a2a_task_id: str | None = None
    model: str | None = None  # "offline-fake" or the cloud model id
    input_hash: str | None = None  # sha256 of the exact prompt/tool input (replay verification)
    flags: tuple[str, ...] = ()  # guardrail flags raised during this step


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: Literal["detection", "event", "anomaly", "intel", "asset", "identity", "model_reason"]
    ref: str
    text: str


@dataclass(frozen=True, slots=True)
class ReportableFacts:
    """Facts the triage/enrichment agents can establish; A's incident clock proposes regime applicability from
    these plus the org profile. A human (`reporting:assess`) makes the determination."""

    malicious_suspected: bool
    affected_jurisdictions: tuple[Jurisdiction, ...]
    affected_entities: tuple[str, ...]  # legal entity names from the org profile
    service_disruption: Literal["none", "degraded", "outage"]
    disruption_hours: float | None
    critical_asset_impact: Literal["none", "relevant", "significant"]  # SOCI availability wording
    personal_data_involved: bool
    records_affected_estimate: int | None
    cross_border: bool
    financial_impact_estimate_eur: float | None


@dataclass(frozen=True, slots=True)
class TriageVerdict:
    verdict: Literal["true_positive", "benign", "false_positive", "needs_review"]
    confidence: float
    summary: str  # plain language, cites evidence by ref
    evidence: tuple[Evidence, ...]
    model_probability: float | None  # the triage classifier's calibrated score the agent cited
    facts: ReportableFacts | None


@dataclass(frozen=True, slots=True)
class KevHit:
    cve_id: str
    vendor: str
    product: str
    name: str
    date_added: str  # YYYY-MM-DD (entries after clock.today() are hidden)
    ransomware_use: Literal["Known", "Unknown"]


@dataclass(frozen=True, slots=True)
class IndicatorFinding:
    type: Literal["ip", "domain", "url", "hash", "cve", "user", "access_key"]
    value: str
    verdict: Literal["malicious", "suspicious", "unknown", "benign"]
    source: str  # "bundled intel", "CISA KEV", "asset inventory", ...
    detail: str
    kev: KevHit | None = None


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    indicators: tuple[IndicatorFinding, ...]
    techniques: tuple[AttackRef, ...]
    asset_notes: tuple[str, ...]
    identity_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposedAction:
    kind: ActionKind
    target: str  # asset name, username, access key id, IP or message id; must be in the incident's entities
    target_type: Literal["host", "user", "access_key", "ip", "email", "ticket"]
    rationale: str
    expected_effect: str  # "Stops the host talking to anything except the security tools"
    reversible: bool
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def destructive(self) -> bool:
        return self.kind in DESTRUCTIVE_ACTIONS


@dataclass(frozen=True, slots=True)
class ApprovalRef:
    approval_id: uuid.UUID
    status: Literal["auto_approved", "pending", "approved", "rejected", "expired", "executed", "failed"]
    required_roles: tuple[str, ...]  # ("responder",) or ("responder", "executive") for crown jewels
    approvals: tuple[dict[str, Any], ...]  # [{userId, role, decidedAt}]
    token: str | None = None  # signed approval token, present once every required role approved


@dataclass(frozen=True, slots=True)
class ActionResult:
    approval_id: uuid.UUID
    kind: ActionKind
    target: str
    executed: bool
    status: Literal["executed", "refused", "failed"]
    detail: str
    executed_at: datetime
    tool_call: str  # "response.isolate_host"


@dataclass(frozen=True, slots=True)
class RunRef:
    run_id: uuid.UUID
    incident_id: uuid.UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline, rules, models (B → A for the API)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ConsumerStat:
    name: str
    pending: int
    ack_pending: int
    redelivered: int


@dataclass(frozen=True, slots=True)
class StreamStat:
    name: str
    messages: int
    bytes: int
    consumers: tuple[ConsumerStat, ...]


@dataclass(frozen=True, slots=True)
class PipelineHealth:
    mode: Literal["nats", "memory"]
    connected: bool
    status: Literal["normal", "catching_up", "degraded", "offline"]
    plain_status: str  # "Taking in events normally" (shown in the status line)
    events_per_minute: float
    streams: tuple[StreamStat, ...]
    dlq_messages: int
    alert_to_triage_p50_s: float | None
    alert_to_triage_p95_s: float | None


@dataclass(frozen=True, slots=True)
class ScenarioInfo:
    id: str  # "halden-night"
    title: str
    events: int
    duration_minutes: float
    incidents_expected: int
    source: Literal["synthetic", "otrf"]


@dataclass(frozen=True, slots=True)
class RuleInfo:
    id: str
    title: str
    source: Literal["sigma", "ueba", "nightjar"]
    level: Severity
    status: str  # Sigma status: stable | test | experimental
    logsource: str  # "windows/process_creation"
    attack: tuple[AttackRef, ...]
    enabled: bool
    hits_7d: int
    precision_estimate: float | None  # from triage feedback / eval
    origin: str  # "SigmaHQ r2025-11-01 (DRL 1.1)" or "Nightjar"


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: Literal["triage", "ueba"]
    version: str
    stage: Literal["champion", "challenger", "archived"]
    trained_on: str  # data description (no wall-clock dates in committed cards)
    metrics: dict[str, float]
    gates_passed: bool
    card_markdown: str
    promoted_by: str | None = None


@dataclass(frozen=True, slots=True)
class AgentCardInfo:
    agent: AgentName
    name: str
    url: str
    skills: tuple[str, ...]
    tool_scopes: tuple[str, ...]
    healthy: bool
