<script lang="ts">
	// The watch (DESIGN.md §4): a scope with four readouts. The watch dial in the centre (every incident's path
	// through the response, on a 24-hour face), the turnover's four numbered readouts at its corners.
	import { page } from '$app/state';
	import WatchDial from '$lib/components/console/WatchDial.svelte';
	import Turnover from '$lib/components/console/Turnover.svelte';
	import { api } from '$lib/api';
	import type { ProposalDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import { Popover } from '@skeletonlabs/skeleton-svelte';

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
	const holdId = $derived(page.url.searchParams.get('hold') ? 'focus-hold' : undefined);
	// the dial's centre already says how many are open and waiting: the line under the name only dates the shift
	$effect(() => {
		ui.setStation(page.url.pathname, 'The watch', `Shift since ${timeOf(shift.win.start, shift.tz)}`);
	});
	let keyOpen = $state(false);
</script>

<svelte:head><title>The watch · Nightjar SOC</title></svelte:head>

<div class="watch rack">
	<Turnover {pending} focusIncidentId={focus?.id ?? null} {holdId}>
		{#snippet centre()}
			<WatchDial win={shift.win} incidents={shift.incidents} log={shift.log} intake={shift.intake} tz={shift.tz} clocks={live.clocks} max={560} />
			<div class="keyrow">
				<Popover open={keyOpen} onOpenChange={(e) => (keyOpen = e.open)} positioning={{ placement: 'top' }} triggerBase="keyb" contentBase="nj-pop keypop" triggerAriaLabel="How to read the dial">
					{#snippet trigger()}Key{/snippet}
					{#snippet content()}
						<p class="legend"><span><i class="hi">━</i> high</span><span><i class="mid">━</i> medium</span><span><i class="pe">━</i> waiting for a person</span><span><i class="ho">┅</i> handover</span><span><i class="hi">▼</i> regulator deadline</span><span><i>✉</i> notice sent</span></p>
					{/snippet}
				</Popover>
			</div>
		{/snippet}
	</Turnover>
</div>

<style>
	.watch {
		padding-top: 22px;
	}
	.keyrow {
		display: flex;
		justify-content: center;
		margin-top: 8px;
	}
	.keyrow :global(.keyb) {
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.18em;
		color: var(--faint);
		padding: 6px 10px;
		border-radius: 2px;
		box-shadow: inset 0 0 0 1px var(--rule-2);
	}
	.keyrow :global(.keyb:hover) {
		color: var(--ink);
	}
	:global(.keypop) {
		padding: 12px 14px;
	}
	.legend {
		margin: 0;
		display: grid;
		gap: 8px;
		font: 600 13px/1 var(--ui);
		color: var(--ink-2);
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
</style>
