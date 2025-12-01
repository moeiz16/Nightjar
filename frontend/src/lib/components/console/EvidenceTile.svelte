<script lang="ts">
	// The auditor's evidence section: what each detection cited, quoted as received.
	import { api } from '$lib/api';
	import type { DetectionDoc } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { timeOf } from '$lib/time/clock';
	import Untrusted from './Untrusted.svelte';

	let { incidentId }: { incidentId: string | null } = $props();
	let dets = $state<DetectionDoc[]>([]);
	$effect(() => {
		if (!incidentId || !session.can('detections:read')) return;
		api.incidents.detections(incidentId).then((d) => (dets = d)).catch(() => (dets = []));
	});
	const ev = $derived(dets.flatMap((d) => d.evidence ?? []));
</script>

<ol class="ev">
	{#each ev as e, i (e.eventId + i)}
		<li>
			<span class="mono faint">{e.ref ?? `E-${String(i + 1).padStart(2, '0')}`} · {timeOf(e.timestamp, undefined, true)} · {e.dataset}{e.host ? ` · ${e.host}` : ''}</span>
			<Untrusted value={e.summary} label={null} />
		</li>
	{:else}
		<li class="mono faint">No evidence cited yet.</li>
	{/each}
</ol>

<style>
	.ev {
		list-style: none;
		margin: 0;
		padding: 14px 16px;
		display: grid;
		gap: 14px;
	}
	li {
		display: grid;
		gap: 5px;
		font-size: 11.5px;
	}
</style>
