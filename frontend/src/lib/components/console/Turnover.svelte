<script lang="ts">
	// The turnover (DESIGN.md §4): the handover, written live, as four numbered readouts set at the corners of the
	// watch dial like the data blocks around a scope: what is waiting on you, the clocks, intake, and what happened
	// since you last looked. Roles choose the readouts (analysts hunt, auditors check the chain). Under 1180 px the
	// dial sits on top and the readouts go 2 × 2; phones read them as an accordion.
	import type { Snippet } from 'svelte';
	import { Accordion } from '@skeletonlabs/skeleton-svelte';
	import AwaitingTile from './AwaitingTile.svelte';
	import ClockList from './ClockList.svelte';
	import EventRiver from './EventRiver.svelte';
	import HuntPanel from './HuntPanel.svelte';
	import ActivityPanel from './ActivityPanel.svelte';
	import EvidenceTile from './EvidenceTile.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui, SECTION_LABEL, type SectionKind } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import type { ProposalDoc } from '$lib/api/types';

	let { pending, focusIncidentId, holdId, centre }: { pending: ProposalDoc[]; focusIncidentId: string | null; holdId?: string; centre?: Snippet } = $props();

	const refOf = (id: string | null) => live.incidents.find((i) => i.id === id)?.reference ?? null;
	const sub = (k: SectionKind) =>
		k === 'awaiting'
			? String(pending.length)
			: k === 'clocks'
				? String(live.clocks.filter((c) => c.state !== 'not_required').length)
				: k === 'intake'
					? `${(live.eventsPerMinute || 0).toLocaleString('en-GB')}/min`
					: k === 'log'
						? String(live.shiftLog.length)
						: '';
	let phoneOpen = $state<string[]>(['awaiting', 'hunt', 'activity']);
</script>

{#snippet body(k: SectionKind)}
	{#if k === 'awaiting'}
		<AwaitingTile bundle={null} {holdId} />
	{:else if k === 'clocks'}
		<div class="pad"><ClockList clocks={live.clocks} now={live.now} /></div>
	{:else if k === 'intake'}
		<EventRiver />
	{:else if k === 'log'}
		<ol class="log">
			{#each live.shiftLog as e, i (e.at + i)}
				<li>
					<span class="mono t">{timeOf(e.at)}</span>
					{#if refOf(e.refId)}<a href="/incidents/{refOf(e.refId)}">{e.text}</a>{:else}<span>{e.text}</span>{/if}
				</li>
			{:else}
				<li class="faint">Nothing new since you last looked.</li>
			{/each}
		</ol>
	{:else if k === 'hunt'}
		<HuntPanel compact />
	{:else if k === 'activity'}
		<ActivityPanel compact />
	{:else if k === 'evidence'}
		<EvidenceTile incidentId={focusIncidentId} />
	{/if}
{/snippet}

<div class="scope" style="--cols:{ui.sections.length}">
	{#if centre}<div class="centre">{@render centre()}</div>{/if}
	{#each ui.sections as k, i (k)}
		<section class="ro r{i + 1}" aria-labelledby="to-{k}">
			<h2 id="to-{k}" class="hd"><span class="n">{i + 1}</span><span class="tt">{SECTION_LABEL[k]}</span>{#if sub(k)}<span class="ct">{sub(k)}</span>{/if}</h2>
			<div class="b">{@render body(k)}</div>
		</section>
	{/each}
</div>

<div class="phone">
	<Accordion value={phoneOpen} onValueChange={(e) => (phoneOpen = e.value)} multiple collapsible spaceY="space-y-0" rounded="rounded-none">
		{#each ui.sections as k, i (k)}
			<Accordion.Item value={k} controlPadding="py-3 px-4" controlRounded="rounded-none" controlHover="hover:bg-[var(--raised)]" panelPadding="p-0" classes="acc">
				{#snippet control()}<span class="ach"><span class="n">{i + 1}</span><span class="eng">{SECTION_LABEL[k]}</span>{#if sub(k)}<span class="faint mono">{sub(k)}</span>{/if}</span>{/snippet}
				{#snippet panel()}{@render body(k)}{/snippet}
			</Accordion.Item>
		{/each}
	</Accordion>
</div>

<style>
	.scope {
		display: grid;
		grid-template-columns: minmax(300px, 1fr) minmax(0, auto) minmax(300px, 1fr);
		grid-template-areas: 'r1 c r2' 'r3 c r4';
		grid-template-rows: auto auto;
		gap: 18px 22px;
		padding: 0 var(--gut);
		align-items: start;
	}
	.centre {
		grid-area: c;
		align-self: start;
		min-width: 0;
		width: min(640px, 44vw);
	}
	.r1 {
		grid-area: r1;
	}
	.r2 {
		grid-area: r2;
	}
	.r3 {
		grid-area: r3;
	}
	.r4 {
		grid-area: r4;
	}
	.ro {
		min-width: 0;
		display: flex;
		flex-direction: column;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--rule-2);
	}
	.r1 {
		border-top-color: var(--acc);
	}
	.hd {
		margin: 0;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 16px 11px;
		border-bottom: 1px solid var(--rule);
		white-space: nowrap;
		min-width: 0;
	}
	.tt {
		font: 700 19px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--ink);
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.ct {
		margin-left: auto;
		font: 500 12.5px/1 var(--code);
		color: var(--dim);
	}
	.n {
		display: inline-grid;
		place-items: center;
		width: 22px;
		height: 22px;
		font: 600 12px/1 var(--code);
		color: var(--acc-ink);
		background: var(--acc);
		border-radius: 1px;
		flex: none;
	}
	.b {
		min-width: 0;
		max-height: 330px;
		overflow: auto;
		scrollbar-width: thin;
		scrollbar-color: var(--rule-2) transparent;
	}
	.r1 .b {
		max-height: 420px;
	}
	.pad {
		padding: 6px 16px 12px;
	}
	.log {
		list-style: none;
		margin: 0;
		padding: 4px 16px 12px;
	}
	.log li {
		display: grid;
		grid-template-columns: 46px minmax(0, 1fr);
		gap: 10px;
		padding: 9px 0;
		border-top: 1px solid var(--rule);
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.log li:first-child {
		border-top: 0;
	}
	.log .t {
		font-size: 12px;
		color: var(--faint);
		padding-top: 1px;
	}
	.log a {
		text-decoration: none;
	}
	.log a:hover {
		color: var(--ink);
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	.phone {
		display: none;
	}
	.phone :global(.acc) {
		border-top: 1px solid var(--rule);
		background: var(--plate);
	}
	.ach {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.ach .eng {
		font-size: 15px;
		color: var(--ink);
	}
	@media (max-width: 1180px) {
		.scope {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
			grid-template-areas: 'c c' 'r1 r2' 'r3 r4';
		}
		.centre {
			width: min(640px, 100%);
			justify-self: center;
		}
	}
	@media (max-width: 760px) {
		.scope {
			display: block;
			padding: 0;
		}
		.scope .ro {
			display: none;
		}
		.phone {
			display: block;
			margin-top: 8px;
		}
		.b {
			max-height: none;
		}
	}
</style>
