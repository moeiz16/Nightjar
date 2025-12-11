<script lang="ts">
	// The turnover (DESIGN.md §4): the handover as four numbered readouts at the corners of the watch dial, like the
	// data blocks around a scope. Each readout shows one number and the one item that matters most; the whole list
	// opens in a sheet on click (progressive disclosure: the count, then the top item, then everything). Roles
	// choose the readouts (analysts hunt, auditors check the chain). Under 1180 px the dial sits on top and the
	// readouts go 2 × 2; phones stack them under the dial.
	import type { Snippet } from 'svelte';
	import AwaitingTile from './AwaitingTile.svelte';
	import ClockList from './ClockList.svelte';
	import EventRiver from './EventRiver.svelte';
	import HuntPanel from './HuntPanel.svelte';
	import ActivityPanel from './ActivityPanel.svelte';
	import EvidenceTile from './EvidenceTile.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui, SECTION_LABEL, type SectionKind } from '$lib/stores/ui.svelte';
	import { clockState, hm, secondsLeft, STATE_GLYPH, timeOf } from '$lib/time/clock';
	import { actionTitle } from '$lib/util/approvals';
	import { clockLongLabel } from '$lib/util/labels';
	import type { ProposalDoc } from '$lib/api/types';

	let { pending, focusIncidentId, holdId, centre }: { pending: ProposalDoc[]; focusIncidentId: string | null; holdId?: string; centre?: Snippet } = $props();

	const refOf = (id: string | null) => live.incidents.find((i) => i.id === id)?.reference ?? null;
	const running = $derived(live.clocks.filter((c) => c.state !== 'not_required' && c.state !== 'submitted'));
	const RANK: Record<string, number> = { overdue: 0, due_soon: 1, on_track: 2, not_started: 3 };
	const urgent = $derived(
		[...running].sort((a, b) => (RANK[clockState(a, live.now)] ?? 9) - (RANK[clockState(b, live.now)] ?? 9) || (a.dueAt ?? '9').localeCompare(b.dueAt ?? '9'))[0] ?? null
	);
	const oldest = $derived([...pending].sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0] ?? null);
	const notable = $derived(live.river.find((l) => l.anomaly || l.incidentId) ?? null);

	/** One readout: a number, what it counts, and the one line worth reading first. */
	interface Read { n: string; unit: string; line: string; tone?: 'person' | 'soon' | 'quiet' }
	const read = (k: SectionKind): Read => {
		if (k === 'awaiting') return pending.length ? { n: String(pending.length), unit: pending.length === 1 ? 'decision' : 'decisions', line: actionTitle(oldest!), tone: 'person' } : { n: '0', unit: 'decisions', line: 'Nothing waiting', tone: 'quiet' };
		if (k === 'clocks') {
			if (!urgent) return { n: '0', unit: 'running', line: 'No clocks running', tone: 'quiet' };
			const st = clockState(urgent, live.now);
			const left = st === 'on_track' || st === 'due_soon' ? hm(secondsLeft(urgent.dueAt, live.now)) : st === 'not_started' ? 'to decide' : 'overdue';
			return { n: String(running.length), unit: 'running', line: `${STATE_GLYPH[st]} ${clockLongLabel(urgent).split(' · ')[0]} · ${left}`, tone: st === 'due_soon' || st === 'overdue' ? 'soon' : undefined };
		}
		if (k === 'intake') return { n: Math.round(live.eventsPerMinute || 0).toLocaleString('en-GB'), unit: 'a minute', line: notable ? `◇ ${notable.text}` : 'All routine', tone: notable ? 'soon' : 'quiet' };
		if (k === 'log') {
			const last = live.shiftLog[0];
			return { n: String(live.shiftLog.length), unit: 'new', line: last ? `${timeOf(last.at)} · ${last.text}` : 'Nothing new', tone: last ? undefined : 'quiet' };
		}
		if (k === 'hunt') return { n: '', unit: '', line: 'Ask in plain words' };
		if (k === 'activity') return { n: '', unit: '', line: 'Sealed, newest first' };
		return { n: '', unit: '', line: 'What the detections cited' };
	};

	let opened = $state<SectionKind | null>(null);
	// ?hold=… arrives from the lamp and ⌘K: open the decisions straight away so the hold can take focus
	$effect(() => {
		if (holdId && pending.length && ui.sections.includes('awaiting')) opened = 'awaiting';
	});
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

