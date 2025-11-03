<script lang="ts">
	// Incident detail (DESIGN.md §6.4): the incident tile zoomed, with its approvals, clocks and notices beside it.
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import Pane from '$lib/components/shell/Pane.svelte';
	import IncidentView from '$lib/components/console/IncidentView.svelte';
	import ApprovalCard from '$lib/components/console/ApprovalCard.svelte';
	import DecisionSheet from '$lib/components/console/DecisionSheet.svelte';
	import { api } from '$lib/api';
	import type { IdentityDoc, ProposalDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { dueBoth, hm, secondsLeft, REGIME_TZ, timeOf, STATE_GLYPH, STATE_WORD, clockState } from '$lib/time/clock';
	import { actionTitle } from '$lib/util/approvals';
	import { clockLongLabel, noticeHref, regimeShort } from '$lib/util/labels';
	import type { IncidentBundle } from '$lib/util/incident';

	let bundle = $state<IncidentBundle | null>(null);
	let selectedKey = $state<string | null>(null);
	let people = $state<IdentityDoc[]>([]);
	const param = $derived(page.params.id ?? '');
	const holdParam = $derived(page.url.searchParams.get('hold'));
	const declineParam = $derived(page.url.searchParams.get('decline'));
	const decideParam = $derived(page.url.searchParams.get('decide'));

	$effect(() => {
		api.directory.identities().then((r) => (people = r)).catch(() => undefined);
	});
	$effect(() => {
		if (bundle) ui.scope = { id: bundle.incident.id, reference: bundle.incident.reference };
	});
	$effect(() => {
		if (holdParam && bundle) tick().then(() => document.getElementById('hold-' + holdParam)?.focus());
	});

	const pending = $derived(bundle?.proposals.filter((p) => p.status === 'pending') ?? []);
	const decided = $derived(bundle?.proposals.filter((p) => p.status !== 'pending') ?? []);
	const deadlines = $derived(bundle?.reporting?.deadlines ?? []);
	const open = $derived((bundle?.incident.regimes ?? []).filter((r) => r.proposal.applies === 'assess' && !r.determination));
	const deciding = $derived(open.find((r) => r.regime === decideParam) ?? null);
	function why(p: ProposalDoc) {
		const s = bundle?.runs.flatMap((r) => r.steps.map((st) => ({ key: `${r.id}:${st.seq}`, st }))).filter((x) => x.st.kind === 'proposal' && Math.abs(+new Date(x.st.startedAt) - +new Date(p.createdAt)) < 90_000).at(-1);
		if (s) selectedKey = s.key;
	}
	function onChange(p: ProposalDoc) {
		if (!bundle) return;
		bundle.proposals = bundle.proposals.map((x) => (x.id === p.id ? p : x));
	}
</script>

<svelte:head><title>{bundle ? `${bundle.incident.reference} ${bundle.incident.title}` : 'Incident'} · Nightjar SOC</title></svelte:head>

<div class="tiles">
	<Pane title="incident" sub={bundle?.incident.reference ?? param} focus label="Incident">
		{#snippet actions()}
			{#if bundle}<span class="hide-s">opened {timeOf(bundle.incident.firstSeen, undefined, true)}</span>{/if}
			<a class="lnk" href="/watch">back to the watch</a>
		{/snippet}
		<IncidentView {param} bind:bundle bind:selectedKey />
	</Pane>
	<Pane title="awaiting you" sub={String(pending.length)} label="Decisions for this incident">
		{#if deciding && bundle}
			<div class="blk"><DecisionSheet incidentId={bundle.incident.id} reference={bundle.incident.reference} a={deciding} onrecorded={() => goto(`/incidents/${bundle!.incident.reference}`, { replaceState: true, invalidateAll: true }).then(() => live.loadClocks())} oncancel={() => goto(`/incidents/${bundle!.incident.reference}`, { replaceState: true })} /></div>
		{/if}
		{#each pending as p (p.id)}
			<ApprovalCard {p} {people} holdId={'hold-' + p.id} startDeclining={declineParam === p.id} onwhy={() => why(p)} onchange={onChange} />
		{:else}
			{#if bundle && !deciding}<p class="quiet">Nothing here is waiting for a person.</p>{/if}
		{/each}

		{#if bundle}
			<section class="blk">
				<h3 class="mono">clocks and notices</h3>
				{#each deadlines.filter((d) => d.state !== 'not_required') as d (d.id)}
					{@const st = clockState(d, live.now)}
					<a class="clk" href={noticeHref(d)}>
						<span><b>{clockLongLabel(d)}</b><small class="mono">{d.state === 'not_started' ? 'not started · waits for the materiality decision' : d.state === 'submitted' ? 'recorded as submitted (demo)' : `due ${dueBoth(d.dueAt!, live.now, REGIME_TZ[d.regime])}`}</small></span>
						<span class="mono st {st}">{STATE_GLYPH[st]} {st === 'on_track' || st === 'due_soon' ? hm(secondsLeft(d.dueAt, live.now)) : STATE_WORD[st]}</span>
					</a>
				{:else}
					<p class="mono faint small">No regulator clocks for this incident.</p>
				{/each}
				{#each open as r (r.regime)}
					{#if r.regime !== decideParam}
						<div class="assess">
							<p><span class="lav mono">◆</span> {regimeShort(r.regime)} · {r.entityName}: to assess</p>
							<ul>{#each r.proposal.reasons as reason (reason)}<li>{reason}</li>{/each}</ul>
							<a class="btn sm" href="?decide={r.regime}">Record the decision</a>
						</div>
					{/if}
				{/each}
			</section>
			{#if decided.length}
				<section class="blk">
					<h3 class="mono">actions decided</h3>
					{#each decided as p (p.id)}
						<div class="r"><span>{actionTitle(p, people)}</span><span class="mono faint">{p.status === 'rejected' ? `✕ declined` : `✓ ${p.approvals.map((a) => a.name.split(' ')[0]).join(' + ')} · ${timeOf(p.approvals.at(-1)?.decidedAt ?? p.createdAt)}`}</span></div>
					{/each}
				</section>
			{/if}
			{#if bundle.incident.tickets.length}
				<section class="blk">
					<h3 class="mono">service desk</h3>
					{#each bundle.incident.tickets as t (t.id)}<div class="r"><span>{t.title}</span><span class="mono faint">{t.key} · {t.status.replace('_', ' ')}</span></div>{/each}
				</section>
			{/if}
		{/if}
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, 1fr) 360px;
		gap: 1px;
		background: var(--line);
		min-height: 0;
	}
	.blk {
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
	}
	h3 {
		margin: 0 0 8px;
		font-size: 11.5px;
		font-weight: 400;
		color: var(--faint);
	}
	.quiet {
		padding: 18px;
		margin: 0;
		color: var(--muted);
		font-size: 13px;
		border-bottom: 1px solid var(--line);
	}
	.clk {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 10px 0;
		border-top: 1px solid var(--line);
		text-decoration: none;
		font-size: 13px;
	}
	.clk b {
		display: block;
		font-weight: 500;
	}
	.clk small {
		font-size: 11.5px;
		color: var(--muted);
	}
	.clk:hover b {
		color: var(--teal);
	}
	.st {
		font-size: 12px;
		white-space: nowrap;
	}
	.st.on_track,
	.st.submitted {
		color: var(--teal);
	}
	.st.due_soon {
		color: var(--amber);
	}
	.st.overdue {
		color: var(--red);
	}
	.st.not_started {
		color: var(--lav);
	}
	.assess {
		margin-top: 10px;
		padding: 10px 12px;
		border-left: 2px solid var(--lav);
		background: var(--lav-wash);
		font-size: 13px;
	}
	.assess p {
		margin: 0 0 6px;
	}
	.assess ul {
		margin: 0 0 10px;
		padding-left: 18px;
		color: var(--ink-2);
		font-size: 12.5px;
	}
	.r {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
		font-size: 12.5px;
		color: var(--ink-2);
	}
	.r .mono {
		font-size: 11.5px;
		white-space: nowrap;
	}
	.small {
		font-size: 11.5px;
	}
	@media (max-width: 1000px) {
		.tiles {
			grid-template-columns: minmax(0, 1fr);
			overflow: auto;
		}
		.tiles > :global(.pane) {
			min-height: auto;
		}
		.tiles > :global(.pane .scroll) {
			overflow: visible;
		}
	}
</style>
