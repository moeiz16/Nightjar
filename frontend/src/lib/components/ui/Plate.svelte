<script lang="ts">
	// A plate: the titled section of every page (DESIGN.md §7). An engraved heading in stencil capitals (an index bar,
	// or a numbered lamp for the turnover's numbered readouts), a summary, the plate's actions, then its body on a
	// hard-edged plate with a 2 px top edge. Corner ticks mark the few plates that hold a decision.
	import type { Snippet } from 'svelte';
	let {
		title,
		sub,
		n,
		actions,
		children,
		label,
		scroll = false,
		fill = false,
		flush = false,
		bare = false,
		ticks = false,
		id,
		class: cls = ''
	}: {
		title: string;
		sub?: string;
		/** section number, for numbered readouts */
		n?: number | string;
		actions?: Snippet;
		children: Snippet;
		label?: string;
		/** the body scrolls inside the plate */
		scroll?: boolean;
		/** the plate grows to fill the remaining height */
		fill?: boolean;
		/** no body padding */
		flush?: boolean;
		/** no plate behind the body (the body draws its own strips) */
		bare?: boolean;
		/** corner ticks, for plates that hold a decision */
		ticks?: boolean;
		id?: string;
		class?: string;
	} = $props();
	const hid = $props.id();
</script>

<section class="plate rack {cls}" class:fill aria-labelledby={label ? undefined : hid} aria-label={label} {id}>
	<header class="ph">
		{#if n !== undefined}<span class="n">{n}</span>{:else}<span class="bar" aria-hidden="true"></span>{/if}
		<h2 id={hid} class="t">{title}</h2>
		{#if sub}<span class="sub">{sub}</span>{/if}
		<span class="sp"></span>
		{#if actions}<span class="act">{@render actions()}</span>{/if}
	</header>
	<div class="body" class:scroll class:flush class:bare class:ticks>{@render children()}</div>
</section>

<style>
	.plate {
		min-width: 0;
		display: flex;
		flex-direction: column;
		margin: 26px var(--gut) 0;
	}
	.plate.fill {
		flex: 1;
		min-height: 0;
	}
	.ph {
		flex: none;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px 12px;
		padding: 0 0 10px;
		min-width: 0;
	}
	.bar {
		width: 4px;
		height: 20px;
		background: var(--acc);
		flex: none;
	}
	.n {
		display: inline-grid;
		place-items: center;
		min-width: 26px;
		height: 26px;
		padding: 0 5px;
		font: 600 13px/1 var(--code);
		color: var(--acc-ink);
		background: var(--acc);
		border-radius: 1px;
		flex: none;
	}
	.t {
		margin: 0;
		font: 700 24px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--ink);
	}
	.sub {
		font: 600 14px/1.3 var(--ui);
		color: var(--dim);
		min-width: 0;
	}
	.sp {
		flex: 1;
	}
	.act {
		display: flex;
		gap: 14px;
		align-items: center;
		flex: none;
	}
	.body {
		min-width: 0;
		background-color: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--rule-2);
		padding: 18px 20px;
	}
	.body.flush {
		padding: 0;
	}
	.body.bare {
		background-color: transparent;
		border: 0;
		padding: 0;
	}
	.body.ticks {
		--tk: var(--acc);
		--tl: 14px;
	}
	.plate.fill > .body {
		flex: 1;
		min-height: 0;
	}
	@media (max-width: 760px) {
		.plate {
			margin-top: 20px;
		}
		.t {
			font-size: 21px;
		}
		.body {
			padding: 14px;
		}
		.act :global(.hide-s) {
			display: none;
		}
	}
</style>
