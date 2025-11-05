<script lang="ts">
	// The brief is its own reading experience: dawn (light) by default, a night edition for late readers. No tiles,
	// no river, no KPI cards. ⌘K still works everywhere.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount, onDestroy } from 'svelte';
	import Logo from '$lib/components/shell/Logo.svelte';
	import CommandPalette from '$lib/components/shell/CommandPalette.svelte';
	import { onSessionEnded } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { useShellKeys } from '$lib/util/keys.svelte';

	let { children } = $props();
	let ready = $state(false);
	onMount(async () => {
		onSessionEnded(() => goto(`/sign-in?next=${encodeURIComponent(page.url.pathname)}`));
		const q = page.url.searchParams.get('theme');
		document.documentElement.dataset.theme = q === 'dark' || q === 'light' ? q : ui.briefTheme;
		const me = await session.load();
		if (!me) {
			goto(`/sign-in?next=${encodeURIComponent(page.url.pathname)}`, { replaceState: true });
			return;
		}
		ready = true;
		live.start();
	});
	onDestroy(() => {
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
	<header class="bar">
		<a class="brand" href={session.home()} aria-label="Nightjar SOC"><Logo /><b>nightjar</b><i>soc</i></a>
		<span class="where mono">{week ? 'weekly report' : 'morning brief'} · {session.org?.name ?? 'Halden Logistics'}</span>
		<span class="sp">
			{#if week}<a class="lk" href="/brief">Morning brief</a>{:else}<a class="lk" href="/brief/week">Weekly report</a>{/if}
			{#if session.role !== 'executive'}<a class="lk" href="/watch">Open the watch</a>{:else}<a class="lk hide-s" href="/watch">Open the watch</a>{/if}
			<button class="lk icon" type="button" onclick={toggle} aria-label={ui.briefTheme === 'light' ? 'Read the night edition (dark)' : 'Read the dawn edition (light)'} title="Edition">{ui.briefTheme === 'light' ? '☾' : '☼'}</button>
			<button class="kbtn" type="button" onclick={() => ui.openPalette()}>Go to anything <kbd>⌘</kbd><kbd>K</kbd></button>
		</span>
	</header>
	{#if ready}{@render children()}{/if}
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
		--teal: #0a7f6d;
		--lav: #5b45c9;
		--lav-wash: rgba(91, 69, 201, 0.06);
		--amber: #8a5d00;
		--sun: rgba(10, 127, 109, 0.16);
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
	.bar {
		position: relative;
		display: flex;
		align-items: center;
		gap: 22px;
		height: 64px;
		max-width: 1160px;
		margin: 0 auto;
		padding: 0 20px;
	}
	.bar :global(.brand svg) {
		color: var(--teal);
	}
	.where {
		font-size: 13px;
		color: var(--muted);
		padding-left: 18px;
		border-left: 1px solid var(--rule-2);
		white-space: nowrap;
	}
	.sp {
		margin-left: auto;
		display: flex;
		gap: 6px;
		align-items: center;
	}
	.lk {
		font-size: 14px;
		color: var(--ink-2);
		text-decoration: none;
		padding: 8px 10px;
		border-radius: 6px;
		white-space: nowrap;
	}
	.lk:hover {
		background: color-mix(in srgb, var(--ink) 6%, transparent);
	}
	.icon {
		font-family: var(--mono);
	}
	.bar .kbtn {
		border-color: var(--rule-2);
		color: var(--ink-2);
	}
	.bar :global(kbd) {
		background: transparent;
		border-color: var(--rule-2);
		color: var(--ink-2);
	}
	@media (max-width: 760px) {
		.bar {
			gap: 10px;
			padding: 0 12px;
		}
		.where,
		.bar .kbtn,
		.hide-s {
			display: none;
		}
		.bar :global(.brand b),
		.bar :global(.brand i) {
			display: none;
		}
		.lk {
			padding: 8px 6px;
			font-size: 13px;
		}
		.wash::after {
			right: -60%;
		}
	}
	@media print {
		.bar .sp,
		.wash {
			display: none;
		}
	}
</style>
