<script lang="ts">
	import Pane from '$lib/components/shell/Pane.svelte';
	import BehaviourPanel from '$lib/components/console/BehaviourPanel.svelte';
</script>

<svelte:head><title>Unusual behaviour · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Pane title="unusual behaviour" sub="each person and device against their own normal" focus><BehaviourPanel /></Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		min-height: 0;
		background: var(--line);
	}
</style>
