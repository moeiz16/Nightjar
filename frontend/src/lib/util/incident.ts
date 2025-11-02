// Load everything an incident view needs. URLs use the readable reference (NJ-1038); the API takes UUIDs, so a
// reference is resolved through the incident list first.
import { api } from '$lib/api';
import type { Incident, IncidentReporting, ProposalDoc, Run, TimelineEntry } from '$lib/api/types';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const cache = new Map<string, string>();

export async function resolveIncidentId(param: string): Promise<string> {
	if (UUID.test(param)) return param;
	const hit = cache.get(param);
	if (hit) return hit;
	try {
		const inc = await api.incidents.get(param);
		cache.set(param, inc.id);
		return inc.id;
	} catch {
		const page = await api.incidents.list({ status: 'all', q: param, pageSize: 5 });
		const found = page.items.find((i) => i.reference === param);
		if (!found) throw new Error('not_found');
		cache.set(param, found.id);
		return found.id;
	}
}

export interface IncidentBundle {
	incident: Incident;
	runs: Run[];
	timeline: TimelineEntry[];
	proposals: ProposalDoc[];
	reporting: IncidentReporting | null;
}

export async function loadIncident(param: string, can: (p: string) => boolean): Promise<IncidentBundle> {
	const id = await resolveIncidentId(param);
	const [incident, summaries, timeline, proposals, reporting] = await Promise.all([
		api.incidents.get(id),
		can('investigations:read') ? api.incidents.investigations(id).catch(() => []) : Promise.resolve([]),
		api.incidents.timeline(id).catch(() => []),
		can('actions:read') ? api.incidents.actions(id).catch(() => []) : Promise.resolve([]),
		can('reporting:read') ? api.incidents.reporting(id).catch(() => null) : Promise.resolve(null)
	]);
	const runs = await Promise.all(summaries.map((r) => api.investigations.get(r.id).catch(() => null)));
	return { incident, runs: runs.filter((r): r is Run => !!r).sort((a, b) => a.startedAt.localeCompare(b.startedAt)), timeline, proposals, reporting };
}

/** Times at which each stage was reached, for the stage rail. */
export function stageTimes(b: IncidentBundle): Record<'triage' | 'investigate' | 'contain', string | null> {
	const steps = b.runs.flatMap((r) => r.steps);
	const verdict = steps.find((s) => s.kind === 'verdict')?.startedAt ?? null;
	const enrich = steps.filter((s) => s.agent === 'enrichment').at(-1)?.startedAt ?? null;
	const exec = b.proposals.filter((p) => p.result?.executedAt).map((p) => p.result!.executedAt).sort().at(-1) ?? null;
	return { triage: verdict, investigate: enrich ?? verdict, contain: exec };
}
