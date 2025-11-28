<script lang="ts">
	// The turnover (DESIGN.md §0.2): the handover sheet, written live. Numbered sections in one band, ruled, never
	// cards: what is waiting on you, the clocks, intake, and what happened since you last looked. Roles choose the
	// sections (analysts hunt, auditors check the chain). Phones read it as an accordion.
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

	let { pending, focusIncidentId, holdId }: { pending: ProposalDoc[]; focusIncidentId: string | null; holdId?: string } = $props();

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
				<li class="mono faint">Nothing new since you last looked.</li>
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

<div class="turnover" style="--cols:{ui.sections.length}">
	{#each ui.sections as k, i (k)}
		<section class="sec" aria-labelledby="to-{k}">
			<h3 id="to-{k}" class="hd mono"><span class="n">{i + 1}</span>{SECTION_LABEL[k]}{#if sub(k)}<span class="faint"> · {sub(k)}</span>{/if}</h3>
			<div class="b">{@render body(k)}</div>
		</section>
	{/each}
</div>

<div class="phone">
	<Accordion value={phoneOpen} onValueChange={(e) => (phoneOpen = e.value)} multiple collapsible spaceY="space-y-0" rounded="rounded-none">
		{#each ui.sections as k, i (k)}
			<Accordion.Item value={k} controlPadding="py-3 px-4" controlRounded="rounded-none" controlHover="hover:bg-[var(--raised)]" panelPadding="p-0" classes="acc">
				{#snippet control()}<span class="mono"><span class="teal">{i + 1}</span> {SECTION_LABEL[k]}{#if sub(k)}<span class="faint"> · {sub(k)}</span>{/if}</span>{/snippet}
				{#snippet panel()}{@render body(k)}{/snippet}
			</Accordion.Item>
		{/each}
	</Accordion>
</div>

<style>
	.turnover {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		background: var(--pane);
	}
	.sec {
		min-width: 0;
		border-left: 1px solid var(--line);
		display: flex;
		flex-direction: column;
	}
	.sec:first-child {
		border-left: 0;
	}
	.hd {
		margin: 0;
		display: flex;
		align-items: baseline;
		gap: 8px;
		padding: 12px 16px 10px;
		font-size: 12px;
		font-weight: 600;
		color: var(--ink);
		border-bottom: 1px solid var(--line);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.hd .faint {
		font-weight: 400;
	}
	.n {
		display: inline-grid;
		place-items: center;
		width: 18px;
		height: 18px;
		border: 1px solid var(--teal-line);
		border-radius: 2px;
		color: var(--teal);
		font-size: 11px;
	}
	.b {
		min-width: 0;
		max-height: 560px;
		overflow: auto;
		scrollbar-width: thin;
	}
	.pad {
		padding: 6px 16px 12px;
	}
	.log {
		list-style: none;
		margin: 0;
		padding: 6px 16px 14px;
	}
	.log li {
		display: grid;
		grid-template-columns: 44px minmax(0, 1fr);
		gap: 8px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
		font-size: 12.5px;
		color: var(--ink-2);
	}
	.log li:first-child {
		border-top: 0;
	}
	.log .t {
		font-size: 11.5px;
		color: var(--faint);
	}
	.log a {
		text-decoration: none;
	}
	.log a:hover {
		color: var(--teal);
		text-decoration: underline;
	}
	.phone {
		display: none;
	}
	.phone :global(.acc) {
		border-top: 1px solid var(--line);
		background: var(--pane);
	}
	@media (max-width: 1180px) {
		.turnover {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.sec:nth-child(3) {
			border-left: 0;
		}
		.sec:nth-child(n + 3) {
			border-top: 1px solid var(--line);
		}
	}
	@media (max-width: 760px) {
		.turnover {
			display: none;
		}
		.phone {
			display: block;
		}
		.b {
			max-height: none;
		}
	}
</style>
