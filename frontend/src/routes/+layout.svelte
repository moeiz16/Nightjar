<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { onNavigate } from '$app/navigation';
	import { prefersReducedMotion } from 'svelte/motion';
	import Toasts from '$lib/components/shell/Toasts.svelte';
	import { ui } from '$lib/stores/ui.svelte';

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
<Toasts />
