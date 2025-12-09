<script lang="ts">
	// Incident (DESIGN.md §4): strip, orders, log. The incident as one flight strip (who, what, reporting, the stage
	// rail, ATT&CK), then the orders waiting on you directly under it, then the record on an engraved index (Skeleton
	// Tabs): what happened and why (the watch log replay) · clocks and notices · actions decided · service desk ·
	// evidence. Never a side column. The materiality decision is a Modal.
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { Tabs } from '@skeletonlabs/skeleton-svelte';
	import Plate from '$lib/components/ui/Plate.svelte';
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
	import { plural, regimeShort, STATUS_WORD } from '$lib/util/labels';

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
		const i = bundle?.incident;
		ui.setStation(page.url.pathname, i ? i.reference : 'Incident', i ? `Incident · opened ${timeOf(i.firstSeen)} · ${(STATUS_WORD[i.status] ?? i.status).toLowerCase()}` : undefined);
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
	<Plate title="Incident" sub={param}>
		<div class="empty"><p>{error}</p><a class="lnk" href="/watch">Back to the watch</a></div>
	</Plate>
{:else if !bundle}
	<Plate title="Incident" sub={param}><p class="empty faint" aria-live="polite">Opening the incident…</p></Plate>
{:else}
	<div class="stripw rack">
		<div class="back"><a class="lnk" href="/watch">← Back to the watch</a></div>
		<IncidentHeader incident={bundle.incident} times={stageTimes(bundle)} clocks={live.clocks} pending={pending.length} />
	</div>

	{#if pending.length || open.length}
		<Plate title="Orders · waiting on you" sub={pending.length ? plural(pending.length, 'decision') : 'a regulator decision'} n="‖" flush class="orders">
			<div class="decide" style="--cols:{Math.min(3, pending.length + open.length)}">
				{#each pending as p, i (p.id)}
					<div class="col"><ApprovalCard {p} {people} index={i + 1} total={pending.length} holdId={'hold-' + p.id} startDeclining={declineParam === p.id} onwhy={() => why(p)} onchange={onChange} /></div>
				{/each}
				{#each open as r (r.regime)}
					<div class="col assess">
						<p class="lbl eng"><span aria-hidden="true">◆</span> {regimeShort(r.regime)} · {r.entityName} · to assess</p>
						<h3>Is this {r.regime === 'sec_8k' ? 'material' : 'reportable'} to {r.entityName}?</h3>
						<ul>{#each r.proposal.reasons as reason (reason)}<li>{reason}</li>{/each}</ul>
						<a class="btn preset-filled-surface-950-50" href="?decide={r.regime}" data-sveltekit-noscroll>Record the decision</a>
					</div>
				{/each}
			</div>
		</Plate>
	{/if}

	<section class="tabs rack" aria-label="The incident record">
		<Tabs
			value={tab}
			onValueChange={(e) => (tab = e.value ?? 'story')}
			listBorder="border-b border-[var(--rule)]"
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
							<p class="faint">Nothing decided yet.</p>
						{/each}
					</div>
				</Tabs.Panel>
				<Tabs.Panel value="desk">
					<div class="pad">
						{#each bundle!.incident.tickets as t (t.id)}
							<div class="r"><span>{t.title}</span><span class="mono faint">{t.key} · {t.status.replace('_', ' ')}</span></div>
						{:else}
							<p class="faint">No service-desk tickets.</p>
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
		color: var(--dim);
		display: grid;
		gap: 12px;
		justify-items: start;
		font-size: 14px;
		margin: 0;
	}
	.stripw {
		margin: 22px var(--gut) 0;
	}
	.back {
		margin-bottom: 10px;
	}
	:global(.orders .ph .n) {
		background: var(--person);
		color: var(--bg);
	}
	.decide {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		box-shadow: inset 0 2px 0 var(--person);
	}
	.col {
		min-width: 0;
		border-left: 1px solid var(--rule);
	}
	.col:first-child {
		border-left: 0;
	}
	.assess {
		padding: 18px 20px 20px;
		background: var(--person-wash);
	}
	.assess .lbl {
		margin: 0 0 10px;
		font-size: 12.5px;
		color: var(--person);
	}
	.assess h3 {
		margin: 0 0 10px;
		font: 800 25px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.assess ul {
		margin: 0 0 16px;
		padding-left: 18px;
		color: var(--ink-2);
		font-size: 14px;
	}
	.tabs {
		margin: 30px var(--gut) 0;
		flex: 1 0 auto;
		min-width: 0;
	}
	.tabs :global([data-scope='tabs'][data-part='root']),
	.tabs :global(.tabl) {
		min-width: 0;
		max-width: 100%;
	}
	.tabs :global(.tabl) {
		gap: 4px;
		overflow-x: auto;
		scrollbar-width: none;
		border-bottom: 2px solid var(--rule-2) !important;
	}
	.tabs :global(.tabc) {
		flex: none;
		margin-bottom: -2px;
	}
	.tabs :global(.tabt) {
		display: flex;
		align-items: center;
		gap: 9px;
		height: 44px;
		padding: 0 16px;
		font: 700 15px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--dim);
		white-space: nowrap;
		background: var(--plate);
		box-shadow: inset 0 0 0 1px var(--rule);
	}
	.tabs :global(.tabc.on .tabt) {
		color: var(--ink);
		background: radial-gradient(120% 120% at 50% 100%, color-mix(in srgb, var(--acc) 26%, transparent), var(--plate) 70%);
		box-shadow:
			inset 0 0 0 1px var(--acc-line),
			inset 0 -3px 0 var(--acc);
	}
	.tabs :global(.tabt:hover) {
		color: var(--ink);
	}
	.ct {
		font: 500 11.5px/1 var(--code);
		letter-spacing: 0;
		color: var(--faint);
		box-shadow: inset 0 0 0 1px var(--rule-2);
		padding: 3px 5px;
	}
	.tabs :global([data-part='content']) {
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 0;
	}
	.pad {
		padding: 14px 22px 22px;
		max-width: 920px;
	}
	.r {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 11px 0;
		border-top: 1px solid var(--rule);
		font-size: 14px;
		color: var(--ink-2);
	}
	.r:first-child {
		border-top: 0;
	}
	.r .mono {
		font-size: 12px;
		white-space: nowrap;
	}
	@media (max-width: 1000px) {
		.decide {
			grid-template-columns: minmax(0, 1fr);
		}
		.col {
			border-left: 0;
			border-top: 1px solid var(--rule);
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
