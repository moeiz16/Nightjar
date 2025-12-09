// The shift's layout logic: the window, the scale, each incident's path through the response stages, the intake bars,
// and the watch dial's polar form of the same layout.
import { describe, expect, it } from 'vitest';
import type { IncidentSummary, ShiftLogEntry } from '$lib/api/types';
import { dialAngle, dialAngleOn, dialPath, incidentPaths, intakeBars, laneOfEntry, LANES, polar, sectorPath, shiftWindow, stepPath, watchOf, xOf } from '$lib/util/shift';
import { zonedParts } from '$lib/time/clock';

const TZ = 'Europe/Brussels';
// Thursday 20 November 2025, 10:24 in Brussels (UTC+1)
const NOW = new Date('2025-11-20T09:24:00Z');
const local = (d: Date) => zonedParts(d, TZ);

function inc(over: Partial<IncidentSummary> = {}): IncidentSummary {
	return {
		id: 'i1',
		reference: 'NJ-1038',
		title: 'Cloud access key used from an unfamiliar network',
		kind: 'cloud_credential_exposure',
		severity: 'high',
		status: 'containing',
		stage: 'contain',
		assets: [],
		identities: [],
		attackTactics: [],
		detectionCount: 6,
		firstSeen: '2025-11-20T00:40:00Z', // 01:40 Brussels
		lastSeen: '2025-11-20T01:05:00Z',
		assignee: null,
		verdict: 'true_positive',
		likelyBenign: false,
		reportable: 'assess',
		nextDeadline: null,
		pendingApprovals: 1,
		...over
	};
}
const entry = (at: string, kind: ShiftLogEntry['kind'], refId = 'i1'): ShiftLogEntry => ({ at, kind, refId, text: `${kind} at ${at}`, severity: null });

describe('shiftWindow', () => {
	const w = shiftWindow(NOW, TZ);
	it('runs from the 18:00 before the last night to now', () => {
		const s = local(w.start);
		expect([s.day, s.hour, s.minute]).toEqual([19, 18, 0]);
		expect(+w.end).toBe(+NOW);
	});
	it('shades the night from 20:00 to 07:00 local, clipped to the window', () => {
		expect(w.nights).toHaveLength(1);
		expect(local(w.nights[0]!.from).hour).toBe(20);
		expect(local(w.nights[0]!.to).hour).toBe(7);
	});
	it('rules both shift changes inside the window, in order', () => {
		expect(w.handovers.map((h) => local(h).hour)).toEqual([20, 8]);
	});
	it('ticks every two hours with the local hour as the label', () => {
		expect(w.ticks.map((t) => t.label)).toEqual(['18', '20', '22', '00', '02', '04', '06', '08', '10']);
		expect(w.ticks.filter((t) => t.major).map((t) => t.label)).toEqual(['18', '00', '06']);
	});
	it('looks back at least twelve hours early in the evening', () => {
		const evening = shiftWindow(new Date('2025-11-20T19:00:00Z'), TZ); // 20:00 Brussels
		expect((+evening.end - +evening.start) / 3600e3).toBeGreaterThanOrEqual(12);
	});
});

describe('xOf', () => {
	const w = { start: new Date('2025-11-19T17:00:00Z'), end: new Date('2025-11-20T09:00:00Z') };
	it('maps the window to 0…1 and clamps outside it', () => {
		expect(xOf(w.start, w)).toBe(0);
		expect(xOf(w.end, w)).toBe(1);
		expect(xOf('2025-11-20T01:00:00Z', w)).toBeCloseTo(0.5);
		expect(xOf('2025-11-18T00:00:00Z', w)).toBe(0);
		expect(xOf('2025-11-21T00:00:00Z', w)).toBe(1);
	});
	it('puts everything at the right edge of an empty window', () => {
		expect(xOf(w.start, { start: w.end, end: w.end })).toBe(1);
	});
});

