import { describe, expect, it } from 'vitest';
import type { ProposalDoc, Run, TimelineEntry, IdentityDoc } from '$lib/api/types';
import { approvalGate, actionTitle } from '$lib/util/approvals';
import { buildBaton, handoffPath, nextStep, rowSub, stepInterval } from '$lib/util/baton';
import { groupRanked, highlight, isTyping, matchText, rankCommands, type Command } from '$lib/commands';
import { parseHunt, parseRange, chipsFromQuery } from '$lib/util/hunt';
import { parseYaml, ruleSentence, ruleOneLine } from '$lib/util/sigma';
import { briefHeadline, decisionQuestion, firstSentence, nightIndex, numberWord } from '$lib/util/brief';
import { parseFrame } from '$lib/api/stream';
import { groupByMinute, minuteTotal } from '$lib/util/river';
import { clockLabel, noticeHref } from '$lib/util/labels';

const p = (o: Partial<ProposalDoc> = {}): ProposalDoc => ({
	id: 'a1', incidentId: 'i1', incidentReference: 'NJ-1038', runId: 'r1', kind: 'revoke_access_key', kindLabel: 'Withdraw this cloud access key', target: 'AKIA1', targetType: 'access_key',
	rationale: 'r', expectedEffect: 'e', reversible: false, destructive: true, crownJewel: false, proposedBy: { type: 'agent', name: 'Response' }, status: 'pending', requiredRoles: ['responder'],
	approvals: [], rejection: null, result: null, createdAt: '2025-11-20T08:02:40Z', expiresAt: '2025-11-21T08:02:40Z', ...o
});
const RESP = ['actions:approve'];

describe('approval gate (least privilege, no self-approval)', () => {
	it('a responder can hold a responder action', () => {
		expect(approvalGate(p(), 'u1', 'responder', RESP)).toEqual({ canHold: true, canDecide: true, reason: '' });
	});
	it('the auditor is read only', () => {
		expect(approvalGate(p(), 'u9', 'auditor', [])).toMatchObject({ canHold: false, reason: 'Read only' });
	});
	it('nobody approves their own request', () => {
		expect(approvalGate(p({ proposedByUserId: 'u1' }), 'u1', 'responder', RESP)).toMatchObject({ canHold: false, canDecide: true, reason: expect.stringMatching(/someone else/) });
	});
	it('an analyst cannot authorise a responder action', () => {
		expect(approvalGate(p(), 'u2', 'analyst', ['actions:propose'])).toMatchObject({ canHold: false, reason: 'Needs a responder' });
	});
	it('two-person rule: after the responder holds, the CISO can, the responder cannot again', () => {
		const cj = p({ crownJewel: true, requiredRoles: ['responder', 'executive'], approvals: [{ userId: 'u1', name: 'Marcus Bell', role: 'responder', decidedAt: 'x', note: null }] });
		expect(approvalGate(cj, 'u1', 'responder', RESP).canHold).toBe(false);
		expect(approvalGate(cj, 'u3', 'executive', ['actions:approve_crown_jewel']).canHold).toBe(true);
	});
	it('decided actions cannot be held', () => {
		expect(approvalGate(p({ status: 'executed' }), 'u1', 'responder', RESP).canHold).toBe(false);
	});
	it('names actions after the person and the thing', () => {
		const joy = { username: 'j.okafor', displayName: 'Joy Okafor', kind: 'person' } as IdentityDoc;
		expect(actionTitle(p({ kind: 'disable_account', target: 'j.okafor' }), [joy])).toBe('Turn off Joy’s account');
		expect(actionTitle(p({ kind: 'revoke_sessions', target: 'j.okafor' }), [joy])).toBe('Sign Joy out everywhere');
		expect(actionTitle(p({ kind: 'isolate_host', target: 'mem-dispatch-01' }))).toBe('Isolate mem-dispatch-01');
	});
});

const run: Run = {
	id: 'r1', incidentId: 'i1', trigger: 'auto', status: 'completed', startedAt: '2025-11-20T00:40:00Z', finishedAt: null, verdict: null, durationSeconds: null, stepCount: 4, requestedBy: null, proposals: [],
	steps: [
		{ seq: 1, agent: 'triage', kind: 'finding', title: 'Grouped 4 alerts', startedAt: '2025-11-20T00:40:12Z', durationMs: 1, tool: null, a2aTaskId: null, model: null, flags: [], detail: { lookedAt: ['a', 'b'] } },
		{ seq: 2, agent: 'triage', kind: 'tool_call', title: 'Checked: sign-in history', startedAt: '2025-11-20T00:40:20Z', durationMs: 1, tool: 'x', a2aTaskId: null, model: null, flags: [], detail: {} },
		{ seq: 3, agent: 'triage', kind: 'verdict', title: 'Likely real: 96 in 100', startedAt: '2025-11-20T00:40:41Z', durationMs: 1, tool: null, a2aTaskId: null, model: null, flags: [], detail: { why: 'new network' } },
		{ seq: 4, agent: 'response', kind: 'proposal', title: 'Asks to withdraw the key', startedAt: '2025-11-20T08:02:40Z', durationMs: 1, tool: null, a2aTaskId: null, model: null, flags: [], detail: {} }
	]
};
const tl: TimelineEntry[] = [{ id: 't1', at: '2025-11-20T00:47:12Z', kind: 'approval', actor: { type: 'user', name: 'Marcus Bell' }, title: 'Marcus held to authorise', body: null, refId: null, executive: true }];

