<script lang="ts">
	// The incident tile body: header, ATT&CK chain, stage rail, and the replayable baton timeline.
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { buildBaton } from '$lib/util/baton';
	import { loadIncident, stageTimes, type IncidentBundle } from '$lib/util/incident';
	import { problemMessage } from '$lib/api';
	import IncidentHeader from './IncidentHeader.svelte';
	import BatonTimeline from './BatonTimeline.svelte';

	let {
		param,
		compact = false,
		bundle = $bindable<IncidentBundle | null>(null),
		selectedKey = $bindable<string | null>(null)
	}: { param: string; compact?: boolean; bundle?: IncidentBundle | null; selectedKey?: string | null } = $props();

	let error = $state('');
	let all = $state(false);
	let loading = $state(true);

	async function load() {
		try {
			bundle = await loadIncident(param, (p) => session.can(p));
			error = '';
		} catch (e) {
			error = e instanceof Error && e.message === 'not_found' ? 'No incident with that reference.' : problemMessage(e);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		void param;
		loading = true;
		load();
	});
	$effect(() => {
		const id = bundle?.incident.id;
		if (!id) return;
		const mine = (d: { incidentId: string }) => d.incidentId === id && load();
		const offs = [
			live.on('approval.decided', mine),
			live.on('action.executed', mine),
			live.on('incident.updated', mine),
			live.on('investigation.step', mine),
			live.on('clock.started', mine),
			live.on('notice.updated', mine)
		];
		return () => offs.forEach((f) => f());
	});

	const rows = $derived(bundle ? buildBaton(bundle.runs, bundle.timeline, bundle.proposals, { all }) : []);
	const pending = $derived(bundle?.proposals.filter((p) => p.status === 'pending').length ?? 0);
</script>

{#if error}
	<div class="empty"><p>{error}</p><a class="lnk" href="/watch">Back to the watch</a></div>
{:else if bundle}
	<IncidentHeader incident={bundle.incident} times={stageTimes(bundle)} clocks={live.clocks} {pending} {compact} />
	<BatonTimeline {rows} bind:all bind:selectedKey incident={bundle.incident} {compact} />
{:else if loading}
	<div class="empty mono faint" aria-live="polite">Opening the incident…</div>
{/if}

<style>
	.empty {
		padding: 40px 22px;
		color: var(--muted);
		display: grid;
		gap: 12px;
		justify-items: start;
		font-size: 13px;
	}
</style>
