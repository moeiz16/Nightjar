<script lang="ts">
	// The watch (DESIGN.md §0.2): band 1 is the shift chart (every incident's path through the response, on the
	// shift axis), band 2 the turnover (the handover sheet, written live, in the role's sections).
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import Band from '$lib/components/ui/Band.svelte';
	import ShiftChart from '$lib/components/console/ShiftChart.svelte';
	import Turnover from '$lib/components/console/Turnover.svelte';
	import { api } from '$lib/api';
	import type { ProposalDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import { plural } from '$lib/util/labels';

	let pending = $state<ProposalDoc[]>([]);
	$effect(() => {
		const load = () => api.approvals.list({ status: 'pending' }).then((r) => (pending = r.items)).catch(() => undefined);
		load();
		const offs = [live.on('approval.decided', load), live.on('approval.requested', load)];
		return () => offs.forEach((f) => f());
	});

	// The incident ⌘K scopes to: the oldest item waiting for you, else the most urgent clock, else the newest open one.
	const focus = $derived.by(() => {
		const mine = pending.filter((p) => session.role && p.requiredRoles.includes(session.role));
		const oldest = [...(mine.length ? mine : pending)].sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0];
		const ref = oldest?.incidentReference ?? live.clocks.find((c) => c.state !== 'submitted')?.incidentReference ?? live.incidents[0]?.reference ?? null;
		return live.incidents.find((i) => i.reference === ref) ?? null;
	});
	$effect(() => {
		ui.scope = focus ? { id: focus.id, reference: focus.reference } : null;
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
	const holdId = $derived(page.url.searchParams.get('hold') ? 'focus-hold' : undefined);
	const open = $derived(live.incidents.length);
	const waiting = $derived(live.incidents.filter((i) => i.pendingApprovals > 0).length);
</script>

<svelte:head><title>The watch · Nightjar SOC</title></svelte:head>

<h1 class="sr">The watch</h1>
<Band title="the shift" sub="{timeOf(shift.win.start, shift.tz)} to now · {plural(open, 'open incident')}{waiting ? ` · ${waiting} waiting for a person` : ''}" flush>
	{#snippet actions()}
		<span class="legend mono hide-s" aria-hidden="true"><i class="r">━</i> high <i class="a">━</i> medium <i class="l">━</i> waiting for a person <i class="h">┃</i> handover</span>
	{/snippet}
	<ShiftChart win={shift.win} incidents={shift.incidents} log={shift.log} intake={shift.intake} tz={shift.tz} />
</Band>
<Band title="turnover" sub="what the next person needs" flush>
	{#snippet actions()}<a class="lnk hide-s" href="/brief">the morning brief</a>{/snippet}
	<Turnover {pending} focusIncidentId={focus?.id ?? null} {holdId} />
</Band>

<style>
	.legend {
		font-size: 11px;
		color: var(--faint);
		display: inline-flex;
		gap: 6px;
		align-items: center;
	}
	.legend i {
		font-style: normal;
		margin-left: 6px;
	}
	.legend .r {
		color: var(--red);
	}
	.legend .a {
		color: var(--amber);
	}
	.legend .l {
		color: var(--lav);
	}
	.legend .h {
		color: var(--amber);
	}
</style>
