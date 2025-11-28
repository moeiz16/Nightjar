<script lang="ts" generics="V extends string">
	// A segmented choice on Skeleton's Segment (Zag radio group): arrow keys move, the indicator slides.
	import { Segment } from '@skeletonlabs/skeleton-svelte';
	let {
		value = $bindable(),
		options,
		label,
		onchange,
		size = 'sm'
	}: {
		value: V;
		options: readonly { value: V; label: string; disabled?: boolean }[];
		label: string;
		onchange?: (v: V) => void;
		size?: 'sm' | 'md';
	} = $props();
	const name = $props.id();
</script>

<span class="choice {size}">
	<span id="{name}-l" class="sr">{label}</span>
	<Segment
		{name}
		{value}
		onValueChange={(e) => {
			if (e.value === null || e.value === value) return;
			value = e.value as V;
			onchange?.(value);
		}}
		labelledby="{name}-l"
		background="preset-outlined-surface-300-700"
		padding="p-0.5"
		gap="gap-0"
		rounded="rounded-base"
		indicatorBg="preset-filled-primary-700-300"
		indicatorText="text-[var(--teal-ink)]"
		indicatorRounded="rounded-[2px]"
	>
		{#each options as o (o.value)}
			<Segment.Item value={o.value} disabled={o.disabled} classes="choice-item">{o.label}</Segment.Item>
		{/each}
	</Segment>
</span>

<style>
	.choice {
		display: inline-flex;
		flex: none;
	}
	.choice :global(.choice-item) {
		height: 24px;
		padding-inline: 10px;
		font: 500 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--ink-2);
	}
	.choice.md :global(.choice-item) {
		height: 30px;
		padding-inline: 12px;
	}
	.choice :global([data-part='root']) {
		position: relative;
	}
	.choice :global(.focused) {
		outline: 2px solid var(--teal);
		outline-offset: -2px;
	}
</style>