describe('laneOfEntry', () => {
	it('moves approvals and actions to Contain', () => {
		expect(laneOfEntry({ kind: 'approval' }, false)).toBe(3);
		expect(laneOfEntry({ kind: 'action' }, false)).toBe(3);
	});
	it('never moves a line for a clock or a notice (they can start before containment)', () => {
		expect(laneOfEntry({ kind: 'clock' }, false)).toBeNull();
		expect(laneOfEntry({ kind: 'notice' }, false)).toBeNull();
	});
	it('reads a teammate or status note as investigation', () => {
		expect(laneOfEntry({ kind: 'incident' }, false)).toBe(2);
		expect(laneOfEntry({ kind: 'incident' }, true)).toBe(1);
	});
});

describe('incidentPaths', () => {
	const w = shiftWindow(NOW, TZ);
	it('steps an incident down the lanes at the times its log records', () => {
		const log = [entry('2025-11-20T00:42:00Z', 'incident'), entry('2025-11-20T00:50:00Z', 'clock'), entry('2025-11-20T01:04:00Z', 'approval')];
		const [p] = incidentPaths([inc()], log, w);
		expect(p!.steps.map((s) => LANES[s.lane])).toEqual(['triage', 'investigate', 'contain']);
		expect(p!.notices.map((d) => d.toISOString())).toEqual(['2025-11-20T00:50:00.000Z']);
		expect(+p!.until).toBe(+w.end); // still open: runs to now
		expect(p!.waiting).toBe(true);
	});
	it('never steps back up', () => {
		const log = [entry('2025-11-20T01:00:00Z', 'approval'), entry('2025-11-20T01:10:00Z', 'incident')];
		const [p] = incidentPaths([inc()], log, w);
		expect(p!.steps.map((s) => s.lane)).toEqual([1, 3]);
	});
	it('lets the recorded stage finish the line when the log is short', () => {
		const [p] = incidentPaths([inc({ stage: 'report', status: 'monitoring' })], [], w);
		expect(p!.lane).toBe(4);
	});
	it('ends a finished incident where it finished, and drops ones over before the window', () => {
		const done = inc({ id: 'i2', reference: 'NJ-1035', status: 'resolved', stage: 'contain', firstSeen: '2025-11-20T00:10:00Z', lastSeen: '2025-11-20T00:30:00Z' });
		const old = inc({ id: 'i3', reference: 'NJ-1001', status: 'closed', firstSeen: '2025-11-17T00:10:00Z', lastSeen: '2025-11-17T02:00:00Z' });
		const paths = incidentPaths([done, old], [entry('2025-11-20T00:20:00Z', 'action', 'i2')], w);
		expect(paths.map((p) => p.reference)).toEqual(['NJ-1035']);
		expect(paths[0]!.open).toBe(false);
		expect(paths[0]!.until.toISOString()).toBe('2025-11-20T00:30:00.000Z');
	});
	it('gives lines their own track in opening order', () => {
		const a = inc({ id: 'a', reference: 'NJ-2', firstSeen: '2025-11-20T02:00:00Z' });
		const b = inc({ id: 'b', reference: 'NJ-1', firstSeen: '2025-11-20T01:00:00Z' });
		const paths = incidentPaths([a, b], [], w);
		expect(paths.map((p) => [p.reference, p.track])).toEqual([
			['NJ-1', 0],
			['NJ-2', 1]
		]);
	});
	it('marks a line that began before the window as clipped', () => {
		const [p] = incidentPaths([inc({ firstSeen: '2025-11-19T10:00:00Z' })], [], w);
		expect(p!.clipped).toBe(true);
	});
});

describe('stepPath', () => {
	it('draws horizontal runs and vertical drops, ending at the line end', () => {
		const w = { start: new Date('2025-11-20T00:00:00Z'), end: new Date('2025-11-20T04:00:00Z') };
		const [p] = incidentPaths([inc({ firstSeen: '2025-11-20T01:00:00Z', status: 'containing' })], [entry('2025-11-20T02:00:00Z', 'approval')], w);
		const d = stepPath(p!, (t) => ((+t - +w.start) / 3600e3) * 100, (lane) => lane * 10);
		expect(d).toBe('M100 10 H200 V30 H400');
	});
});

