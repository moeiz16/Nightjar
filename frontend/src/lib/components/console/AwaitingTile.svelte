<script lang="ts">
	// "awaiting you": the approval cards and the actions already decided in this incident. Notice drafts live
	// with their clocks, so the count on the readout and the cards in this sheet are the same thing.
	import { api } from '$lib/api';
	import type { ProposalDoc, IdentityDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { timeOf } from '$lib/time/clock';
	import { actionTitle } from '$lib/util/approvals';
	import type { IncidentBundle } from '$lib/util/incident';
	import ApprovalCard from './ApprovalCard.svelte';

	let { bundle, onwhy, holdId }: { bundle: IncidentBundle | null; onwhy?: (p: ProposalDoc) => void; holdId?: string } = $props();

	let pending = $state<ProposalDoc[]>([]);
	let people = $state<IdentityDoc[]>([]);

	async function load() {
		try {
			const r = await api.approvals.list({ status: 'pending' });
			pending = r.items;
		} catch {
			pending = [];
		}
	}
	$effect(() => {
		load();
		api.directory.identities().then((r) => (people = r)).catch(() => undefined);
		const offs = [live.on('approval.requested', load), live.on('approval.decided', load)];
		return () => offs.forEach((f) => f());
	});

	// cards: this incident's first, then the rest, oldest first
	const focusId = $derived(bundle?.incident.id ?? null);
	const cards = $derived([...pending].sort((a, b) => Number(b.incidentId === focusId) - Number(a.incidentId === focusId) || a.createdAt.localeCompare(b.createdAt)));
	const doneHere = $derived((bundle?.proposals ?? []).filter((p) => p.status === 'executed' || p.status === 'approved' || p.status === 'rejected'));
	const firstName = $derived(session.firstName);
</script>

{#each cards as p, i (p.id)}
	<ApprovalCard {p} index={i + 1} total={cards.length} {people} holdId={i === 0 ? holdId : undefined} onwhy={onwhy ? () => onwhy(p) : undefined} onchange={() => setTimeout(load, 1200)} />
{:else}
	<div class="quiet">
		<p>Nothing is waiting for {firstName || 'you'}.</p>
	</div>
{/each}

{#if doneHere.length}
	<section class="others" aria-label="Already decided in this incident">
		<h4 class="eng faint">Already decided · {bundle?.incident.reference}</h4>
		{#each doneHere as p (p.id)}
			<div class="r">
				<span>{actionTitle(p, people)}</span>
				<span class="mono">{p.status === 'rejected' ? `✕ declined ${timeOf(p.rejection!.decidedAt)}` : `✓ authorised ${timeOf(p.approvals.at(-1)?.decidedAt ?? p.createdAt)}`}</span>
			</div>
		{/each}
	</section>
{/if}

<style>
	.quiet {
		padding: 22px 18px;
		border-bottom: 1px solid var(--rule);
		font-size: 14.5px;
		color: var(--ink-2);
	}
	.quiet p {
		margin: 0 0 6px;
	}
	.others {
		padding: 12px 18px 16px;
	}
	h4 {
		margin: 0 0 4px;
		font-size: 12.5px;
	}
	.r {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		padding: 9px 0;
		border-top: 1px solid var(--rule);
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.r .mono {
		font-size: 12px;
		color: var(--faint);
		white-space: nowrap;
	}
</style>
