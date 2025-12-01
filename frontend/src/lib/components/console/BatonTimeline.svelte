<script lang="ts">
	// The baton timeline (DESIGN.md §6.4): four lane tracks, one dot per step, S-curve hand-offs, and Replay.
	// The current step is lit; later steps sit at 35 %; the baton bead travels along each hand-off.
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicInOut } from 'svelte/easing';
	import { tick } from 'svelte';
	import type { BatonRow } from '$lib/util/baton';
	import { handoffPath, nextStep, rowSub, stepInterval } from '$lib/util/baton';
	import { timeOf } from '$lib/time/clock';
	import StepDrawer from './StepDrawer.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
	import Choice from '$lib/components/ui/Choice.svelte';
	import { Switch } from '@skeletonlabs/skeleton-svelte';
	import type { IncidentSummary } from '$lib/api/types';

	let {
		rows,
		all = $bindable(false),
		incident,
		selectedKey = $bindable<string | null>(null),
		compact = false
	}: { rows: BatonRow[]; all?: boolean; incident: IncidentSummary; selectedKey?: string | null; compact?: boolean } = $props();

	let playhead = $state(-1); // -1 = settled (everything lit)
	let playing = $state(false);
	let speed = $state<1 | 4>(1);
	let grid = $state<HTMLDivElement | null>(null);
	let lanesEl = $state<HTMLDivElement | null>(null);
	let pts = $state<{ x: number; y: number }[]>([]);
	let laneX = $state<number[]>([]);
	let H = $state(0);
	const cur = $derived(playhead < 0 ? rows.length - 1 : playhead);
	const selected = $derived(rows.find((r) => r.key === selectedKey) ?? null);
	const waitRow = $derived(rows.findLastIndex((r) => r.state === 'wait'));

	function measure() {
		if (!grid || !lanesEl) return;
		const gx = grid.getBoundingClientRect().left;
		laneX = [...lanesEl.querySelectorAll<HTMLElement>('.who')].map((w) => {
			const b = w.getBoundingClientRect();
			return b.left - gx + b.width / 2;
		});
		const rowEls = [...grid.querySelectorAll<HTMLElement>('.brow')];
		pts = rowEls.map((r, i) => ({ x: laneX[(rows[i]?.lane ?? 1) - 1] ?? 0, y: r.offsetTop + 15 }));
		H = grid.offsetHeight;
	}
	$effect(() => {
		void rows.length;
		void all;
		void compact;
		tick().then(measure);
		document.fonts?.ready.then(measure);
		const ro = new ResizeObserver(() => measure());
		if (grid) ro.observe(grid);
		return () => ro.disconnect();
	});

	// ---------- replay ----------
	const bead = new Tween(0, { duration: 900, easing: cubicInOut });
	let beadPath = $state('');
	function goTo(i: number, animate = true) {
		const prev = cur;
		playhead = Math.max(0, Math.min(rows.length - 1, i));
		if (animate && !prefersReducedMotion.current && playhead === prev + 1 && pts[playhead] && pts[prev]) {
			beadPath = handoffPath(pts[prev]!, pts[playhead]!);
			bead.set(0, { duration: 0 }).then(() => bead.set(1, { duration: speed === 4 ? 280 : 900 }));
		}
	}
	function play() {
		if (playing) {
			playing = false;
			return;
		}
		if (playhead < 0 || playhead >= rows.length - 1) goTo(0, false);
		playing = true;
	}
	$effect(() => {
		if (!playing) return;
		const id = setInterval(() => {
			const n = nextStep(cur, rows.length);
			if (n === null) {
				playing = false;
				return;
			}
			goTo(n);
		}, stepInterval(speed));
		return () => clearInterval(id);
	});
	function key(e: KeyboardEvent) {
		if (e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;
		if (e.key === 'ArrowRight') {
			e.preventDefault();
			playing = false;
			goTo(cur + 1);
		} else if (e.key === 'ArrowLeft') {
			e.preventDefault();
			playing = false;
			goTo(cur - 1, false);
		} else if (e.key === 'Escape' && selectedKey) {
			selectedKey = null;
		}
	}
	function exportReplay() {
		const lines = rows.map((r) => `${timeOf(r.at, undefined, true)}\t${r.who}\t${r.title}\t${rowSub(r)}`);
		const blob = new Blob([`${incident.reference} · ${incident.title}\n\n${lines.join('\n')}\n`], { type: 'text/plain' });
		const a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = `${incident.reference}-replay.txt`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
	const dim = (i: number) => playhead >= 0 && i > playhead;
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="baton" class:compact onkeydown={key}>
	<div class="replay">
		<span class="transport" role="group" aria-label="Replay">
			<button class="btn btn-sm preset-outlined-surface-300-700" type="button" aria-label="Previous step" onclick={() => ((playing = false), goTo(cur - 1, false))}>⏮</button>
			<button class="btn btn-sm {playing ? 'preset-filled-primary-700-300' : 'preset-outlined-surface-300-700'}" type="button" onclick={play} aria-pressed={playing}>{playing ? '❚❚ Pause' : '▶ Replay'}</button>
			<button class="btn btn-sm preset-outlined-surface-300-700" type="button" aria-label="Next step" onclick={() => ((playing = false), goTo(cur + 1))}>⏭</button>
		</span>
		<Choice label="Replay speed" value={speed === 1 ? '1' : '4'} options={[{ value: '1', label: '1×' }, { value: '4', label: '4×' }]} onchange={(v) => (speed = v === '4' ? 4 : 1)} />
		<span class="steps" aria-hidden="true">
			{#each rows as r, i (r.key)}<i class:on={i <= cur} class:now={r.state === 'wait' && i <= cur}></i>{/each}
		</span>
		<span class="sp">
			<span class="mono stepl" aria-live="polite">step {cur + 1} of {rows.length}{rows[cur] ? ` · ${timeOf(rows[cur]!.at, undefined, true)}` : ''}</span>
			{#if !compact}
				<Switch name="all-activity" checked={all} onCheckedChange={(e) => (all = e.checked)} classes="tog" controlActive="bg-[var(--teal)]" controlInactive="bg-[var(--line-strong)]" compact={false}>all activity</Switch>
				<button class="lnk" type="button" onclick={exportReplay}>export replay</button>
			{/if}
		</span>
	</div>

	<div class="lanes" bind:this={lanesEl} aria-hidden="true">
		<div></div>
		<div class="ln"><span class="who">T</span><span class="nm">triage</span></div>
		<div class="ln"><span class="who">E</span><span class="nm">enrichment</span></div>
		<div class="ln"><span class="who">R</span><span class="nm">response</span></div>
		<div class="ln people"><span class="who">P</span><span class="nm">people</span></div>
		<div class="hd">what happened, and why</div>
	</div>

	<div class="rows" bind:this={grid} role="list" aria-label="What happened, step by step. Left and right arrows step through the replay.">
		{#each rows as r, i (r.key)}
			<div class="brow {r.state}" class:minor={r.minor} class:dim={dim(i)} class:head={playhead >= 0 && i === playhead} class:sel={selectedKey === r.key} role="listitem">
				<span class="tm mono">{timeOf(r.at, undefined, true)}</span>
				<button class="card" type="button" onclick={() => (selectedKey = selectedKey === r.key ? null : r.key)} aria-expanded={selectedKey === r.key}>
					<b>{#if r.state === 'ok'}<span class="ck" aria-hidden="true">✓</span>{/if}{r.title}</b>
					<small><span class="whos">{r.who}</span> · {#if r.step?.kind === 'verdict' && r.why}<em>why</em> {r.why}{:else}{rowSub(r)}{/if}</small>
				</button>
			</div>
		{/each}
		<svg class="links" width="100%" height={H} aria-hidden="true">
			{#each laneX as x, l (l)}<line class="guide" x1={x} y1="0" x2={x} y2={H} />{/each}
			{#each pts as p, i (i)}
				{#if i > 0}
					<path class:w={rows[i]?.state === 'wait'} class:dim={dim(i)} d={handoffPath(pts[i - 1]!, p)} />
				{/if}
			{/each}
			{#if waitRow >= 0 && pts[waitRow] && laneX[3] !== undefined}
				{@const wp = pts[waitRow]!}
				<path class="w" d="M{wp.x},{wp.y} C{wp.x + 20},{wp.y + 2} {laneX[3]},{wp.y - 8} {laneX[3]},{wp.y}" />
				<circle class="halo" cx={laneX[3]} cy={wp.y} r="9" />
				<circle class="dot now" cx={laneX[3]} cy={wp.y} r="4.5" />
			{/if}
			{#each pts as p, i (i)}
				<circle class="dot" class:p={rows[i]?.lane === 4} class:wd={rows[i]?.state === 'wait'} class:dim={dim(i)} class:lit={playhead >= 0 && i === playhead} cx={p.x} cy={p.y} r="4.5" />
			{/each}
		</svg>
		{#if beadPath && bead.current > 0 && bead.current < 1}
			<i class="bead" style="offset-path: path('{beadPath}'); offset-distance: {bead.current * 100}%; opacity: {Math.min(1, bead.current * 6, (1 - bead.current) * 6)}"></i>
		{/if}
	</div>
</div>

<Sheet kind="sheet" label={selected ? `Step: ${selected.title}` : 'Step'} open={!!selected} onclose={() => (selectedKey = null)}>
	{#if selected}<StepDrawer row={selected} {incident} onclose={() => (selectedKey = null)} />{/if}
</Sheet>

<style>
	.baton {
		--tcol: 64px;
		--lane: 62px;
		display: flex;
		flex-direction: column;
		min-height: 0;
		outline: none;
	}
	.replay {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 22px;
		border-bottom: 1px solid var(--line);
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		flex-wrap: nowrap;
		white-space: nowrap;
	}
	.steps {
		display: flex;
		gap: 3px;
		margin-left: 6px;
		min-width: 0;
		overflow: hidden;
		flex: 0 1 auto;
	}
	.steps i {
		width: 14px;
		height: 6px;
		border-radius: 1px;
		background: var(--line-strong);
	}
	.steps i.on {
		background: var(--teal);
	}
	.steps i.now {
		background: var(--lav);
	}
	.sp {
		margin-left: auto;
		display: flex;
		gap: 14px;
		align-items: center;
	}
	.transport {
		display: inline-flex;
		gap: 2px;
	}
	.replay :global(.tog) {
		display: inline-flex;
		gap: 8px;
		align-items: center;
		cursor: pointer;
		white-space: nowrap;
	}
	.lanes,
	.brow {
		display: grid;
		grid-template-columns: var(--tcol) repeat(4, var(--lane)) minmax(0, 1fr);
	}
	.lanes {
		height: 46px;
		border-bottom: 1px solid var(--line);
		align-items: center;
		flex: none;
	}
	.ln {
		display: grid;
		justify-items: center;
		gap: 4px;
		font: 400 10.5px/1 var(--sans);
		font-variation-settings: 'MONO' 0;
		color: var(--muted);
	}
	.who {
		width: 20px;
		height: 20px;
		border-radius: 50%;
		display: grid;
		place-items: center;
		font: 600 10px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		border: 1px solid var(--teal-line);
		color: var(--teal);
		background: var(--pane);
	}
	.people .who {
		border-color: color-mix(in srgb, var(--lav) 55%, transparent);
		color: var(--lav);
	}
	.hd {
		padding-left: 18px;
		font: 400 11.5px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--faint);
	}
	.rows {
		position: relative;
	}
	.brow {
		min-height: 50px;
		position: relative;
		transition: opacity 240ms ease-out;
	}
	.brow.dim {
		opacity: 0.35;
	}
	.tm {
		padding: 11px 0 0 18px;
		font-size: 11.5px;
		line-height: 1;
		color: var(--faint);
		position: relative;
		z-index: 1;
	}
	.card {
		grid-column: 6;
		padding: 8px 18px;
		border-top: 1px solid var(--line);
		text-align: left;
		min-width: 0;
		display: block;
		width: 100%;
	}
	.brow:first-child .card {
		border-top: 0;
	}
	.card:hover b {
		color: var(--teal);
	}
	.card b {
		display: block;
		font-size: 13.5px;
		line-height: 18px;
		font-weight: 500;
		color: var(--ink);
	}
	.card small {
		display: block;
		margin-top: 3px;
		font: 400 11.5px/15px var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		overflow-wrap: anywhere;
	}
	.card small .whos {
		color: var(--ink-2);
	}
	.card small em {
		font-style: normal;
		color: var(--teal);
	}
	.ck {
		margin-right: 6px;
		color: var(--teal);
		font-family: var(--mono);
		font-variation-settings: 'MONO' 1;
	}
	.brow.minor .card b {
		font-weight: 400;
		color: var(--ink-2);
		font-size: 12.5px;
	}
	.brow.minor {
		min-height: 40px;
	}
	.brow.wait {
		background: linear-gradient(90deg, transparent, var(--lav-wash) 30%);
	}
	.brow.wait::before,
	.brow.head::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 2px;
		background: var(--lav);
	}
	.brow.head {
		background: var(--sel);
	}
	.brow.head::before {
		background: var(--teal);
	}
	.brow.sel .card b {
		color: var(--teal);
	}
	.brow.wait .card small,
	.brow.wait .tm {
		color: var(--lav);
	}
	svg.links {
		position: absolute;
		left: 0;
		top: 0;
		pointer-events: none;
		overflow: visible;
	}
	.guide {
		stroke: var(--line);
		stroke-dasharray: 2 4;
	}
	path {
		fill: none;
		stroke: var(--teal);
		stroke-width: 1.6;
		opacity: 0.7;
		transition: opacity 360ms ease-out;
	}
	path.dim {
		opacity: 0.15;
	}
	path.w {
		stroke: var(--lav);
		stroke-dasharray: 3 4;
		opacity: 1;
	}
	.dot {
		fill: var(--pane);
		stroke: var(--teal);
		stroke-width: 1.6;
		transition: opacity 240ms;
	}
	.dot.p {
		stroke: var(--lav);
	}
	.dot.wd {
		stroke: var(--lav);
	}
	.dot.dim {
		opacity: 0.35;
	}
	.dot.lit {
		fill: var(--teal);
	}
	.dot.now {
		fill: var(--lav);
		stroke: var(--lav);
	}
	.halo {
		fill: none;
		stroke: var(--lav);
		opacity: 0.35;
	}
	.bead {
		position: absolute;
		left: 0;
		top: 0;
		width: 18px;
		height: 6px;
		border-radius: 3px;
		background: var(--teal);
		box-shadow: 0 0 10px var(--teal);
		offset-rotate: auto;
		pointer-events: none;
		z-index: 2;
	}
	.compact .replay {
		padding: 10px 14px;
	}
	@media (max-width: 760px) {
		.baton {
			--tcol: 8px;
			--lane: 20px;
		}
		.replay .lnk {
			display: none;
		}
		.tm {
			display: none;
		}
		.nm,
		.hd {
			display: none;
		}
		.who {
			width: 16px;
			height: 16px;
			font-size: 9px;
		}
		.card {
			padding: 8px 12px;
		}
		.replay {
			padding: 10px 12px;
		}
		.steps {
			display: none;
		}
		.replay {
			flex-wrap: wrap;
		}
		.replay .sp {
			margin-left: 0;
		}
	}
</style>
