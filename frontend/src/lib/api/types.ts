// Wire types from docs/API_CONTRACT.md Part 1 (camelCase JSON). Keep in step with the contract.

export type ISODate = string; // ISO-8601 with offset
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type Role = 'admin' | 'executive' | 'responder' | 'analyst' | 'auditor';
export type Regime = 'nis2' | 'sec_8k' | 'soci' | 'gdpr_art33';

/** Text that came from a log. Render as plain text only (contract §1.1). */
export interface UntrustedText {
	text: string;
	field?: string;
	flags: string[];
	truncated?: boolean;
}

export interface Paged<T> {
	items: T[];
	page: number;
	pageSize: number;
	total: number;
	totalPages: number;
}

// ---------- auth, org ----------
export interface User {
	id: string;
	email: string;
	fullName: string;
	title: string;
	avatarColor: string;
	createdAt: ISODate;
}
export interface OrgSummary {
	id: string;
	name: string;
	slug: string;
	role: Role;
	roleLabel: string;
}
export interface LegalEntity {
	id: string;
	name: string;
	jurisdiction: 'EU' | 'US' | 'AU';
	country: string;
	timezone: string;
	nis2?: {
		applies: boolean;
		sector: string;
		subsector?: string;
		entityType: 'essential' | 'important';
		memberState: string;
		authority: string;
		authorityName: string;
	};
	sec?: { registrant: boolean; filerType: 'domestic'; cik?: string; ticker?: string; disclosureCommittee: string[] };
	soci?: { responsibleEntity: boolean; assetClasses: string[]; assets: string[] };
	gdpr?: { controller: boolean; leadAuthority: string; leadAuthorityName: string };
}
export interface OrgProfile {
	entities: LegalEntity[];
	crownJewelTags: string[];
}
export interface RegimeContact {
	name: string;
	role: string;
	email: string;
	phone: string;
	notes: string;
}
export interface RegimeContactBlock {
	jurisdiction: string;
	authorityName: string;
	portalUrl: string;
	internalOwner: { userId: string; name: string };
	contacts: RegimeContact[];
}
export type RegimeContacts = Partial<Record<Regime, RegimeContactBlock>>;
export interface OrgSettings {
	regimeContacts: RegimeContacts;
	crownJewelAssetTags: string[];
	autoInvestigate: boolean;
	weeklyBriefDay: string;
}
export interface Org {
	id: string;
	name: string;
	slug: string;
	timezone: string;
	profile: OrgProfile;
	settings: OrgSettings;
	isSample: boolean;
	createdAt: ISODate;
}
export interface Me {
	user: User;
	activeOrg: Org | null;
	membership: { role: Role; roleLabel: string; permissions: string[] } | null;
	orgs: OrgSummary[];
	session: { expiresAt: ISODate; absoluteExpiresAt: ISODate };
	clock: { today: string; now?: string; demo: boolean };
}
export interface Member {
	id: string;
	user: User;
	role: Role;
	roleLabel: string;
	joinedAt: ISODate;
}
export interface Invitation {
	id: string;
	email: string;
	role: Role;
	roleLabel: string;
	invitedBy: string;
	expiresAt: ISODate;
	acceptUrl?: string;
}
export interface InvitationPreview {
	orgName: string;
	email: string;
	role: Role;
	roleLabel: string;
	expiresAt: ISODate;
	accountExists: boolean;
}

// ---------- events ----------
export interface RiverLine {
	eventId: string;
	at: ISODate;
	text: string;
	dataset: string;
	severity: Severity | null;
	anomaly: boolean;
	incidentId: string | null;
}
export interface NormalizedEventDoc {
	eventId: string;
	timestamp: ISODate;
	ingestedAt: ISODate;
	dataset: string;
	kind: 'event' | 'alert' | 'signal';
	category: string[];
	type: string[];
	action: string;
	outcome: 'success' | 'failure' | 'unknown';
	severity: number | null;
	message: UntrustedText;
	host: { name: string; ip: string[]; os: string; id: string } | null;
	user: { name: string; domain: string; email: string; id: string } | null;
	source: { ip: string; port: number | null; geoCountry: string | null; asOrg: string | null } | null;
	url?: string | null;
	untrustedFlags?: string[];
	raw?: Record<string, unknown>;
}

