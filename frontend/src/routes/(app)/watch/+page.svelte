<script lang="ts">
	// The watch (DESIGN.md §5): the live console in the person's role layout. 1–4 tiles with hairline gutters;
	// ⌘\ split, ⌘[ ⌘] focus, ⌥W close, ⌘⇧↵ zoom; the tile title swaps what a tile shows.
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import Pane from '$lib/components/shell/Pane.svelte';
	import EventRiver from '$lib/components/console/EventRiver.svelte';
	import IncidentView from '$lib/components/console/IncidentView.svelte';
	import AwaitingTile from '$lib/components/console/AwaitingTile.svelte';
	import HuntPanel from '$lib/components/console/HuntPanel.svelte';
	import ActivityPanel from '$lib/components/console/ActivityPanel.svelte';
	import EvidenceTile from '$lib/components/console/EvidenceTile.svelte';
	import BehaviourPanel from '$lib/components/console/BehaviourPanel.svelte';
	import { api } from '$lib/api';
	import type { ProposalDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui, TILE_LABEL, type TileKind } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import type { IncidentBundle } from '$lib/util/incident';
	import { plural } from '$lib/util/labels';

	let pending = $state<ProposalDoc[]>([]);
	let bundle = $state<IncidentBundle | null>(null);
	let selectedKey = $state<string | null>(null);

	$effect(() => {
		const load = () => api.approvals.list({ status: 'pending' }).then((r) => (pending = r.items)).catch(() => undefined);
		load();
		const offs = [live.on('approval.decided', load), live.on('approval.requested', load)];
		return () => offs.forEach((f) => f());
	});

	// The incident with the oldest item waiting for you, else the most urgent clock, else the newest open one.
	const focusRef = $derived.by(() => {
		const mine = pending.filter((p) => session.role && p.requiredRoles.includes(session.role));
		const oldest = [...(mine.length ? mine : pending)].sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0];
		if (oldest) return oldest.incidentReference;
		const clock = live.clocks.find((c) => c.state !== 'submitted');
		if (clock) return clock.incidentReference;
		return live.incidents[0]?.reference ?? null;
	});
	$effect(() => {
		if (bundle) ui.scope = { id: bundle.incident.id, reference: bundle.incident.reference };
	});
	onDestroy(() => (ui.statusNote = ''));
	$effect(() => {
		const log = live.shiftLog;
		if (!log.length) {
			ui.statusNote = '';
			return;
		}
		const since = log.at(-1)!.at;
		const incs = log.filter((e) => e.kind === 'incident').length;
		const contained = live.incidents.filter((i) => i.status === 'monitoring').length;
		ui.statusNote = `Since ${timeOf(since)}: ${plural(incs, 'incident')}, ${contained} contained, ${pending.length} waiting for you`;
	});

	function why(p: ProposalDoc) {
		const row = bundle?.runs.flatMap((r) => r.steps.map((s) => ({ key: `${r.id}:${s.seq}`, s }))).filter((x) => x.s.kind === 'proposal').at(-1);
		if (row) selectedKey = row.key;
		const i = ui.tiles.indexOf('incident');
		if (i >= 0) ui.focus = i;
		void p;
	}

	const WIDTH: Record<TileKind, string> = { events: '300px', awaiting: '344px', incident: 'minmax(0, 1fr)', hunt: 'minmax(0, 1fr)', activity: 'minmax(0, 1fr)', evidence: '360px', behaviour: 'minmax(0, 1fr)' };
	const visible = $derived(ui.zoomed ? [ui.tiles[ui.focus] ?? 'incident'] : ui.tiles);
	const cols = $derived.by(() => {
		const ws = visible.map((k) => WIDTH[k]);
		if (!ws.some((w) => w.includes('1fr'))) ws[ws.length - 1] = 'minmax(0, 1fr)';
		return ws.join(' ');
	});
	function swapTitle(i: number) {
		ui.focus = ui.zoomed ? ui.focus : i;
		ui.openPalette('tile');
	}
	// mobile: one tile at a time; swipe or the dots switch tiles
	let sx = 0;
	function touchstart(e: TouchEvent) {
		sx = e.touches[0]?.clientX ?? 0;
	}
	function touchend(e: TouchEvent) {
		const dx = (e.changedTouches[0]?.clientX ?? 0) - sx;
		if (Math.abs(dx) > 60) ui.mobileTile = Math.max(0, Math.min(ui.tiles.length - 1, ui.mobileTile + (dx < 0 ? 1 : -1)));
	}
	const holdId = $derived(page.url.searchParams.get('hold') ? 'focus-hold' : undefined);
	const subFor = (k: TileKind) =>
		k === 'events' ? 'live' : k === 'incident' ? (bundle?.incident.reference ?? '') : k === 'awaiting' ? String(pending.filter((p) => session.role && p.requiredRoles.includes(session.role)).length) : '';
