// The shell's model and the band primitives: turnover sections by role, the numbered windows, toasts through the
// Skeleton toaster, and the Band, ClockList and ShiftChart components.
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { sectionsFor, SECTIONS, toaster, ui, WINDOWS, windowFor } from '$lib/stores/ui.svelte';
import { createRawSnippet } from 'svelte';
import Band from '$lib/components/ui/Band.svelte';
import ClockList from '$lib/components/console/ClockList.svelte';
import ShiftChart from '$lib/components/console/ShiftChart.svelte';
import type { Deadline, IncidentSummary } from '$lib/api/types';
import { shiftWindow } from '$lib/util/shift';

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
	it('returns a copy, so a preset cannot be edited in place', () => {
		const a = sectionsFor('analyst');
		a.pop();
		expect(sectionsFor('analyst')).toHaveLength(4);
	});
});

describe('the watch bar windows', () => {
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

describe('Band', () => {
	it('is a labelled region with a numbered rule and its actions', () => {
		render(Band, { props: { title: 'waiting on you', sub: '2', n: 1, children: text('body'), actions: text('act') } });
		const region = screen.getByRole('region', { name: 'waiting on you' });
		expect(region.querySelector('.n')?.textContent).toBe('1');
		expect(region.textContent).toContain('· 2');
		expect(screen.getByText('act')).toBeTruthy();
		expect(screen.getByText('body')).toBeTruthy();
	});
	it('takes an explicit label over the title', () => {
		render(Band, { props: { title: 'notice', label: 'Notice draft', children: text('x') } });
		expect(screen.getByRole('region', { name: 'Notice draft' })).toBeTruthy();
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

describe('ShiftChart', () => {
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
		render(ShiftChart, { props: { win, incidents: [inc('a', 'NJ-1038', { pendingApprovals: 1 }), inc('b', 'NJ-1035', { status: 'resolved' })], log: [], tz: 'Europe/Brussels' } });
		const line = screen.getByRole('link', { name: /^NJ-1038 .*now in contain, waiting for a person$/ });
		expect(line.getAttribute('href')).toBe('/incidents/NJ-1038');
		expect(screen.getByRole('link', { name: /^NJ-1035 .*finished$/ })).toBeTruthy();
	});
	it('summarises the lanes for screen readers and lists open incidents in the now column', () => {
		const { container } = render(ShiftChart, { props: { win, incidents: [inc('a', 'NJ-1038')], log: [], tz: 'Europe/Brussels' } });
		expect(screen.getByRole('figure').getAttribute('aria-label')).toBe('1 incidents on this shift: 0 open in triage, 0 open in investigate, 1 open in contain, 0 open in report');
		expect(container.querySelectorAll('.nowcol .chip2')).toHaveLength(1);
	});
	it('links somewhere else when asked (the landing sends people to sign in)', () => {
		render(ShiftChart, { props: { win, incidents: [inc('a', 'NJ-1038')], log: [], tz: 'Europe/Brussels', hrefFor: () => '/sign-in' } });
		for (const a of screen.getAllByRole('link', { name: /NJ-1038/ })) expect(a.getAttribute('href')).toBe('/sign-in');
	});
});
