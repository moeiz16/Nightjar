<script lang="ts">
	// The watch (DESIGN.md §4): a scope with four readouts. The watch dial in the centre (every incident's path
	// through the response, on a 24-hour face), the turnover's four numbered readouts at its corners.
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import WatchDial from '$lib/components/console/WatchDial.svelte';
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
	$effect(() => {
		ui.setStation(page.url.pathname, 'The watch', `${plural(open, 'open incident')}${waiting ? ` · ${waiting} waiting for a person` : ''} · the shift since ${timeOf(shift.win.start, shift.tz)}`);
	});
</script>

<svelte:head><title>The watch · Nightjar SOC</title></svelte:head>

<div class="watch rack">
	<Turnover {pending} focusIncidentId={focus?.id ?? null} {holdId}>
		{#snippet centre()}
			<WatchDial win={shift.win} incidents={shift.incidents} log={shift.log} intake={shift.intake} tz={shift.tz} clocks={live.clocks} max={560} />
			<p class="legend" aria-hidden="true"><span><i class="hi">━</i> high</span><span><i class="mid">━</i> medium</span><span><i class="pe">━</i> waiting for a person</span><span><i class="ho">┅</i> handover</span><span><i class="hi">▼</i> deadline</span><a class="lnk" href="/brief">The morning brief</a></p>
		{/snippet}
	</Turnover>
</div>

<style>
	.watch {
		padding-top: 22px;
	}
	.legend {
		margin: 10px 0 0;
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 6px 16px;
		font: 600 12.5px/1 var(--ui);
		color: var(--faint);
	}
	.legend i {
		font-style: normal;
		margin-right: 5px;
	}
	.legend .hi {
		color: var(--acc);
	}
	.legend .mid {
		color: var(--soon);
	}
	.legend .pe {
		color: var(--person);
	}
	.legend .ho {
		color: var(--soon);
	}
	@media (max-width: 760px) {
		.legend {
			display: none;
		}
	}
</style>
