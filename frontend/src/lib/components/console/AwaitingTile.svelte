<script lang="ts">
	// "awaiting you": the approval cards, the draft notice for the most urgent clock with its required-fields
	// check, and the actions already authorised in this incident.
	import { api } from '$lib/api';
	import type { Notice, ProposalDoc, IdentityDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { dueLabel, REGIME_TZ, timeOf } from '$lib/time/clock';
	import { actionTitle } from '$lib/util/approvals';
	import { clockLongLabel, noticeHref } from '$lib/util/labels';
	import type { IncidentBundle } from '$lib/util/incident';
	import ApprovalCard from './ApprovalCard.svelte';

	let { bundle, onwhy, holdId }: { bundle: IncidentBundle | null; onwhy?: (p: ProposalDoc) => void; holdId?: string } = $props();

	let pending = $state<ProposalDoc[]>([]);
	let notice = $state<Notice | null>(null);
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

	const urgent = $derived(live.clocks.find((c) => c.noticeId && c.state !== 'submitted' && c.state !== 'not_started') ?? null);
	$effect(() => {
		const id = urgent?.noticeId;
		if (!id) {
			notice = null;
			return;
		}
		api.notices.get(id).then((n) => (notice = n)).catch(() => (notice = null));
	});
	// cards: this incident's first, then the rest, oldest first
	const focusId = $derived(bundle?.incident.id ?? null);
	const cards = $derived([...pending].sort((a, b) => Number(b.incidentId === focusId) - Number(a.incidentId === focusId) || a.createdAt.localeCompare(b.createdAt)));
	const doneHere = $derived((bundle?.proposals ?? []).filter((p) => p.status === 'executed' || p.status === 'approved' || p.status === 'rejected'));
	const missing = $derived(notice?.sections.filter((s) => s.required && !s.filled) ?? []);
	const firstName = $derived(session.firstName);
</script>

{#each cards as p, i (p.id)}
	<ApprovalCard {p} index={i + 1} total={cards.length} {people} holdId={i === 0 ? holdId : undefined} onwhy={onwhy ? () => onwhy(p) : undefined} onchange={() => setTimeout(load, 1200)} />
{:else}
	<div class="quiet">
		<p>Nothing is waiting for {firstName || 'you'}.</p>
	</div>
{/each}

{#if notice && urgent}
	<section class="notice" aria-label="Draft notice">
		<div class="hd"><span class="eng">Notice draft</span><span>due {dueLabel(urgent.dueAt!, live.now, REGIME_TZ[urgent.regime])}</span></div>
		<h3 title="To {notice.recipient.name} · {urgent.incidentReference}">{clockLongLabel(urgent)}</h3>
		{#if missing.length}<p class="miss">◐ {missing.length} {missing.length === 1 ? 'field' : 'fields'} to confirm</p>{/if}
		<div class="btns"><a class="btn preset-filled-surface-950-50" href={noticeHref(urgent)}>Open the draft</a></div>
	</section>
{/if}

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
	.notice {
		padding: 16px 18px;
		border-bottom: 1px solid var(--rule);
		border-left: 3px solid var(--soon);
	}
	.hd {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		font: 500 12.5px/1 var(--code);
		color: var(--faint);
		gap: 8px;
	}
	.hd .eng {
		font-size: 12.5px;
		color: var(--dim);
	}
	h3 {
		margin: 10px 0 4px;
		font: 800 21px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.btns {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.miss {
		margin: 8px 0 12px;
		font-size: 13px;
		font-weight: 600;
		color: var(--soon);
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
