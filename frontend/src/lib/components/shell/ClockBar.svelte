<script lang="ts">
	// The regulator clock bar (DESIGN.md §3): one cell per running clock, ordered by time left. New cells drop in
	// from above with a small overshoot while neighbours make room (Drop + flip).
	import { flip } from 'svelte/animate';
	import { backOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { slide } from 'svelte/transition';
	import type { Snippet } from 'svelte';
	import Logo from './Logo.svelte';
	import ClockCell from './ClockCell.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { hms, secondsLeft, clockState, STATE_GLYPH } from '$lib/time/clock';
	import { regimeShort, noticeHref, clockLabel } from '$lib/util/labels';

	let { idle = false, right }: { idle?: boolean; right?: Snippet } = $props();

	const clocks = $derived(idle ? [] : live.clocks);
	let ready = $state(false);
	let sheet = $state(false);
	$effect(() => {
		if (live.clocksLoaded && !ready) setTimeout(() => (ready = true), 400);
	});

	function drop(_node: Element, { delay = 0 }: { delay?: number } = {}) {
		if (!ready || prefersReducedMotion.current) return { duration: 0 };
		return {
			delay,
			duration: 620,
			easing: backOut,
			css: (t: number, u: number) => `transform: translateY(${-56 * u}px); opacity: ${Math.min(1, t / 0.6)}`
		};
	}
	const urgent = $derived(clocks.find((c) => c.state !== 'not_started' && c.state !== 'submitted') ?? clocks[0]);
</script>

<header class="clockbar">
	<button class="brand" type="button" onclick={() => ui.openPalette('all', '')} aria-label="Nightjar SOC: go to anything">
		<Logo /><b>nightjar</b><i>soc</i>
	</button>

	<div class="clocks" role="list" aria-label="Regulator clocks">
		{#if !idle && clocks.length === 0 && live.clocksLoaded}
			<div class="idle"><i></i>No regulator clocks running</div>
		{:else if idle}
			<div class="idle"><i></i>No regulator clocks running</div>
		{/if}
		{#each clocks as d, i (d.id)}
			<div role="listitem" class="cellwrap" in:drop={{ delay: i * 120 }} animate:flip={{ duration: prefersReducedMotion.current ? 0 : 300 }}>
				<ClockCell {d} now={live.now} />
			</div>
		{/each}
	</div>

	{#if urgent}
		<button class="mchip mono" type="button" onclick={() => (sheet = !sheet)} aria-expanded={sheet} aria-controls="clocksheet">
			<span class="g">{STATE_GLYPH[clockState(urgent, live.now)]}</span>
			{regimeShort(urgent.regime)}
			{urgent.state === 'not_started' ? 'decide' : hms(secondsLeft(urgent.dueAt, live.now)).slice(0, 5)}
			{#if clocks.length > 1}<span class="faint">· +{clocks.length - 1}</span>{/if}
			<span aria-hidden="true">▾</span>
		</button>
	{/if}

	<div class="right">
		{#if right}
			{@render right()}
		{:else}
			<button class="kbtn" type="button" onclick={() => ui.openPalette()}>Go to anything <kbd>⌘</kbd><kbd>K</kbd></button>
			<a class="iconbtn" href="/brief" title="Morning brief (⌘B)" aria-label="Open the morning brief">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="13" r="4" /><path d="M12 3v2M4.9 6.4l1.4 1.4M19.1 6.4l-1.4 1.4M2 17h20M6 21h12" /></svg>
			</a>
			<button class="iconbtn" type="button" onclick={() => ui.toggleTheme()} aria-label={ui.theme === 'dark' ? 'Switch to the day watch (light)' : 'Switch to night (dark)'} title="Theme">
				{#if ui.theme === 'dark'}
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
				{:else}
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></svg>
				{/if}
			</button>
		{/if}
	</div>
</header>

{#if sheet}
	<div id="clocksheet" class="sheet" transition:slide={{ duration: prefersReducedMotion.current ? 0 : 200 }}>
		{#each clocks as d (d.id)}
			<a href={noticeHref(d)} onclick={() => (sheet = false)}>
				<span class="mono faint">{clockLabel(d)}</span>
				<span class="mono">{d.state === 'not_started' ? '◆ decide' : hms(secondsLeft(d.dueAt, live.now))}</span>
			</a>
		{/each}
	</div>
{/if}

<style>
	.clockbar {
		position: relative;
		z-index: 5;
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: stretch;
		height: 56px;
		border-bottom: 1px solid var(--line);
		background: var(--bg);
	}
	.brand {
		padding: 0 20px;
		border-right: 1px solid var(--line);
	}
	.clocks {
		display: flex;
		min-width: 0;
		overflow-x: auto;
		overflow-y: hidden;
		scrollbar-width: none;
	}
	.clocks::-webkit-scrollbar {
		display: none;
	}
	.cellwrap {
		display: flex;
		flex: none;
		height: 100%;
	}
	.idle {
		display: flex;
		align-items: center;
		padding: 0 18px;
		font: 400 12px/1 var(--mono);
		color: var(--faint);
		gap: 8px;
		white-space: nowrap;
	}
	.idle i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		border: 1px solid var(--faint);
	}
	.right {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 14px;
		border-left: 1px solid var(--line);
	}
	.mchip {
		display: none;
	}
	.sheet {
		position: fixed;
		top: 56px;
		left: 0;
		right: 0;
		z-index: 30;
		background: var(--pane);
		border-bottom: 1px solid var(--line-strong);
		box-shadow: var(--shadow);
		display: grid;
	}
	.sheet a {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 14px 16px;
		border-top: 1px solid var(--line);
		text-decoration: none;
		font-size: 13px;
	}
	@media (max-width: 760px) {
		.clockbar {
			grid-template-columns: auto minmax(0, 1fr) auto;
			height: 52px;
		}
		.brand {
			padding: 0 12px;
			border-right: 0;
		}
		.brand :global(b),
		.brand :global(i) {
			display: none;
		}
		.clocks {
			display: none;
		}
		.mchip {
			display: inline-flex;
			align-items: center;
			gap: 6px;
			justify-self: start;
			align-self: center;
			height: 32px;
			padding: 0 10px;
			border: 1px solid var(--line-strong);
			border-radius: 6px;
			font-size: 12.5px;
			color: var(--ink);
			white-space: nowrap;
			max-width: 100%;
			overflow: hidden;
		}
		.mchip .g {
			color: var(--teal);
		}
		.right {
			border-left: 0;
			padding: 0 8px;
			gap: 2px;
		}
		.right :global(.kbtn) {
			display: none;
		}
	}
</style>
