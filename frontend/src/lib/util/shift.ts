// The shift (DESIGN.md §8): the window of time the watch dial draws, and each incident's path through the response
// stages. An incident steps from Triage → Investigate → Contain → Report at the times its log records, like a train
// on a dispatcher's timetable graph; the dial draws the same path in polar form (time round the face, stages as
// rings). Pure functions, so the layout is testable.
import type { IncidentSummary, ShiftLogEntry, Stage } from '$lib/api/types';
import { zonedParts, zonedTimeToUtc } from '$lib/time/clock';

const H = 3600_000;

export const LANES = ['intake', 'triage', 'investigate', 'contain', 'report'] as const;
export type Lane = (typeof LANES)[number];
export const LANE_LABEL: Record<Lane, string> = {
	intake: 'intake',
	triage: 'triage',
	investigate: 'investigate',
	contain: 'contain',
	report: 'report'
};
const STAGE_LANE: Record<Stage, number> = { triage: 1, investigate: 2, contain: 3, report: 4 };

export interface ShiftWindow {
	start: Date;
	end: Date;
	/** night hours (20:00–07:00 local) inside the window */
	nights: { from: Date; to: Date }[];
	/** shift changes (08:00 and 20:00 local) inside the window */
	handovers: Date[];
	/** hour ticks every two hours, labelled "22", "00" … in the org's time */
	ticks: { at: Date; label: string; major: boolean }[];
}

/** Local wall-clock hour h on the calendar day of `d` in tz, plus `days`. */
function at(d: Date, tz: string, h: number, days = 0): Date {
	const p = zonedParts(d, tz);
	const base = zonedTimeToUtc(p.year, p.month, p.day, 12, 0, tz);
	const shifted = zonedParts(new Date(+base + days * 24 * H), tz);
	return zonedTimeToUtc(shifted.year, shifted.month, shifted.day, h, 0, tz);
}

/** The axis runs from the 18:00 before the last night (or 12 hours back, whichever is earlier) to now. */
export function shiftWindow(now: Date, tz: string, minHours = 12): ShiftWindow {
	const today18 = at(now, tz, 18);
	const last18 = +today18 <= +now ? today18 : at(now, tz, 18, -1);
	const back = new Date(+now - minHours * H);
	const raw = +last18 < +back ? last18 : back;
	const start = new Date(Math.floor(+raw / H) * H);
	const end = now;
	const nights: ShiftWindow['nights'] = [];
	const handovers: Date[] = [];
	for (let day = -2; day <= 1; day++) {
		const from = at(now, tz, 20, day);
		const to = at(now, tz, 7, day + 1);
		if (+to > +start && +from < +end) nights.push({ from: new Date(Math.max(+from, +start)), to: new Date(Math.min(+to, +end)) });
		for (const h of [8, 20]) {
			const t = at(now, tz, h, day);
			if (+t > +start && +t < +end) handovers.push(t);
		}
	}
	handovers.sort((a, b) => +a - +b);
	const ticks: ShiftWindow['ticks'] = [];
	for (let t = +start; t <= +end; t += H) {
		const p = zonedParts(new Date(t), tz);
		if (p.minute !== 0 || p.hour % 2 !== 0) continue;
		ticks.push({ at: new Date(t), label: String(p.hour).padStart(2, '0'), major: p.hour % 6 === 0 });
	}
	return { start, end, nights, handovers, ticks };
}

/** 0…1 position of a time on the window (clamped). */
export function xOf(t: Date | string, w: Pick<ShiftWindow, 'start' | 'end'>): number {
	const span = +w.end - +w.start;
	if (span <= 0) return 1;
	const v = (+new Date(t) - +w.start) / span;
	return Math.max(0, Math.min(1, v));
}

/** Where a shift-log entry puts an incident, or null when it does not move it. A regulator clock or notice can
 *  start while containment is still waiting, so it marks the line (✉) without moving it; Report is reached from
 *  the incident's own stage. Lines only ever step down. */
export function laneOfEntry(e: Pick<ShiftLogEntry, 'kind'>, isFirst: boolean): number | null {
	if (e.kind === 'approval' || e.kind === 'action') return 3;
	if (e.kind === 'clock' || e.kind === 'notice') return null;
	return isFirst ? 1 : 2;
}

export interface Step {
	at: Date;
	lane: number;
}
export interface IncidentPath {
	id: string;
	reference: string;
	title: string;
	severity: IncidentSummary['severity'];
	open: boolean;
	waiting: boolean;
	steps: Step[];
	/** the line runs to here: now for open incidents, else the last recorded step */
	until: Date;
	/** regulator clocks and notices on this incident's line */
	notices: Date[];
	/** the line began before the window (drawn from the left edge with a ‹) */
	clipped: boolean;
	lane: number;
	/** vertical slot inside a lane, so lines in the same lane do not overprint */
	track: number;
}

