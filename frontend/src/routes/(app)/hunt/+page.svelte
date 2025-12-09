<script lang="ts">
	// Hunt: one task at a time. When ⌘K was scoped to an incident, a scope band says so above the hunt.
	import { page } from '$app/state';
	import Plate from '$lib/components/ui/Plate.svelte';
	import HuntPanel from '$lib/components/console/HuntPanel.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	const ask = $derived(page.url.searchParams.get('ask') ?? '');
	const saved = $derived(page.url.searchParams.get('saved') ?? '');
	const scope = $derived(ui.scope);
	const inc = $derived(scope ? live.incidents.find((i) => i.id === scope.id) : null);
</script>

<svelte:head><title>Hunt · Nightjar SOC</title></svelte:head>
{#if scope}
	<div class="scope rack">
		<span class="eng faint">Hunting around</span>
		<a class="mono ref" href="/incidents/{scope.reference}">{scope.reference}</a>
		{#if inc}<span class="tt">{inc.title}</span>{/if}
		<button class="lnk" type="button" onclick={() => (ui.scope = null)}>Clear</button>
	</div>
{/if}
<Plate title="The plot" sub={saved ? 'A saved search' : ask ? 'Asked the watch' : 'Ask in plain words'} flush>
	{#snippet actions()}<a class="lnk" href="/hunt/saved">Saved searches</a>{/snippet}
	{#key ask + saved}<HuntPanel initial={ask} savedId={saved} />{/key}
</Plate>

<style>
	.scope {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin: 22px var(--gut) 0;
		padding: 10px 16px;
		background: var(--acc-wash);
		border: 1px solid var(--acc-line);
		font-size: 14px;
		min-width: 0;
	}
	.scope .mono {
		font-size: 11.5px;
	}
	.ref {
		color: var(--acc-hi);
		font-weight: 600;
	}
	.tt {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.scope .lnk {
		margin-left: auto;
	}
</style>
