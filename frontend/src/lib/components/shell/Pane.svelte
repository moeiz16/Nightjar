<script lang="ts">
	// A tile: radius 0, hairline gutters, lowercase mono title, a 2 px teal rule when focused.
	import type { Snippet } from 'svelte';
	let {
		title,
		sub,
		focus = false,
		onTitle,
		actions,
		children,
		label,
		scroll = true,
		class: cls = '',
		onfocusin
	}: {
		title: string;
		sub?: string;
		focus?: boolean;
		onTitle?: () => void;
		actions?: Snippet;
		children: Snippet;
		label?: string;
		scroll?: boolean;
		class?: string;
		onfocusin?: () => void;
	} = $props();
</script>

<section class="pane {cls}" class:focus aria-label={label ?? title} {onfocusin}>
	<div class="ph">
		{#if onTitle}
			<button class="ttl" type="button" onclick={onTitle} title="Show something else in this tile">{title}</button>
		{:else}
			<b>{title}</b>
		{/if}
		{#if sub}<span class="sub">{sub}</span>{/if}
		{#if actions}<span class="sp">{@render actions()}</span>{/if}
	</div>
	{#if scroll}<div class="scroll">{@render children()}</div>{:else}{@render children()}{/if}
</section>

<style>
	.ttl {
		font: 500 12px/1 var(--mono);
		color: var(--ink-2);
		padding: 4px 6px;
		margin-left: -6px;
		border-radius: 4px;
	}
	.ttl:hover {
		background: var(--raised);
		color: var(--ink);
	}
	.sub {
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
