<script lang="ts">
	import { page } from '$app/state';
	import Plate from '$lib/components/ui/Plate.svelte';
	import ActivityPanel from '$lib/components/console/ActivityPanel.svelte';
	import { session } from '$lib/stores/session.svelte';
</script>

<svelte:head><title>Activity log · Nightjar SOC</title></svelte:head>
<Plate title="The log book" sub="Every sensitive action, newest first, each sealed to the one before" bare>
	{#if session.can('audit:read')}
		<ActivityPanel autoVerify={page.url.searchParams.has('verify')} autoExport={page.url.searchParams.has('export')} />
	{:else}
		<p class="pad">The activity log is for the CISO, administrators and auditors. Your own actions are recorded there too.</p>
	{/if}
</Plate>

<style>
	.pad {
		margin: 0;
		color: var(--dim);
	}
</style>
