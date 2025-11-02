<script lang="ts">
	import { fly } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import { ui } from '$lib/stores/ui.svelte';
</script>

<div class="toasts" aria-live="polite" aria-atomic="false">
	{#each ui.toasts as t (t.id)}
		<div class="toast {t.tone}" transition:fly={{ y: 8, duration: prefersReducedMotion.current ? 0 : 220 }}>
			<span class="g mono">{t.tone === 'ok' ? '✓' : t.tone === 'bad' ? '●' : '○'}</span>{t.text}
		</div>
	{/each}
</div>
<div class="sr" aria-live="polite">{ui.announcement}</div>

<style>
	.toasts {
		position: fixed;
		left: 50%;
		bottom: 44px;
		transform: translateX(-50%);
		display: grid;
		gap: 8px;
		z-index: 60;
		pointer-events: none;
		width: min(520px, calc(100vw - 32px));
	}
	.toast {
		display: flex;
		gap: 10px;
		align-items: baseline;
		padding: 11px 14px;
		border-radius: 8px;
		background: var(--raised);
		border: 1px solid var(--line-strong);
		box-shadow: var(--shadow);
		font-size: 13.5px;
		color: var(--ink);
	}
	.g {
		color: var(--teal);
	}
	.bad .g {
		color: var(--red);
	}
	.info .g {
		color: var(--muted);
	}
</style>
