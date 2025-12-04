<script lang="ts" generics="V extends string">
	// A segmented choice on Skeleton's Segment (Zag radio group), drawn as a row of small lamp windows: the chosen
	// one is lit. Arrow keys move.
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
		background="bg-[var(--sunk)] shadow-[inset_0_0_0_1px_var(--rule)]"
		padding="p-0.5"
		gap="gap-0"
		rounded="rounded-base"
		indicatorBg="bg-[var(--acc)]"
		indicatorText="text-[var(--acc-ink)]"
		indicatorRounded="rounded-[1px]"
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
		max-width: 100%;
	}
	.choice :global(.choice-item) {
		height: 26px;
		padding-inline: 11px;
		font: 700 13px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--dim);
		white-space: nowrap;
	}
	.choice.md :global(.choice-item) {
		height: 32px;
		padding-inline: 14px;
		font-size: 14px;
	}
	.choice :global([data-part='root']) {
		position: relative;
		max-width: 100%;
		overflow-x: auto;
		scrollbar-width: none;
	}
	.choice :global([data-state='checked']) {
		color: var(--acc-ink);
	}
	.choice :global(.focused) {
		outline: 2px solid var(--acc);
		outline-offset: -2px;
	}
</style>
