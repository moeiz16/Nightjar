import { describe, expect, it } from 'vitest';
import {
	addUsBusinessDays, clockState, demoNight, dueBoth, dueLabel, elapsedFraction, hm, hms, isoWeek, isoWeekRange, isUsBusinessDay, nightAt,
	nightOfLabel, nis2, sec8k, secondsLeft, soci, usBusinessDaysLeft, zonedParts, zonedTimeToUtc, NY, BRUSSELS
} from '$lib/time/clock';
import type { Deadline } from '$lib/api/types';

const at = new Date('2025-11-20T08:18:23Z'); // Thu 20 Nov 2025 09:18:23 Brussels
const dl = (p: Partial<Deadline>): Deadline => ({ id: 'd', incidentId: 'i', incidentReference: 'NJ-1', regime: 'nis2', regimeLabel: 'NIS2', noticeType: 'x', noticeLabel: 'x', jurisdiction: 'Belgium', startsAt: null, dueAt: null, dueAtLocal: null, businessDays: null, state: 'on_track', remainingSeconds: null, noticeId: null, basis: '', ...p });

describe('countdown formatting', () => {
	it('hms pads and does not wrap hours', () => {
		expect(hms(17 * 3600 + 53 * 60 + 38)).toBe('17:53:38');
		expect(hms(72 * 3600)).toBe('72:00:00');
		expect(hms(-5)).toBe('00:00:00');
	});
	it('hm reads like the brief', () => {
		expect(hm(17 * 3600 + 53 * 60)).toBe('17 h 53 m');
		expect(hm(130 * 3600)).toBe('5 d 10 h');
	});
	it('secondsLeft counts down from dueAt', () => {
		expect(secondsLeft(new Date(+at + 90_000).toISOString(), at)).toBe(90);
	});
	it('elapsedFraction clamps to 0..1', () => {
		expect(elapsedFraction('2025-11-20T00:00:00Z', '2025-11-21T00:00:00Z', new Date('2025-11-20T06:00:00Z'))).toBeCloseTo(0.25);
		expect(elapsedFraction('2025-11-20T00:00:00Z', '2025-11-21T00:00:00Z', new Date('2025-11-22T00:00:00Z'))).toBe(1);
		expect(elapsedFraction(null, null)).toBe(0);
	});
});

describe('regime windows', () => {
	const aware = new Date('2025-11-19T03:52:00Z');
	it('NIS2: early warning 24 h, notification 72 h', () => {
		expect(nis2.earlyWarningDue(aware).toISOString()).toBe('2025-11-20T03:52:00.000Z');
		expect(nis2.notificationDue(aware).toISOString()).toBe('2025-11-22T03:52:00.000Z');
	});
	it('NIS2 final report: one month after the notification, clamped to month end', () => {
		expect(nis2.finalReportDue(new Date('2025-11-22T03:52:00Z')).toISOString()).toBe('2025-12-22T03:52:00.000Z');
		expect(nis2.finalReportDue(new Date('2026-01-31T10:00:00Z')).toISOString()).toBe('2026-02-28T10:00:00.000Z');
	});
	it('SOCI: 12 h significant, 72 h relevant, written record 84 h / 48 h', () => {
		expect(soci.reportDue(aware, 'significant').toISOString()).toBe('2025-11-19T15:52:00.000Z');
		expect(soci.reportDue(aware, 'relevant').toISOString()).toBe('2025-11-22T03:52:00.000Z');
		expect(soci.writtenRecordDue(aware, 'significant').toISOString()).toBe('2025-11-22T15:52:00.000Z');
		expect(soci.writtenRecordDue(aware, 'relevant').toISOString()).toBe('2025-11-21T03:52:00.000Z');
	});
	it('SEC 8-K: 17:30 New York on the 4th business day after the determination', () => {
		// Wed 19 Nov 16:00 ET → Thu, Fri, Mon, Tue 25 Nov 17:30 ET (22:30Z, EST)
		expect(sec8k.item105Due(new Date('2025-11-19T21:00:00Z')).toISOString()).toBe('2025-11-25T22:30:00.000Z');
		// Thu 20 Nov → Wed 26 Nov (the brief's "If material: 8-K due Wed 26 Nov")
		expect(sec8k.item105Due(at).toISOString()).toBe('2025-11-26T22:30:00.000Z');
	});
	it('SEC 8-K skips Thanksgiving (27 Nov 2025)', () => {
		// Mon 24 Nov → Tue 25, Wed 26, (Thu 27 holiday), Fri 28, Mon 1 Dec
		expect(sec8k.item105Due(new Date('2025-11-24T15:00:00Z')).toISOString()).toBe('2025-12-01T22:30:00.000Z');
		expect(isUsBusinessDay(2025, 11, 27)).toBe(false);
	});
	it('SEC 8-K uses the New York calendar date, not UTC', () => {
		// 03:00Z Thu = 22:00 Wed in New York → counts from Wednesday
		expect(sec8k.item105Due(new Date('2025-11-20T03:00:00Z')).toISOString()).toBe('2025-11-25T22:30:00.000Z');
	});
	it('business-day arithmetic skips weekends', () => {
		expect(addUsBusinessDays(2025, 11, 21, 1)).toEqual({ y: 2025, m: 11, d: 24 });
		expect(usBusinessDaysLeft(at, new Date('2025-11-25T22:30:00Z'))).toBe(3);
	});
});

