<script lang="ts">
	// Hunt: one task at a time. When ⌘K was scoped to an incident, a scope band says so above the hunt.
	import { page } from '$app/state';
	import Band from '$lib/components/ui/Band.svelte';
	import HuntPanel from '$lib/components/console/HuntPanel.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	const ask = $derived(page.url.searchParams.get('ask') ?? '');
	const saved = $derived(page.url.searchParams.get('saved') ?? '');
	const scope = $derived(ui.scope);
	const inc = $derived(scope ? live.incidents.find((i) => i.id === scope.id) : null);
</script>

<svelte:head><title>Hunt · Nightjar SOC</title></svelte:head>
<h1 class="sr">Hunt</h1>
{#if scope}
	<div class="scope band-in">
		<span class="mono faint">hunting around</span>
		<a class="mono ref" href="/incidents/{scope.reference}">{scope.reference}</a>
		{#if inc}<span class="tt">{inc.title}</span>{/if}
		<button class="lnk" type="button" onclick={() => (ui.scope = null)}>clear</button>
	</div>
{/if}
<Band title="hunt" sub={saved ? 'saved search' : ask ? 'asked the watch' : 'ask in plain words'} fill>
	{#snippet actions()}<a class="lnk" href="/hunt/saved">saved searches</a>{/snippet}
	{#key ask + saved}<HuntPanel initial={ask} savedId={saved} />{/key}
</Band>

<style>
	.scope {
		display: flex;
		align-items: baseline;
		gap: 10px;
		padding: 10px 16px;
		background: var(--teal-wash);
		border-bottom: 1px solid var(--teal-line);
		font-size: 13px;
		min-width: 0;
	}
	.scope .mono {
		font-size: 11.5px;
	}
	.ref {
		color: var(--teal);
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
