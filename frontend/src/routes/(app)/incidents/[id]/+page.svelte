<script lang="ts">
	// Incident (DESIGN.md §0.2): bands, never a side column. The header band (who, what, ATT&CK, the stage rail in
	// time), then the decisions waiting on you directly under it, then Skeleton Tabs: what happened and why (the baton
	// replay) · clocks and notices · actions decided · service desk · evidence. The materiality decision is a Modal.
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { Tabs } from '@skeletonlabs/skeleton-svelte';
	import Band from '$lib/components/ui/Band.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
	import IncidentHeader from '$lib/components/console/IncidentHeader.svelte';
	import BatonTimeline from '$lib/components/console/BatonTimeline.svelte';
	import ApprovalCard from '$lib/components/console/ApprovalCard.svelte';
	import DecisionSheet from '$lib/components/console/DecisionSheet.svelte';
	import ClockList from '$lib/components/console/ClockList.svelte';
	import EvidenceTile from '$lib/components/console/EvidenceTile.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { IdentityDoc, ProposalDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import { actionTitle } from '$lib/util/approvals';
	import { buildBaton } from '$lib/util/baton';
	import { loadIncident, stageTimes, type IncidentBundle } from '$lib/util/incident';
	import { plural, regimeShort } from '$lib/util/labels';

	let bundle = $state<IncidentBundle | null>(null);
	let error = $state('');
	let selectedKey = $state<string | null>(null);
	let all = $state(false);
	let people = $state<IdentityDoc[]>([]);
	let tab = $state('story');
	const param = $derived(page.params.id ?? '');
	const holdParam = $derived(page.url.searchParams.get('hold'));
	const declineParam = $derived(page.url.searchParams.get('decline'));
	const decideParam = $derived(page.url.searchParams.get('decide'));

	async function load() {
		try {
			bundle = await loadIncident(param, (p) => session.can(p));
			error = '';
		} catch (e) {
			error = e instanceof Error && e.message === 'not_found' ? 'No incident with that reference.' : problemMessage(e);
		}
	}
	$effect(() => {
		void param;
		load();
	});
	$effect(() => {
		const id = bundle?.incident.id;
		if (!id) return;
		const mine = (d: { incidentId: string }) => d.incidentId === id && load();
		const offs = [live.on('approval.decided', mine), live.on('action.executed', mine), live.on('incident.updated', mine), live.on('investigation.step', mine), live.on('clock.started', mine), live.on('notice.updated', mine)];
		return () => offs.forEach((f) => f());
	});
	$effect(() => {
		api.directory.identities().then((r) => (people = r)).catch(() => undefined);
	});
	$effect(() => {
		if (bundle) ui.scope = { id: bundle.incident.id, reference: bundle.incident.reference };
	});
	$effect(() => {
		if (holdParam && bundle) tick().then(() => document.getElementById('hold-' + holdParam)?.focus());
	});

	const rows = $derived(bundle ? buildBaton(bundle.runs, bundle.timeline, bundle.proposals, { all }) : []);
	const pending = $derived(bundle?.proposals.filter((p) => p.status === 'pending') ?? []);
	const decided = $derived(bundle?.proposals.filter((p) => p.status !== 'pending') ?? []);
	const deadlines = $derived(bundle?.reporting?.deadlines ?? []);
	const open = $derived((bundle?.incident.regimes ?? []).filter((r) => r.proposal.applies === 'assess' && !r.determination));
	const deciding = $derived(open.find((r) => r.regime === decideParam) ?? null);
	function why(p: ProposalDoc) {
		const s = bundle?.runs.flatMap((r) => r.steps.map((st) => ({ key: `${r.id}:${st.seq}`, st }))).filter((x) => x.st.kind === 'proposal' && Math.abs(+new Date(x.st.startedAt) - +new Date(p.createdAt)) < 90_000).at(-1);
		tab = 'story';
		if (s) selectedKey = s.key;
	}
	function onChange(p: ProposalDoc) {
		if (!bundle) return;
		bundle.proposals = bundle.proposals.map((x) => (x.id === p.id ? p : x));
	}
	const closeDecision = () => bundle && goto(`/incidents/${bundle.incident.reference}`, { replaceState: true, noScroll: true });
