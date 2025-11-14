// A stateful, in-browser stand-in for the Nightjar SOC API, shaped exactly like contract Part 1. It answers
// `fetch` with real Response objects (MSW-style), enforces the same permissions, self-approval and conflict
// rules, and emits the same SSE events, so the app code is identical in mock and real mode.
// Used by `npm run dev:mock` / PUBLIC_API_MOCK=1 and by the tests; the real API is the default.
import type * as T from '../types';
import { buildSeed, DEMO_PASSWORD, PERMISSIONS, ROLE_LABEL, hashOf, routineLine, rnd, type SeedState } from './seed';
import { sec8k, soci as sociRule, nis2 as nis2Rule, clockState, now as clockNow, demoNight } from '$lib/time/clock';

type Json = unknown;
interface Ctx {
	method: string;
	path: string;
	query: URLSearchParams;
	body: Record<string, unknown>;
	headers: Headers;
}
type Handler = (ctx: Ctx, params: string[]) => Json | Promise<Json>;

class Problem extends Error {
	constructor(public status: number, public code: string, public title: string, public detail?: string, public errors?: { field: string; message: string }[]) {
		super(detail ?? title);
	}
}

type Listener = (e: T.StreamEvent) => void;

export interface MockServerOptions {
	now?: () => Date;
	/** Persist the signed-in mock user across reloads (browser only). */
	storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
	/** Emit the live river every 2 s. */
	live?: boolean;
	/** Set the readable CSRF cookie (browser only). */
	setCookie?: (name: string, value: string) => void;
}

