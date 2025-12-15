// The shell's model and its primitives: turnover readouts by role, the lamp panel's windows, station names, toasts
// through the Skeleton toaster, and the Plate, ClockList, WatchDial, LampPanel and ApprovalCard components. The
// layering rules are tested too: labels of three words at most, and detail one click deeper than the action.
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { sectionsFor, SECTIONS, SECTION_LABEL, stationFor, stationLine, toaster, ui, WINDOWS, windowFor } from '$lib/stores/ui.svelte';
import { createRawSnippet } from 'svelte';
import Plate from '$lib/components/ui/Plate.svelte';
import ClockList from '$lib/components/console/ClockList.svelte';
import WatchDial from '$lib/components/console/WatchDial.svelte';
import LampPanel from '$lib/components/shell/LampPanel.svelte';
import ApprovalCard from '$lib/components/console/ApprovalCard.svelte';
import type { Deadline, IncidentSummary, ProposalDoc } from '$lib/api/types';
import { shiftWindow } from '$lib/util/shift';

// LampPanel reads the current page and the live watch; neither exists outside the browser app.
vi.mock('$app/state', () => ({ page: { url: new URL('http://localhost/watch') } }));
vi.mock('$lib/stores/live.svelte', () => ({ live: { incidents: [], clocks: [] } }));
vi.mock('$lib/stores/session.svelte', () => ({ session: { can: () => true, me: { user: { id: 'u-marcus' }, membership: { permissions: ['actions:approve'] } }, role: 'responder', name: 'Marcus Bell' } }));
// the approval card talks to the API only when someone holds or declines
vi.mock('$lib/api', () => ({ api: {}, problemMessage: (e: unknown) => String(e) }));

const text = (t: string) => createRawSnippet(() => ({ render: () => `<p>${t}</p>` }));

describe('turnover sections', () => {
	it('puts what is waiting first for the people who decide', () => {
		expect(sectionsFor('responder')[0]).toBe('awaiting');
		expect(sectionsFor('admin')[0]).toBe('awaiting');
	});
	it('gives analysts a hunt and auditors the activity chain instead', () => {
		expect(sectionsFor('analyst')[0]).toBe('hunt');
		expect(sectionsFor('auditor')[0]).toBe('activity');
	});
	it('shows every role the clocks', () => {
		for (const s of Object.values(SECTIONS)) expect(s).toContain('clocks');
	});
	it('falls back to the responder turnover for the CISO and before sign-in', () => {
		expect(sectionsFor('executive')).toEqual(SECTIONS.responder);
		expect(sectionsFor(null)).toEqual(SECTIONS.responder);
	});
	it('labels every readout in three words or fewer', () => {
		for (const label of Object.values(SECTION_LABEL)) expect(label.split(/\s+/).length).toBeLessThanOrEqual(3);
	});
	it('returns a copy, so a preset cannot be edited in place', () => {
		const a = sectionsFor('analyst');
		a.pop();
		expect(sectionsFor('analyst')).toHaveLength(4);
	});
});

