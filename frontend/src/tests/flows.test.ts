// Key flows end to end through the typed client and the contract-shaped mock API.
import { describe, expect, it } from 'vitest';
import { createClient, ApiError } from '$lib/api/client';
import { endpoints } from '$lib/api/endpoints';
import { createMockServer } from '$lib/api/mock/server';
import type { StreamEvent } from '$lib/api/types';

const AT = new Date('2025-11-20T08:18:23Z');
function setup(email?: string) {
	const server = createMockServer({ now: () => AT });
	const api = endpoints(createClient({ fetch: server.fetch, readCookie: () => 'csrf' }));
	const events: StreamEvent[] = [];
	server.subscribe((e) => events.push(e));
	if (email) server.signInAs(email);
	return { server, api, events };
}
const MARCUS = 'marcus.bell@halden.example';
const INGRID = 'ingrid.vos@halden.example';

describe('sign-in', () => {
	it('rejects a wrong password with a generic problem', async () => {
		const { api } = setup();
		const e = await api.auth.login(MARCUS, 'nope').catch((x) => x);
		expect(e).toBeInstanceOf(ApiError);
		expect(e.code).toBe('invalid_credentials');
	});
	it('signs in a demo role and returns the server calendar date', async () => {
		const { api } = setup();
		const me = await api.auth.login(INGRID, 'Nightjar-demo-2025!');
		expect(me.membership?.role).toBe('executive');
		expect(me.clock).toEqual({ today: '2025-11-20', demo: true });
	});
	it('refuses unsafe calls without a CSRF token', async () => {
		const server = createMockServer({ now: () => AT });
		const res = await server.fetch('/api/v1/auth/login', { method: 'POST', body: '{}' });
		expect(res.status).toBe(403);
		expect((await res.json()).code).toBe('csrf_failed');
	});
	it('is 401 before sign-in', async () => {
		const { api } = setup();
		expect((await api.auth.me().catch((x) => x)).code).toBe('not_authenticated');
	});
});

describe('the watch', () => {
	it('lists clocks soonest first, with the SEC decision not started', async () => {
		const { api } = setup(MARCUS);
		const clocks = await api.reporting.clocks();
		expect(clocks.map((c) => `${c.incidentReference} ${c.regime} ${c.state}`)).toEqual([
			'NJ-1036 nis2 on_track',
			'NJ-1036 soci on_track',
			'NJ-1036 sec_8k on_track',
			'NJ-1038 sec_8k not_started'
		]);
		expect(clocks[0]!.remainingSeconds).toBe(43 * 3600 + 33 * 60 + 37);
	});
	it('has one pending approval: the leaked key', async () => {
		const { api } = setup(MARCUS);
		const w = await api.watch.get();
		expect(w.pendingApprovals).toBe(1);
		const r = await api.approvals.list({ status: 'pending' });
		expect(r.items[0]!.target).toBe('AKIAEXAMPLEHALDEN01');
	});
	it('river lines are plain words with an incident marker', async () => {
		const { api } = setup(MARCUS);
		const r = await api.events.river();
		expect(r.lines.some((l) => l.incidentId)).toBe(true);
		expect(r.lines.some((l) => l.anomaly)).toBe(true);
	});
});

describe('hold to authorise → approve', () => {
	it('a responder approves; the action executes and SSE says so', async () => {
		const { api, events } = setup(MARCUS);
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		const res = await api.approvals.approve(p!.id);
		expect(res.status).toBe('approved');
		expect(events.some((e) => e.type === 'approval.decided')).toBe(true);
		await new Promise((r) => setTimeout(r, 950));
		expect(events.some((e) => e.type === 'action.executed')).toBe(true);
		expect((await api.incidents.get('NJ-1038')).status).toBe('monitoring');
	});
	it('approving twice is a conflict', async () => {
		const { api } = setup(MARCUS);
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		await api.approvals.approve(p!.id);
		expect((await api.approvals.approve(p!.id).catch((x) => x)).code).toBe('already_decided');
	});
	it('nobody approves their own request (409 self_approval)', async () => {
		const { api, server } = setup(MARCUS);
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		server.setRequester(p!.id, server.db.users.find((u) => u.email === MARCUS)!.id);
		expect((await api.approvals.approve(p!.id).catch((x) => x)).code).toBe('self_approval');
	});
	it('the auditor cannot approve (403)', async () => {
		const { api } = setup('samira.haddad@halden.example');
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		expect((await api.approvals.approve(p!.id).catch((x) => x)).status).toBe(403);
	});
	it('declining needs a reason that goes back to Response', async () => {
		const { api } = setup(MARCUS);
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		expect((await api.approvals.reject(p!.id, ' ').catch((x) => x)).code).toBe('validation_error');
		const r = await api.approvals.reject(p!.id, 'Business-critical right now');
		expect(r.rejection?.reason).toBe('Business-critical right now');
	});
});

