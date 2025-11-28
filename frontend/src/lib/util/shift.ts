// The shift chart (DESIGN.md §0): the night is the x-axis of the product, and response stages are lanes. Each
// incident is one line that steps down through Triage → Investigate → Contain → Report at the times its log
// records, like a train on a dispatcher's timetable graph. Pure functions, so the layout is testable.
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

/** Marks for the shift axis: incidents opened, approvals, notices and clocks, in time order. */
export interface AxisMark {
	at: Date;
	kind: 'incident' | 'approval' | 'notice';
	glyph: string;
	text: string;
	refId: string | null;
}
export function axisMarks(log: ShiftLogEntry[], incidents: IncidentSummary[], w: Pick<ShiftWindow, 'start' | 'end'>): AxisMark[] {
	const marks: AxisMark[] = [];
	for (const i of incidents) {
		if (+new Date(i.firstSeen) >= +w.start) marks.push({ at: new Date(i.firstSeen), kind: 'incident', glyph: '▲', text: `${i.reference} opened: ${i.title}`, refId: i.id });
	}
	for (const e of log) {
		if (+new Date(e.at) < +w.start) continue;
		if (e.kind === 'approval') marks.push({ at: new Date(e.at), kind: 'approval', glyph: '‖', text: e.text, refId: e.refId });
		else if (e.kind === 'notice' || e.kind === 'clock') marks.push({ at: new Date(e.at), kind: 'notice', glyph: '✉', text: e.text, refId: e.refId });
	}
	return marks.sort((a, b) => +a.at - +b.at);
}
