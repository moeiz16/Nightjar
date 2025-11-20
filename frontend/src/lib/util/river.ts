// The event river's minute grouping (DESIGN.md §5). Pure, unit-tested.
import type { RiverLine } from '$lib/api/types';

/** Group river lines by minute (newest minute first). Pure, so it is unit-tested. */
export function groupByMinute(lines: RiverLine[], tz?: string): { key: string; label: string; lines: RiverLine[] }[] {
	const out = new Map<string, { key: string; label: string; lines: RiverLine[] }>();
	const fmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', ...(tz ? { timeZone: tz } : {}) });
	for (const l of [...lines].sort((a, b) => b.at.localeCompare(a.at))) {
		const d = new Date(l.at);
		const key = new Date(Math.floor(+d / 60000) * 60000).toISOString();
		let g = out.get(key);
		if (!g) out.set(key, (g = { key, label: fmt.format(d), lines: [] }));
		g.lines.push(l);
	}
	return [...out.values()];
}

/** Events in a minute: the exact total from the API (`minutes`) or the live batches, never fewer than the lines shown. */
export function minuteTotal(counted: number | undefined, linesShown: number): number {
	return Math.max(counted ?? 0, linesShown);
}

/** Minute totals from `GET /events/river`, keyed like `groupByMinute`. */
export function minuteCounts(minutes: { minute: string; count: number }[] | undefined): Record<string, number> {
	const out: Record<string, number> = {};
	for (const m of minutes ?? []) out[new Date(Math.floor(+new Date(m.minute) / 60000) * 60000).toISOString()] = m.count;
	return out;
}

/** One line per event (a batch can repeat a line already shown, or one linked after the fact): keeps the first,
 *  but carries an incident or behaviour link from any copy. */
export function uniqueLines(lines: RiverLine[], max: number): RiverLine[] {
	const byId = new Map<string, RiverLine>();
	for (const l of lines) {
		const had = byId.get(l.eventId);
		if (!had) byId.set(l.eventId, l);
		else if (!had.incidentId && !had.anomaly && (l.incidentId || l.anomaly)) byId.set(l.eventId, { ...had, incidentId: l.incidentId, anomaly: l.anomaly });
	}
	return [...byId.values()].slice(0, max);
}