// ---------- detections, rules ----------
export interface AttackRef {
	techniqueId: string;
	name: string;
	tactics: string[];
}
export interface Reason {
	feature: string;
	value: unknown;
	baseline: unknown;
	contribution: number;
	text: string;
}
export interface RuleInfo {
	id: string;
	title: string;
	source: 'sigma' | 'ueba' | 'nightjar';
	level: string;
	status: string;
	logsource: Record<string, string>;
	attack: AttackRef[];
	enabled: boolean;
	hits7d: number;
	precisionEstimate: number | null;
	origin: string;
	/** hits per day for the last 7 days, oldest first */
	hits7dDaily?: number[];
	/** active per-org exceptions ("Lower the noise", "Mark as expected") */
	exceptions?: number;
}
export interface EventSnapshotDoc {
	eventId: string;
	timestamp: ISODate;
	dataset: string;
	action: string;
	summary: UntrustedText;
	host: string | null;
	user: string | null;
	fields: Record<string, unknown>;
}

// ---------- incidents ----------
export type IncidentStatus = 'new' | 'triage' | 'investigating' | 'containing' | 'monitoring' | 'resolved' | 'closed';
export type Stage = 'triage' | 'investigate' | 'contain' | 'report';
export interface Deadline {
	id: string;
	incidentId: string;
	incidentReference: string;
	regime: Regime;
	regimeLabel: string;
	noticeType: string;
	noticeLabel: string;
	jurisdiction: string;
	entityId?: string;
	startsAt: ISODate | null;
	dueAt: ISODate | null;
	dueAtLocal: { at: string; timezone: string } | null;
	businessDays: number | null;
	state: 'not_started' | 'on_track' | 'due_soon' | 'overdue' | 'submitted' | 'not_required';
	remainingSeconds: number | null;
	noticeId: string | null;
	basis: string;
	/** when the notice went out (drives the 10-minute "✓ Sent" cell) */
	submittedAt?: ISODate | null;
	/** an open assessment with "Not yet: decide by…" recorded */
	decideBy?: ISODate | null;
}
export interface IncidentSummary {
	id: string;
	reference: string;
	title: string;
	kind: string;
	severity: Severity;
	status: IncidentStatus;
	statusLabel?: string;
	stage: Stage;
	assets: string[];
	identities: string[];
	attackTactics: string[];
	detectionCount: number;
	firstSeen: ISODate;
	lastSeen: ISODate;
	assignee: User | null;
	verdict: 'true_positive' | 'benign' | 'false_positive' | 'needs_review' | null;
	likelyBenign: boolean;
	reportable: 'yes' | 'no' | 'assess' | null;
	nextDeadline: Deadline | null;
	pendingApprovals: number;
}
export interface AssetDoc {
	id: string;
	name: string;
	kind: string;
	ips: string[];
	os: string;
	site: string;
	jurisdiction: string;
	owner: string | null;
	businessService: string | null;
	criticality: 'low' | 'medium' | 'high' | 'crown_jewel';
	tags: string[];
	isolated: boolean;
	vulnerabilities: { cveId: string; name: string; knownRansomware: boolean }[];
}
export interface IdentityDoc {
	id: string;
	username: string;
	displayName: string;
	email: string;
	department: string;
	title: string;
	jurisdiction: string;
	manager: string | null;
	privileged: boolean;
	mfa: string;
	usualCountries: string[];
	disabled: boolean;
	/** the directory says employee, contractor or service; everything but service is a person */
	kind: 'person' | 'employee' | 'contractor' | 'service';
}
export interface RegimeAssessment {
	regime: Regime;
	regimeLabel: string;
	jurisdiction: string;
	entityId: string;
	entityName: string;
	proposal: { applies: 'yes' | 'no' | 'assess'; reasons: string[]; proposedAt: ISODate };
	determination: {
		applies: boolean;
		significant?: boolean;
		material?: boolean;
		impactLevel?: 'significant' | 'relevant';
		awareAt?: ISODate;
		materialityDeterminedAt?: ISODate;
		rationale: string;
		by: User;
		at: ISODate;
	} | null;
	/** "Not yet: decide by…" while no determination exists */
	deferral?: { decideBy: ISODate; note: string; by: string; at: ISODate } | null;
}
export interface Ticket {
	id: string;
	key: string;
	incidentId: string;
	title: string;
	body: string;
	priority: 'p1' | 'p2' | 'p3' | 'p4';
	assigneeTeam: string;
	status: 'open' | 'in_progress' | 'done';
	createdBy: { type: string; name: string };
	createdAt: ISODate;
}
export interface Incident extends IncidentSummary {
	version: number;
	summary: string;
	attack: AttackRef[];
	entities: Record<string, string[]>;
	assetsDetail: AssetDoc[];
	identitiesDetail: IdentityDoc[];
	triage: { verdict: string; confidence: number; summary: string; modelProbability: number; decidedAt: ISODate; runId: string } | null;
	timeToTriageSeconds: number | null;
	timeToContainSeconds: number | null;
	resolution: { reason: string; note: string; by: string; at: ISODate } | null;
	mergedInto: string | null;
	regimes: RegimeAssessment[];
	tickets: Ticket[];
}
export interface TimelineEntry {
	id: string;
	at: ISODate;
	kind: 'detection' | 'anomaly' | 'agent' | 'handoff' | 'approval' | 'action' | 'status' | 'note' | 'clock' | 'notice';
	actor: { type: 'user' | 'agent' | 'system'; name: string };
	title: string;
	body: string | null;
	refId: string | null;
	executive: boolean;
}

