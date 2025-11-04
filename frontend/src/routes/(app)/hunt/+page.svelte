<script lang="ts">
	import { page } from '$app/state';
	import Pane from '$lib/components/shell/Pane.svelte';
	import HuntPanel from '$lib/components/console/HuntPanel.svelte';
	import IncidentView from '$lib/components/console/IncidentView.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	const ask = $derived(page.url.searchParams.get('ask') ?? '');
	const saved = $derived(page.url.searchParams.get('saved') ?? '');
	const scope = $derived(ui.scope);
</script>

<svelte:head><title>Hunt · Nightjar SOC</title></svelte:head>
<div class="tiles" class:two={!!scope}>
	{#if scope}
		<Pane title="incident" sub={scope.reference} label="Incident"><IncidentView param={scope.reference} compact /></Pane>
	{/if}
	<Pane title="hunt" sub={saved ? 'saved search' : ask ? 'asked the watch' : ''} focus label="Hunt">
		{#snippet actions()}<a class="lnk" href="/hunt/saved">saved searches</a>{/snippet}
		{#key ask + saved}<HuntPanel initial={ask} savedId={saved} />{/key}
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 1px;
		background: var(--line);
		min-height: 0;
	}
	.tiles.two {
		grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.25fr);
	}
	@media (max-width: 1000px) {
		.tiles.two {
			grid-template-columns: minmax(0, 1fr);
		}
		.tiles.two > :global(.pane:first-child) {
			display: none;
		}
	}
</style>
