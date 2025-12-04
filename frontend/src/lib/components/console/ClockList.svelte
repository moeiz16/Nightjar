<script lang="ts">
	// Regulator clocks as strips: what is due, by when (regime time and yours), and its state in glyph + word.
	import type { Deadline } from '$lib/api/types';
	import { clockState, dueBoth, hm, REGIME_TZ, secondsLeft, STATE_GLYPH, STATE_WORD } from '$lib/time/clock';
	import { clockLongLabel, noticeHref } from '$lib/util/labels';

	let { clocks, now, showRef = true }: { clocks: Deadline[]; now: Date; showRef?: boolean } = $props();
	const list = $derived(clocks.filter((d) => d.state !== 'not_required'));
</script>

{#each list as d (d.id)}
	{@const st = clockState(d, now)}
	<a class="clk" href={noticeHref(d)}>
		<span class="w">
			<b>{clockLongLabel(d)}{#if showRef}<span class="mono faint"> · {d.incidentReference}</span>{/if}</b>
			<small>{d.state === 'not_started' ? 'not started · waits for the materiality decision' : d.state === 'submitted' ? 'recorded as submitted (demo)' : `due ${dueBoth(d.dueAt!, now, REGIME_TZ[d.regime])}`}</small>
		</span>
		<span class="mono st {st}">{STATE_GLYPH[st]} {st === 'on_track' || st === 'due_soon' ? hm(secondsLeft(d.dueAt, now)) : STATE_WORD[st]}</span>
	</a>
{:else}
	<p class="faint none">No regulator clocks running.</p>
{/each}

<style>
	.clk {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
		padding: 10px 0 10px 12px;
		border-top: 1px solid var(--rule);
		border-left: 3px solid var(--rule-2);
		margin-left: -2px;
		text-decoration: none;
		font-size: 14px;
	}
	.clk:first-child {
		border-top: 0;
	}
	.clk:has(.due_soon) {
		border-left-color: var(--soon);
	}
	.clk:has(.overdue) {
		border-left-color: var(--acc);
	}
	.clk:has(.not_started) {
		border-left-color: var(--person);
	}
	.w {
		flex: 1;
		min-width: 0;
	}
	.clk b {
		display: block;
		font-weight: 700;
		color: var(--ink);
	}
	.clk b .mono {
		white-space: nowrap;
		font-weight: 400;
		font-size: 12px;
	}
	.clk small {
		font-size: 12.5px;
		color: var(--dim);
	}
	.clk:hover b {
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	.st {
		flex: none;
		font-size: 13px;
		font-weight: 500;
		white-space: nowrap;
		color: var(--ink-2);
	}
	.st.submitted {
		color: var(--ok);
	}
	.st.due_soon {
		color: var(--soon);
	}
	.st.overdue {
		background: var(--acc);
		color: var(--acc-ink);
		padding: 2px 5px;
	}
	.st.not_started {
		color: var(--person);
		font-weight: 600;
	}
	.none {
		font-size: 13.5px;
		margin: 4px 0;
	}
</style>