describe('regulator clocks and notices', () => {
	it('recording "material" starts the SEC clock (drops into the bar)', async () => {
		const { api, events } = setup(INGRID);
		const inc = await api.incidents.get('NJ-1038');
		await api.incidents.assess(inc.id, 'sec_8k', { applies: true, material: true, materialityDeterminedAt: AT.toISOString(), rationale: '38 invoice files were read; customers may be affected.', entityId: 'halden-us' });
		const started = events.find((e) => e.type === 'clock.started');
		expect(started).toBeTruthy();
		expect((started as Extract<StreamEvent, { type: 'clock.started' }>).data.dueAt).toBe('2025-11-26T22:30:00.000Z');
		const clocks = await api.reporting.clocks();
		expect(clocks.find((c) => c.incidentReference === 'NJ-1038')!.state).toBe('on_track');
	});
	it('"not material" needs reasons and removes the clock', async () => {
		const { api } = setup(INGRID);
		const inc = await api.incidents.get('NJ-1038');
		expect((await api.incidents.assess(inc.id, 'sec_8k', { applies: false, material: false, rationale: 'no', entityId: 'halden-us' }).catch((x) => x)).code).toBe('validation_error');
		await api.incidents.assess(inc.id, 'sec_8k', { applies: false, material: false, rationale: 'Read-only access to invoices, no money moved.', entityId: 'halden-us' });
		expect((await api.reporting.clocks()).some((c) => c.incidentReference === 'NJ-1038')).toBe(false);
	});
	it('only the executive can record determinations', async () => {
		const { api } = setup(MARCUS);
		const inc = await api.incidents.get('NJ-1038');
		expect((await api.incidents.assess(inc.id, 'sec_8k', { applies: true, rationale: 'x'.repeat(20), entityId: 'halden-us' }).catch((x) => x)).status).toBe(403);
	});
	it('submission lists missing fields first, then records a mock receipt and stops the clock', async () => {
		const { api } = setup(INGRID);
		const nis2 = (await api.reporting.clocks()).find((c) => c.regime === 'nis2')!;
		let n = await api.notices.get(nis2.noticeId!);
		expect(n.missing).toEqual(['contact']);
		expect((await api.notices.approve(n.id).catch((x) => x)).code).toBe('validation_error');
		n = await api.notices.patch(n.id, [{ key: 'contact', body: 'Ingrid Vos, CISO, +32 3 555 0142' }], n.version);
		n = await api.notices.approve(n.id);
		n = await api.notices.submit(n.id, 'portal');
		expect(n.submission?.receipt).toMatch(/^CCB-MOCK-[0-9A-F]{8}$/);
		expect(n.submission?.note).toBe('Recorded as submitted (demo)');
		const after = (await api.reporting.clocks()).find((c) => c.id === nis2.id)!;
		expect(after.state).toBe('submitted');
	});
	it('editing a stale draft is a conflict', async () => {
		const { api } = setup(INGRID);
		const nis2 = (await api.reporting.clocks()).find((c) => c.regime === 'nis2')!;
		const n = await api.notices.get(nis2.noticeId!);
		expect((await api.notices.patch(n.id, [], n.version - 1).catch((x) => x)).code).toBe('stale_version');
	});
});

describe('hunting, audit, tenancy of untrusted text', () => {
	it('a structured search finds the four link openers', async () => {
		const { api } = setup('lukas.peeters@halden.example');
		const r = await api.hunting.search({ from: new Date(+AT - 7 * 864e5).toISOString(), to: AT.toISOString(), filters: [{ field: 'url', op: 'contains', value: 'halden-logistlcs.example' }, { field: 'action', op: 'eq', value: 'url.clicked' }], groupBy: [], metric: 'count', bucket: null, limit: 200 });
		expect(r.totalMatched).toBe(4);
	});
	it('instruction-like log text comes back as plain summary text', async () => {
		const { api } = setup('lukas.peeters@halden.example');
		const r = await api.hunting.search({ from: new Date(+AT - 864e5).toISOString(), to: AT.toISOString(), filters: [{ field: 'action', op: 'eq', value: 'inbox_rule.created' }], groupBy: [], metric: 'count', bucket: null, limit: 10 });
		expect(r.columns.at(-1)).toBe('summary');
		expect(String(r.rows[0]!.at(-1))).toContain('SYSTEM NOTE');
	});
	it('the activity log chain verifies', async () => {
		const { api } = setup('samira.haddad@halden.example');
		const v = await api.audit.verify();
		expect(v.valid).toBe(true);
	});
	it('approving appends to the chain and it still verifies', async () => {
		const { api, server } = setup(MARCUS);
		const [p] = (await api.approvals.list({ status: 'pending' })).items;
		await api.approvals.approve(p!.id);
		server.signInAs('samira.haddad@halden.example');
		const list = await api.audit.list();
		expect(list.items[0]!.label).toMatch(/held to authorise: withdraw this cloud access key/);
		expect((await api.audit.verify()).valid).toBe(true);
	});
	it('unknown ids are 404, never 403', async () => {
		const { api } = setup(MARCUS);
		expect((await api.incidents.get('NJ-9999').catch((x) => x)).status).toBe(404);
	});
});