describe('clock state', () => {
	it('on track, due soon (≤ max(25 %, 6 h)), overdue', () => {
		const startsAt = '2025-11-19T03:52:00Z';
		expect(clockState(dl({ startsAt, dueAt: '2025-11-22T03:52:00Z' }), at)).toBe('on_track');
		expect(clockState(dl({ startsAt, dueAt: new Date(+at + 5 * 3600e3).toISOString() }), at)).toBe('due_soon');
		expect(clockState(dl({ startsAt, dueAt: new Date(+at - 1000).toISOString() }), at)).toBe('overdue');
	});
	it('keeps not_started and submitted as given', () => {
		expect(clockState(dl({ state: 'not_started' }), at)).toBe('not_started');
		expect(clockState(dl({ state: 'submitted', dueAt: '2025-11-19T00:00:00Z' }), at)).toBe('submitted');
	});
	it('SEC is due soon with one business day left', () => {
		expect(clockState(dl({ regime: 'sec_8k', startsAt: '2025-11-19T21:00:00Z', dueAt: '2025-11-21T22:30:00Z' }), at)).toBe('due_soon');
		expect(clockState(dl({ regime: 'sec_8k', startsAt: '2025-11-19T21:00:00Z', dueAt: '2025-11-25T22:30:00Z' }), at)).toBe('on_track');
	});
});

describe('dates computed from today', () => {
	it('dueLabel says today / tomorrow / weekday', () => {
		expect(dueLabel('2025-11-20T14:00:00Z', at, BRUSSELS)).toBe('today 15:00');
		expect(dueLabel('2025-11-21T02:12:00Z', at, BRUSSELS)).toBe('tomorrow 03:12');
		expect(dueLabel('2025-11-22T03:52:00Z', at, BRUSSELS)).toBe('Sat 22 Nov 04:52');
	});
	it('dueBoth adds the regime wall clock when it differs', () => {
		expect(dueBoth('2025-11-25T22:30:00Z', at, NY, BRUSSELS)).toBe('Tue 25 Nov 23:30 · 17:30 New York');
		expect(dueBoth('2025-11-22T03:52:00Z', at, BRUSSELS, BRUSSELS)).toBe('Sat 22 Nov 04:52');
	});
	it('the demo night is the most recent Brussels midnight at least 9 h ago', () => {
		expect(demoNight(at).toISOString()).toBe('2025-11-19T23:00:00.000Z');
		expect(demoNight(new Date('2025-11-20T07:00:00Z')).toISOString()).toBe('2025-11-18T23:00:00.000Z');
		expect(nightAt(3, 12, 4, 0, at).toISOString()).toBe('2025-11-20T02:12:04.000Z');
	});
	it('names the night of Wednesday to Thursday', () => {
		expect(nightOfLabel(at)).toBe('Wednesday to Thursday');
	});
	it('ISO weeks', () => {
		expect(isoWeek(at)).toBe('2025-W47');
		expect(isoWeekRange('2025-W47')!.from.toISOString().slice(0, 10)).toBe('2025-11-17');
		expect(isoWeekRange('bad')).toBeNull();
	});
	it('zoned conversions round-trip across DST zones', () => {
		const t = zonedTimeToUtc(2025, 11, 25, 17, 30, NY);
		expect(t.toISOString()).toBe('2025-11-25T22:30:00.000Z');
		expect(zonedParts(t, NY)).toMatchObject({ hour: 17, minute: 30, day: 25, weekday: 2 });
		expect(zonedTimeToUtc(2025, 7, 1, 17, 30, NY).toISOString()).toBe('2025-07-01T21:30:00.000Z');
	});
});
