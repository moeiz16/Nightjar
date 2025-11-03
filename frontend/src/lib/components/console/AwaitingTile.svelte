<script lang="ts">
	// "awaiting you": the approval cards, the draft notice for the most urgent clock with its required-fields
	// check, and the actions already authorised in this incident.
	import { api } from '$lib/api';
	import type { Notice, ProposalDoc, IdentityDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { dueLabel, REGIME_TZ, timeOf, clockState } from '$lib/time/clock';
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
		<p class="mono faint">New approvals land here and in the status line.</p>
	</div>
{/each}

{#if notice && urgent}
	<section class="notice" aria-label="Draft notice">
		<div class="hd mono"><span>notice · draft {notice.version}</span><span>due {dueLabel(urgent.dueAt!, live.now, REGIME_TZ[urgent.regime])}</span></div>
		<h3>{clockLongLabel(urgent)}</h3>
		<div class="to">To {notice.recipient.name} · {urgent.incidentReference}</div>
		<ul>
			{#each notice.sections.filter((s) => s.required) as s (s.key)}
				<li class:todo={!s.filled}><span class="k mono" aria-hidden="true">{s.filled ? '✓' : '◐'}</span><span>{s.filled ? s.title : `${s.title}: confirm`}<span class="sr">{s.filled ? ' (filled)' : ' (missing)'}</span></span></li>
			{/each}
		</ul>
		<div class="btns"><a class="btn pri" href={noticeHref(urgent)}>Open the draft</a><a class="btn" href="{noticeHref(urgent)}&compare=1">Compare with evidence</a></div>
		{#if missing.length}<p class="mono miss">◐ {missing.length} required {missing.length === 1 ? 'field' : 'fields'} still missing · {clockState(urgent, live.now) === 'on_track' ? 'on track' : 'due soon'}</p>{/if}
	</section>
{/if}

{#if doneHere.length}
	<section class="others" aria-label="Already decided in this incident">
		<h4 class="mono faint">already decided · {bundle?.incident.reference}</h4>
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
		border-bottom: 1px solid var(--line);
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.quiet p {
		margin: 0 0 6px;
	}
	.quiet .mono {
		font-size: 11.5px;
	}
	.notice {
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
	}
	.hd {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		font-size: 11.5px;
		color: var(--faint);
		gap: 8px;
	}
	h3 {
		margin: 10px 0 2px;
		font: 600 15px/1.3 var(--sans);
	}
	.to {
		font-size: 12.5px;
		color: var(--muted);
	}
	ul {
		list-style: none;
		margin: 12px 0 14px;
		padding: 0;
		display: grid;
		gap: 7px;
	}
	li {
		display: grid;
		grid-template-columns: 18px 1fr;
		font-size: 12.5px;
		color: var(--ink-2);
	}
	li .k {
		color: var(--teal);
	}
	li.todo .k {
		color: var(--amber);
	}
	li.todo {
		color: var(--ink);
	}
	.btns {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.miss {
		margin: 12px 0 0;
		font-size: 11.5px;
		color: var(--amber);
	}
	.others {
		padding: 12px 18px 16px;
	}
	h4 {
		margin: 0 0 4px;
		font-size: 11px;
		font-weight: 400;
	}
	.r {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		padding: 9px 0;
		border-top: 1px solid var(--line);
		font-size: 12.5px;
		color: var(--ink-2);
	}
	.r .mono {
		font-size: 11.5px;
		color: var(--faint);
		white-space: nowrap;
	}
</style>