// ---------- investigations ----------
export type Teammate = 'triage' | 'enrichment' | 'response' | 'orchestrator';
export interface AgentStepDoc {
	seq: number;
	agent: Teammate;
	kind: 'plan' | 'tool_call' | 'tool_result' | 'handoff_out' | 'handoff_in' | 'finding' | 'verdict' | 'proposal' | 'guardrail' | 'error';
	title: string;
	startedAt: ISODate;
	durationMs: number;
	tool: string | null;
	a2aTaskId: string | null;
	model: string | null;
	flags: string[];
	inputHash?: string | null;
	detail?: {
		lookedAt?: string[];
		why?: string;
		reasons?: { text: string; weight: 'strong' | 'medium' | 'weak' }[];
		evidence?: { ref: string; at: ISODate; source: string; text: UntrustedText }[];
		[k: string]: unknown;
	};
}
export interface RunSummary {
	id: string;
	incidentId: string;
	trigger: 'auto' | 'manual' | 'rerun';
	status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
	startedAt: ISODate;
	finishedAt: ISODate | null;
	verdict: string | null;
	durationSeconds: number | null;
	stepCount: number;
	requestedBy: User | null;
	reason?: string | null;
}
export interface Run extends RunSummary {
	steps: AgentStepDoc[];
	proposals: ProposalDoc[];
	verdictDetail?: { verdict: string; confidence: number; summary: string; evidence: { kind: string; ref: string; text: UntrustedText | string }[]; modelProbability: number } | null;
}

// ---------- approvals ----------
export interface ProposalDoc {
	id: string;
	incidentId: string;
	incidentReference: string;
	runId: string | null;
	kind: string;
	kindLabel: string;
	target: string;
	targetType: string;
	rationale: string;
	expectedEffect: string;
	reversible: boolean;
	destructive: boolean;
	crownJewel: boolean;
	proposedBy: { type: 'agent' | 'user'; name: string };
	status: 'auto_approved' | 'pending' | 'approved' | 'rejected' | 'expired' | 'executed' | 'failed';
	requiredRoles: Role[];
	approvals: { userId: string; name: string; role: Role; decidedAt: ISODate; note: string | null }[];
	rejection: { userId: string; name: string; reason: string; decidedAt: ISODate } | null;
	result: { executed: boolean; status: string; detail: string; executedAt: ISODate } | null;
	createdAt: ISODate;
	expiresAt: ISODate;
	/** who asked for it (null when a teammate proposed it); nobody may be the only approver of their own request */
	proposedByUserId?: string | null;
	/** roles still needed, and whether the caller may hold now (computed by the API for the caller) */
	awaitingRoles?: Role[];
	canApprove?: boolean;
	/** web-only hint, optional on the wire */
	undo?: string;
}

// ---------- reporting ----------
export interface NoticeSummary {
	id: string;
	regime: Regime;
	type: string;
	typeLabel: string;
	status: 'draft' | 'approved' | 'submitted';
	version: number;
	dueAt: ISODate | null;
	submittedAt: ISODate | null;
}
export interface NoticeSection {
	key: string;
	title: string;
	body: string;
	required: boolean;
	filled: boolean;
	evidence?: string[];
	hint?: string;
}
export interface Notice extends NoticeSummary {
	incidentId: string;
	entity: { id: string; name: string };
	recipient: { name: string; channel: string; contact: RegimeContact | null };
	sections: NoticeSection[];
	versions: { version: number; editedBy: string; editedAt: ISODate }[];
	approvedBy: User | null;
	approvedAt: ISODate | null;
	submission: { channel: string; receipt: string; submittedBy: User; submittedAt: ISODate; note: string | null } | null;
	/** required section keys not yet filled ("[to be confirmed]" counts as unfilled) */
	missing?: string[];
	generatedBy?: 'offline' | 'cloud';
}
export interface IncidentReporting {
	assessments: RegimeAssessment[];
	deadlines: Deadline[];
	notices: NoticeSummary[];
}
export interface RegimeDetermination {
	applies: boolean;
	rationale: string;
	awareAt?: ISODate;
	materialityDeterminedAt?: ISODate;
	impactLevel?: 'significant' | 'relevant';
	entityId: string;
	material?: boolean;
	decideBy?: ISODate;
}

