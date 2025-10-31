// The one clock module (DESIGN.md §16). Regime windows, business days with the US federal holiday calendar,
// clock states and every countdown / due-date string. The UI never computes deadlines inline.
// "Now" comes from the browser clock corrected by the server offset (contract §1.17: countdowns tick in the
// browser from dueAt and the server clock offset).

import type { Deadline, Regime } from '$lib/api/types';

const HOUR = 3600;
const DAY = 24 * HOUR;

// ---------- now ----------
let serverOffsetMs = 0;
/** Record the server clock offset from a response Date header (or Me.clock). */
export function setServerOffset(ms: number): void {
	serverOffsetMs = Number.isFinite(ms) ? ms : 0;
}
export function serverOffset(): number {
	return serverOffsetMs;
}
export function now(): Date {
	return new Date(Date.now() + serverOffsetMs);
}
/** Offset from an HTTP Date header, ignoring sub-second skew (Date headers have 1 s resolution). */
export function offsetFromDateHeader(header: string | null, localMs = Date.now()): number {
	if (!header) return 0;
	const t = Date.parse(header);
	if (Number.isNaN(t)) return 0;
	const diff = t - localMs;
	return Math.abs(diff) < 2000 ? 0 : diff;
}

// ---------- time zones (Intl only, no library) ----------
export interface ZonedParts {
	year: number;
	month: number; // 1-12
	day: number;
	hour: number;
	minute: number;
	second: number;
	weekday: number; // 0 = Sunday
}
const partFormatters = new Map<string, Intl.DateTimeFormat>();
function partFormatter(tz: string): Intl.DateTimeFormat {
	let f = partFormatters.get(tz);
	if (!f) {
		f = new Intl.DateTimeFormat('en-US', {
			timeZone: tz,
			hourCycle: 'h23',
			year: 'numeric',
			month: 'numeric',
			day: 'numeric',
			hour: 'numeric',
			minute: 'numeric',
			second: 'numeric',
			weekday: 'short'
		});
		partFormatters.set(tz, f);
	}
	return f;
}
const WD: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
export function zonedParts(date: Date, tz: string): ZonedParts {
	const p: Record<string, string> = {};
	for (const part of partFormatter(tz).formatToParts(date)) p[part.type] = part.value;
	return {
		year: Number(p.year),
		month: Number(p.month),
		day: Number(p.day),
		hour: Number(p.hour) % 24,
		minute: Number(p.minute),
		second: Number(p.second),
		weekday: WD[p.weekday ?? 'Sun'] ?? 0
	};
}
/** The UTC instant at which the wall clock in `tz` reads the given local time. */
export function zonedTimeToUtc(y: number, m: number, d: number, hh: number, mm: number, tz: string, ss = 0): Date {
	const target = Date.UTC(y, m - 1, d, hh, mm, ss);
	let guess = target;
	for (let i = 0; i < 3; i++) {
		const p = zonedParts(new Date(guess), tz);
		const seen = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
		const delta = target - seen;
		if (delta === 0) break;
		guess += delta;
	}
	return new Date(guess);
}