export function createMockServer(opts: MockServerOptions = {}) {
	const now = opts.now ?? (() => clockNow());
	const db: SeedState = buildSeed(now());
	const storage = opts.storage ?? null;
	let currentUserId: string | null = storage?.getItem('nj_mock_user') ?? null;
	if (currentUserId && !db.users.some((u) => u.id === currentUserId)) {
		// ids are deterministic per seed, but guard against an old value
		const byEmail = db.users.find((u) => u.email === storage?.getItem('nj_mock_email'));
		currentUserId = byEmail?.id ?? null;
	}
	const csrf = hashOf('csrf' + Math.random()).slice(0, 24);
	const listeners = new Set<Listener>();
	let seq = 1;
	let riverIdx = 0;
	let lastSeenAt = new Date(+now() - 14 * 3600e3).toISOString();

	const demoNightEnd = () => new Date(+demoNight(now()) + 9 * 3600e3);
	function emit(e: T.StreamEvent) {
		seq++;
		for (const l of listeners) l(e);
	}

	// ---------- helpers ----------
	const me = () => db.users.find((u) => u.id === currentUserId) ?? null;
	const membership = () => db.members.find((m) => m.user.id === currentUserId) ?? null;
	const perms = () => (membership() ? PERMISSIONS[membership()!.role] : []);
	function need(perm: string | string[]) {
		if (!me()) throw new Problem(401, 'not_authenticated', 'Not signed in');
		const list = Array.isArray(perm) ? perm : [perm];
		if (!list.some((p) => perms().includes(p))) throw new Problem(403, 'forbidden', 'Forbidden', 'Your role cannot do this.');
	}
	function incident(idOrRef: string): T.Incident {
		const inc = db.incidents.find((i) => i.id === idOrRef || i.reference === idOrRef);
		if (!inc) throw new Problem(404, 'not_found', 'Not found');
		return inc;
	}
	function meDoc(): T.Me {
		const u = me();
		const m = membership();
		if (!u || !m) throw new Problem(401, 'not_authenticated', 'Not signed in');
		const t = now();
		return {
			user: u, activeOrg: db.org, membership: { role: m.role, roleLabel: m.roleLabel, permissions: PERMISSIONS[m.role] },
			orgs: [{ id: db.org.id, name: db.org.name, slug: db.org.slug, role: m.role, roleLabel: m.roleLabel }],
			session: { expiresAt: new Date(+t + 30 * 60e3).toISOString(), absoluteExpiresAt: new Date(+t + 12 * 3600e3).toISOString() },
			clock: { today: t.toLocaleDateString('sv-SE', { timeZone: 'Europe/Brussels' }), demo: true }
		};
	}
	function liveDeadline(d: T.Deadline): T.Deadline {
		const t = now();
		const state = clockState(d, t);
		return { ...d, state, remainingSeconds: d.dueAt ? Math.floor((+new Date(d.dueAt) - +t) / 1000) : null };
	}
	function clocks(): T.Deadline[] {
		const open = new Set(db.incidents.filter((i) => i.status !== 'closed').map((i) => i.id));
		const t = +now();
		return db.deadlines
			.filter((d) => open.has(d.incidentId))
			.filter((d) => d.state !== 'not_required')
			.filter((d) => d.state !== 'submitted' || (d.submittedAt && t - +new Date(d.submittedAt) < 10 * 60e3))
			.map(liveDeadline)
			.sort((a, b) => (a.dueAt ?? '9999').localeCompare(b.dueAt ?? '9999'));
	}
	function summary(inc: T.Incident): T.IncidentSummary {
		const next = clocks().filter((d) => d.incidentId === inc.id && d.state !== 'submitted')[0] ?? null;
		const pending = db.proposals.filter((p) => p.incidentId === inc.id && p.status === 'pending').length;
		const { version: _v, summary: _s, attack: _a, entities: _e, assetsDetail: _ad, identitiesDetail: _id, triage: _t, timeToTriageSeconds: _tt, timeToContainSeconds: _tc, resolution: _r, mergedInto: _m, regimes: _rg, tickets: _tk, ...rest } = inc;
		void [_v, _s, _a, _e, _ad, _id, _t, _tt, _tc, _r, _m, _rg, _tk];
		return { ...rest, statusLabel: STATUS_LABEL[inc.status] ?? inc.status, nextDeadline: next, pendingApprovals: pending };
	}
	function fullIncident(inc: T.Incident): T.Incident {
		return { ...inc, ...summary(inc), regimes: db.assessments[inc.id] ?? [] };
	}
	function paged<X>(items: X[], q: URLSearchParams): T.Paged<X> {
		const page = Math.max(1, Number(q.get('page') ?? 1));
		const pageSize = Math.min(200, Math.max(1, Number(q.get('pageSize') ?? 25)));
		return { items: items.slice((page - 1) * pageSize, page * pageSize), page, pageSize, total: items.length, totalPages: Math.max(1, Math.ceil(items.length / pageSize)) };
	}
	function audit(actorType: T.AuditEntry['actorType'], actorName: string, action: string, label: string, resourceId: string | null) {
		const prev = db.audit[0]?.hash ?? '0'.repeat(64);
		const row = { createdAt: now().toISOString(), actorName, actorType, action, label, resourceType: 'incident', resourceId, metadata: {}, ip: '192.0.2.10' };
		const hash = hashOf(prev + JSON.stringify(row)).slice(0, 64);
		db.audit.unshift({ ...row, id: String(Number(db.audit[0]?.id ?? 18380) + 1), hash, prevHash: prev });
	}
	function timeline(incId: string, kind: T.TimelineEntry['kind'], actor: T.TimelineEntry['actor'], title: string) {
		(db.timelines[incId] ??= []).push({ id: hashOf(title + now()).slice(0, 12), at: now().toISOString(), kind, actor, title, body: null, refId: null, executive: true });
	}
	const firstName = (u: T.User) => u.fullName.split(' ')[0];
	/** ProposalDoc as the API serves it to the caller: awaitingRoles and canApprove are per caller. */
	function pdoc(p: T.ProposalDoc): T.ProposalDoc {
		const role = membership()?.role;
		const awaitingRoles = p.status === 'pending' ? p.requiredRoles.filter((r) => !p.approvals.some((a) => a.role === r)) : [];
		const mine = !!role && awaitingRoles.includes(role) && p.proposedByUserId !== currentUserId && !p.approvals.some((a) => a.userId === currentUserId);
		return { ...p, awaitingRoles, canApprove: mine && (perms().includes('actions:approve') || perms().includes('actions:approve_crown_jewel')) };
	}
	const UNFILLED = (b: string) => !b.trim() || b.includes('[to be confirmed]');
	function ndoc(n: T.Notice): T.Notice {
		return { ...n, missing: n.sections.filter((s) => s.required && UNFILLED(s.body)).map((s) => s.key), generatedBy: 'offline' };
	}
	const STATUS_LABEL: Record<string, string> = { new: 'New', triage: 'In triage', investigating: 'Investigating', containing: 'Containing', monitoring: 'Contained, monitoring', resolved: 'Resolved', closed: 'Closed' };

	// ---------- routes ----------
	const routes: [string, RegExp, Handler][] = [];
	const on = (method: string, pattern: string, h: Handler) => {
		const rx = new RegExp('^' + pattern.replace(/:[a-zA-Z]+/g, '([^/]+)') + '$');
		routes.push([method, rx, h]);
	};

	on('GET', '/auth/csrf', () => {
		opts.setCookie?.('nj_csrf', csrf);
		return undefined;
	});
	on('POST', '/auth/login', ({ body }) => {
		const email = String(body.email ?? '').trim().toLowerCase();
		const user = db.users.find((u) => u.email === email);
		if (!user || body.password !== DEMO_PASSWORD) throw new Problem(401, 'invalid_credentials', 'Sign-in failed', 'That email and password do not match.');
		currentUserId = user.id;
		storage?.setItem('nj_mock_user', user.id);
		storage?.setItem('nj_mock_email', user.email);
		audit('user', user.fullName, 'auth.login', 'signed in', null);
		return meDoc();
	});
	on('POST', '/auth/logout', () => {
		currentUserId = null;
		storage?.removeItem('nj_mock_user');
		storage?.removeItem('nj_mock_email');
		return undefined;
	});
	on('GET', '/auth/me', () => meDoc());
	on('POST', '/auth/refresh', () => {
		const m = meDoc();
		return m.session;
	});
	on('GET', '/org', () => (need('settings:read'), db.org));
	on('GET', '/org/members', () => (need('members:read'), { items: [...db.members].sort((a, b) => a.user.fullName.localeCompare(b.user.fullName)) }));
	on('PATCH', '/org/members/:id', ({ body }, [mid]) => {
		need('members:manage');
		const m = db.members.find((x) => x.id === mid);
		if (!m) throw new Problem(404, 'not_found', 'Not found');
		m.role = body.role as T.Role;
		m.roleLabel = ROLE_LABEL[m.role];
		audit('user', me()!.fullName, 'member.role_changed', `changed ${m.user.fullName}'s role to ${m.roleLabel}`, null);
		return m;
	});
	on('GET', '/org/invitations', () => (need('members:manage'), db.invitations));
	on('POST', '/org/invitations', ({ body }) => {
		need('members:manage');
		const email = String(body.email ?? '');
		if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Problem(422, 'validation_error', 'Validation failed', 'One or more fields are invalid.', [{ field: 'email', message: 'Enter a valid email address.' }]);
		const role = body.role as T.Role;
		const token = hashOf(email + now()).slice(0, 32);
		const inv: T.Invitation = { id: hashOf('inv' + email).slice(0, 12), email, role, roleLabel: ROLE_LABEL[role], invitedBy: me()!.fullName, expiresAt: new Date(+now() + 7 * 864e5).toISOString(), acceptUrl: `/invite/${token}` };
		db.invitations.push({ ...inv, acceptUrl: undefined });
		pendingTokens.set(token, inv);
		audit('user', me()!.fullName, 'invitation.created', `invited ${email} as ${inv.roleLabel}`, null);
		return inv;
	});
	on('DELETE', '/org/invitations/:id', (_c, [iid]) => {
		need('members:manage');
		db.invitations = db.invitations.filter((i) => i.id !== iid);
		return undefined;
	});
	const pendingTokens = new Map<string, T.Invitation>();
	pendingTokens.set('halden-demo-invite', { id: 'x', email: 'nora.kelly@halden.example', role: 'analyst', roleLabel: ROLE_LABEL.analyst, invitedBy: 'Pieter Claes', expiresAt: new Date(+now() + 3 * 864e5).toISOString() });
	on('GET', '/invitations/:token', (_c, [token]) => {
		const inv = pendingTokens.get(token ?? '');
		if (!inv) throw new Problem(410, 'expired', 'Invitation expired', 'This invitation link has expired or was revoked.');
		return { orgName: db.org.name, email: inv.email, role: inv.role, roleLabel: inv.roleLabel, expiresAt: inv.expiresAt, accountExists: false } satisfies T.InvitationPreview;
	});
	on('POST', '/invitations/:token/accept', ({ body }, [token]) => {
		const inv = pendingTokens.get(token ?? '');
		if (!inv) throw new Problem(410, 'expired', 'Invitation expired');
		const pw = String(body.password ?? '');
		if (pw.length < 12) throw new Problem(422, 'weak_password', 'Weak password', 'Use at least 12 characters.', [{ field: 'password', message: 'Use at least 12 characters, and avoid common words.' }]);
		const name = String(body.fullName ?? '').trim();
		if (!name) throw new Problem(422, 'validation_error', 'Validation failed', '', [{ field: 'fullName', message: 'Enter your name.' }]);
		const u: T.User = { id: hashOf(inv.email).slice(0, 8) + '-0000-4000-8000-000000000001', email: inv.email, fullName: name, title: inv.roleLabel, avatarColor: '#3CE6C8', createdAt: now().toISOString() };
		db.users.push(u);
		db.members.push({ id: hashOf('m' + inv.email).slice(0, 12), user: u, role: inv.role, roleLabel: inv.roleLabel, joinedAt: now().toISOString() });
		pendingTokens.delete(token ?? '');
		currentUserId = u.id;
		storage?.setItem('nj_mock_user', u.id);
		storage?.setItem('nj_mock_email', u.email);
		return meDoc();
	});

	// events
	on('GET', '/events/river', ({ query }) => {
		need('watch:read');
		const limit = Math.min(80, Number(query.get('limit') ?? 50));
		return { lines: riverHistory(limit), eventsPerMinute: 1284 };
	});
	on('GET', '/events', ({ query }) => {
		need('events:read');
		const qq = (query.get('q') ?? '').toLowerCase();
		return paged(db.events.filter((e) => !qq || e.message.text.toLowerCase().includes(qq)), query);
	});
	on('GET', '/events/:id', (_c, [eid]) => {
		need('events:read');
		const e = db.events.find((x) => x.eventId === eid);
		if (!e) throw new Problem(404, 'not_found', 'Not found');
		return { ...e, raw: { message: e.message.text } };
	});

	// rules
	on('GET', '/rules', ({ query }) => {
		need('rules:read');
		const qq = (query.get('q') ?? '').toLowerCase();
		return db.rules.filter((r) => !qq || r.title.toLowerCase().includes(qq)).map(({ yaml: _y, ...r }) => (void _y, r));
	});
	on('GET', '/rules/:id', (_c, [rid]) => {
		need('rules:read');
		const r = db.rules.find((x) => x.id === rid);
		if (!r) throw new Problem(404, 'not_found', 'Not found');
		const { yaml, ...rule } = r;
		return { rule, yaml, exceptions: [] as T.RuleException[] };
	});
	on('PATCH', '/rules/:id', ({ body }, [rid]) => {
		need('rules:manage');
		const r = db.rules.find((x) => x.id === rid);
		if (!r) throw new Problem(404, 'not_found', 'Not found');
		r.enabled = Boolean(body.enabled);
		audit('user', me()!.fullName, 'rule.toggled', `${r.enabled ? 'turned on' : 'turned off'} "${r.title}"`, null);
		const { yaml: _y, ...rule } = r;
		void _y;
		return rule;
	});

	// incidents
	on('GET', '/incidents', ({ query }) => {
		need('incidents:read');
		const status = query.get('status') ?? 'open';
		const sevRank: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
		let list = db.incidents;
		if (status === 'open') list = list.filter((i) => i.status !== 'resolved' && i.status !== 'closed');
		else if (status !== 'all') list = list.filter((i) => i.status === status);
		if (query.get('reportable') === 'true') list = list.filter((i) => i.reportable === 'yes' || i.reportable === 'assess');
		const qq = (query.get('q') ?? '').toLowerCase();
		if (qq) list = list.filter((i) => (i.title + i.reference).toLowerCase().includes(qq));
		list = [...list].sort((a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9) || b.lastSeen.localeCompare(a.lastSeen));
		return paged(list.map(summary), query);
	});
	on('GET', '/incidents/:id', (_c, [iid]) => (need('incidents:read'), fullIncident(incident(iid!))));
	on('GET', '/incidents/:id/timeline', ({ query }, [iid]) => {
		need('incidents:read');
		const inc = incident(iid!);
		const list = [...(db.timelines[inc.id] ?? [])].sort((a, b) => a.at.localeCompare(b.at));
		return query.get('audience') === 'executive' ? list.filter((t) => t.executive) : list;
	});
	on('GET', '/incidents/:id/evidence', (_c, [iid]) => {
		need('incidents:read');
		const inc = incident(iid!);
		const ev = db.evidence[inc.id] ?? [];
		const detections: T.EvidenceItem[] = ev.map((e, k) => ({ id: `${inc.id}-${k}`, source: 'sigma', ruleTitle: e.action.replace(/-/g, ' '), severity: inc.severity, attack: inc.attack, firstSeen: e.timestamp, lastSeen: e.timestamp, eventCount: 1, hosts: e.host ? [e.host] : [], users: e.user ? [e.user] : [], band: 'likely_real', suppressed: false }));
		return { detections, huntMatches: (db.timelines[inc.id] ?? []).filter((t) => t.kind === 'detection' && t.actor.type === 'user') } satisfies T.EvidenceSummary;
	});
	on('POST', '/incidents/:id/evidence', ({ body }, [iid]) => {
		need('incidents:write');
		const inc = incident(iid!);
		const eventId = String(body.eventId ?? '');
		if ((db.timelines[inc.id] ?? []).some((t) => t.refId === eventId)) throw new Problem(409, 'conflict', 'Conflict', 'This event is already part of the incident’s evidence.');
		timeline(inc.id, 'detection', { type: 'user', name: me()!.fullName }, 'Added from a hunt');
		const entry = db.timelines[inc.id]!.at(-1)!;
		entry.refId = eventId;
		return entry;
	});
	on('POST', '/investigations/:id/steps/:seq/feedback', ({ body }, [rid, seq]) => {
		need('incidents:write');
		const run = db.runs.find((r) => r.id === rid);
		if (!run) throw new Problem(404, 'not_found', 'Not found');
		const verdict = String(body.verdict) as T.Verdict;
		timeline(run.incidentId, 'note', { type: 'user', name: me()!.fullName }, `Disagreed with step ${seq}`);
		return { id: hashOf(rid! + seq).slice(0, 12), runId: run.id, seq: Number(seq), verdict, note: String(body.note ?? ''), recordedVerdict: run.verdict, labelledDetections: (db.evidence[run.incidentId] ?? []).length, by: me()!.fullName, at: now().toISOString() } satisfies T.StepFeedback;
	});
	on('POST', '/incidents/:id/reporting/:regime/defer', ({ body }, [iid, regime]) => {
		need('reporting:assess');
		const inc = incident(iid!);
		const a = (db.assessments[inc.id] ?? []).find((x) => x.regime === regime);
		if (!a) throw new Problem(404, 'not_found', 'Not found');
		if (a.determination) throw new Problem(409, 'already_decided', 'Conflict', 'This decision has already been recorded.');
		a.deferral = { decideBy: String(body.decideBy), note: String(body.note ?? ''), by: me()!.fullName, at: now().toISOString() };
		const dl = db.deadlines.find((d) => d.incidentId === inc.id && d.regime === regime && d.state === 'not_started');
		if (dl) Object.assign(dl, { decideBy: a.deferral.decideBy, noticeLabel: 'Decision due' });
		timeline(inc.id, 'clock', { type: 'user', name: me()!.fullName }, `Decision set for ${a.deferral.decideBy.slice(0, 10)}`);
		return a;
	});
	const exceptions: T.RuleException[] = [];
	on('GET', '/suppressions', ({ query }) => (need('rules:read'), exceptions.filter((x) => !query.get('ruleId') || x.ruleId === query.get('ruleId'))));
	on('POST', '/suppressions', ({ body }) => {
		need('rules:manage');
		const reason = String(body.reason ?? '').trim();
		if (reason.length < 10) throw new Problem(422, 'validation_error', 'Validation failed', 'Give a reason of at least 10 characters.', [{ field: 'reason', message: 'At least 10 characters.' }]);
		const x: T.RuleException = { id: hashOf(String(body.ruleId) + body.value + exceptions.length).slice(0, 12), ruleId: String(body.ruleId), field: body.field as T.RuleException['field'], value: String(body.value), reason, createdBy: me()!.fullName, createdAt: now().toISOString(), expiresAt: body.expiresInDays ? new Date(+now() + Number(body.expiresInDays) * 864e5).toISOString() : null };
		exceptions.push(x);
		audit('user', me()!.fullName, 'rule.exception_added', `exception added: ${x.field} ${x.value}`, null);
		return x;
	});
	on('DELETE', '/suppressions/:id', (_c, [sid]) => {
		need('rules:manage');
		const i = exceptions.findIndex((x) => x.id === sid);
		if (i < 0) throw new Problem(404, 'not_found', 'Not found');
		exceptions.splice(i, 1);
		return undefined;
	});
	on('GET', '/incidents/:id/detections', (_c, [iid]) => {
		need('detections:read');
		const inc = incident(iid!);
		const ev = db.evidence[inc.id] ?? [];
		return [{ id: hashOf(inc.id).slice(0, 12), source: 'sigma', ruleId: null, ruleTitle: inc.title, severity: inc.severity, confidence: 0.9, attack: inc.attack, firstSeen: inc.firstSeen, lastSeen: inc.lastSeen, eventCount: ev.length, summary: inc.summary, incidentId: inc.id, evidence: ev }] satisfies T.DetectionDoc[];
	});
	on('PATCH', '/incidents/:id', ({ body }, [iid]) => {
		need('incidents:write');
		const inc = incident(iid!);
		if (Number(body.version) !== inc.version) throw new Problem(409, 'stale_version', 'Changed by someone else');
		Object.assign(inc, { ...(body.status ? { status: body.status } : {}), ...(body.severity ? { severity: body.severity } : {}) });
		inc.version++;
		return fullIncident(inc);
	});
	on('POST', '/incidents/:id/notes', ({ body }, [iid]) => {
		need('incidents:write');
		const inc = incident(iid!);
		timeline(inc.id, 'note', { type: 'user', name: me()!.fullName }, String(body.body ?? '').slice(0, 4000));
		return db.timelines[inc.id]!.at(-1);
	});
	on('GET', '/incidents/:id/investigations', (_c, [iid]) => {
		need('investigations:read');
		const inc = incident(iid!);
		return db.runs.filter((r) => r.incidentId === inc.id).map(({ steps: _s, proposals: _p, ...r }) => (void _s, void _p, r));
	});
	on('POST', '/incidents/:id/investigations', (_c, [iid]) => {
		need('investigations:run');
		const inc = incident(iid!);
		const r = db.runs.find((x) => x.incidentId === inc.id);
		return { ...r, trigger: 'rerun', status: 'queued' };
	});
	on('GET', '/investigations/:runId', (_c, [rid]) => {
		need('investigations:read');
		const r = db.runs.find((x) => x.id === rid);
		if (!r) throw new Problem(404, 'not_found', 'Not found');
		return { ...r, proposals: db.proposals.filter((p) => p.runId === r.id).map(pdoc) };
	});
	on('GET', '/investigations/:runId/steps/:seq', (_c, [rid, s]) => {
		need('investigations:read');
		const st = db.runs.find((x) => x.id === rid)?.steps.find((x) => x.seq === Number(s));
		if (!st) throw new Problem(404, 'not_found', 'Not found');
		return st;
	});
	on('GET', '/incidents/:id/actions', (_c, [iid]) => (need('actions:read'), db.proposals.filter((p) => p.incidentId === incident(iid!).id).map(pdoc)));

	// approvals
	on('GET', '/approvals', ({ query }) => {
		need('actions:read');
		const status = query.get('status') ?? 'pending';
		let list = db.proposals;
		if (status === 'pending') list = list.filter((p) => p.status === 'pending');
		const inc = query.get('incidentId');
		if (inc) list = list.filter((p) => p.incidentId === inc);
		if (query.get('mine') === 'true') list = list.filter((p) => pdoc(p).canApprove);
		return paged([...list].sort((a, b) => a.createdAt.localeCompare(b.createdAt)).map(pdoc), query);
	});
	on('POST', '/approvals/:id/approve', ({ body }, [aid]) => {
		need(['actions:approve', 'actions:approve_crown_jewel']);
		const p = db.proposals.find((x) => x.id === aid);
		if (!p) throw new Problem(404, 'not_found', 'Not found');
		if (p.status !== 'pending') throw new Problem(409, 'already_decided', 'Already decided');
		if (+new Date(p.expiresAt) < +now()) throw new Problem(410, 'expired', 'Expired');
		const u = me()!;
		const role = membership()!.role;
		if (p.proposedByUserId === u.id) throw new Problem(409, 'self_approval', 'Self approval', 'You asked for this action, so someone else has to authorise it.');
		if (!p.requiredRoles.includes(role)) throw new Problem(403, 'forbidden', 'Forbidden', 'Your role cannot authorise this action.');
		if (p.approvals.some((a) => a.userId === u.id || a.role === role)) throw new Problem(409, 'already_decided', 'Already decided', 'You have already held to authorise this.');
		p.approvals.push({ userId: u.id, name: u.fullName, role, decidedAt: now().toISOString(), note: (body.note as string) ?? null });
		const done = p.requiredRoles.every((r) => p.approvals.some((a) => a.role === r));
		audit('user', u.fullName, 'approval.approved', `held to authorise: ${p.kindLabel.toLowerCase()} ${p.target}${done ? '' : ` (${p.approvals.length} of ${p.requiredRoles.length})`}`, p.incidentReference);
		timeline(p.incidentId, 'approval', { type: 'user', name: u.fullName }, `${firstName(u)} held to authorise · ${p.kindLabel.toLowerCase()}`);
		if (done) {
			p.status = 'approved';
			emit({ type: 'approval.decided', data: { approvalId: p.id, incidentId: p.incidentId, status: 'approved' } });
			setTimeout(() => {
				p.status = 'executed';
				p.result = { executed: true, status: 'executed', detail: 'Done', executedAt: now().toISOString() };
				const inc = db.incidents.find((i) => i.id === p.incidentId);
				if (inc && inc.status === 'containing' && !db.proposals.some((x) => x.incidentId === inc.id && x.status === 'pending')) {
					inc.status = 'monitoring';
					inc.stage = 'report';
					emit({ type: 'incident.updated', data: { incidentId: inc.id, reference: inc.reference, status: inc.status, stage: inc.stage, severity: inc.severity, changed: ['status', 'stage'] } });
				}
				audit('agent', 'Response', 'action.executed', `${p.kindLabel.toLowerCase()}: ${p.target}`, p.incidentReference);
				emit({ type: 'action.executed', data: { approvalId: p.id, incidentId: p.incidentId, kind: p.kind, target: p.target, status: 'executed' } });
			}, 900);
		}
		return pdoc(p);
	});
	on('POST', '/approvals/:id/reject', ({ body }, [aid]) => {
		need(['actions:approve', 'actions:approve_crown_jewel']);
		const p = db.proposals.find((x) => x.id === aid);
		if (!p) throw new Problem(404, 'not_found', 'Not found');
		if (p.status !== 'pending') throw new Problem(409, 'already_decided', 'Already decided');
		const reason = String(body.reason ?? '').trim();
		if (!reason) throw new Problem(422, 'validation_error', 'Validation failed', 'A reason is required.', [{ field: 'reason', message: 'Choose or write a reason.' }]);
		const u = me()!;
		p.status = 'rejected';
		p.rejection = { userId: u.id, name: u.fullName, reason, decidedAt: now().toISOString() };
		audit('user', u.fullName, 'approval.rejected', `declined: ${p.kindLabel.toLowerCase()} ${p.target} (${reason})`, p.incidentReference);
		timeline(p.incidentId, 'approval', { type: 'user', name: u.fullName }, `${firstName(u)} declined · ${reason}`);
		emit({ type: 'approval.decided', data: { approvalId: p.id, incidentId: p.incidentId, status: 'rejected' } });
		return pdoc(p);
	});

	// reporting
	on('GET', '/reporting/clocks', () => (need('reporting:read'), clocks()));
	on('GET', '/incidents/:id/reporting', (_c, [iid]) => {
		need('reporting:read');
		const inc = incident(iid!);
		return {
			assessments: db.assessments[inc.id] ?? [],
			deadlines: db.deadlines.filter((d) => d.incidentId === inc.id).map(liveDeadline),
			notices: db.notices.filter((n) => n.incidentId === inc.id).map(({ id, regime, type, typeLabel, status, version, dueAt, submittedAt }) => ({ id, regime, type, typeLabel, status, version, dueAt, submittedAt }))
		} satisfies T.IncidentReporting;
	});
	on('POST', '/incidents/:id/reporting/:regime/assess', ({ body }, [iid, regime]) => {
		need('reporting:assess');
		const inc = incident(iid!);
		const rationale = String(body.rationale ?? '').trim();
		if (rationale.length < 10) throw new Problem(422, 'validation_error', 'Validation failed', 'Give a reason of at least 10 characters.', [{ field: 'rationale', message: 'Give a reason of at least 10 characters.' }]);
		const list = (db.assessments[inc.id] ??= []);
		const a = list.find((x) => x.regime === regime);
		if (!a) throw new Problem(404, 'not_found', 'Not found');
		const u = me()!;
		const at = now().toISOString();
		a.determination = {
			applies: Boolean(body.applies), material: body.material as boolean | undefined, significant: body.significant as boolean | undefined, impactLevel: body.impactLevel as 'significant' | 'relevant' | undefined,
			awareAt: body.awareAt as string | undefined, materialityDeterminedAt: body.materialityDeterminedAt as string | undefined, rationale, by: u, at
		};
		const dl = db.deadlines.find((d) => d.incidentId === inc.id && d.regime === regime && d.state === 'not_started');
		if (regime === 'sec_8k') {
			if (body.applies && body.material) {
				const determined = new Date(String(body.materialityDeterminedAt ?? at));
				const due = sec8k.item105Due(determined).toISOString();
				const noticeId = hashOf('n' + inc.id + regime).slice(0, 12);
				if (dl) Object.assign(dl, { startsAt: determined.toISOString(), dueAt: due, state: 'on_track', noticeId, basis: 'US-listed registrant; the disclosure committee determined the incident material', businessDays: 4 });
				db.notices.push({
					id: noticeId, regime: 'sec_8k', type: 'sec_8k.item_105', typeLabel: 'Form 8-K, Item 1.05', status: 'draft', version: 1, dueAt: due, submittedAt: null, incidentId: inc.id,
					entity: { id: 'halden-us', name: 'Halden Logistics, Inc.' }, recipient: { name: 'SEC EDGAR', channel: 'portal', contact: null },
					sections: [
						{ key: 'nature', title: 'Nature, scope and timing', body: inc.summary, required: true, filled: true, evidence: ['E-01'] },
						{ key: 'impact', title: 'Material impact or reasonably likely material impact', body: '', required: true, filled: false, evidence: [] }
					],
					versions: [{ version: 1, editedBy: 'Response (teammate)', editedAt: at }], approvedBy: null, approvedAt: null, submission: null
				});
				inc.reportable = 'yes';
				audit('user', u.fullName, 'reporting.assessed', 'recorded materiality: material (SEC 8-K)', inc.reference);
				audit('system', 'Nightjar SOC', 'reporting.clock_started', 'started SEC 8-K Item 1.05 clock', inc.reference);
				timeline(inc.id, 'clock', { type: 'user', name: u.fullName }, 'Material · SEC 8-K clock started');
				emit({ type: 'clock.started', data: { incidentId: inc.id, regime: 'sec_8k', noticeType: 'sec_8k.item_105', dueAt: due } });
			} else if (body.applies === false || body.material === false) {
				if (dl) dl.state = 'not_required';
				audit('user', u.fullName, 'reporting.assessed', 'recorded materiality: not material (SEC 8-K)', inc.reference);
				timeline(inc.id, 'clock', { type: 'user', name: u.fullName }, 'Not material · no SEC filing');
				emit({ type: 'clock.state', data: { deadlineId: dl?.id ?? '', incidentId: inc.id, regime: 'sec_8k', state: 'not_required' } });
			}
		} else if (body.applies && body.awareAt) {
			const aware = new Date(String(body.awareAt));
			const due = regime === 'soci' ? sociRule.reportDue(aware, (body.impactLevel as 'significant' | 'relevant') ?? 'relevant') : regime === 'nis2' ? nis2Rule.earlyWarningDue(aware) : new Date(+aware + 72 * 3600e3);
			emit({ type: 'clock.started', data: { incidentId: inc.id, regime: regime as T.Regime, noticeType: regime + '.report', dueAt: due.toISOString() } });
		}
		return a;
	});
	on('POST', '/incidents/:id/notices', ({ body }, [iid]) => {
		need('notices:draft');
		const inc = incident(iid!);
		const existing = db.notices.find((n) => n.incidentId === inc.id && n.type === body.type);
		if (existing) return existing;
		throw new Problem(409, 'clock_not_started', 'Clock not started');
	});
	on('GET', '/notices/:id', (_c, [nid]) => {
		need('reporting:read');
		const n = db.notices.find((x) => x.id === nid);
		if (!n) throw new Problem(404, 'not_found', 'Not found');
		const contacts = db.org.settings.regimeContacts[n.regime]?.contacts ?? [];
		return ndoc({ ...n, recipient: { ...n.recipient, contact: contacts[0] ?? null } });
	});
	on('PATCH', '/notices/:id', ({ body }, [nid]) => {
		need('notices:draft');
		const n = db.notices.find((x) => x.id === nid);
		if (!n) throw new Problem(404, 'not_found', 'Not found');
		if (n.status === 'submitted') throw new Problem(409, 'already_submitted', 'Already submitted');
		if (Number(body.version) !== n.version) throw new Problem(409, 'stale_version', 'Changed by someone else');
		for (const s of (body.sections as { key: string; body: string }[]) ?? []) {
			const sec = n.sections.find((x) => x.key === s.key);
			if (sec) {
				sec.body = s.body;
				sec.filled = s.body.trim().length > 0;
			}
		}
		n.version++;
		n.status = 'draft';
		n.versions.push({ version: n.version, editedBy: me()!.fullName, editedAt: now().toISOString() });
		emit({ type: 'notice.updated', data: { noticeId: n.id, incidentId: n.incidentId, regime: n.regime, type: n.type, status: n.status } });
		return ndoc(n);
	});
	on('POST', '/notices/:id/approve', (_c, [nid]) => {
		need('notices:approve');
		const n = db.notices.find((x) => x.id === nid);
		if (!n) throw new Problem(404, 'not_found', 'Not found');
		if (n.sections.some((s) => s.required && UNFILLED(s.body))) throw new Problem(422, 'validation_error', 'Required fields missing', 'Fill every required field first.', n.sections.filter((s) => s.required && UNFILLED(s.body)).map((s) => ({ field: `sections.${s.key}`, message: `${s.title} is required.` })));
		n.status = 'approved';
		n.approvedBy = me();
		n.approvedAt = now().toISOString();
		return ndoc(n);
	});
	on('POST', '/notices/:id/submit', ({ body }, [nid]) => {
		need('notices:submit');
		const n = db.notices.find((x) => x.id === nid);
		if (!n) throw new Problem(404, 'not_found', 'Not found');
		if (n.status === 'submitted') throw new Problem(409, 'already_submitted', 'Already submitted');
		if (n.status !== 'approved') throw new Problem(409, 'conflict', 'Not approved', 'Approve the notice before submitting it.');
		const u = me()!;
		const at = now();
		const prefix = n.regime === 'nis2' ? 'CCB' : n.regime === 'sec_8k' ? 'EDGAR' : n.regime === 'soci' ? 'ASD' : 'APD';
		n.status = 'submitted';
		n.submittedAt = at.toISOString();
		n.submission = { channel: String(body.channel ?? 'portal'), receipt: `${prefix}-MOCK-${hashOf(n.id + at.toISOString()).slice(0, 8).toUpperCase()}`, submittedBy: u, submittedAt: at.toISOString(), note: 'Recorded as submitted (demo)' };
		const dl = db.deadlines.find((d) => d.noticeId === n.id);
		if (dl) {
			dl.state = 'submitted';
			dl.submittedAt = at.toISOString();
		}
		audit('user', u.fullName, 'notice.submitted', `submitted ${n.typeLabel} (demo)`, db.incidents.find((i) => i.id === n.incidentId)?.reference ?? null);
		timeline(n.incidentId, 'notice', { type: 'user', name: u.fullName }, `${n.typeLabel} recorded as submitted (demo)`);
		emit({ type: 'notice.updated', data: { noticeId: n.id, incidentId: n.incidentId, regime: n.regime, type: n.type, status: 'submitted' } });
		emit({ type: 'clock.state', data: { deadlineId: dl?.id ?? '', incidentId: n.incidentId, regime: n.regime, state: 'submitted' } });
		return ndoc(n);
	});

	// hunting
	const FIELDS: T.HuntField[] = [
		{ field: 'user', label: 'person', type: 'string', ops: ['eq', 'contains', 'in'] },
		{ field: 'host', label: 'device', type: 'string', ops: ['eq', 'contains', 'prefix'] },
		{ field: 'action', label: 'event', type: 'string', ops: ['eq', 'in'] },
		{ field: 'url', label: 'link', type: 'string', ops: ['contains'] },
		{ field: 'source.ip', label: 'address', type: 'ip', ops: ['eq', 'cidr'] },
		{ field: 'source.geoCountry', label: 'country', type: 'string', ops: ['eq', 'ne'] },
		{ field: 'dataset', label: 'source', type: 'string', ops: ['eq'] }
	];
	function search(q: T.HuntQueryDoc): T.HuntResultDoc {
		const to = q.to ? +new Date(q.to) : +now();
		const from = q.from ? +new Date(q.from) : to - (q.relativeHours ?? 24) * 3600e3;
		const val = (e: (typeof db.events)[number], f: string): string => {
			switch (f) {
				case 'user':
				case 'user.name': return e.user?.name ?? '';
				case 'host':
				case 'host.name': return e.host?.name ?? '';
				case 'url': return e.url ?? '';
				case 'source.ip': return e.source?.ip ?? '';
				case 'source.geoCountry': return e.source?.geoCountry ?? '';
				case 'email.sender': return e.message.text;
				case 'domain': return (e.url ?? '') + ' ' + e.message.text;
				default: return String((e as unknown as Record<string, unknown>)[f] ?? '');
			}
		};
		const rows = db.events.filter((e) => {
			const t = +new Date(e.timestamp);
			if (t < from || t > to) return false;
			return q.filters.every((f) => {
				const v = val(e, f.field).toLowerCase();
				const want = String(f.value).toLowerCase();
				switch (f.op) {
					case 'eq': return v === want;
					case 'ne': return v !== want;
					case 'contains': return v.includes(want);
					case 'prefix': return v.startsWith(want);
					case 'in': return (Array.isArray(f.value) ? f.value : [f.value]).map((x) => String(x).toLowerCase()).includes(v);
					default: return true;
				}
			}) && (!q.text || q.filters.length > 0 || e.message.text.toLowerCase().includes(q.text.toLowerCase()));
		}).sort((a, b) => b.timestamp.localeCompare(a.timestamp));
		return {
			columns: ['timestamp', 'eventId', 'dataset', 'action', 'outcome', 'host.name', 'user.name', 'source.ip', 'destination.ip', 'summary'],
			rows: rows.slice(0, q.limit).map((e) => [e.timestamp, e.eventId, e.dataset, e.action, e.outcome, e.host?.name ?? null, e.user?.name ?? null, e.source?.ip ?? null, null, e.message.text]),
			totalMatched: rows.length, elapsedMs: 180 + Math.round(rnd(rows.length) * 300), truncated: rows.length > q.limit, searched: 18_200_000
		};
	}
	on('GET', '/hunting/fields', () => (need('hunting:read'), FIELDS));
	on('POST', '/hunting/search', ({ body }) => {
		need('hunting:read');
		const q = body as unknown as T.HuntQueryDoc;
		if (!Array.isArray(q.filters)) throw new Problem(422, 'invalid_query', 'Invalid search', 'The search is missing a time range or filters.');
		return search(q);
	});
	on('GET', '/hunting/saved', () => (need('hunting:read'), db.saved));
	on('POST', '/hunting/saved', ({ body }) => {
		need('hunting:write');
		const name = String(body.name ?? '').trim();
		if (!name) throw new Problem(422, 'validation_error', 'Validation failed', 'Name the search.', [{ field: 'name', message: 'Give the search a name.' }]);
		const s: T.SavedSearch = { id: hashOf('s' + name + now()).slice(0, 12), name, description: String(body.description ?? ''), query: body.query as T.HuntQueryDoc, createdBy: me()!, createdAt: now().toISOString(), lastRunAt: null, lastCount: null, schedule: 'on demand', alert: false };
		db.saved.unshift(s);
		audit('user', me()!.fullName, 'hunt.saved', `saved search "${name}"`, null);
		return s;
	});
	on('PATCH', '/hunting/saved/:id', ({ body }, [sid]) => {
		need('hunting:write');
		const s = db.saved.find((x) => x.id === sid);
		if (!s) throw new Problem(404, 'not_found', 'Not found');
		Object.assign(s, body);
		return s;
	});
	on('DELETE', '/hunting/saved/:id', (_c, [sid]) => {
		need('hunting:write');
		db.saved = db.saved.filter((x) => x.id !== sid);
		return undefined;
	});
	on('POST', '/hunting/saved/:id/run', (_c, [sid]) => {
		need('hunting:read');
		const s = db.saved.find((x) => x.id === sid);
		if (!s) throw new Problem(404, 'not_found', 'Not found');
		const r = search(s.query);
		s.lastRunAt = now().toISOString();
		s.lastCount = r.totalMatched;
		audit('user', me()!.fullName, 'hunt.run', `ran saved search "${s.name}"`, null);
		return r;
	});
	on('GET', '/hunting/suggestions', () => (need('hunting:read'), db.suggestions));

	// watch, executive
	on('GET', '/watch', () => {
		need('watch:read');
		return {
			clocks: clocks(),
			incidents: db.incidents.filter((i) => i.status !== 'resolved' && i.status !== 'closed').map(summary),
			pendingApprovals: db.proposals.filter((p) => p.status === 'pending').length,
			shiftLog: shiftLog(lastSeenAt),
			pipeline: { status: 'normal', plainStatus: 'Taking in events normally', eventsPerMinute: 1284 }
		} satisfies T.WatchDoc;
	});
	on('GET', '/watch/shift-log', ({ query }) => (need('watch:read'), shiftLog(query.get('since') ?? lastSeenAt)));
	on('POST', '/watch/seen', () => {
		lastSeenAt = now().toISOString();
		return undefined;
	});
	function shiftLog(since: string): T.ShiftLogEntry[] {
		const out: T.ShiftLogEntry[] = [];
		for (const i of db.incidents) if (i.firstSeen >= since) out.push({ at: i.firstSeen, text: `${i.reference} opened: ${i.title}`, kind: 'incident', refId: i.id, severity: i.severity });
		for (const p of db.proposals) for (const a of p.approvals) if (a.decidedAt >= since) out.push({ at: a.decidedAt, text: `${a.name} authorised: ${p.kindLabel.toLowerCase()}`, kind: 'approval', refId: p.incidentId, severity: null });
		return out.sort((a, b) => b.at.localeCompare(a.at));
	}
	on('GET', '/executive/posture', () => (need('exec:read'), { ...db.posture, clocks: clocks() }));
	on('GET', '/executive/morning', () => {
		need('exec:read');
		const end = demoNightEnd();
		const start = new Date(+end - 15 * 3600e3);
		const opened = db.incidents.filter((i) => +new Date(i.firstSeen) >= +start && +new Date(i.firstSeen) < +end).sort((a, b) => a.firstSeen.localeCompare(b.firstSeen));
		const serious = opened.filter((i) => (i.severity === 'critical' || i.severity === 'high') && i.verdict !== 'benign');
		const pending = db.proposals.filter((p) => p.status === 'pending').length;
		return {
			asOf: new Date(+end - 62 * 60e3).toISOString(),
			night: { from: start.toISOString(), to: end.toISOString() },
			headline: `Overnight: ${opened.length} incidents, ${serious.length} serious; ${pending} decision${pending === 1 ? '' : 's'} waiting for you`,
			incidents: opened.map(summary),
			contained: opened.filter((i) => ['monitoring', 'resolved', 'closed'].includes(i.status)).length,
			pendingApprovals: pending,
			alerts: db.posture.alerts,
			clocks: clocks().filter((c) => c.state !== 'not_started'),
			highlights: serious.map((i) => `${i.reference}: ${i.title} (${(STATUS_LABEL[i.status] ?? i.status).toLowerCase()}).`)
		} satisfies T.MorningBrief;
	});
	on('GET', '/executive/briefs', () => (need('exec:read'), db.briefs.map((b) => ({ week: b.week, generatedAt: b.to, headline: b.headline }))));
	on('GET', '/executive/briefs/:week', (_c, [w]) => {
		need('exec:read');
		const b = db.briefs.find((x) => x.week === w);
		if (!b) throw new Problem(404, 'not_found', 'Not found', 'No weekly report for that week.');
		return { ...b, incidents: b.incidents.length ? db.incidents.map(summary) : [] };
	});

	// behaviour, directory, settings, audit
	on('GET', '/ueba/anomalies', ({ query }) => {
		need('ueba:read');
		const t = query.get('entityType');
		return paged(db.anomalies.filter((a) => !t || a.entityType === t).sort((a, b) => b.score - a.score), query);
	});
	on('GET', '/assets', () => (need('incidents:read'), db.assets));
	on('GET', '/identities', () => (need('incidents:read'), db.identities));
	on('GET', '/settings/regulators', () => (need('settings:read'), db.org.settings.regimeContacts));
	on('PUT', '/settings/regulators', ({ body }) => {
		need('settings:manage');
		db.org.settings.regimeContacts = body as T.RegimeContacts;
		audit('user', me()!.fullName, 'settings.contacts_updated', 'updated regulator contacts', null);
		return db.org.settings.regimeContacts;
	});
	on('GET', '/settings/profile', () => (need('settings:read'), db.org.profile));
	on('PATCH', '/settings/preferences', ({ body }) => {
		need('settings:manage');
		Object.assign(db.org.settings, body);
		return db.org.settings;
	});
	on('GET', '/audit', ({ query }) => {
		need('audit:read');
		const actorType = query.get('actorType');
		const action = query.get('action');
		return paged(db.audit.filter((a) => (!actorType || a.actorType === actorType) && (!action || a.action.startsWith(action))), query);
	});
	on('GET', '/audit/verify', () => {
		need('audit:read');
		const rows = [...db.audit].reverse();
		let prev = '0'.repeat(64);
		for (const r of rows) {
			if (r.prevHash !== prev) return { valid: false, checked: rows.length, brokenAt: r.id };
			prev = r.hash;
		}
		return { valid: true, checked: 18381 + rows.length, brokenAt: null } satisfies T.AuditVerify;
	});

	// ---------- the river ----------
	const ANOMALY_LINES: [number, string, string, string | null][] = [
		[1.2, 'Baljit Singh failed to sign in 3 times from Melbourne, then succeeded from a new device', 'okta.system', null],
		[0.35, 'Response asked you to approve withdrawing the leaked deploy key', 'nightjar.synthetic', 'NJ-1038'],
		[2.3, 'Wendy Chen downloaded 2.9 GB from the finance share, nine times her usual', 'windows.security', null]
	];
	function riverHistory(limit: number): T.RiverLine[] {
		const t = +now();
		const out: T.RiverLine[] = [];
		for (let i = 0; i < limit; i++) {
			const at = new Date(t - i * 11_000 - Math.round(rnd(i) * 4000));
			const r = routineLine(i, at);
			out.push({ eventId: r.eventId, at: r.at, text: r.text, dataset: r.dataset, severity: null, anomaly: false, incidentId: null });
		}
		for (const [minAgo, text, dataset, ref] of ANOMALY_LINES) {
			const at = new Date(t - minAgo * 60_000);
			out.push({ eventId: hashOf(text).slice(0, 32), at: at.toISOString(), text, dataset, severity: ref ? 'high' : 'medium', anomaly: !ref, incidentId: ref ? (db.incidents.find((x) => x.reference === ref)?.id ?? null) : null });
		}
		return out.sort((a, b) => b.at.localeCompare(a.at));
	}
	let riverTimer: ReturnType<typeof setInterval> | null = null;
	function startRiver() {
		if (riverTimer || !opts.live) return;
		riverTimer = setInterval(() => {
			if (!listeners.size || !currentUserId) return;
			const n = 1 + Math.floor(rnd(riverIdx * 3) * 2);
			const lines: T.RiverLine[] = [];
			for (let k = 0; k < n; k++) {
				const r = routineLine(riverIdx++ + 7, now());
				lines.push({ eventId: r.eventId, at: r.at, text: r.text, dataset: r.dataset, severity: null, anomaly: false, incidentId: null });
			}
			const epm = 1240 + Math.round(rnd(riverIdx) * 90);
			emit({ type: 'river.batch', data: { count: Math.round(epm / 30), lines, eventsPerMinute: epm } });
		}, 2000);
	}

	// ---------- transport ----------
	async function handle(method: string, url: string, init?: RequestInit): Promise<Response> {
		const u = new URL(url, 'http://mock.local');
		const path = u.pathname.replace(/^\/api\/v1/, '');
		const headers = new Headers(init?.headers);
		let body: Record<string, unknown> = {};
		if (typeof init?.body === 'string' && init.body) {
			try {
				body = JSON.parse(init.body);
			} catch {
				return problem(new Problem(400, 'bad_request', 'Malformed JSON'));
			}
		}
		if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && path !== '/auth/csrf') {
			const token = headers.get('X-CSRF-Token');
			if (!token) return problem(new Problem(403, 'csrf_failed', 'CSRF check failed', 'Reload the page and try again.'));
		}
		for (const [m, rx, h] of routes) {
			if (m !== method) continue;
			const match = rx.exec(path);
			if (!match) continue;
			try {
				const out = await h({ method, path, query: u.searchParams, body, headers }, match.slice(1).map(decodeURIComponent));
				const h2 = { 'content-type': 'application/json', 'x-request-id': hashOf(path + seq).slice(0, 16) };
				return out === undefined ? new Response(null, { status: 204, headers: h2 }) : new Response(JSON.stringify(out), { status: method === 'POST' && /\/(invitations|saved)$/.test(path) ? 201 : 200, headers: h2 });
			} catch (e) {
				if (e instanceof Problem) return problem(e);
				throw e;
			}
		}
		return problem(new Problem(404, 'not_found', 'Not found'));
	}
	function problem(p: Problem): Response {
		return new Response(JSON.stringify({ type: `https://nightjar-soc.dev/problems/${p.code}`, title: p.title, status: p.status, detail: p.detail ?? p.title, code: p.code, errors: p.errors, requestId: hashOf(p.code + seq).slice(0, 16) }), { status: p.status, headers: { 'content-type': 'application/problem+json' } });
	}

	const fetchImpl: typeof fetch = async (input, init) => {
		const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
		const method = (init?.method ?? (typeof input === 'object' && 'method' in input ? input.method : 'GET')).toUpperCase();
		await new Promise((r) => setTimeout(r, 0));
		return handle(method, url, init);
	};

	return {
		db,
		fetch: fetchImpl,
		subscribe(fn: Listener): () => void {
			listeners.add(fn);
			startRiver();
			queueMicrotask(() => fn({ type: 'ready', data: { lastEventId: String(seq) } }));
			return () => {
				listeners.delete(fn);
				if (!listeners.size && riverTimer) {
					clearInterval(riverTimer);
					riverTimer = null;
				}
			};
		},
		signInAs(email: string) {
			const u = db.users.find((x) => x.email === email);
			currentUserId = u?.id ?? null;
		},
		setRequester(approvalId: string, userId: string) {
			const p = db.proposals.find((x) => x.id === approvalId);
			if (p) {
				p.proposedBy = { type: 'user', name: db.users.find((u) => u.id === userId)?.fullName ?? 'someone' };
				p.proposedByUserId = userId;
			}
		}
	};
}

export type MockServer = ReturnType<typeof createMockServer>;