describe('baton timeline', () => {
	it('key steps only by default; all activity adds lookups', () => {
		expect(buildBaton([run], [], []).length).toBe(3);
		expect(buildBaton([run], [], [], { all: true }).length).toBe(4);
	});
	it('people rows come from approvals and sit on lane 4, in time order', () => {
		const rows = buildBaton([run], tl, []);
		expect(rows.map((r) => r.lane)).toEqual([1, 1, 4, 3]);
		expect(rows[2]!.state).toBe('ok');
	});
	it('a pending proposal hands the baton to people', () => {
		const rows = buildBaton([run], tl, [p()]);
		const last = rows.at(-1)!;
		expect(last.state).toBe('wait');
		expect(rowSub(last)).toBe('‖ handed to people · awaiting');
	});
	it('subs read "why" for verdicts and "looked at" for findings', () => {
		const rows = buildBaton([run], [], []);
		expect(rowSub(rows[0]!)).toBe('looked at a, b');
		expect(rowSub(rows[1]!)).toBe('why new network');
	});
	it('replay steps at 1.2 s (1×) or 0.3 s (4×) and stops at the end', () => {
		expect(stepInterval(1)).toBe(1200);
		expect(stepInterval(4)).toBe(300);
		expect(nextStep(2, 4)).toBe(3);
		expect(nextStep(3, 4)).toBeNull();
	});
	it('hand-offs are S-curves between lane dots', () => {
		expect(handoffPath({ x: 10, y: 0 }, { x: 50, y: 40 })).toBe('M10,0 C10,20 50,20 50,40');
	});
});

describe('⌘K', () => {
	const c = (id: string, title: string, group: Command['group'] = 'go to', scope?: string): Command => ({ id, title, group, icon: '', scope, run: () => {} });
	it('matches substrings, word starts best', () => {
		expect(matchText('Isolate the laptop', 'iso')!.ranges).toEqual([[0, 3]]);
		expect(matchText('Isolate', 'zzz')).toBeNull();
		expect(matchText('Saved searches', 'svd')).not.toBeNull();
	});
	it('scopes to the focused incident first; Tab widens', () => {
		const cmds = [c('a', 'Hold NJ-1038 key', 'do', 'NJ-1038'), c('b', 'Hold NJ-1036 server', 'do', 'NJ-1036'), c('w', 'The watch')];
		expect(rankCommands(cmds, '', { scope: 'NJ-1038' }).map((r) => r.command.id)).toEqual(['a', 'w']);
		expect(rankCommands(cmds, '', { scope: 'NJ-1038', widened: true }).map((r) => r.command.id).sort()).toEqual(['a', 'b', 'w']);
	});
	it('groups do / go to / switch in that order', () => {
		const cmds = [c('s', 'Theme', 'switch'), c('g', 'Rules', 'go to'), c('d', 'Decline', 'do')];
		expect(groupRanked(rankCommands(cmds, '')).map((g) => g.group)).toEqual(['do', 'go to', 'switch']);
	});
	it('highlights matches', () => {
		expect(highlight('Isolate', [[0, 3]])).toEqual([{ text: 'Iso', hit: true }, { text: 'late', hit: false }]);
	});
	it('never fires shortcuts while typing', () => {
		expect(isTyping(document.createElement('input'))).toBe(true);
		expect(isTyping(document.createElement('div'))).toBe(false);
	});
});

describe('plain-words hunting', () => {
	const now = new Date('2025-11-20T08:18:23Z');
	it('turns a sentence into filters', () => {
		const r = parseHunt('laptops that opened links from halden-logistlcs.example in the last 7 days', now);
		expect(r.chips.map((c) => c.label)).toEqual(['domain = halden-logistlcs.example', 'event = link opened']);
		expect(r.range).toBe('last 7 days');
		expect(r.query.from).toBe(new Date(+now - 7 * 864e5).toISOString());
	});
	it('finds people, devices and addresses', () => {
		const r = parseHunt('sign-ins for j.okafor on okta from 198.51.100.77', now);
		expect(r.chips.map((c) => c.filter.field)).toEqual(['source.ip', 'host.name', 'user.name', 'action']);
	});
	it('reads relative ranges from today', () => {
		expect(parseRange('last 24 hours', now).label).toBe('last 24 hours');
		expect(parseRange('anything', now).label).toBe('last 7 days');
	});
	it('rebuilds chips for saved searches', () => {
		expect(chipsFromQuery({ from: '', to: '', filters: [{ field: 'url', op: 'contains', value: 'x' }], groupBy: [], metric: 'count', bucket: null, limit: 10 })[0]!.label).toBe('link ∋ x');
	});
});