describe('the lamp panel windows', () => {
	it('numbers eight windows 1 to 8, the watch first and the brief last', () => {
		expect(WINDOWS.map((w) => w.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
		expect(WINDOWS[0]!.href).toBe('/watch');
		expect(WINDOWS.at(-1)!.href).toBe('/brief');
	});
	it('keeps an incident and its notice under the watch window', () => {
		expect(windowFor('/incidents/NJ-1038')).toBe(1);
		expect(windowFor('/incidents/NJ-1037/notice/nis2')).toBe(1);
	});
	it('finds the window for nested pages and none for settings', () => {
		expect(windowFor('/hunt/saved')).toBe(2);
		expect(windowFor('/identities')).toBe(6);
		expect(windowFor('/brief/week/2025-W47')).toBe(8);
		expect(windowFor('/settings/members')).toBe(0);
	});
});

describe('station names', () => {
	it('names every page in the head before the page names itself', () => {
		expect(stationFor('/watch')).toBe('The watch');
		expect(stationFor('/incidents/NJ-1038')).toBe('Incident');
		expect(stationFor('/incidents/NJ-1037/notice/nis2')).toBe('Notice draft');
		expect(stationFor('/hunt/saved')).toBe('Saved searches');
		expect(stationFor('/rules/r-1')).toBe('Rule');
		expect(stationFor('/rules')).toBe('Detection rules');
		expect(stationFor('/brief/week/2025-W47')).toBe('Weekly report');
		expect(stationFor('/brief')).toBe('Morning brief');
	});
	it('keeps what a register is for as a plain sentence (the name’s tooltip), and the watch none of its own', () => {
		expect(stationLine('/assets')).toMatch(/server, laptop and cloud account/);
		expect(stationLine('/hunt/saved')).toMatch(/^Searches worth repeating/);
		expect(stationLine('/watch')).toBe('');
	});
	it('keeps a page’s own name only on that page', () => {
		ui.setStation('/incidents/NJ-1038', 'Incident NJ-1038', 'Opened 02:16');
		expect(ui.station).toEqual({ path: '/incidents/NJ-1038', title: 'Incident NJ-1038', sub: 'Opened 02:16' });
	});
});

describe('LampPanel', () => {
	it('lights the station you are on and hides none a role may open', () => {
		render(LampPanel);
		const nav = screen.getByRole('navigation', { name: 'Stations' });
		expect(nav.querySelectorAll('a')).toHaveLength(8);
		expect(nav.querySelector('a[aria-current="page"]')?.getAttribute('href')).toBe('/watch');
	});
	it('draws public windows with the lit one marked as the current page', () => {
		render(LampPanel, { props: { items: [{ n: 1, href: '/', label: 'The night' }, { n: 2, href: '/sign-in', label: 'Sign on', lit: true }] } });
		const nav = screen.getByRole('navigation', { name: 'Nightjar SOC' });
		const links = nav.querySelectorAll('a');
		expect(links).toHaveLength(2);
		expect(links[1]!.getAttribute('aria-current')).toBe('page');
		expect(links[0]!.getAttribute('aria-current')).toBeNull();
	});
});

describe('toasts', () => {
	it('go through the Skeleton toaster with a type for the tone, and are announced', async () => {
		const spy = vi.spyOn(toaster, 'create');
		ui.toast('Declined.', 'info');
		ui.toast('Something went wrong.', 'bad');
		ui.toast('Recorded.');
		expect(spy.mock.calls.map((c) => [c[0].description, c[0].type])).toEqual([
			['Declined.', 'info'],
			['Something went wrong.', 'error'],
			['Recorded.', 'success']
		]);
		await Promise.resolve();
		expect(ui.announcement).toBe('Recorded.');
		spy.mockRestore();
	});
});

describe('Plate', () => {
	it('is a labelled region with a numbered lamp, its summary and its actions', () => {
		render(Plate, { props: { title: 'waiting on you', sub: '2 decisions', n: 1, children: text('body'), actions: text('act') } });
		const region = screen.getByRole('region', { name: 'waiting on you' });
		expect(region.querySelector('.n')?.textContent).toBe('1');
		expect(region.querySelector('.sub')?.textContent).toBe('2 decisions');
		expect(screen.getByText('act')).toBeTruthy();
		expect(screen.getByText('body')).toBeTruthy();
	});
	it('takes an explicit label over the title', () => {
		render(Plate, { props: { title: 'notice', label: 'Notice draft', children: text('x') } });
		expect(screen.getByRole('region', { name: 'Notice draft' })).toBeTruthy();
	});
	it('keeps a quiet plate’s heading for screen readers only, so the page has one visible title', () => {
		const { container } = render(Plate, { props: { title: 'The register', sub: '18 of 18', quiet: true, children: text('rows') } });
		const h = screen.getByRole('heading', { name: 'The register' });
		expect(h.classList.contains('sr')).toBe(true);
		expect(screen.getByRole('region', { name: 'The register' })).toBeTruthy();
		expect(container.querySelector('.bar')).toBeNull();
		expect(container.querySelector('.sub')?.textContent).toBe('18 of 18');
	});
	it('marks the plates that hold a decision with corner ticks', () => {
		const { container } = render(Plate, { props: { title: 'rule', ticks: true, children: text('x') } });
		expect(container.querySelector('.body.ticks')).toBeTruthy();
	});
});

describe('ClockList', () => {
	const base: Omit<Deadline, 'id' | 'state' | 'regime' | 'noticeLabel'> = {
		incidentId: 'i1',
		incidentReference: 'NJ-1036',
		regimeLabel: 'NIS2',
		noticeType: 'nis2.early_warning',
		jurisdiction: 'EU',
		startsAt: '2025-11-20T03:52:00Z',
		dueAt: '2025-11-21T03:52:00Z',
		dueAtLocal: null,
		businessDays: null,
		remainingSeconds: null,
		noticeId: 'n1',
		basis: 'test'
	};
	it('lists running clocks with a glyph and a word, and hides the ones that do not apply', () => {
		render(ClockList, {
			props: {
				now: new Date('2025-11-20T09:24:00Z'),
				clocks: [
					{ ...base, id: 'a', state: 'on_track', regime: 'nis2', noticeLabel: 'early warning' },
					{ ...base, id: 'b', state: 'not_started', regime: 'sec_8k', noticeLabel: 'Form 8-K Item 1.05', dueAt: null },
					{ ...base, id: 'c', state: 'not_required', regime: 'soci', noticeLabel: 'incident report' }
				]
			}
		});
		const links = screen.getAllByRole('link');
		expect(links).toHaveLength(2);
		expect(links[1]!.textContent).toMatch(/waits for the materiality decision/);
		expect(links[1]!.textContent).toMatch(/◆/);
	});
	it('says so when nothing is running', () => {
		render(ClockList, { props: { clocks: [], now: new Date('2025-11-20T09:24:00Z') } });
		expect(screen.getByText('No regulator clocks running.')).toBeTruthy();
	});
});

describe('WatchDial', () => {
	const now = new Date('2025-11-20T09:24:00Z');
	const win = shiftWindow(now, 'Europe/Brussels');
	const inc = (id: string, ref: string, over: Partial<IncidentSummary> = {}): IncidentSummary => ({
		id,
		reference: ref,
		title: `Incident ${ref}`,
		kind: 'x',
		severity: 'high',
		status: 'containing',
		stage: 'contain',
		assets: [],
		identities: [],
		attackTactics: [],
		detectionCount: 1,
		firstSeen: '2025-11-20T00:40:00Z',
		lastSeen: '2025-11-20T01:00:00Z',
		assignee: null,
		verdict: null,
		likelyBenign: false,
		reportable: null,
		nextDeadline: null,
		pendingApprovals: 0,
		...over
	});
	it('gives every incident line an accessible link that says where it is now', () => {
		render(WatchDial, { props: { win, incidents: [inc('a', 'NJ-1038', { pendingApprovals: 1 }), inc('b', 'NJ-1035', { status: 'resolved' })], log: [], tz: 'Europe/Brussels' } });
		const line = screen.getByRole('link', { name: /^NJ-1038 .*now in contain, waiting for a person$/ });
		expect(line.getAttribute('href')).toBe('/incidents/NJ-1038');
		expect(screen.getByRole('link', { name: /^NJ-1035 .*finished$/ })).toBeTruthy();
	});
	it('summarises the rings for screen readers and lists open incidents under the dial', () => {
		const { container } = render(WatchDial, { props: { win, incidents: [inc('a', 'NJ-1038')], log: [], tz: 'Europe/Brussels' } });
		expect(screen.getByRole('figure').getAttribute('aria-label')).toBe('1 incidents on this shift: 0 open in triage, 0 open in investigate, 1 open in contain, 0 open in report');
		expect(container.querySelectorAll('.open .tag')).toHaveLength(1);
		expect(container.querySelector('.centre .w')?.textContent).toBe('forenoon watch');
	});
	it('links somewhere else when asked (the landing sends people to sign in)', () => {
		render(WatchDial, { props: { win, incidents: [inc('a', 'NJ-1038')], log: [], tz: 'Europe/Brussels', hrefFor: () => '/sign-in' } });
		for (const a of screen.getAllByRole('link', { name: /NJ-1038/ })) expect(a.getAttribute('href')).toBe('/sign-in');
	});
});

describe('ApprovalCard', () => {
	const proposal: ProposalDoc = {
		id: 'p-1',
		incidentId: 'i-1',
		incidentReference: 'NJ-1038',
		runId: null,
		kind: 'revoke_access_key',
		kindLabel: 'Withdraw a cloud access key',
		target: 'AKIAEXAMPLEHALDEN01',
		targetType: 'access_key',
		rationale: 'The key is public and being used by someone else.',
		expectedEffect: 'Anything using this key stops working until a new key is issued.',
		reversible: false,
		destructive: true,
		crownJewel: false,
		proposedBy: { type: 'agent', name: 'Response colleague' },
		status: 'pending',
		requiredRoles: ['responder'],
		approvals: [],
		rejection: null,
		result: null,
		createdAt: '2025-11-20T01:04:00Z',
		expiresAt: '2025-11-20T09:04:00Z'
	};
	it('shows the action, whether it can be undone and the hold first, with what it does and why under Details', () => {
		const { container } = render(ApprovalCard, { props: { p: proposal } });
		expect(screen.getByRole('heading', { level: 2 }).textContent).toMatch(/key/i);
		expect(screen.getByRole('button', { name: /Hold to authorise/i })).toBeTruthy();
		const more = container.querySelector('details.more') as HTMLDetailsElement;
		expect(more).toBeTruthy();
		expect(more.open).toBe(false);
		expect(more.querySelector('summary')?.textContent).toBe('Details');
		expect(more.textContent).toContain(proposal.expectedEffect);
		expect(more.textContent).toContain(proposal.rationale);
		expect(container.querySelector('.cq')?.textContent).toBe('✕ Cannot be undone');
		expect(more.textContent).not.toContain('Cannot be undone');
	});
	it('names a crown jewel in a few words, with the rule in its tooltip', () => {
		const { container } = render(ApprovalCard, { props: { p: { ...proposal, crownJewel: true } } });
		const cj = container.querySelector('.cj');
		expect(cj?.textContent).toBe('◆ Crown jewel · two people');
		expect(cj?.getAttribute('title')).toMatch(/responder and the CISO/);
	});
});
