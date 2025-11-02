<script lang="ts">
	// The console shell: clock bar on top, the page's tiles, the mono status line, and ⌘K everywhere.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import ClockBar from '$lib/components/shell/ClockBar.svelte';
	import StatusLine from '$lib/components/shell/StatusLine.svelte';
	import CommandPalette from '$lib/components/shell/CommandPalette.svelte';
	import { onSessionEnded, client } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { useShellKeys } from '$lib/util/keys.svelte';

	let { children } = $props();
	let ready = $state(false);

	onMount(async () => {
		onSessionEnded(() => goto(`/sign-in?next=${encodeURIComponent(page.url.pathname + page.url.search)}`));
		const me = await session.load();
		if (!me) {
			goto(`/sign-in?next=${encodeURIComponent(page.url.pathname + page.url.search)}`, { replaceState: true });
			return;
		}
		document.documentElement.dataset.theme = ui.theme;
		ui.loadLayout(me.user.id, me.membership?.role ?? null);
		ready = true;
		live.start();
	});
	onDestroy(() => live.stop());
	useShellKeys();

	// Slide the session while the person is active (at most every 5 minutes).
	function activity() {
		client.touch();
	}
	const isWatch = $derived(page.url.pathname === '/watch');
</script>

<svelte:window onpointerdown={activity} onkeydown={activity} />

<a class="skip-link" href="#main">Skip to the console</a>
<div class="shell">
	<ClockBar />
	<main id="main" class="work" tabindex="-1">
		{#if ready}{@render children()}{:else}<div class="boot mono faint" aria-live="polite">Taking the watch…</div>{/if}
	</main>
	<StatusLine layout={isWatch} />
</div>
<button class="fab" type="button" onclick={() => ui.openPalette()} aria-label="Go to anything">
	<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></svg>
</button>
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
		display: flex;
		flex-direction: column;
		outline: none;
	}
	.boot {
		padding: 40px;
		font-size: 12px;
	}
	.fab {
		display: none;
	}
	@media (max-width: 760px) {
		.fab {
			display: grid;
			place-items: center;
			position: fixed;
			right: 16px;
			bottom: 16px;
			width: 52px;
			height: 52px;
			border-radius: 50%;
			background: var(--teal);
			color: var(--teal-ink);
			box-shadow: var(--shadow);
			z-index: 40;
		}
		.fab svg {
			width: 22px;
			height: 22px;
		}
	}
</style>
