<script lang="ts">
	// The brief is its own reading mode: the morning report, under a "first light" horizon, in the day presentation
	// by default with a night edition. The same station head as the console, so the lamps and ⌘K work here too.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import StationHead from '$lib/components/shell/StationHead.svelte';
	import CommandPalette from '$lib/components/shell/CommandPalette.svelte';
	import { onSessionEnded } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { useShellKeys } from '$lib/util/keys.svelte';

	let { children } = $props();
	let ready = $state(false);
	onMount(async () => {
		onSessionEnded(() => {
			// the handler outlives this layout: never send the sign-in page to itself
			if (!page.url.pathname.startsWith('/sign-in')) goto(`/sign-in?next=${encodeURIComponent(page.url.pathname)}`);
		});
		const q = page.url.searchParams.get('theme');
		document.documentElement.dataset.theme = q === 'dark' || q === 'light' ? q : ui.briefTheme;
		const me = await session.load();
		if (!me) {
			goto(`/sign-in?next=${encodeURIComponent(page.url.pathname)}`, { replaceState: true });
			return;
		}
		ready = true;
		live.start();
		shift.start(me.activeOrg?.timezone);
	});
	onDestroy(() => {
		shift.stop();
		if (typeof document !== 'undefined') document.documentElement.dataset.theme = ui.theme;
	});
	useShellKeys();
	const week = $derived(page.url.pathname.startsWith('/brief/week'));
	function toggle() {
		ui.setBriefTheme(ui.briefTheme === 'light' ? 'dark' : 'light');
	}
</script>

<div class="briefroot">
	<div class="horizon" aria-hidden="true"></div>
	{#if ready}
		<StationHead>
			{#snippet extra()}
				{#if week}<a class="lnk" href="/brief">The morning brief</a>{:else}<a class="lnk" href="/brief/week">The weekly report</a>{/if}
				{#if session.role !== 'executive'}<a class="lnk hide-s" href="/watch">Open the watch</a>{/if}
				<button class="lnk" type="button" onclick={toggle} aria-label={ui.briefTheme === 'light' ? 'Read the night edition (dark)' : 'Read the dawn edition (light)'} title="Edition">{ui.briefTheme === 'light' ? '☾ Night edition' : '☼ Dawn edition'}</button>
			{/snippet}
		</StationHead>
		{@render children()}
	{/if}
</div>
<CommandPalette />

<style>
	.briefroot {
		position: relative;
		min-height: 100vh;
		background: var(--bg);
		color: var(--ink);
		overflow-x: hidden;
		padding-bottom: 56px;
	}
	/* first light: the night's red running into the day's bone */
	.horizon {
		height: 18px;
		background: linear-gradient(90deg, #2a0e09 0%, #8e2a1c 28%, #e07a52 52%, #e9b49c 72%, var(--bg) 100%);
	}
	:global([data-theme='dark']) .horizon {
		background: linear-gradient(90deg, #0c0605 0%, #3a0f08 40%, #8e2a1c 75%, #e07a52 100%);
	}
	.lnk {
		cursor: pointer;
	}
	@media (max-width: 760px) {
		.hide-s {
			display: none;
		}
	}
	@media print {
		.horizon {
			display: none;
		}
	}
</style>
