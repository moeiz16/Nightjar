// The shift: one window of time that the axis, the watch's chart and the brief's night band all draw from.
// It reads the shift log since the window opened, every incident that touched the window and, for people who may
// hunt, the hourly intake. Live events refresh it (debounced); the window's right edge follows the clock.
import { api } from '$lib/api';
import type { IncidentSummary, ShiftLogEntry } from '$lib/api/types';
import { BRUSSELS } from '$lib/time/clock';
import { shiftWindow, type ShiftWindow } from '$lib/util/shift';
import { live } from './live.svelte';
import { session } from './session.svelte';

class Shift {
	tz = $state(BRUSSELS);
	log = $state<ShiftLogEntry[]>([]);
	incidents = $state<IncidentSummary[]>([]);
	intake = $state<{ at: Date; count: number }[]>([]);
	loaded = $state(false);
	/** the minute the axis is drawn to; the window is re-derived only when it changes */
	minute = $derived(Math.floor(+live.now / 60_000));
	win = $derived<ShiftWindow>(shiftWindow(new Date(this.minute * 60_000), this.tz));
	#timer: ReturnType<typeof setTimeout> | null = null;
	#offs: (() => void)[] = [];
	#from = '';

	start(tz?: string) {
		if (tz) this.tz = tz;
		if (this.#offs.length) return;
		const again = () => this.refresh();
		this.#offs = [
			live.on('incident.created', again),
			live.on('incident.updated', again),
			live.on('approval.decided', again),
			live.on('approval.requested', again),
			live.on('clock.started', again),
			live.on('notice.updated', again),
			live.on('action.executed', again)
		];
		this.load();
	}
	stop() {
		this.#offs.forEach((f) => f());
		this.#offs = [];
	}
	refresh() {
		if (this.#timer) return;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			this.load();
		}, 600);
	}
	async load() {
		const w = this.win;
		this.#from = w.start.toISOString();
		const tasks: Promise<unknown>[] = [
			api.watch
				.shiftLog(this.#from)
				.then((l) => (this.log = l))
				.catch(() => undefined),
			api.incidents
				.list({ status: 'all', pageSize: 100 })
				.then((p) => (this.incidents = p.items.filter((i) => +new Date(i.lastSeen) >= +w.start || ['new', 'triage', 'investigating', 'containing', 'monitoring'].includes(i.status))))
				.catch(() => (this.incidents = live.incidents))
		];
		if (session.can('hunting:read')) {
			tasks.push(
				api.hunting
					.search({ from: this.#from, to: w.end.toISOString(), filters: [], groupBy: [], metric: 'count', bucket: '1h', limit: 100 })
					.then((r) => {
						const bi = r.columns.findIndex((c) => /bucket|time/i.test(c));
						const ci = r.columns.findIndex((c) => /count/i.test(c));
						this.intake = r.rows
							.map((row) => ({ at: new Date(String(row[bi < 0 ? 0 : bi])), count: Number(row[ci < 0 ? row.length - 1 : ci]) || 0 }))
							.filter((x) => !Number.isNaN(+x.at));
					})
					.catch(() => undefined)
			);
		}
		await Promise.allSettled(tasks);
		this.loaded = true;
	}
}

export const shift = new Shift();
