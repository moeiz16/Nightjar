/* eslint-disable svelte/prefer-svelte-reactivity -- the Maps, Sets and Dates here are local, non-reactive temporaries */
// The live watch: regulator clocks, the event river, shift health and pending approvals. One SSE connection
// feeds everything; views subscribe to typed events with live.on(type, fn) and refetch what they show.
import { api, MOCK, mock } from '$lib/api';
import { eventSourceStream } from '$lib/api/stream';
import type { Deadline, IncidentSummary, PipelineStatus, RiverLine, ShiftLogEntry, StreamEvent, StreamEventType } from '$lib/api/types';
import { now } from '$lib/time/clock';
import { ui } from './ui.svelte';
import { minuteCounts } from '$lib/util/river';

type Handler<K extends StreamEventType> = (data: Extract<StreamEvent, { type: K }>['data']) => void;

export { groupByMinute, minuteTotal } from '$lib/util/river';

class Live {
	now = $state(now());
	clocks = $state<Deadline[]>([]);
	clocksLoaded = $state(false);
	river = $state<RiverLine[]>([]);
	counts = $state<Record<string, number>>({});
	held = $state<RiverLine[]>([]);
	eventsPerMinute = $state(0);
	pipeline = $state<PipelineStatus>({ status: 'normal', plainStatus: 'Taking in events normally', eventsPerMinute: 0 });
	incidents = $state<IncidentSummary[]>([]);
	pendingApprovals = $state(0);
	shiftLog = $state<ShiftLogEntry[]>([]);
	connected = $state(false);
	#handlers = new Map<StreamEventType, Set<(d: unknown) => void>>();
	#stop: (() => void) | null = null;
	#ticker: ReturnType<typeof setInterval> | null = null;
	#refreshClocks: ReturnType<typeof setTimeout> | null = null;
	/** When true, new river rows wait behind a "n new · show" counter (reduced motion). */
	holdRiver = false;

	on<K extends StreamEventType>(type: K, fn: Handler<K>): () => void {
		let set = this.#handlers.get(type);
		if (!set) this.#handlers.set(type, (set = new Set()));
		set.add(fn as (d: unknown) => void);
		return () => set!.delete(fn as (d: unknown) => void);
	}

	async start() {
		if (this.#stop) return;
		this.#ticker = setInterval(() => (this.now = now()), 1000);
		const source = MOCK ? mock()! : eventSourceStream();
		this.#stop = source.subscribe((e) => this.#dispatch(e));
		await Promise.all([this.loadWatch(), this.loadRiver()]);
	}
	stop() {
		this.#stop?.();
		this.#stop = null;
		if (this.#ticker) clearInterval(this.#ticker);
		this.#ticker = null;
		this.connected = false;
	}

	async loadWatch() {
		try {
			const w = await api.watch.get();
			this.clocks = w.clocks;
			this.incidents = w.incidents;
			this.pendingApprovals = w.pendingApprovals;
			this.shiftLog = w.shiftLog;
			this.pipeline = w.pipeline;
			this.eventsPerMinute = w.pipeline.eventsPerMinute;
		} catch {
			try {
				this.clocks = await api.reporting.clocks();
			} catch {
				/* the bar shows "No regulator clocks running" */
			}
		}
		this.clocksLoaded = true;
	}
	async loadClocks() {
		try {
			this.clocks = await api.reporting.clocks();
		} catch {
			/* keep the last known clocks */
		}
	}
	async loadRiver() {
		try {
			const r = await api.events.river(undefined, 50);
			this.river = r.lines;
			this.eventsPerMinute = r.eventsPerMinute;
			this.counts = { ...this.counts, ...minuteCounts(r.minutes) };
		} catch {
			/* river shows its empty state */
		}
	}
	showHeld() {
		this.river = [...this.held, ...this.river].slice(0, 160);
		this.held = [];
	}

	#dispatch(e: StreamEvent) {
		switch (e.type) {
			case 'ready':
				this.connected = true;
				break;
			case 'river.batch': {
				// count each line under its own minute of event time (the same keys the river groups by); events
				// beyond the lines sent fold into the newest line's minute
				const keyOf = (at: string) => new Date(Math.floor(+new Date(at) / 60000) * 60000).toISOString();
				for (const l of e.data.lines) this.counts[keyOf(l.at)] = (this.counts[keyOf(l.at)] ?? 0) + 1;
				const extra = e.data.count - e.data.lines.length;
				if (extra > 0) {
					const newest = e.data.lines.reduce((m, l) => (l.at > m ? l.at : m), e.data.lines[0]?.at ?? now().toISOString());
					this.counts[keyOf(newest)] = (this.counts[keyOf(newest)] ?? 0) + extra;
				}
				this.eventsPerMinute = e.data.eventsPerMinute;
				// at most 4 rows a second are drawn; the rest fold into the minute's count
				const lines = e.data.lines.slice(0, 8);
				if (this.holdRiver) this.held = [...lines, ...this.held].slice(0, 99);
				else this.river = [...lines, ...this.river].slice(0, 160);
				break;
			}
			case 'pipeline.status':
				this.pipeline = e.data;
				break;
			case 'clock.started':
				ui.announce(`${e.data.regime === 'sec_8k' ? 'SEC 8-K' : e.data.regime.toUpperCase()} clock started`);
				this.#scheduleClocks();
				break;
			case 'clock.state':
			case 'notice.updated':
				this.#scheduleClocks();
				break;
			case 'incident.created':
				this.loadWatch();
				this.#scheduleRiverLinks();
				break;
			case 'detection.created':
				this.#scheduleRiverLinks();
				break;
			case 'approval.requested':
				this.pendingApprovals++;
				break;
			case 'approval.decided':
				this.loadWatch();
				break;
			default:
				break;
		}
		for (const fn of this.#handlers.get(e.type) ?? []) fn(e.data);
	}
	#refreshRiver: ReturnType<typeof setTimeout> | null = null;
	/** A line reaches the river before its detection and incident exist: once they do, re-read the newest lines
	 *  and carry their incident and behaviour links over, so the event stops being folded away as routine. */
	#scheduleRiverLinks() {
		if (this.#refreshRiver) return;
		this.#refreshRiver = setTimeout(async () => {
			this.#refreshRiver = null;
			try {
				const r = await api.events.river(undefined, 200);
				const linked = new Map(r.lines.filter((l) => l.incidentId || l.anomaly).map((l) => [l.eventId, l]));
				if (!linked.size) return;
				const seen = new Set(this.river.map((l) => l.eventId));
				this.river = [...this.river.map((l) => linked.get(l.eventId) ?? l), ...[...linked.values()].filter((l) => !seen.has(l.eventId))]
					.sort((a, b) => b.at.localeCompare(a.at))
					.slice(0, 200);
			} catch {
				/* the next batch brings the links */
			}
		}, 700);
	}
	#scheduleClocks() {
		if (this.#refreshClocks) clearTimeout(this.#refreshClocks);
		this.#refreshClocks = setTimeout(() => this.loadClocks(), 120);
	}
}

export const live = new Live();
