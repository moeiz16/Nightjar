<script lang="ts">
	// Rolling digits. Only changed digits move; separators stay put and are dimmed.
	import Digit from './Digit.svelte';
	let { value, fast = false, label, class: cls = '' }: { value: string; fast?: boolean; label?: string; class?: string } = $props();
	const chars = $derived([...value]);
</script>

<span class="tick {cls}" aria-label={label ?? value} role="img">
	{#each chars as ch, i (i)}
		{#if ch >= '0' && ch <= '9'}<Digit n={Number(ch)} {fast} />{:else}<span class="s" aria-hidden="true">{ch}</span>{/if}
	{/each}
</span>

<style>
	.tick {
		display: inline-flex;
		font-feature-settings: 'tnum', 'zero';
		white-space: nowrap;
	}
	.s {
		display: inline-block;
		opacity: 0.5;
	}
</style>