// ---------- hunting ----------
export interface HuntFilter {
	field: string;
	op: 'eq' | 'ne' | 'in' | 'contains' | 'prefix' | 'cidr' | 'gt' | 'lt';
	value: string | number | string[];
}
export interface HuntQueryDoc {
	from: ISODate;
	to: ISODate;
	filters: HuntFilter[];
	groupBy: string[];
	metric: string;
	bucket: '5m' | '1h' | '1d' | null;
	limit: number;
	relativeHours?: number;
	/** web-only: the plain words the search came from */
	text?: string;
}
export interface HuntResultDoc {
	columns: string[];
	rows: unknown[][];
	totalMatched: number;
	elapsedMs: number;
	truncated: boolean;
	searched?: number;
}
export interface HuntField {
	field: string;
	label: string;
	type: 'string' | 'number' | 'ip' | 'time';
	ops: string[];
}
export interface SavedSearch {
	id: string;
	name: string;
	description: string;
	query: HuntQueryDoc;
	createdBy: User;
	createdAt: ISODate;
	lastRunAt: ISODate | null;
	lastCount: number | null;
	schedule?: string;
	alert?: boolean;
}
export interface Suggestion {
	id: string;
	title: string;
	why: string;
	query: HuntQueryDoc;
	basis: { kind: 'technique' | 'anomaly' | 'vulnerability'; ref: string };
}

// ---------- watch, executive ----------
export interface ShiftLogEntry {
	at: ISODate;
	text: string;
	kind: 'incident' | 'approval' | 'clock' | 'action' | 'notice' | 'pipeline';
	refId: string | null;
	severity: Severity | null;
}
export interface PipelineStatus {
	status: 'normal' | 'catching_up' | 'degraded' | 'offline';
	plainStatus: string;
	eventsPerMinute: number;
}
export interface WatchDoc {
	clocks: Deadline[];
	incidents: IncidentSummary[];
	pendingApprovals: number;
	shiftLog: ShiftLogEntry[];
	pipeline: PipelineStatus;
}
export interface Posture {
	asOf: ISODate;
	score: number;
	scoreBand: 'steady' | 'watch' | 'strained';
	components: { key: string; label: string; score: number; note: string }[];
	openBySeverity: Record<Severity, number>;
	reportableOpen: number;
	meanTimeToTriage: { last7dSeconds: number; last30dSeconds: number; trend: 'better' | 'worse' | 'flat' };
	meanTimeToContain: { last7dSeconds: number; last30dSeconds: number; trend: 'better' | 'worse' | 'flat' };
	alerts: { last7d: number; incidentsLast7d: number; suppressedAsNoise: number; reductionPct: number };
	clocks: Deadline[];
	highlights: string[];
	history?: number[];
}
export interface BriefListItem {
	week: string;
	generatedAt: ISODate;
	headline: string;
}
export interface WeeklyBrief {
	week: string;
	from: string;
	to: string;
	headline: string;
	sections: { title: string; body: string }[];
	metrics: Posture;
	incidents: IncidentSummary[];
	generatedBy: 'offline' | 'cloud';
}

// ---------- behaviour ----------
export interface AnomalyDoc {
	id: string;
	entityType: 'user' | 'host';
	entityId: string;
	windowStart: ISODate;
	windowEnd: ISODate;
	score: number;
	reasons: Reason[];
	features: Record<string, number>;
	detectionId: string | null;
	incidentId: string | null;
	/** web hint: 14 x 24 deviation grid (0..1), oldest day first; optional on the wire */
	rhythm?: number[][];
	headline?: string;
	label?: string;
}

// ---------- audit ----------
export interface AuditEntry {
	id: string;
	action: string;
	label: string;
	actorName: string;
	actorType: 'user' | 'agent' | 'system';
	resourceType: string;
	resourceId: string | null;
	metadata: Record<string, unknown>;
	ip: string | null;
	createdAt: ISODate;
	hash: string;
	prevHash: string;
}
export interface AuditVerify {
	valid: boolean;
	checked: number;
	brokenAt: string | null;
}