// ---------- US federal holidays (observed), 2025-2026 ----------
export const US_FEDERAL_HOLIDAYS = new Set([
	'2025-01-01', '2025-01-20', '2025-02-17', '2025-05-26', '2025-06-19', '2025-07-04', '2025-09-01',
	'2025-10-13', '2025-11-11', '2025-11-27', '2025-12-25',
	'2026-01-01', '2026-01-19', '2026-02-16', '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07',
	'2026-10-12', '2026-11-11', '2026-11-26', '2026-12-25'
]);
const iso = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
export function isUsBusinessDay(y: number, m: number, d: number): boolean {
	const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
	return wd !== 0 && wd !== 6 && !US_FEDERAL_HOLIDAYS.has(iso(y, m, d));
}
/** Calendar date `n` US business days after the given New York calendar date. */
export function addUsBusinessDays(y: number, m: number, d: number, n: number): { y: number; m: number; d: number } {
	const cur = new Date(Date.UTC(y, m - 1, d));
	let left = n;
	while (left > 0) {
		cur.setUTCDate(cur.getUTCDate() + 1);
		if (isUsBusinessDay(cur.getUTCFullYear(), cur.getUTCMonth() + 1, cur.getUTCDate())) left--;
	}
	return { y: cur.getUTCFullYear(), m: cur.getUTCMonth() + 1, d: cur.getUTCDate() };
}
/** Whole US business days from `from` (exclusive of its date) up to and including the due date. */
export function usBusinessDaysLeft(from: Date, due: Date): number {
	const a = zonedParts(from, NY);
	const b = zonedParts(due, NY);
	const cur = new Date(Date.UTC(a.year, a.month - 1, a.day));
	const end = Date.UTC(b.year, b.month - 1, b.day);
	let n = 0;
	while (cur.getTime() < end) {
		cur.setUTCDate(cur.getUTCDate() + 1);
		if (isUsBusinessDay(cur.getUTCFullYear(), cur.getUTCMonth() + 1, cur.getUTCDate())) n++;
	}
	return n;
}

// ---------- regime windows ----------
export const NY = 'America/New_York';
export const BRUSSELS = 'Europe/Brussels';
export const MELBOURNE = 'Australia/Melbourne';
export const REGIME_TZ: Record<Regime, string> = {
	nis2: BRUSSELS,
	gdpr_art33: BRUSSELS,
	sec_8k: NY,
	soci: MELBOURNE
};

export const nis2 = {
	earlyWarningDue: (awareAt: Date) => new Date(+awareAt + 24 * HOUR * 1000),
	notificationDue: (awareAt: Date) => new Date(+awareAt + 72 * HOUR * 1000),
	/** final report: one calendar month after the incident notification was submitted */
	finalReportDue: (notificationSubmittedAt: Date) => {
		const d = new Date(notificationSubmittedAt);
		const day = d.getUTCDate();
		d.setUTCDate(1);
		d.setUTCMonth(d.getUTCMonth() + 1);
		const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
		d.setUTCDate(Math.min(day, last));
		return d;
	}
};
export const soci = {
	reportDue: (awareAt: Date, impact: 'significant' | 'relevant') =>
		new Date(+awareAt + (impact === 'significant' ? 12 : 72) * HOUR * 1000),
	writtenRecordDue: (oralAt: Date, impact: 'significant' | 'relevant') =>
		new Date(+oralAt + (impact === 'significant' ? 84 : 48) * HOUR * 1000)
};
export const sec8k = {
	/** Form 8-K Item 1.05: 17:30 New York time on the 4th business day after the materiality determination. */
	item105Due: (determinedAt: Date): Date => {
		const p = zonedParts(determinedAt, NY);
		const due = addUsBusinessDays(p.year, p.month, p.day, 4);
		return zonedTimeToUtc(due.y, due.m, due.d, 17, 30, NY);
	}
};
export const gdpr = { notificationDue: (awareAt: Date) => new Date(+awareAt + 72 * HOUR * 1000) };

// ---------- clock state ----------
export type ClockState = Deadline['state'];
/**
 * State from the time left. due_soon when remaining ≤ max(25 % of the window, 6 h); for the 8-K when ≤ 1 business day.
 */