const OPEN = new Set(['new', 'triage', 'investigating', 'containing', 'monitoring']);

export function incidentPaths(incidents: IncidentSummary[], log: ShiftLogEntry[], w: Pick<ShiftWindow, 'start' | 'end'>): IncidentPath[] {
	const byIncident = new Map<string, ShiftLogEntry[]>();
	for (const e of log) {
		if (!e.refId) continue;
		const list = byIncident.get(e.refId) ?? [];
		list.push(e);
		byIncident.set(e.refId, list);
	}
	const paths: IncidentPath[] = [];
	for (const inc of incidents) {
		const open = OPEN.has(inc.status);
		const entries = (byIncident.get(inc.id) ?? []).slice().sort((a, b) => a.at.localeCompare(b.at));
		const first = new Date(inc.firstSeen);
		const steps: Step[] = [{ at: first, lane: 1 }];
		let lane = 1;
		const notices: Date[] = [];
		entries.forEach((e) => {
			const t = new Date(e.at);
			if (+t < +first) return;
			const moved = laneOfEntry(e, false);
			if (moved === null) {
				notices.push(t);
				return;
			}
			const next = Math.max(lane, moved);
			if (next !== lane) {
				steps.push({ at: t, lane: next });
				lane = next;
			}
		});
		// the incident's recorded stage wins at the end (the log is capped; the stage is authoritative)
		const stageLane = STAGE_LANE[inc.stage] ?? 1;
		const last = entries.at(-1);
		const lastAt = last ? new Date(last.at) : new Date(inc.lastSeen);
		if (stageLane > lane) {
			steps.push({ at: new Date(Math.max(+lastAt, +steps.at(-1)!.at)), lane: stageLane });
			lane = stageLane;
		}
		const until = open ? w.end : new Date(Math.max(+lastAt, +new Date(inc.lastSeen), +steps.at(-1)!.at));
		if (+until < +w.start) continue; // over before the window opened
		if (+first > +w.end) continue;
		paths.push({
			id: inc.id,
			reference: inc.reference,
			title: inc.title,
			severity: inc.severity,
			open,
			waiting: inc.pendingApprovals > 0,
			steps,
			until,
			notices,
			clipped: +first < +w.start,
			lane,
			track: 0
		});
	}
	paths.sort((a, b) => +a.steps[0]!.at - +b.steps[0]!.at);
	paths.forEach((p, i) => (p.track = i));
	return paths;
}

/** The SVG path of one incident: horizontal runs along each lane, vertical drops between lanes. Coordinates come
 *  from the caller's x (time → px) and y (lane, track → px) functions. */
export function stepPath(p: IncidentPath, x: (t: Date) => number, y: (lane: number, track: number) => number): string {
	const pts: string[] = [];
	p.steps.forEach((s, i) => {
		const sx = x(s.at);
		if (i === 0) pts.push(`M${r(sx)} ${r(y(s.lane, p.track))}`);
		else {
			pts.push(`H${r(sx)}`);
			pts.push(`V${r(y(s.lane, p.track))}`);
		}
	});
	pts.push(`H${r(x(p.until))}`);
	return pts.join(' ');
}
const r = (n: number) => Math.round(n * 10) / 10;

/** Hourly intake counts to bar heights (0…1), loudest hour = 1. */
export function intakeBars(rows: { at: Date; count: number }[], w: Pick<ShiftWindow, 'start' | 'end'>): { at: Date; x: number; h: number; count: number }[] {
	const inWin = rows.filter((r) => +r.at >= +w.start - H && +r.at <= +w.end);
	const max = Math.max(1, ...inWin.map((r) => r.count));
	return inWin.map((r) => ({ at: r.at, x: xOf(r.at, w), h: r.count / max, count: r.count }));
}

// ---------- the watch dial (DESIGN.md §8): the same layout in polar form ----------
// A 24-hour face, midnight at the top, read clockwise. The shift is the lit sector, the hand is now, and each
// incident runs clockwise along its stage ring and steps inward when its log records the next stage.

