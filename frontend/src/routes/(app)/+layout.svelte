<script lang="ts">
	// The console shell (DESIGN.md §0.1): the shift axis on top, the page's bands, the watch bar at the bottom,
	// and ⌘K everywhere. No sidebar, no top navigation, no side panels.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import ShiftAxis from '$lib/components/shell/ShiftAxis.svelte';
	import WatchBar from '$lib/components/shell/WatchBar.svelte';
	import CommandPalette from '$lib/components/shell/CommandPalette.svelte';
	import { onSessionEnded, client } from '$lib/api';
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
			if (!page.url.pathname.startsWith('/sign-in')) goto(`/sign-in?next=${encodeURIComponent(page.url.pathname + page.url.search)}`);
		});
		const me = await session.load();
		if (!me) {
			goto(`/sign-in?next=${encodeURIComponent(page.url.pathname + page.url.search)}`, { replaceState: true });
			return;
		}
		document.documentElement.dataset.theme = ui.theme;
		ui.loadSections(me.membership?.role ?? null);
		ready = true;
		live.start();
		shift.start(me.activeOrg?.timezone);
	});
	onDestroy(() => {
		live.stop();
		shift.stop();
	});
	useShellKeys();

	// Slide the session while the person is active (at most every 5 minutes).
	function activity() {
		client.touch();
	}
</script>

<svelte:window onpointerdown={activity} onkeydown={activity} />

<a class="skip-link" href="#main">Skip to the console</a>
<div class="shell">
	<ShiftAxis />
	<main id="main" class="work" tabindex="-1">
		{#if ready}{@render children()}{:else}<div class="boot mono faint" aria-live="polite">Taking the watch…</div>{/if}
	</main>
	<WatchBar />
</div>
<CommandPalette />

<style>
	.shell {
		height: 100vh;
		height: 100dvh;
		display: grid;
		grid-template-rows: auto minmax(0, 1fr) auto;
		overflow: hidden;
	}
	.work {
		min-height: 0;
		min-width: 0;
		display: flex;
		flex-direction: column;
		overflow-y: auto;
		overflow-x: hidden;
		scrollbar-gutter: stable;
		outline: none;
		background: var(--bg);
		scrollbar-width: thin;
		scrollbar-color: var(--line-strong) transparent;
	}
	.boot {
		padding: 40px;
		font-size: 12px;
	}
</style>