export function clockState(d: Pick<Deadline, 'state' | 'startsAt' | 'dueAt' | 'regime'>, at: Date = now()): ClockState {
	if (d.state === 'submitted' || d.state === 'not_required' || d.state === 'not_started') return d.state;
	if (!d.dueAt) return 'not_started';
	const due = new Date(d.dueAt);
	const left = (+due - +at) / 1000;
	if (left < 0) return 'overdue';
	if (d.regime === 'sec_8k') return usBusinessDaysLeft(at, due) <= 1 ? 'due_soon' : 'on_track';
	const windowS = d.startsAt ? (+due - +new Date(d.startsAt)) / 1000 : 72 * HOUR;
	return left <= Math.max(0.25 * windowS, 6 * HOUR) ? 'due_soon' : 'on_track';
}
export const STATE_GLYPH: Record<ClockState, string> = {
	on_track: '○',
	due_soon: '◐',
	overdue: '●',
	not_started: '◆',
	submitted: '✓',
	not_required: '–'
};
export const STATE_WORD: Record<ClockState, string> = {
	on_track: 'On track',
	due_soon: 'Due soon',
	overdue: 'Overdue',
	not_started: 'Materiality decision',
	submitted: 'Sent',
	not_required: 'Not required'
};
export function secondsLeft(dueAt: string | Date | null, at: Date = now()): number {
	if (!dueAt) return 0;
	return Math.floor((+new Date(dueAt) - +at) / 1000);
}
/** Fraction of the window already used, 0..1. */
export function elapsedFraction(startsAt: string | null, dueAt: string | null, at: Date = now()): number {
	if (!startsAt || !dueAt) return 0;
	const total = +new Date(dueAt) - +new Date(startsAt);
	if (total <= 0) return 1;
	return Math.min(1, Math.max(0, (+at - +new Date(startsAt)) / total));
}