export interface DetectionDoc {
	id: string;
	source: 'sigma' | 'ueba' | 'external';
	ruleId: string | null;
	ruleTitle: string;
	severity: Severity;
	confidence: number;
	attack: AttackRef[];
	firstSeen: ISODate;
	lastSeen: ISODate;
	eventCount: number;
	summary: string;
	incidentId: string | null;
	evidence?: (EventSnapshotDoc & { ref?: string })[];
}

/** Server-sent events (contract §1.17). */
export type StreamEvent =
	| { type: 'ready'; data: { lastEventId: string } }
	| { type: 'river.batch'; data: { count: number; lines: RiverLine[]; eventsPerMinute: number } }
	| { type: 'pipeline.status'; data: PipelineStatus }
	| { type: 'detection.created'; data: { detectionId: string; severity: Severity; title: string; band: string; incidentId: string | null } }
	| { type: 'anomaly.created'; data: { anomalyId: string; entityType: string; entityId: string; score: number } }
	| { type: 'incident.created'; data: { incidentId: string; reference: string; title: string; severity: Severity; kind: string } }
	| { type: 'incident.updated'; data: { incidentId: string; reference: string; status: IncidentStatus; stage: Stage; severity: Severity; changed: string[] } }
	| { type: 'investigation.started'; data: { runId: string; incidentId: string } }
	| { type: 'investigation.step'; data: { runId: string; incidentId: string; seq: number; agent: Teammate; kind: string; title: string } }
	| { type: 'investigation.completed'; data: { runId: string; incidentId: string; verdict: string; durationSeconds: number } }
	| { type: 'approval.requested'; data: { approvalId: string; incidentId: string; kind: string; kindLabel: string; target: string; requiredRoles: Role[] } }
	| { type: 'approval.decided'; data: { approvalId: string; incidentId: string; status: ProposalDoc['status'] } }
	| { type: 'action.executed'; data: { approvalId: string; incidentId: string; kind: string; target: string; status: string } }
	| { type: 'clock.started'; data: { incidentId: string; regime: Regime; noticeType: string; dueAt: ISODate } }
	| { type: 'clock.state'; data: { deadlineId: string; incidentId: string; regime: Regime; state: Deadline['state'] } }
	| { type: 'notice.updated'; data: { noticeId: string; incidentId: string; regime: Regime; type: string; status: NoticeSummary['status'] } }
	| { type: 'job.updated'; data: { jobId: string; kind: string; status: string; progress: number } }
	| { type: 'brief.ready'; data: { week: string } };
export type StreamEventType = StreamEvent['type'];
export const STREAM_EVENT_TYPES: StreamEventType[] = [
	'ready', 'river.batch', 'pipeline.status', 'detection.created', 'anomaly.created', 'incident.created', 'incident.updated',
	'investigation.started', 'investigation.step', 'investigation.completed', 'approval.requested', 'approval.decided',
	'action.executed', 'clock.started', 'clock.state', 'notice.updated', 'job.updated', 'brief.ready'
];

export interface MorningBrief {
	asOf: ISODate;
	night: { from: ISODate; to: ISODate };
	headline: string;
	incidents: IncidentSummary[];
	contained: number;
	pendingApprovals: number;
	alerts: { last7d: number; incidentsLast7d: number; suppressedAsNoise: number; reductionPct: number };
	clocks: Deadline[];
	highlights: string[];
}

export type Verdict = 'true_positive' | 'benign' | 'false_positive' | 'needs_review';
export interface MinuteTotal {
	minute: ISODate;
	count: number;
}
export interface RuleException {
	id: string;
	ruleId: string;
	field: 'user' | 'host' | 'ip';
	value: string;
	reason: string;
	createdBy: string;
	createdAt: ISODate;
	expiresAt: ISODate | null;
}
export interface EvidenceItem {
	id: string;
	source: string;
	ruleTitle: string;
	severity: Severity;
	attack: AttackRef[];
	firstSeen: ISODate;
	lastSeen: ISODate;
	eventCount: number;
	hosts: string[];
	users: string[];
	band: 'likely_real' | 'uncertain' | 'likely_noise' | null;
	suppressed: boolean;
}
export interface EvidenceSummary {
	detections: EvidenceItem[];
	huntMatches: TimelineEntry[];
}
export interface StepFeedback {
	id: string;
	runId: string;
	seq: number;
	verdict: Verdict;
	note: string;
	recordedVerdict: string | null;
	labelledDetections: number;
	by: string;
	at: ISODate;
}
