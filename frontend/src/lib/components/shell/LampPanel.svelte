<script lang="ts">
	// The lamp panel (DESIGN.md §3): the navigation as an annunciator. Eight engraved windows in a 4 × 2 grid; the
	// window you are in is lit; a window with something that needs a person shows a count and flashes twice, then
	// glows (ISA-18.1 style). Alt+1 … Alt+8 still work.
	import { page } from '$app/state';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { WINDOWS, windowFor } from '$lib/stores/ui.svelte';

	let {
		onpick,
		cols = 4,
		items
	}: {
		onpick?: () => void;
		cols?: number;
		/** public pages: their own windows instead of the stations */
		items?: { n: number; href: string; label: string; lit?: boolean }[];
	} = $props();
	const current = $derived(items ? (items.find((i) => i.lit)?.n ?? 0) : windowFor(page.url.pathname));
	const windows = $derived(items ?? WINDOWS.filter((w) => !w.perm || session.can(w.perm)));
	// what lights a window: approvals waiting (the watch) and decisions only the CISO can make (the brief)
	const alarms = $derived<Record<number, { n: number; word: string }>>(items ? {} : {
		1: { n: live.incidents.reduce((s, i) => s + (i.pendingApprovals || 0), 0), word: 'waiting for a person' },
		8: { n: live.clocks.filter((c) => c.state === 'not_started').length, word: 'decision to make' }
	});
	const nav = $derived(items ? 'Nightjar SOC' : 'Stations');
</script>

<nav class="lamps" aria-label={nav} style="--cols:{cols}">
	{#each windows as w (w.n)}
		{@const al = alarms[w.n]}
		<a
			class="win"
			class:lit={current === w.n}
			class:alarm={!!al?.n}
			class:flash={!!al?.n}
			href={w.href}
			aria-current={current === w.n ? 'page' : undefined}
			title={items ? w.label : `${w.label} (Alt+${w.n})`}
			onclick={() => onpick?.()}
		>
			<span class="lbl">{w.label}</span>
			{#if al?.n}<span class="cnt" aria-label="{al.n} {al.word}">▲ {al.n}</span>{/if}
			<span class="n" aria-hidden="true">{w.n}</span>
		</a>
	{/each}
</nav>

<style>
	.lamps {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		gap: 4px;
		padding: 4px;
		background: var(--sunk);
		border: 1px solid var(--rule);
		border-radius: 2px;
	}
	.win {
		position: relative;
		display: grid;
		place-items: center;
		height: 44px;
		min-width: 0;
		padding: 0 14px;
		text-decoration: none;
		background: linear-gradient(180deg, color-mix(in srgb, var(--raised) 70%, var(--plate)), var(--plate));
		box-shadow:
			inset 0 0 0 1px var(--rule),
			inset 0 1px 0 rgba(255, 255, 255, 0.03);
		border-radius: 1px;
		transition: background 0.18s;
	}
	.lbl {
		font: 700 15px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.2em;
		color: var(--faint);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 100%;
		padding-left: 0.2em;
	}
	.n {
		position: absolute;
		left: 6px;
		bottom: 4px;
		font: 400 9.5px/1 var(--code);
		color: var(--faint);
		opacity: 0.7;
	}
	.win:hover {
		background: var(--raised);
	}
	.win:hover .lbl {
		color: var(--ink-2);
	}
	.win.lit {
		background: radial-gradient(120% 140% at 50% 50%, color-mix(in srgb, var(--acc) 34%, transparent), color-mix(in srgb, var(--acc) 10%, var(--plate)) 70%);
		box-shadow:
			inset 0 0 0 1px var(--acc-line),
			0 0 24px -8px var(--acc-glow);
	}
	.win.lit .lbl {
		color: var(--ink);
		text-shadow: 0 0 14px var(--acc-glow);
	}
	.win.lit .n {
		color: var(--ink-2);
	}
	.cnt {
		position: absolute;
		right: 3px;
		top: 3px;
		font: 600 10.5px/1 var(--code);
		padding: 2px 4px;
		background: var(--person);
		color: var(--bg);
		border-radius: 1px;
	}
	.win.alarm:not(.lit) .lbl {
		color: var(--person);
	}
	:global([data-theme='light']) .win {
		background: linear-gradient(180deg, var(--plate), var(--raised));
	}
	:global([data-theme='light']) .win.lit {
		background: var(--acc);
	}
	:global([data-theme='light']) .win.lit .lbl,
	:global([data-theme='light']) .win.lit .n {
		color: #fff;
		text-shadow: none;
	}
	@media (max-width: 1280px) {
		.lbl {
			font-size: 13.5px;
			letter-spacing: 0.14em;
		}
		.win {
			padding: 0 8px;
		}
	}
</style>
