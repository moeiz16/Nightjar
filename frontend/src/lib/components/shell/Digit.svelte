<script lang="ts">
	// One rolling digit (Tick, DESIGN.md §11): a vertical 0–9 strip driven by a Tween.
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';

	let { n, fast = false }: { n: number; fast?: boolean } = $props();
	const pos = Tween.of(() => n, {
		duration: () => (prefersReducedMotion.current ? 0 : fast ? 70 : 420),
		easing: cubicOut
	});
</script>

<span class="d" aria-hidden="true"><span style="transform: translateY({-pos.current}em)">{#each { length: 10 } as _, i (i)}<i>{i}</i>{/each}</span></span>

<style>
	.d {
		display: inline-block;
		height: 1em;
		line-height: 1;
		overflow: hidden;
		text-align: center;
		vertical-align: bottom;
	}
	.d > span {
		display: flex;
		flex-direction: column;
		will-change: transform;
	}
	i {
		font-style: normal;
		height: 1em;
		line-height: 1;
	}
</style>