// ---------- formatting ----------
const pad = (n: number) => String(n).padStart(2, '0');
/** 17:53:38; negative values clamp to 00:00:00. Hours are not wrapped (72:00:00). */
export function hms(sec: number): string {
	const s = Math.max(0, Math.floor(sec));
	return `${pad(Math.floor(s / HOUR))}:${pad(Math.floor((s % HOUR) / 60))}:${pad(s % 60)}`;
}
/** 17 h 53 m; days are spelled out past 99 h. */
export function hm(sec: number): string {
	const s = Math.max(0, Math.floor(sec));
	const h = Math.floor(s / HOUR);
	const m = Math.floor((s % HOUR) / 60);
	if (h >= 100) return `${Math.floor(h / 24)} d ${h % 24} h`;
	return `${h} h ${pad(m)} m`;
}
export function hmParts(sec: number): { h: number; m: string } {
	const s = Math.max(0, Math.floor(sec));
	return { h: Math.floor(s / HOUR), m: pad(Math.floor((s % HOUR) / 60)) };
}
export function viewerTz(): string {
	try {
		return Intl.DateTimeFormat().resolvedOptions().timeZone || BRUSSELS;
	} catch {
		return BRUSSELS;
	}
}
export function timeOf(d: string | Date, tz = viewerTz(), seconds = false): string {
	return new Date(d).toLocaleTimeString('en-GB', {
		timeZone: tz,
		hour: '2-digit',
		minute: '2-digit',
		...(seconds ? { second: '2-digit' } : {})
	});
}
/** "Sat 22 Nov" (built from parts so every engine renders it the same way). */
export function shortDay(d: string | Date, tz = viewerTz()): string {
	const p: Record<string, string> = {};
	for (const x of new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short', day: 'numeric', month: 'short' }).formatToParts(new Date(d))) p[x.type] = x.value;
	return `${p.weekday} ${p.day} ${p.month}`;
}
export function longDate(d: Date, tz = viewerTz()): string {
	return d.toLocaleDateString('en-GB', { timeZone: tz, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}
export function weekdayName(d: Date, tz = viewerTz(), style: 'long' | 'short' = 'long'): string {
	return d.toLocaleDateString('en-GB', { timeZone: tz, weekday: style });
}
function sameZonedDay(a: Date, b: Date, tz: string): boolean {
	const x = zonedParts(a, tz);
	const y = zonedParts(b, tz);
	return x.year === y.year && x.month === y.month && x.day === y.day;
}
/** "today 03:12", "tomorrow 03:12", "yesterday 15:20" or "Sun 23 Nov 03:12", in the given zone. */
export function dueLabel(d: string | Date, at: Date = now(), tz = viewerTz()): string {
	const due = new Date(d);
	const t = timeOf(due, tz);
	if (sameZonedDay(due, at, tz)) return `today ${t}`;
	if (sameZonedDay(due, new Date(+at + DAY * 1000), tz)) return `tomorrow ${t}`;
	if (sameZonedDay(due, new Date(+at - DAY * 1000), tz)) return `yesterday ${t}`;
	return `${shortDay(due, tz)} ${t}`;
}
const CITY: Record<string, string> = { 'America/New_York': 'New York', 'Australia/Melbourne': 'Melbourne', 'Europe/Brussels': 'Brussels' };
/** Due label in the viewer's zone, plus the regime's own wall-clock time when it differs: "Sat 22 Nov 04:52 · 14:52 Melbourne". */
export function dueBoth(d: string | Date, at: Date = now(), regimeTz: string, tz = viewerTz()): string {
	const mine = dueLabel(d, at, tz);
	if (regimeTz === tz || timeOf(d, regimeTz) === timeOf(d, tz)) return mine;
	return `${mine} · ${timeOf(d, regimeTz)} ${CITY[regimeTz] ?? regimeTz}`;
}
/** Short relative time for lists: "09:18" today, "Tue 14:02" this week, otherwise "12 Nov". */
export function whenShort(d: string | Date, at: Date = now(), tz = viewerTz()): string {
	const x = new Date(d);
	if (sameZonedDay(x, at, tz)) return timeOf(x, tz);
	const age = (+at - +x) / 1000;
	if (age < 6 * DAY && age > 0) return `${weekdayName(x, tz, 'short')} ${timeOf(x, tz)}`;
	return x.toLocaleDateString('en-GB', { timeZone: tz, day: 'numeric', month: 'short' });
}

// ---------- the demo night ----------
/** Most recent 00:00 Europe/Brussels such that now ≥ night + 09:00 (contract Part 6). */
export function demoNight(at: Date = now()): Date {
	const p = zonedParts(new Date(+at - 9 * HOUR * 1000), BRUSSELS);
	return zonedTimeToUtc(p.year, p.month, p.day, 0, 0, BRUSSELS);
}
/** Brussels wall-clock time on the night, offset by whole days: nightAt(3, 12, 4) = 03:12:04 last night. */
export function nightAt(h: number, m: number, s = 0, plusDays = 0, at: Date = now()): Date {
	const night = demoNight(at);
	const p = zonedParts(new Date(+night + plusDays * DAY * 1000 + 12 * HOUR * 1000), BRUSSELS);
	return zonedTimeToUtc(p.year, p.month, p.day, h, m, BRUSSELS, s);
}
/** "Wednesday to Thursday" for the night that ended this morning. */
export function nightOfLabel(at: Date = now(), tz = BRUSSELS): string {
	const night = demoNight(at);
	return `${weekdayName(new Date(+night - HOUR * 1000), tz)} to ${weekdayName(new Date(+night + HOUR * 1000), tz)}`;
}
/** ISO week id, e.g. 2025-W47, for the given instant. */
export function isoWeek(d: Date): string {
	const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
	const dayNum = t.getUTCDay() || 7;
	t.setUTCDate(t.getUTCDate() + 4 - dayNum);
	const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
	const week = Math.ceil(((+t - +yearStart) / 86400000 + 1) / 7);
	return `${t.getUTCFullYear()}-W${pad(week)}`;
}
/** Monday..Sunday calendar dates of an ISO week id. */
export function isoWeekRange(id: string): { from: Date; to: Date } | null {
	const m = /^(\d{4})-W(\d{2})$/.exec(id);
	if (!m) return null;
	const y = Number(m[1]);
	const w = Number(m[2]);
	const jan4 = new Date(Date.UTC(y, 0, 4));
	const mon = new Date(+jan4 - ((jan4.getUTCDay() || 7) - 1) * DAY * 1000);
	const from = new Date(+mon + (w - 1) * 7 * DAY * 1000);
	return { from, to: new Date(+from + 6 * DAY * 1000) };
}