describe('intakeBars', () => {
	const w = { start: new Date('2025-11-20T00:00:00Z'), end: new Date('2025-11-20T03:00:00Z') };
	it('scales the loudest hour to full height and places bars on the window', () => {
		const bars = intakeBars(
			[
				{ at: new Date('2025-11-20T00:00:00Z'), count: 50 },
				{ at: new Date('2025-11-20T01:00:00Z'), count: 100 },
				{ at: new Date('2025-11-20T09:00:00Z'), count: 999 }
			],
			w
		);
		expect(bars.map((b) => b.h)).toEqual([0.5, 1]);
		expect(bars[1]!.x).toBeCloseTo(1 / 3);
	});
	it('copes with an empty intake', () => {
		expect(intakeBars([], w)).toEqual([]);
	});
});

describe('the watch dial', () => {
	it('names the watch a moment falls in, by local hour', () => {
		expect(watchOf(NOW, TZ).key).toBe('forenoon'); // 10:24
		expect(watchOf(new Date('2025-11-20T02:12:00Z'), TZ).key).toBe('middle'); // 03:12
		expect(watchOf(new Date('2025-11-20T05:30:00Z'), TZ).key).toBe('morning'); // 06:30
		expect(watchOf(new Date('2025-11-19T21:30:00Z'), TZ).key).toBe('first'); // 22:30
		expect(watchOf(new Date('2025-11-19T23:00:00Z'), TZ).key).toBe('middle'); // 00:00
		expect(watchOf(new Date('2025-11-20T17:00:00Z'), TZ).key).toBe('dog'); // 18:00
	});
	it('puts midnight at the top and runs clockwise: six at the right, noon at the bottom, 18:00 at the left', () => {
		expect(dialAngle('2025-11-19T23:00:00Z', TZ)).toBe(0);
		expect(dialAngle('2025-11-20T05:00:00Z', TZ)).toBe(90);
		expect(dialAngle('2025-11-20T11:00:00Z', TZ)).toBe(180);
		expect(dialAngle('2025-11-20T17:00:00Z', TZ)).toBe(270);
	});
	it('unwraps across midnight so the hand and the lines never run backwards', () => {
		const w = shiftWindow(NOW, TZ); // from 18:00 yesterday
		const evening = dialAngleOn('2025-11-19T20:00:00Z', w, TZ); // 21:00
		const night = dialAngleOn('2025-11-20T02:12:00Z', w, TZ); // 03:12
		const now = dialAngleOn(NOW, w, TZ);
		expect(evening).toBeCloseTo(315);
		expect(night).toBeCloseTo(360 + 48);
		expect(now).toBeGreaterThan(night);
		expect(now).toBeCloseTo(360 + 156);
	});
	it('clamps anything before the window to its start', () => {
		const w = shiftWindow(NOW, TZ);
		expect(dialAngleOn('2025-11-18T12:00:00Z', w, TZ)).toBe(dialAngle(w.start, TZ));
	});
	it('places points by angle and radius around the centre', () => {
		const [x, y] = polar(100, 100, 50, 90);
		expect(x).toBeCloseTo(150);
		expect(y).toBeCloseTo(100);
		const [x2, y2] = polar(100, 100, 50, 0);
		expect([Math.round(x2), Math.round(y2)]).toEqual([100, 50]);
	});
	it('draws an incident as arcs along its stage rings with steps inward between them', () => {
		const w = shiftWindow(NOW, TZ);
		const [p] = incidentPaths([inc()], [entry('2025-11-20T00:52:00Z', 'incident'), entry('2025-11-20T01:04:00Z', 'approval')], w);
		const radius = (lane: number) => 200 - lane * 30;
		const d = dialPath(p!, (t) => dialAngleOn(t, w, TZ), radius, 250, 250);
		expect(d.startsWith('M')).toBe(true);
		expect((d.match(/A/g) ?? []).length).toBeGreaterThanOrEqual(2);
		expect((d.match(/L/g) ?? []).length).toBe(p!.steps.length - 1);
	});
	it('closes a ring segment for intake bars and the lit shift', () => {
		const d = sectorPath(100, 100, 40, 60, 10, 40);
		expect(d.startsWith('M')).toBe(true);
		expect(d.endsWith('Z')).toBe(true);
		expect(sectorPath(100, 100, 40, 60, 0, 360)).toContain('Z M');
	});
});
