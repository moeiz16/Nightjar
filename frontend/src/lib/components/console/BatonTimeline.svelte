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
	// the log opens on its first steps; the rest is one click (or the replay) away
	const CAP = 10;
	let full = $state(false);
	const capped = $derived(!compact && !full && playhead < 0 && rows.length > CAP + 2);
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
			<button class="btn btn-sm {playing ? 'preset-filled-primary-500' : 'preset-outlined-surface-300-700'}" type="button" onclick={play} aria-pressed={playing}>{playing ? '❚❚ Pause' : '▶ Replay'}</button>
			<button class="btn btn-sm preset-outlined-surface-300-700" type="button" aria-label="Next step" onclick={() => ((playing = false), goTo(cur + 1))}>⏭</button>
		</span>
		<Choice label="Replay speed" value={speed === 1 ? '1' : '4'} options={[{ value: '1', label: '1×' }, { value: '4', label: '4×' }]} onchange={(v) => (speed = v === '4' ? 4 : 1)} />
		<span class="steps" aria-hidden="true">
			{#each rows as r, i (r.key)}<i class:on={i <= cur} class:now={r.state === 'wait' && i <= cur}></i>{/each}
		</span>
		<span class="sp">
			<span class="mono stepl" aria-live="polite">step {cur + 1} of {rows.length}{rows[cur] ? ` · ${timeOf(rows[cur]!.at, undefined, true)}` : ''}</span>
			{#if !compact}
				<Switch name="all-activity" checked={all} onCheckedChange={(e) => (all = e.checked)} classes="tog" controlActive="bg-[var(--acc)]" controlInactive="bg-[var(--rule-2)]" compact={false}>all activity</Switch>
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
	</div>

	<div class="rows" class:capped bind:this={grid} role="list" aria-label="What happened, step by step. Left and right arrows step through the replay.">
		{#each rows as r, i (r.key)}
			<div class="brow {r.state}" class:minor={r.minor} class:dim={dim(i)} class:head={playhead >= 0 && i === playhead} class:sel={selectedKey === r.key} role="listitem">
				<span class="tm mono">{timeOf(r.at, undefined, true)}</span>
				<!-- one line per step; who and why open in the step sheet -->
				<button class="card" type="button" onclick={() => (selectedKey = selectedKey === r.key ? null : r.key)} aria-expanded={selectedKey === r.key} title="{r.who} · {r.step?.kind === 'verdict' && r.why ? r.why : rowSub(r)}">
					<b>{#if r.state === 'ok'}<span class="ck" aria-hidden="true">✓</span>{/if}{r.title}</b>
					<small class="sr">{r.who} · {r.step?.kind === 'verdict' && r.why ? r.why : rowSub(r)}</small>
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
	{#if capped}<button class="all lnk" type="button" onclick={() => (full = true)}>Show all {rows.length} steps</button>{/if}
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
		border-bottom: 1px solid var(--rule);
		font: 500 12.5px/1 var(--code);
		color: var(--dim);
		background: var(--sunk);
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
		width: 10px;
		height: 14px;
		border-radius: 1px;
		background: var(--rule-2);
	}
	.steps i.on {
		background: var(--acc);
	}
	.steps i.now {
		background: var(--person);
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
		height: 52px;
		border-bottom: 1px solid var(--rule);
		align-items: center;
		flex: none;
	}
	.ln {
		display: grid;
		justify-items: center;
		gap: 4px;
		font: 700 11px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--dim);
	}
	.who {
		width: 22px;
		height: 22px;
		border-radius: 1px;
		display: grid;
		place-items: center;
		font: 800 13px/1 var(--display);
		box-shadow: inset 0 0 0 1px var(--acc-line);
		color: var(--acc-hi);
		background: var(--plate);
	}
	.people .who {
		box-shadow: inset 0 0 0 1px var(--person);
		color: var(--person);
	}
	.rows {
		position: relative;
	}
	.rows.capped {
		max-height: 404px;
		overflow: hidden;
		mask-image: linear-gradient(180deg, #000 80%, transparent);
	}
	.all {
		display: block;
		margin: 10px auto 14px;
	}
	.brow {
		min-height: 40px;
		position: relative;
		transition: opacity 240ms ease-out;
	}
	.brow.dim {
		opacity: 0.35;
	}
	.tm {
		padding: 12px 0 0 18px;
		font-size: 12px;
		line-height: 1;
		color: var(--faint);
		position: relative;
		z-index: 1;
	}
	.card {
		grid-column: 6;
		padding: 8px 18px;
		border-top: 1px solid var(--rule);
		text-align: left;
		min-width: 0;
		display: block;
		width: 100%;
	}
	.brow:first-child .card {
		border-top: 0;
	}
	.card:hover b {
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	.card b {
		display: block;
		font-size: 14.5px;
		line-height: 19px;
		font-weight: 700;
		color: var(--ink);
	}
	.ck {
		margin-right: 6px;
		color: var(--ok);
		font-family: var(--code);
	}
	.brow.minor .card b {
		font-weight: 500;
		color: var(--ink-2);
		font-size: 13.5px;
	}
	.brow.minor {
		min-height: 40px;
	}
	.brow.wait {
		background: linear-gradient(90deg, transparent, var(--person-wash) 30%);
	}
	.brow.wait::before,
	.brow.head::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--person);
	}
	.brow.head {
		background: var(--sel);
	}
	.brow.head::before {
		background: var(--acc);
	}
	.brow.sel .card b {
		color: var(--acc-hi);
	}
	.brow.wait .tm {
		color: var(--person);
	}
	svg.links {
		position: absolute;
		left: 0;
		top: 0;
		pointer-events: none;
		overflow: visible;
	}
	.guide {
		stroke: var(--rule);
		stroke-dasharray: 2 4;
	}
	path {
		fill: none;
		stroke: var(--dim);
		stroke-width: 1.6;
		opacity: 0.8;
		transition: opacity 360ms ease-out;
	}
	path.dim {
		opacity: 0.15;
	}
	path.w {
		stroke: var(--person);
		stroke-dasharray: 3 4;
		opacity: 1;
	}
	.dot {
		fill: var(--plate);
		stroke: var(--ink-2);
		stroke-width: 1.6;
		transition: opacity 240ms;
	}
	.dot.p {
		stroke: var(--person);
	}
	.dot.wd {
		stroke: var(--person);
	}
	.dot.dim {
		opacity: 0.35;
	}
	.dot.lit {
		fill: var(--acc);
	}
	.dot.now {
		fill: var(--person);
		stroke: var(--person);
	}
	.halo {
		fill: none;
		stroke: var(--person);
		opacity: 0.35;
	}
	.bead {
		position: absolute;
		left: 0;
		top: 0;
		width: 18px;
		height: 6px;
		border-radius: 1px;
		background: var(--acc);
		box-shadow: 0 0 12px var(--acc-glow);
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
		.nm {
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