</script>

<svelte:head><title>{bundle ? `${bundle.incident.reference} · ` : ''}The watch · Nightjar SOC</title></svelte:head>

<h1 class="sr">The watch</h1>
<!-- svelte-ignore a11y_no_static_element_interactions (swipe is a shortcut; the dots and ⌘[ ] do the same) -->
<div class="tiles" style="grid-template-columns:{cols}" ontouchstart={touchstart} ontouchend={touchend}>
	{#each visible as kind, vi (kind + vi)}
		{@const i = ui.zoomed ? ui.focus : vi}
		<!-- svelte-ignore a11y_no_static_element_interactions (focus follows the pointer; keyboard focus uses focusin) -->
		<div class="slot" class:mshow={ui.mobileTile === i} onfocusin={() => (ui.focus = i)} onpointerdown={() => (ui.focus = i)}>
			<Pane title={TILE_LABEL[kind]} sub={subFor(kind)} focus={ui.focus === i} onTitle={() => swapTitle(i)} label={TILE_LABEL[kind]}>
				{#snippet actions()}
					{#if kind === 'events'}<span class="teal">{(live.eventsPerMinute || 0).toLocaleString('en-GB')}/min</span>{/if}
					{#if kind === 'incident' && bundle}<span class="hide-s">opened {timeOf(bundle.incident.firstSeen, undefined, true)}</span><span class="hide-s">split <kbd>⌘</kbd><kbd>\</kbd></span>{/if}
					{#if kind === 'awaiting'}<span>{session.name}</span>{/if}
				{/snippet}
				{#if kind === 'events'}
					<EventRiver />
				{:else if kind === 'incident'}
					{#if focusRef}
						<IncidentView param={focusRef} bind:bundle bind:selectedKey />
					{:else}
						<div class="quiet">
							<p class="big">Nothing needs a person. The watch is quiet.</p>
							<p class="mono faint">Try a saved hunt: <a href="/hunt/saved">laptops that opened the same link</a></p>
						</div>
					{/if}
				{:else if kind === 'awaiting'}
					<AwaitingTile {bundle} onwhy={why} {holdId} />
				{:else if kind === 'hunt'}
					<HuntPanel compact />
				{:else if kind === 'activity'}
					<ActivityPanel compact />
				{:else if kind === 'evidence'}
					<EvidenceTile incidentId={bundle?.incident.id ?? null} />
				{:else if kind === 'behaviour'}
					<BehaviourPanel compact />
				{/if}
			</Pane>
		</div>
	{/each}
</div>
<nav class="dots" aria-label="Tiles">
	{#each ui.tiles as k, i (k + i)}
		<button type="button" class:on={ui.mobileTile === i} aria-label="Show {TILE_LABEL[k]}" aria-current={ui.mobileTile === i} onclick={() => ((ui.mobileTile = i), (ui.focus = i))}></button>
	{/each}
</nav>

<style>
	.tiles {
		flex: 1;
		display: grid;
		gap: 1px;
		background: var(--line);
		min-height: 0;
	}
	.slot {
		display: flex;
		min-width: 0;
		min-height: 0;
	}
	.slot > :global(.pane) {
		flex: 1;
	}
	.quiet {
		padding: 60px 28px;
	}
	.quiet .big {
		font: 500 20px/1.35 var(--sans);
		margin: 0 0 10px;
	}
	.dots {
		display: none;
	}
	@media (max-width: 1180px) {
		.tiles {
			grid-template-columns: 280px minmax(0, 1fr) !important;
		}
		.slot:nth-child(n + 3) {
			display: none;
		}
	}
	@media (max-width: 760px) {
		.tiles {
			grid-template-columns: minmax(0, 1fr) !important;
		}
		.slot,
		.slot:nth-child(n + 3) {
			display: none;
		}
		.slot.mshow {
			display: flex;
		}
		.dots {
			display: flex;
			justify-content: center;
			gap: 10px;
			padding: 10px 0 14px;
			background: var(--bg);
			border-top: 1px solid var(--line);
		}
		.dots button {
			width: 8px;
			height: 8px;
			border-radius: 50%;
			background: var(--line-strong);
		}
		.dots button.on {
			background: var(--teal);
		}
		:global(.hide-s) {
			display: none;
		}
	}
</style>
