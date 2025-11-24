// The baton timeline's rows (DESIGN.md §6.4): one row per key step on its lane (T triage · E enrichment ·
// R response · P people), oldest first. People rows come from the incident timeline (approvals, determinations,
// submissions). "All activity" adds every lookup between key steps. Pure, so replay maths is unit-tested.
import type { AgentStepDoc, ProposalDoc, Run, TimelineEntry } from '$lib/api/types';
import { LANE } from './labels';

export interface BatonRow {
	key: string;
	at: string;
	lane: 1 | 2 | 3 | 4;
	title: string;
	who: string;
	lookedAt: string[];
	why: string | null;
	state: '' | 'ok' | 'wait';
	step?: AgentStepDoc;
	runId?: string;
	entry?: TimelineEntry;
	proposal?: ProposalDoc;
	evidence?: NonNullable<NonNullable<AgentStepDoc['detail']>['evidence']>;
	minor: boolean;
}

const KEY_KINDS = new Set<AgentStepDoc['kind']>(['finding', 'verdict', 'proposal', 'guardrail', 'error']);
const PEOPLE_KINDS = new Set<TimelineEntry['kind']>(['approval', 'clock', 'notice', 'note']);

/** Tool ids are never shown; lookups read as plain words ("Checked: known exploited vulnerabilities"). */
export function plainLookup(step: AgentStepDoc): string {
	return step.title.replace(/^Checked:\s*/i, 'checked ').replace(/_/g, ' ');
}

export function buildBaton(runs: Run[], timeline: TimelineEntry[], proposals: ProposalDoc[], opts: { all?: boolean } = {}): BatonRow[] {
	const rows: BatonRow[] = [];
	for (const run of runs) {
		// Lookups between key steps become the next key step's "looked at" (the API records them as tool steps).
		const pendingLookups = new Map<string, string[]>();
		for (const s of run.steps) {
			const isKey = KEY_KINDS.has(s.kind);
			if (s.kind === 'tool_call') {
				const list = pendingLookups.get(s.agent) ?? [];
				// "Looked up attack technique T1552.001" → "attack technique T1552.001" (the row says "looked at …")
				const what = plainLookup(s).replace(/^(checked:?|looked (up|at))\s+/i, '').replace(/\.$/, '');
				list.push(what.charAt(0).toLowerCase() + what.slice(1));
				pendingLookups.set(s.agent, list);
			}
			if (!isKey && !opts.all) continue;
			const lane = (LANE[s.agent]?.n ?? 1) as BatonRow['lane'];
			const d = s.detail ?? {};
			const looked = Array.isArray(d.lookedAt) ? d.lookedAt : isKey ? (pendingLookups.get(s.agent) ?? []).slice(-3) : [];
			if (isKey) pendingLookups.delete(s.agent);
			const summary = typeof d.summary === 'string' ? d.summary : null;
			const why = typeof d.why === 'string' ? d.why : s.kind === 'verdict' && summary ? summary.split(/(?<=[.!?])\s/)[0]! : null;
			const evidence = Array.isArray(d.evidence)
				? d.evidence
				: s.kind === 'verdict' && run.verdictDetail?.evidence
					? run.verdictDetail.evidence.map((e, k) => ({ ref: `E-${String(k + 1).padStart(2, '0')}`, at: s.startedAt, source: e.kind, text: typeof e.text === 'string' ? { text: e.text, flags: [] } : e.text }))
					: [];
			rows.push({
				key: `${run.id}:${s.seq}`,
				at: s.startedAt,
				lane,
				title: s.title,
				who: LANE[s.agent]?.label ?? 'Triage',
				lookedAt: looked,
				why,
				evidence,
				state: '',
				step: s,
				runId: run.id,
				minor: !isKey
			});
		}
	}
	for (const e of timeline) {
		if (!PEOPLE_KINDS.has(e.kind) || e.actor.type !== 'user') continue;
		rows.push({ key: `tl:${e.id}`, at: e.at, lane: 4, title: e.title, who: 'People', lookedAt: [], why: e.body, state: e.kind === 'approval' && !/declined/i.test(e.title) ? 'ok' : '', entry: e, minor: false });
	}
	rows.sort((a, b) => a.at.localeCompare(b.at) || a.lane - b.lane);
	// A pending proposal hands the baton to people: mark the step that asked, or add it.
	for (const p of proposals.filter((x) => x.status === 'pending')) {
		const t = +new Date(p.createdAt);
		let idx = -1;
		rows.forEach((r, i) => {
			if (r.step?.kind !== 'proposal' || Math.abs(+new Date(r.at) - t) >= 90_000) return;
			// the step that names this action's target wins over other proposals made in the same minute
			if (idx < 0 || r.title.includes(p.target)) idx = i;
		});
		if (idx >= 0) {
			rows[idx] = { ...rows[idx]!, state: 'wait', proposal: p };
		} else {
			rows.push({ key: `p:${p.id}`, at: p.createdAt, lane: 3, title: `Asks: ${p.kindLabel.toLowerCase()}`, who: 'Response', lookedAt: [], why: p.rationale, state: 'wait', proposal: p, minor: false });
		}
	}
	return rows.sort((a, b) => a.at.localeCompare(b.at) || a.lane - b.lane);
}

/** The mono line under a row title: "Triage · why …" or "Enrichment · looked at …" or a wait marker. */
export function rowSub(r: BatonRow): string {
	if (r.state === 'wait') return '‖ handed to people · awaiting';
	if (r.why && r.lookedAt.length && r.minor) return `looked at ${r.lookedAt.join(', ')}`;
	if (r.why && r.step?.kind === 'verdict') return `why ${r.why}`;
	if (r.lookedAt.length) return `looked at ${r.lookedAt.join(', ')}`;
	if (r.why) return r.why;
	if (r.entry) return r.entry.kind === 'approval' ? 'held to authorise · logged with who and when' : r.entry.kind === 'notice' ? 'recorded as submitted (demo)' : r.entry.kind === 'clock' ? 'determination recorded with reasons' : 'note';
	return r.step ? plainLookup(r.step) : '';
}

/** Hand-off curve between two lane dots (S-curve), in the timeline's coordinates. */
export function handoffPath(a: { x: number; y: number }, b: { x: number; y: number }): string {
	const my = (a.y + b.y) / 2;
	return `M${a.x},${a.y} C${a.x},${my} ${b.x},${my} ${b.x},${b.y}`;
}

/** Replay timing: one step per 1.2 s at 1×, 0.3 s at 4×. */
export function stepInterval(speed: 1 | 4): number {
	return speed === 4 ? 300 : 1200;
}
/** Next playhead; returns null at the end (replay stops). */
export function nextStep(i: number, total: number): number | null {
	return i + 1 < total ? i + 1 : null;
}
