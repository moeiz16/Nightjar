<script lang="ts">
	// Regulator clocks as a quiet ledger: what is due, by when (regime time and yours), and its state in glyph + word.
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
			<small class="mono">{d.state === 'not_started' ? 'not started · waits for the materiality decision' : d.state === 'submitted' ? 'recorded as submitted (demo)' : `due ${dueBoth(d.dueAt!, now, REGIME_TZ[d.regime])}`}</small>
		</span>
		<span class="mono st {st}">{STATE_GLYPH[st]} {st === 'on_track' || st === 'due_soon' ? hm(secondsLeft(d.dueAt, now)) : STATE_WORD[st]}</span>
	</a>
{:else}
	<p class="mono faint none">No regulator clocks running.</p>
{/each}

<style>
	.clk {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 10px;
		padding: 10px 0;
		border-top: 1px solid var(--line);
		text-decoration: none;
		font-size: 13px;
	}
	.clk:first-child {
		border-top: 0;
	}
	.w {
		min-width: 0;
	}
	.clk b {
		display: block;
		font-weight: 500;
	}
	.clk b .mono {
		font-weight: 400;
		font-size: 11.5px;
	}
	.clk small {
		font-size: 11.5px;
		color: var(--muted);
	}
	.clk:hover b {
		color: var(--teal);
	}
	.st {
		font-size: 12px;
		white-space: nowrap;
	}
	.st.on_track,
	.st.submitted {
		color: var(--teal);
	}
	.st.due_soon {
		color: var(--amber);
	}
	.st.overdue {
		color: var(--red);
	}
	.st.not_started {
		color: var(--lav);
	}
	.none {
		font-size: 12px;
		margin: 4px 0;
	}
</style>