</script>

<svelte:head><title>{bundle ? `${bundle.incident.reference} ${bundle.incident.title}` : 'Incident'} · Nightjar SOC</title></svelte:head>

{#if error}
	<Band title="incident" sub={param}>
		<div class="empty"><p>{error}</p><a class="lnk" href="/watch">Back to the watch</a></div>
	</Band>
{:else if !bundle}
	<Band title="incident" sub={param}><p class="empty mono faint" aria-live="polite">Opening the incident…</p></Band>
{:else}
	<Band title="incident" sub="{bundle.incident.reference} · opened {timeOf(bundle.incident.firstSeen, undefined, true)}" flush>
		{#snippet actions()}<a class="lnk" href="/watch">back to the watch</a>{/snippet}
		<IncidentHeader incident={bundle.incident} times={stageTimes(bundle)} clocks={live.clocks} pending={pending.length} />
	</Band>

	{#if pending.length || open.length}
		<Band title="waiting on you" sub={pending.length ? plural(pending.length, 'decision') : 'a regulator decision'} n="‖" flush>
			<div class="decide" style="--cols:{Math.min(3, pending.length + open.length)}">
				{#each pending as p, i (p.id)}
					<div class="col"><ApprovalCard {p} {people} index={i + 1} total={pending.length} holdId={'hold-' + p.id} startDeclining={declineParam === p.id} onwhy={() => why(p)} onchange={onChange} /></div>
				{/each}
				{#each open as r (r.regime)}
					<div class="col assess">
						<p class="lbl mono"><span class="lav">◆</span> {regimeShort(r.regime)} · {r.entityName} · to assess</p>
						<h3>Is this {r.regime === 'sec_8k' ? 'material' : 'reportable'} to {r.entityName}?</h3>
						<ul>{#each r.proposal.reasons as reason (reason)}<li>{reason}</li>{/each}</ul>
						<a class="btn preset-filled-surface-950-50" href="?decide={r.regime}" data-sveltekit-noscroll>Record the decision</a>
					</div>
				{/each}
			</div>
		</Band>
	{/if}

	<section class="tabs band-in" aria-label="The incident record">
		<Tabs
			value={tab}
			onValueChange={(e) => (tab = e.value ?? 'story')}
			listBorder="border-b border-[var(--line)]"
			listMargin="mb-0"
			listGap="gap-0"
			listClasses="tabl"
		>
			{#snippet list()}
				<Tabs.Control value="story" base="tabc" padding="" translateX="" labelBase="tabt" stateActive="on" stateInactive="">what happened, and why <span class="ct">{rows.length}</span></Tabs.Control>
				<Tabs.Control value="clocks" base="tabc" padding="" translateX="" labelBase="tabt" stateActive="on" stateInactive="">clocks and notices <span class="ct">{deadlines.filter((d) => d.state !== 'not_required').length}</span></Tabs.Control>
				<Tabs.Control value="decided" base="tabc" padding="" translateX="" labelBase="tabt" stateActive="on" stateInactive="">actions decided <span class="ct">{decided.length}</span></Tabs.Control>
				<Tabs.Control value="desk" base="tabc" padding="" translateX="" labelBase="tabt" stateActive="on" stateInactive="">service desk <span class="ct">{bundle!.incident.tickets.length}</span></Tabs.Control>
				{#if session.can('detections:read')}<Tabs.Control value="evidence" base="tabc" padding="" translateX="" labelBase="tabt" stateActive="on" stateInactive="">evidence</Tabs.Control>{/if}
			{/snippet}
			{#snippet content()}
				<Tabs.Panel value="story">
					<BatonTimeline {rows} bind:all bind:selectedKey incident={bundle!.incident} />
				</Tabs.Panel>
				<Tabs.Panel value="clocks">
					<div class="pad"><ClockList clocks={deadlines} now={live.now} showRef={false} /></div>
				</Tabs.Panel>
				<Tabs.Panel value="decided">
					<div class="pad">
						{#each decided as p (p.id)}
							<div class="r"><span>{actionTitle(p, people)}</span><span class="mono faint">{p.status === 'rejected' ? `✕ declined · ${p.rejection?.reason ?? ''}` : `✓ ${p.approvals.map((a) => a.name.split(' ')[0]).join(' + ')} · ${timeOf(p.approvals.at(-1)?.decidedAt ?? p.createdAt)}`}</span></div>
						{:else}
							<p class="mono faint">Nothing decided yet.</p>
						{/each}
					</div>
				</Tabs.Panel>
				<Tabs.Panel value="desk">
					<div class="pad">
						{#each bundle!.incident.tickets as t (t.id)}
							<div class="r"><span>{t.title}</span><span class="mono faint">{t.key} · {t.status.replace('_', ' ')}</span></div>
						{:else}
							<p class="mono faint">No service-desk tickets.</p>
						{/each}
					</div>
				</Tabs.Panel>
				<Tabs.Panel value="evidence">
					<EvidenceTile incidentId={bundle!.incident.id} />
				</Tabs.Panel>
			{/snippet}
		</Tabs>
	</section>

	<Sheet kind="dialog" label="Record the decision for {bundle.incident.reference}" width="680px" open={!!deciding} onclose={closeDecision}>
		{#if deciding}
			<div class="dlg"><DecisionSheet incidentId={bundle.incident.id} reference={bundle.incident.reference} a={deciding} onrecorded={() => goto(`/incidents/${bundle!.incident.reference}`, { replaceState: true, invalidateAll: true }).then(() => (live.loadClocks(), load()))} oncancel={closeDecision} /></div>
		{/if}
	</Sheet>
{/if}

<style>
	.dlg {
		padding: 18px 22px 20px;
	}
	.empty {
		padding: 28px 22px;
		color: var(--muted);
		display: grid;
		gap: 12px;
		justify-items: start;
		font-size: 13px;
		margin: 0;
	}
	.decide {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		background: var(--pane);
	}
	.col {
		min-width: 0;
		border-left: 1px solid var(--line);
	}
	.col:first-child {
		border-left: 0;
	}
	.assess {
		padding: 16px 20px 18px;
		background: var(--lav-wash);
	}
	.assess .lbl {
		margin: 0 0 8px;
		font-size: 11.5px;
		color: var(--lav);
	}
	.assess h3 {
		margin: 0 0 8px;
		font-size: 17px;
		font-weight: 600;
	}
	.assess ul {
		margin: 0 0 14px;
		padding-left: 18px;
		color: var(--ink-2);
		font-size: 13px;
	}
	.tabs {
		background: var(--pane);
		flex: 1 0 auto;
	}
	.tabs :global(.tabl) {
		background: var(--bg);
		padding: 0 10px;
		overflow-x: auto;
		scrollbar-width: none;
	}
	.tabs :global(.tabc) {
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		flex: none;
	}
	.tabs :global(.tabc.on) {
		border-bottom-color: var(--teal);
	}
	.tabs :global(.tabt) {
		display: flex;
		align-items: center;
		gap: 8px;
		height: 40px;
		padding: 0 12px;
		font: 500 12.5px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		white-space: nowrap;
	}
	.tabs :global(.tabc.on .tabt) {
		color: var(--ink);
	}
	.tabs :global(.tabt:hover) {
		color: var(--ink);
	}
	.ct {
		font-size: 11px;
		color: var(--faint);
		border: 1px solid var(--line-strong);
		border-radius: 2px;
		padding: 2px 5px;
	}
	.pad {
		padding: 14px 22px 22px;
		max-width: 920px;
	}
	.r {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 10px 0;
		border-top: 1px solid var(--line);
		font-size: 13px;
		color: var(--ink-2);
	}
	.r:first-child {
		border-top: 0;
	}
	.r .mono {
		font-size: 11.5px;
		white-space: nowrap;
	}
	@media (max-width: 1000px) {
		.decide {
			grid-template-columns: minmax(0, 1fr);
		}
		.col {
			border-left: 0;
			border-top: 1px solid var(--line);
		}
		.col:first-child {
			border-top: 0;
		}
	}
	@media (max-width: 760px) {
		.pad {
			padding: 12px 14px 18px;
		}
		.r {
			flex-direction: column;
			gap: 3px;
		}
		.r .mono {
			white-space: normal;
		}
	}
</style>
