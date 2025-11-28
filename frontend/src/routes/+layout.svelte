<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { onNavigate } from '$app/navigation';
	import { prefersReducedMotion } from 'svelte/motion';
	import { Toaster } from '@skeletonlabs/skeleton-svelte';
	import { ui, toaster } from '$lib/stores/ui.svelte';

	let { children } = $props();
	onMount(() => ui.init());

	// Dawn: going from the watch to the brief, the brief rises from the bottom like first light (DESIGN.md §11).
	onNavigate((nav) => {
		const to = nav.to?.url.pathname ?? '';
		const from = nav.from?.url.pathname ?? '';
		if (!to.startsWith('/brief') || from.startsWith('/brief') || !document.startViewTransition) return;
		const root = document.documentElement;
		root.classList.add('dawn');
		return new Promise((resolve) => {
			const vt = document.startViewTransition(async () => {
				resolve();
				await nav.complete;
			});
			vt.finished.finally(() => root.classList.remove('dawn'));
			void prefersReducedMotion.current;
		});
	});
</script>

{@render children()}
<Toaster
	{toaster}
	base="nj-toast flex items-baseline gap-3"
	width="w-[min(480px,calc(100vw-32px))]"
	padding="py-3 px-4"
	rounded="rounded-[3px]"
	titleBase="sr"
	descriptionBase="text-[13.5px]"
	btnDismissBase="btn-icon btn-sm ml-auto"
	stateInfo="nj-toast-info"
	stateSuccess="nj-toast-ok"
	stateWarning="nj-toast-info"
	stateError="nj-toast-bad"
/>
<div class="sr" aria-live="polite">{ui.announcement}</div>

<style>
	:global(.nj-toast) {
		background: var(--raised);
		color: var(--ink);
		border: 1px solid var(--line-strong);
		border-left: 2px solid var(--teal);
		box-shadow: var(--shadow);
	}
	:global(.nj-toast-bad) {
		border-left-color: var(--red);
	}
	:global(.nj-toast-info) {
		border-left-color: var(--muted);
	}
	:global([data-scope='toast'][data-part='group']) {
		bottom: 60px !important;
	}
</style>