describe('rules as sentences', () => {
	const y = `title: T\nlogsource:\n  product: m365\n  service: audit\ndetection:\n  selection:\n    Operation: New-InboxRule\n    ForwardTo|contains: '@'\n  filter_internal:\n    ForwardTo|endswith: '@halden.example'\n  condition: selection and not filter_internal\n`;
	it('parses the Sigma subset', () => {
		const d = parseYaml(y);
		expect((d.logsource as Record<string, string>).product).toBe('m365');
		expect(((d.detection as Record<string, unknown>).selection as Record<string, string>)['ForwardTo|contains']).toBe('@');
	});
	it('reads as When / Unless', () => {
		const s = ruleSentence(y);
		expect(s.when).toBe('a Microsoft 365 mailbox change');
		expect(s.clauses).toEqual(['the mailbox action is creating an inbox rule', 'the forwarding address contains “@”']);
		expect(s.unless).toEqual(['the forwarding address ends with “@halden.example”']);
	});
	it('reads counts and lists', () => {
		const c = `logsource:\n  product: okta\n  service: system\ndetection:\n  selection:\n    eventType: mfa.push.denied\n  timeframe: 10m\n  condition: selection | count() by user >= 4\n`;
		expect(ruleSentence(c).count).toBe('4 or more times for the same person within 10 minutes');
		const l = `logsource:\n  product: windows\n  category: process_creation\ndetection:\n  selection:\n    CommandLine|contains:\n      - 'delete shadows'\n      - 'resize shadowstorage'\n  condition: selection\n`;
		expect(ruleOneLine(l)).toContain('the command line contains one of “delete shadows”, “resize shadowstorage”');
	});
});

describe('the brief', () => {
	it('headline counts in words', () => {
		const h = briefHeadline(1, 1, 2, 'the last two nights');
		expect(h.lead + h.tail + h.em + h.end).toBe('One incident from the last two nights is reportable. It is contained, and two decisions are yours today.');
		expect(briefHeadline(0, 0, 0, 'overnight').lead).toBe('Nothing from overnight is reportable.');
		expect(numberWord(3, false)).toBe('three');
	});
	it('asks the decision in business words', () => {
		const a = { regime: 'sec_8k', regimeLabel: 'SEC Form 8-K' } as never;
		expect(decisionQuestion(a, { kind: 'cloud_credential_exposure', title: '' })).toBe('Is the cloud key exposure at the invoices store material to Halden Logistics?');
	});
	it('places incidents on the seven-nights strip', () => {
		const night = new Date('2025-11-19T23:00:00Z');
		expect(nightIndex(new Date('2025-11-20T02:12:00Z'), night)).toMatchObject({ row: 6 });
		expect(nightIndex(new Date('2025-11-19T03:10:00Z'), night)!.row).toBe(5);
		expect(nightIndex(new Date('2025-11-19T12:00:00Z'), night)).toBeNull();
		expect(firstSentence('One. Two.')).toBe('One.');
	});
});

describe('live stream and river', () => {
	it('parses known SSE frames and drops unknown or malformed ones', () => {
		expect(parseFrame('clock.started', '{"incidentId":"i","regime":"sec_8k","noticeType":"x","dueAt":"d"}')).toMatchObject({ type: 'clock.started' });
		expect(parseFrame('nope', '{}')).toBeNull();
		expect(parseFrame('river.batch', '{bad')).toBeNull();
	});
	it('groups river lines by minute, newest first', () => {
		const g = groupByMinute([{ eventId: '1', at: '2025-11-20T08:17:10Z', text: 'a', dataset: 'x', severity: null, anomaly: false, incidentId: null }, { eventId: '2', at: '2025-11-20T08:18:05Z', text: 'b', dataset: 'x', severity: null, anomaly: true, incidentId: null }], 'Europe/Brussels');
		expect(g.map((x) => x.label)).toEqual(['09:18', '09:17']);
		expect(minuteTotal(undefined, 1284, '2025-11-20T08:17:00.000Z', new Date('2025-11-20T08:18:30Z'))).toBeGreaterThan(1200);
	});
	it('labels clocks and links them to the notice or the decision', () => {
		const d = { id: 'd', incidentId: 'i', incidentReference: 'NJ-1038', regime: 'sec_8k', regimeLabel: 'SEC 8-K', noticeType: 'x', noticeLabel: 'Item 1.05', jurisdiction: 'United States', startsAt: null, dueAt: null, dueAtLocal: null, businessDays: null, state: 'not_started', remainingSeconds: null, noticeId: null, basis: '' } as const;
		expect(clockLabel({ ...d })).toBe('SEC 8-K · US · NJ-1038');
		expect(noticeHref({ ...d })).toBe('/incidents/NJ-1038?decide=sec_8k');
	});
});
