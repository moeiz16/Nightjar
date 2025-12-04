<script lang="ts">
	// Every overlay is a Skeleton Modal (Zag dialog: focus trap, Escape, scroll lock, labelled). Two shapes: a sheet
	// that rises from the bottom with a red top edge (step detail, an asset, a person), and a dialog that drops from
	// the station head (⌘K, a decision, the phone's panel). Detail never opens as a side column.
	import type { Snippet } from 'svelte';
	import { Modal } from '@skeletonlabs/skeleton-svelte';
	import { prefersReducedMotion } from 'svelte/motion';
	let {
		open = $bindable(false),
		kind = 'sheet',
		label,
		onclose,
		children,
		width = '720px',
		initialFocus
	}: {
		open?: boolean;
		/** 'sheet' rises from the bottom; 'dialog' drops from the axis */
		kind?: 'sheet' | 'dialog';
		label: string;
		onclose?: () => void;
		children: Snippet;
		width?: string;
		initialFocus?: () => HTMLElement | null;
	} = $props();
	const still = $derived(prefersReducedMotion.current);
	const flyIn = $derived(still ? { duration: 0 } : kind === 'sheet' ? { y: 24, duration: 220 } : { y: -12, duration: 160 });
	const flyOut = $derived(still ? { duration: 0 } : kind === 'sheet' ? { y: 24, duration: 160 } : { y: -8, duration: 120 });
</script>

<Modal
	{open}
	onOpenChange={(e) => {
		open = e.open;
		if (!e.open) onclose?.();
	}}
	initialFocusEl={initialFocus}
	aria-label={label}
	backdropBackground="bg-[rgba(20,6,3,0.45)] dark:bg-[rgba(8,2,1,0.7)] backdrop-blur-[2px]"
	positionerJustify={kind === 'sheet' ? 'justify-center' : 'justify-center'}
	positionerAlign={kind === 'sheet' ? 'items-end' : 'items-start'}
	positionerPadding={kind === 'sheet' ? 'p-0' : 'pt-[64px] px-3 pb-3'}
	contentBase="nj-sheet {kind}"
	transitionsPositionerIn={flyIn}
	transitionsPositionerOut={flyOut}
	transitionsBackdropIn={{ duration: still ? 0 : 100 }}
	transitionsBackdropOut={{ duration: still ? 0 : 100 }}
>
	{#snippet content()}
		<div class="inner" style="--w:{width}">{@render children()}</div>
	{/snippet}
</Modal>

<style>
	:global(.nj-sheet) {
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-2);
		box-shadow: var(--shadow);
		max-width: 100vw;
		outline: none;
	}
	:global(.nj-sheet.sheet) {
		width: min(100vw, 1180px);
		max-height: min(84vh, 840px);
		border-bottom: 0;
		border-top: 3px solid var(--acc);
		display: flex;
		flex-direction: column;
	}
	:global(.nj-sheet.dialog) {
		border-radius: 0;
		border-top: 3px solid var(--acc);
		max-height: calc(100vh - 90px);
		display: flex;
		flex-direction: column;
	}
	.inner {
		display: flex;
		flex-direction: column;
		min-height: 0;
		overflow: auto;
		width: min(var(--w), calc(100vw - 24px));
	}
	:global(.nj-sheet.sheet) .inner {
		width: 100%;
	}
</style>
