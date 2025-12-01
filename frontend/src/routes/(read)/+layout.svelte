<script lang="ts">
	// The brief is its own reading mode, dawn (light) by default with a night edition, inside the same grammar as the
	// console: the shift axis on top, dawn bands, the watch bar at the bottom. No KPI cards. ⌘K works everywhere.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import ShiftAxis from '$lib/components/shell/ShiftAxis.svelte';
	import WatchBar from '$lib/components/shell/WatchBar.svelte';
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
	<div class="wash" aria-hidden="true"></div>
	<div class="axisw"><ShiftAxis /></div>
	<header class="bar mono">
		<span class="dash" aria-hidden="true">──</span>
		<b>{week ? 'weekly report' : 'morning brief'}</b><span class="where">· {session.org?.name ?? 'Halden Logistics'}</span>
		<span class="run" aria-hidden="true"></span>
		{#if week}<a class="lk" href="/brief">morning brief</a>{:else}<a class="lk" href="/brief/week">weekly report</a>{/if}
		{#if session.role !== 'executive'}<a class="lk hide-s" href="/watch">open the watch</a>{/if}
		<button class="lk" type="button" onclick={toggle} aria-label={ui.briefTheme === 'light' ? 'Read the night edition (dark)' : 'Read the dawn edition (light)'} title="Edition">{ui.briefTheme === 'light' ? '☾ night edition' : '☼ dawn edition'}</button>
	</header>
	{#if ready}{@render children()}{/if}
	<div class="wbar"><WatchBar /></div>
</div>
<CommandPalette />

<style>
	:global(:root[data-theme='light']) .briefroot {
		--page: #f7f9f8;
		--wash-a: #cbe5de;
		--wash-b: #e4f0ec;
		--ink: #0d1a20;
		--ink-2: #33444c;
		--muted: #4c5b63;
		--faint: #5e6d74;
		--rule: #dce4e2;
		--rule-2: #c9d4d1;
		--teal: #087565;
		--lav: #5b45c9;
		--lav-wash: rgba(91, 69, 201, 0.06);
		--amber: #8a5d00;
		--sun: rgba(8, 117, 101, 0.16);
		--box: color-mix(in srgb, #f7f9f8 70%, #fff);
	}
	:global(:root[data-theme='dark']) .briefroot {
		--page: #0a1016;
		--wash-a: #142a30;
		--wash-b: #0e1a20;
		--ink: #e6edf1;
		--ink-2: #bac7ce;
		--muted: #8f9ea8;
		--faint: #8392a0;
		--rule: #1b2731;
		--rule-2: #283643;
		--teal: #3ce6c8;
		--lav: #b6a8ff;
		--lav-wash: rgba(182, 168, 255, 0.08);
		--sun: rgba(60, 230, 200, 0.14);
		--box: #0f1820;
	}
	.briefroot {
		position: relative;
		min-height: 100vh;
		background: var(--page);
		color: var(--ink);
		font-size: 16px;
		overflow-x: hidden;
	}
	:global(body:has(.briefroot)) {
		background: var(--page, #f7f9f8);
	}
	.wash {
		position: absolute;
		inset: 0 0 auto 0;
		height: 760px;
		-webkit-mask-image: linear-gradient(#000 55%, transparent);
		mask-image: linear-gradient(#000 55%, transparent);
		background: linear-gradient(180deg, var(--wash-a) 0%, var(--wash-b) 34%, var(--page) 100%);
		pointer-events: none;
		overflow: hidden;
	}
	.wash::after {
		content: '';
		position: absolute;
		right: 7%;
		top: 300px;
		width: 560px;
		height: 560px;
		border-radius: 50%;
		border: 1px solid var(--sun);
		box-shadow:
			0 0 0 46px color-mix(in srgb, var(--sun) 18%, transparent),
			0 0 0 92px color-mix(in srgb, var(--sun) 8%, transparent);
	}
	.axisw {
		position: relative;
		z-index: 4;
	}
	.axisw :global(.axis) {
		background: color-mix(in srgb, var(--page) 55%, transparent);
	}
	.bar {
		position: relative;
		display: flex;
		align-items: center;
		gap: 10px;
		height: 38px;
		padding: 0 16px;
		font-size: 12px;
		color: var(--muted);
		border-bottom: 1px solid var(--rule-2);
		white-space: nowrap;
	}
	.bar b {
		color: var(--ink);
		font-weight: 600;
	}
	.dash {
		color: var(--rule-2);
	}
	.run {
		flex: 1;
		min-width: 12px;
		height: 1px;
		background: repeating-linear-gradient(90deg, var(--rule-2) 0 6px, transparent 6px 9px);
	}
	.lk {
		font-size: 12px;
		color: var(--ink-2);
		text-decoration: none;
		padding: 6px 8px;
		border-radius: 3px;
		white-space: nowrap;
	}
	.lk:hover {
		background: color-mix(in srgb, var(--ink) 6%, transparent);
	}
	.wbar {
		position: sticky;
		bottom: 0;
		z-index: 6;
	}
	@media (max-width: 760px) {
		.bar {
			gap: 6px;
			padding: 0 10px;
		}
		.where,
		.hide-s {
			display: none;
		}
		.lk {
			padding: 6px 4px;
		}
		.wash::after {
			right: -60%;
		}
	}
	@media print {
		.axisw,
		.wbar,
		.wash {
			display: none;
		}
	}
</style>
