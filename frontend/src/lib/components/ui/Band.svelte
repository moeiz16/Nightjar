<script lang="ts">
	// A band: the one container of the shift-chart layout (DESIGN.md §0). Full width, hard-edged, opened by a
	// terminal-style rule "── n · title · context ────── actions ─". Bands stack; they never sit side by side.
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
		id,
		class: cls = ''
	}: {
		title: string;
		sub?: string;
		/** section number, for turnover-style numbered bands */
		n?: number | string;
		actions?: Snippet;
		children: Snippet;
		label?: string;
		/** the body scrolls inside the band (console pages that fill the screen) */
		scroll?: boolean;
		/** the band grows to fill the remaining height */
		fill?: boolean;
		/** no body padding */
		flush?: boolean;
		id?: string;
		class?: string;
	} = $props();
	const hid = $props.id();
</script>

<section class="band band-in {cls}" class:fill aria-labelledby={label ? undefined : hid} aria-label={label} {id}>
	<header class="rule">
		<span class="dash" aria-hidden="true">──</span>
		{#if n !== undefined}<span class="n">{n}</span>{/if}
		<h2 id={hid} class="t">{title}</h2>
		{#if sub}<span class="sub">· {sub}</span>{/if}
		<span class="run" aria-hidden="true"></span>
		{#if actions}<span class="act">{@render actions()}</span>{/if}
	</header>
	{#if scroll}
		<div class="scroll body" class:flush>{@render children()}</div>
	{:else}
		<div class="body" class:flush>{@render children()}</div>
	{/if}
</section>

<style>
	.band {
		min-width: 0;
		display: flex;
		flex-direction: column;
		background: var(--pane);
		border-bottom: 1px solid var(--line);
	}
	.band.fill {
		flex: 1;
		min-height: 0;
	}
	.rule {
		height: 34px;
		flex: none;
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 16px;
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		white-space: nowrap;
		min-width: 0;
		background: var(--bg);
		border-bottom: 1px solid var(--line);
	}
	.dash {
		color: var(--line-strong);
		letter-spacing: -0.1em;
	}
	.n {
		color: var(--teal);
		font-weight: 600;
	}
	.t {
		margin: 0;
		font: 600 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--ink);
	}
	.sub {
		overflow: hidden;
		text-overflow: ellipsis;
		min-width: 0;
	}
	.run {
		flex: 1;
		min-width: 12px;
		height: 1px;
		background: repeating-linear-gradient(90deg, var(--line-strong) 0 6px, transparent 6px 9px);
	}
	.act {
		display: flex;
		gap: 12px;
		align-items: center;
		flex: none;
	}
	.body {
		min-width: 0;
	}
	.band.fill > .body {
		flex: 1;
		min-height: 0;
	}
	.body.flush {
		padding: 0;
	}
	@media (max-width: 760px) {
		.rule {
			padding: 0 12px;
		}
		.act :global(.hide-s) {
			display: none;
		}
	}
</style>