/** The six watches of a ship's day, by local hour. */
export const WATCHES = [
	{ key: 'middle', label: 'middle', from: 0, to: 4 },
	{ key: 'morning', label: 'morning', from: 4, to: 8 },
	{ key: 'forenoon', label: 'forenoon', from: 8, to: 12 },
	{ key: 'afternoon', label: 'afternoon', from: 12, to: 16 },
	{ key: 'dog', label: 'dog', from: 16, to: 20 },
	{ key: 'first', label: 'first', from: 20, to: 24 }
] as const;
/** The watch a moment falls in, in the org's time: "forenoon". */
export function watchOf(d: Date, tz: string): (typeof WATCHES)[number] {
	const h = zonedParts(d, tz).hour;
	return WATCHES.find((w) => h >= w.from && h < w.to) ?? WATCHES[0];
}
/** Degrees on the 24-hour face (0 = midnight at the top, clockwise) of a local time, in [0, 360). */
export function dialAngle(t: Date | string, tz: string): number {
	const p = zonedParts(new Date(t), tz);
	return ((p.hour * 3600 + p.minute * 60 + p.second) / 86400) * 360;
}
/** Unwrapped degrees from the window's start, so a line or the hand never runs backwards across midnight. The
 *  window is at most a day; anything before it sits at its start. */
export function dialAngleOn(t: Date | string, w: Pick<ShiftWindow, 'start' | 'end'>, tz: string): number {
	const a0 = dialAngle(w.start, tz);
	const span = Math.min(+w.end - +w.start, 23.75 * H);
	const dt = Math.max(0, Math.min(span, +new Date(t) - +w.start));
	return a0 + (dt / (24 * H)) * 360;
}
/** A point at `deg` degrees and radius r around (cx, cy). */
export function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
	const a = (deg * Math.PI) / 180;
	return [cx + r * Math.sin(a), cy - r * Math.cos(a)];
}
/** An arc command from the current point at radius r, clockwise from a1 to a2 (degrees, a2 ≥ a1). */
function arcTo(cx: number, cy: number, r: number, a1: number, a2: number): string {
	if (a2 - a1 < 0.01) return '';
	const parts: string[] = [];
	let from = a1;
	while (a2 - from > 179.9) {
		const mid = from + 179.9;
		const [x, y] = polar(cx, cy, r, mid);
		parts.push(`A${r1(r)} ${r1(r)} 0 0 1 ${r1(x)} ${r1(y)}`);
		from = mid;
	}
	const [x, y] = polar(cx, cy, r, a2);
	parts.push(`A${r1(r)} ${r1(r)} 0 0 1 ${r1(x)} ${r1(y)}`);
	return parts.join(' ');
}
/** The SVG path of one incident on the dial: arcs along each stage ring, radial steps inward between rings. */
export function dialPath(p: IncidentPath, angle: (t: Date) => number, radius: (lane: number, track: number) => number, cx: number, cy: number): string {
	const pts: string[] = [];
	let a = angle(p.steps[0]!.at);
	let r = radius(p.steps[0]!.lane, p.track);
	const [x0, y0] = polar(cx, cy, r, a);
	pts.push(`M${r1(x0)} ${r1(y0)}`);
	for (const s of p.steps.slice(1)) {
		const a2 = Math.max(a, angle(s.at));
		const arc = arcTo(cx, cy, r, a, a2);
		if (arc) pts.push(arc);
		a = a2;
		r = radius(s.lane, p.track);
		const [x, y] = polar(cx, cy, r, a);
		pts.push(`L${r1(x)} ${r1(y)}`);
	}
	const end = Math.max(a, angle(p.until));
	const arc = arcTo(cx, cy, r, a, end);
	if (arc) pts.push(arc);
	return pts.join(' ');
}
/** An annular sector (a ring segment) from a1 to a2 between radii r0 < r1, for intake bars and the lit shift. */
export function sectorPath(cx: number, cy: number, rIn: number, rOut: number, a1: number, a2: number): string {
	if (a2 - a1 >= 359.9) {
		const [ox, oy] = polar(cx, cy, rOut, 0);
		const [ix, iy] = polar(cx, cy, rIn, 0);
		return `M${r1(ox)} ${r1(oy)} ${arcTo(cx, cy, rOut, 0, 359.95)} Z M${r1(ix)} ${r1(iy)} ${arcTo(cx, cy, rIn, 0, 359.95)} Z`;
	}
	const [x1, y1] = polar(cx, cy, rOut, a1);
	const [x2, y2] = polar(cx, cy, rIn, a2);
	const large = a2 - a1 > 180 ? 1 : 0;
	return `M${r1(x1)} ${r1(y1)} ${arcTo(cx, cy, rOut, a1, a2)} L${r1(x2)} ${r1(y2)} A${r1(rIn)} ${r1(rIn)} 0 ${large} 0 ${r1(polar(cx, cy, rIn, a1)[0])} ${r1(polar(cx, cy, rIn, a1)[1])} Z`;
}
const r1 = (n: number) => Math.round(n * 10) / 10;
