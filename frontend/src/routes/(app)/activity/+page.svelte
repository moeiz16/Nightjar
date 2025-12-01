<script lang="ts">
	import { page } from '$app/state';
	import Band from '$lib/components/ui/Band.svelte';
	import ActivityPanel from '$lib/components/console/ActivityPanel.svelte';
	import { session } from '$lib/stores/session.svelte';
</script>

<svelte:head><title>Activity log · Nightjar SOC</title></svelte:head>
<h1 class="sr">Activity log</h1>
<Band title="activity" sub="every sensitive action, newest first" fill>
	{#if session.can('audit:read')}
		<ActivityPanel autoVerify={page.url.searchParams.has('verify')} autoExport={page.url.searchParams.has('export')} />
	{:else}
		<p class="pad">The activity log is for the CISO, administrators and auditors. Your own actions are recorded there too.</p>
	{/if}
</Band>

<style>
	.pad {
		padding: 22px;
		color: var(--muted);
	}
</style>
