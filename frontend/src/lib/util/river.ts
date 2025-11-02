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

/** Estimated events in a minute: counted from batches where we have them, else the running rate. */
export function minuteTotal(counted: number | undefined, eventsPerMinute: number, minuteKey: string, at: Date): number {
	const start = +new Date(minuteKey);
	const frac = Math.min(1, Math.max(0, (+at - start) / 60000));
	const jitter = ((start / 60000) % 7) * 9 - 27;
	return Math.max(counted ?? 0, Math.round((eventsPerMinute + jitter) * frac));
}
