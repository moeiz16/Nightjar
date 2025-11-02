<script lang="ts">
	// The mono status line (DESIGN.md §3): health in plain words, who is on shift, you, the layout and ⌘K.
	import type { Snippet } from 'svelte';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';

	let { extra, idle = false, layout = false }: { extra?: Snippet; idle?: boolean; layout?: boolean } = $props();
	const status = $derived(live.pipeline.status);
	const healthy = $derived(status === 'normal');
</script>

<footer class="statusline">
	<span class="c" class:live={healthy} class:warn={!healthy}>
		{#if healthy}<i class="pulse" aria-hidden="true"></i>{:else}<span aria-hidden="true">◐</span>{/if}
		{idle ? 'Taking in events normally · Halden Logistics demo' : live.pipeline.plainStatus}
	</span>
	{#if !idle}
		<span class="c hide-s">Triage · Enrichment · Response on shift</span>
		{#if session.me}<span class="c hide-s">{session.name} · {session.roleLabel}</span>{/if}
		{#if ui.statusNote}<span class="c note hide-m" title={ui.statusNote}>{ui.statusNote}</span>{/if}
		{#if extra}{@render extra()}{/if}
		{#if layout}<span class="c hide-m">layout: {session.role ?? 'responder'} · <kbd>⌘</kbd><kbd>[</kbd><kbd>]</kbd> tiles</span>{/if}
	{/if}
	<span class="c end">
		{#if idle}⌘K after sign-in{:else}<button type="button" onclick={() => ui.openPalette()}><kbd>⌘</kbd><kbd>K</kbd> go anywhere</button>{/if}
	</span>
</footer>

<style>
	.statusline {
		height: 28px;
		display: flex;
		align-items: center;
		border-top: 1px solid var(--line);
		font: 400 11.5px/1 var(--mono);
		color: var(--muted);
		background: var(--bg);
		white-space: nowrap;
		overflow: hidden;
	}
	.note {
		flex: 0 1 auto !important;
		overflow: hidden;
		text-overflow: ellipsis;
		display: block !important;
		line-height: 27px;
	}
	.c {
		flex: none;
		padding: 0 14px;
		border-right: 1px solid var(--line);
		height: 100%;
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}
	.c.end {
		border-right: 0;
		margin-left: auto;
		border-left: 1px solid var(--line);
	}
	.live {
		color: var(--teal);
	}
	.warn {
		color: var(--amber);
	}
	.end button {
		display: inline-flex;
		gap: 6px;
		align-items: center;
		color: var(--muted);
		font: inherit;
	}
	.end button:hover {
		color: var(--ink);
	}
	@media (max-width: 1100px) {
		.hide-m {
			display: none !important;
		}
	}
	@media (max-width: 760px) {
		.hide-s {
			display: none !important;
		}
		.statusline {
			display: none;
		}
	}
</style>
