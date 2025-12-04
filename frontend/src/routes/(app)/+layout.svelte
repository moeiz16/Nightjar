<script lang="ts">
	// The shell (DESIGN.md §3): the station head (plate, lamp panel, chronometer) and the page under it, scrolling as
	// one sheet, with ⌘K everywhere. No side rail, no bottom bar, no right panel.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import StationHead from '$lib/components/shell/StationHead.svelte';
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

<a class="skip-link" href="#main">Skip to the page</a>
<div class="room">
	{#if ready}<StationHead />{/if}
	<main id="main" class="work" tabindex="-1">
		{#if ready}{@render children()}{:else}<div class="boot eng faint" aria-live="polite">Taking the watch…</div>{/if}
	</main>
</div>
<CommandPalette />

<style>
	.room {
		min-height: 100vh;
		min-height: 100dvh;
		display: flex;
		flex-direction: column;
	}
	.work {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		outline: none;
		padding-bottom: 48px;
	}
	.boot {
		padding: 48px var(--gut);
		font-size: 15px;
	}
</style>