<div class="scope">
	{#if centre}<div class="centre">{@render centre()}</div>{/if}
	{#each ui.sections as k, i (k)}
		{@const r = read(k)}
		<section class="ro r{i + 1} {r.tone ?? ''}" aria-labelledby="to-{k}">
			<button class="open" type="button" onclick={() => (opened = k)} aria-haspopup="dialog" aria-describedby="to-{k}-l">
				<span class="hd"><span class="n" aria-hidden="true">{i + 1}</span><span id="to-{k}" class="tt">{SECTION_LABEL[k]}</span><span class="more" aria-hidden="true">→</span></span>
				{#if r.n}<span class="big"><b class="mono">{r.n}</b><small>{r.unit}</small></span>{/if}
				<span id="to-{k}-l" class="one">{r.line}</span>
			</button>
		</section>
	{/each}
</div>

<Sheet kind="sheet" label={opened ? SECTION_LABEL[opened] : 'Readout'} open={!!opened} onclose={() => (opened = null)}>
	{#if opened}
		<div class="sh">
			<p class="eng sht">{SECTION_LABEL[opened]}</p>
			<div class="shb">{@render body(opened)}</div>
		</div>
	{/if}
</Sheet>

<style>
	.scope {
		display: grid;
		grid-template-columns: minmax(240px, 1fr) minmax(0, auto) minmax(240px, 1fr);
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
		align-self: end;
	}
	.r4 {
		grid-area: r4;
		align-self: end;
	}
	.ro {
		min-width: 0;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--rule-2);
	}
	.r1 {
		border-top-color: var(--acc);
	}
	.open {
		display: grid;
		gap: 12px;
		width: 100%;
		padding: 14px 16px 16px;
		text-align: left;
		color: inherit;
	}
	.open:hover {
		background: var(--raised);
	}
	.open:hover .more {
		color: var(--acc-hi);
		transform: translateX(3px);
	}
	.hd {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.tt {
		font: 700 17px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--ink-2);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.more {
		margin-left: auto;
		color: var(--faint);
		transition: transform 0.16s ease;
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
	.big {
		display: flex;
		align-items: baseline;
		gap: 8px;
	}
	.big b {
		font: 400 48px/0.9 var(--code);
		letter-spacing: -0.02em;
		color: var(--ink);
	}
	.big small {
		font: 700 13px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--faint);
	}
	.person .big b {
		color: var(--person);
		text-shadow: 0 0 22px var(--acc-glow);
	}
	.quiet .big b {
		color: var(--dim);
	}
	.one {
		font: 600 14px/1.35 var(--ui);
		color: var(--ink);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.quiet .one {
		color: var(--faint);
		font-weight: 500;
	}
	.soon .one {
		color: var(--soon);
	}
	.sh {
		padding: 16px 0 6px;
	}
	.sht {
		margin: 0;
		padding: 0 20px 12px;
		font-size: 15px;
		color: var(--ink);
		border-bottom: 1px solid var(--rule);
	}
	.shb {
		max-height: min(70vh, 640px);
		overflow: auto;
	}
	.pad {
		padding: 6px 20px 12px;
	}
	.log {
		list-style: none;
		margin: 0;
		padding: 4px 20px 12px;
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
	@media (max-width: 1180px) {
		.scope {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
			grid-template-areas: 'c c' 'r1 r2' 'r3 r4';
		}
		.r3,
		.r4 {
			align-self: start;
		}
		.centre {
			width: min(640px, 100%);
			justify-self: center;
		}
	}
	@media (max-width: 760px) {
		.scope {
			gap: 10px;
			padding: 0 var(--gut);
		}
		.open {
			padding: 12px;
			gap: 8px;
		}
		.big b {
			font-size: 34px;
		}
		.tt {
			font-size: 14px;
			letter-spacing: 0.08em;
			white-space: normal;
			line-height: 1.1;
		}
		.one {
			font-size: 13px;
		}
	}
</style>
