<script lang="ts">
	// The shift chart (DESIGN.md §0.2): the night across, the response down. Intake is an hourly bar strip; each
	// incident is one line that steps down Triage → Investigate → Contain → Report at the times its log records,
	// like a train on a dispatcher's timetable graph. The x-scale is the shift axis's, column for column.
	// Open incidents end in the "now" column as chips; finished ones end where they finished.
	import { prefersReducedMotion } from 'svelte/motion';
	import { Accordion } from '@skeletonlabs/skeleton-svelte';
	import type { IncidentSummary, ShiftLogEntry } from '$lib/api/types';
	import { timeOf } from '$lib/time/clock';
	import { incidentPaths, intakeBars, LANES, LANE_LABEL, stepPath, xOf, type IncidentPath, type ShiftWindow } from '$lib/util/shift';

	let {
		win,
		incidents,
		log,
		intake = [],
		tz,
		compact = false,
		dawn = false,
		hrefFor = (ref: string) => `/incidents/${ref}`
	}: {
		win: ShiftWindow;
		incidents: IncidentSummary[];
		log: ShiftLogEntry[];
		intake?: { at: Date; count: number }[];
		tz: string;
		/** the brief's read-only night band: shorter lanes, no now column */
		compact?: boolean;
		dawn?: boolean;
		hrefFor?: (ref: string) => string;
	} = $props();

	let W = $state(800);
	const LANE_H = $derived(compact ? 30 : 42);
	const INTAKE_H = $derived(compact ? 30 : 46);
	const PAD = 8;
	const H = $derived(INTAKE_H + LANE_H * 4 + PAD);
	const paths = $derived(incidentPaths(incidents, log, win));
	const bars = $derived(intakeBars(intake, win));
	const hourW = $derived(Math.max(2, (W * 3_600_000) / Math.max(1, +win.end - +win.start) - 2));
	const n = $derived(Math.max(1, paths.length));
	const laneTop = (lane: number) => (lane === 0 ? 0 : INTAKE_H + (lane - 1) * LANE_H);
	const yOf = (lane: number, track: number) => {
		const inner = LANE_H - 14;
		const step = n > 1 ? Math.min(6, inner / (n - 1)) : 0;
		return laneTop(lane) + 7 + (n > 1 ? track * step + (inner - step * (n - 1)) / 2 : inner / 2);
	};
	const xPx = (t: Date) => xOf(t, win) * W;
	let hot = $state<string | null>(null);
	const byLane = $derived(LANES.map((_, i) => paths.filter((p) => p.open && p.lane === i)));
	const SEV_TONE: Record<string, string> = { critical: 'red', high: 'red', medium: 'amber', low: 'muted', info: 'muted' };
	const tone = (p: IncidentPath) => (p.waiting ? 'lav' : SEV_TONE[p.severity] ?? 'muted');
	const still = $derived(prefersReducedMotion.current);
	let phoneOpen = $state<string[]>(['contain']);
	const summary = $derived(
		`${paths.length} incidents on this shift: ` +
			LANES.slice(1)
				.map((l, i) => `${byLane[i + 1]!.length} open in ${l}`)
				.join(', ')
	);
</script>

<div class="chart" class:compact class:dawn role="figure" aria-label={summary}>
	<div class="lanes" aria-hidden="true">
		{#each LANES as l, i (l)}
			<div class="ln" style="height:{i === 0 ? INTAKE_H : LANE_H}px">
				<span>{LANE_LABEL[l]}</span>
				{#if i > 0 && !compact}<b class:on={byLane[i]!.length > 0}>{byLane[i]!.length}</b>{/if}
			</div>
		{/each}
	</div>

	<div class="plot" bind:clientWidth={W}>
		<svg width={W} height={H} viewBox="0 0 {W} {H}" aria-hidden="true">
			{#each win.nights as nt (nt.from.toISOString())}
				<rect class="night" x={xPx(nt.from)} y="0" width={Math.max(0, xPx(nt.to) - xPx(nt.from))} height={H} />
			{/each}
			{#each LANES as _l, i (i)}
				<line class="lane" x1="0" x2={W} y1={laneTop(i)} y2={laneTop(i)} />
			{/each}
			{#each win.ticks as t (t.at.toISOString())}
				<line class="grid" class:major={t.major} x1={xPx(t.at)} x2={xPx(t.at)} y1="0" y2={H} />
			{/each}
			{#each win.handovers as h (h.toISOString())}
				<line class="ho" x1={xPx(h)} x2={xPx(h)} y1="0" y2={H} />
			{/each}
			{#each bars as b (b.at.toISOString())}
				<rect class="bar" x={b.x * W} y={INTAKE_H - 4 - b.h * (INTAKE_H - 12)} width={Math.max(1, Math.min(hourW, W - 2 - b.x * W))} height={Math.max(1, b.h * (INTAKE_H - 12))} />
			{/each}
			{#each paths as p (p.id)}
				{@const d = stepPath(p, xPx, yOf)}
				<g class="inc {tone(p)}" class:dim={hot && hot !== p.id} class:hot={hot === p.id} class:closed={!p.open}>
					<path class="halo" {d} />
					<path class="ln2" class:draw={!still} {d} />
					{#each p.steps as s, si (si)}
						<circle class="st" cx={xPx(s.at)} cy={yOf(s.lane, p.track)} r={si === 0 ? 3.5 : 2.6} />
					{/each}
					{#each p.notices as t (t.toISOString())}
						{@const ln = p.steps.filter((s) => +s.at <= +t).at(-1)?.lane ?? p.lane}
						<text class="env mono" x={xPx(t)} y={yOf(ln, p.track) - 5} text-anchor="middle">✉</text>
					{/each}
					{#if !p.open}
						<text class="end mono" x={Math.min(W - 4, xPx(p.until) + 6)} y={yOf(p.lane, p.track) + 3.5} text-anchor={xPx(p.until) > W - 70 ? 'end' : 'start'}>{p.reference}</text>
					{/if}
				</g>
			{/each}
			<line class="now" x1={W - 1} x2={W - 1} y1="0" y2={H} />
		</svg>
		<!-- the accessible layer: every line is a link at its head -->
		{#each paths as p (p.id)}
			<a
				class="hit"
				href={hrefFor(p.reference)}
				style="left:{xOf(p.steps[0]!.at, win) * 100}%; width:{Math.max(1.5, (xOf(p.until, win) - xOf(p.steps[0]!.at, win)) * 100)}%; top:{yOf(1, p.track) - 6}px; height:{yOf(p.lane, p.track) - yOf(1, p.track) + 12}px"
				onpointerenter={() => (hot = p.id)}
				onpointerleave={() => (hot = null)}
				onfocus={() => (hot = p.id)}
				onblur={() => (hot = null)}
				aria-label="{p.reference} {p.title}: opened {timeOf(p.steps[0]!.at, tz)}, now in {LANE_LABEL[LANES[p.lane]!]}{p.open ? '' : ', finished'}{p.waiting ? ', waiting for a person' : ''}"
			></a>
		{/each}
	</div>

	{#if !compact}
		<div class="nowcol" aria-label="Open incidents by stage, now">
			{#each LANES as l, i (l)}
				<div class="cell" style="height:{i === 0 ? INTAKE_H : LANE_H}px">
					{#if i === 0}
						<span class="mono faint">{bars.at(-1) ? `${bars.at(-1)!.count.toLocaleString('en-GB')} events this hour` : 'events by hour'}</span>
					{:else}
						{#each byLane[i]! as p (p.id)}
							<a
								class="chip2 {tone(p)}"
								class:hot={hot === p.id}
								href={hrefFor(p.reference)}
								onpointerenter={() => (hot = p.id)}
								onpointerleave={() => (hot = null)}
								onfocus={() => (hot = p.id)}
								onblur={() => (hot = null)}
								title={p.title}
							>
								<span class="g" aria-hidden="true">{p.waiting ? '‖' : p.severity === 'critical' || p.severity === 'high' ? '▲' : '△'}</span>{p.reference}
								{#if p.waiting}<span class="sr">waiting for a person</span>{/if}
							</a>
						{/each}
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if !compact}
	<!-- phones: the same lanes as a list, Contain open first -->
	<div class="phone">
		<Accordion value={phoneOpen} onValueChange={(e) => (phoneOpen = e.value)} multiple collapsible spaceY="space-y-0" rounded="rounded-none">
			{#each LANES.slice(1) as l, i (l)}
				{@const list = paths.filter((p) => p.lane === i + 1)}
				<Accordion.Item value={l} controlPadding="py-3 px-4" controlRounded="rounded-none" controlHover="hover:bg-[var(--raised)]" panelPadding="px-4 pb-3" classes="acc">
					{#snippet control()}<span class="mono">{LANE_LABEL[l]}</span> <span class="mono faint">· {list.filter((p) => p.open).length} open{list.some((p) => !p.open) ? `, ${list.filter((p) => !p.open).length} finished` : ''}</span>{/snippet}
					{#snippet panel()}
						{#each list as p (p.id)}
							<a class="prow" href={hrefFor(p.reference)}>
								<span class="mono {tone(p)}">{p.waiting ? '‖' : '▲'} {p.reference}</span>
								<span>{p.title}</span>
								<span class="mono faint">since {timeOf(p.steps.at(-1)!.at, tz)}{p.open ? '' : ' · finished'}</span>
							</a>
						{:else}
							<p class="mono faint none">Nothing in {l} on this shift.</p>
						{/each}
					{/snippet}
				</Accordion.Item>
			{/each}
		</Accordion>
	</div>
{/if}

<style>
	.chart {
		display: grid;
		grid-template-columns: var(--gut) minmax(0, 1fr) var(--dl);
		padding: 10px 0 14px;
		background: var(--pane);
	}
	.chart.compact {
		grid-template-columns: 118px minmax(0, 1fr);
		padding: 8px 0 10px;
		background: transparent;
	}
	.lanes {
		border-right: 1px solid var(--line);
	}
	.ln {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 0 12px 0 16px;
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		border-top: 1px solid var(--line);
	}
	.ln:first-child {
		border-top: 0;
	}
	.ln b {
		font-weight: 600;
		color: var(--faint);
	}
	.ln b.on {
		color: var(--ink);
	}
	.plot {
		position: relative;
		min-width: 0;
		margin: 0 14px 0 6px;
	}
	svg {
		display: block;
		overflow: visible;
	}
	.night {
		fill: var(--night);
	}
	.lane {
		stroke: var(--line);
	}
	.grid {
		stroke: var(--line);
		stroke-dasharray: 2 4;
	}
	.grid.major {
		stroke: var(--line-strong);
	}
	.ho {
		stroke: var(--amber);
		stroke-opacity: 0.6;
	}
	.bar {
		fill: color-mix(in srgb, var(--teal) 45%, transparent);
	}
	.now {
		stroke: var(--teal);
		stroke-width: 2;
	}
	.inc {
		--c: var(--muted);
		transition: opacity 160ms;
	}
	.inc.red {
		--c: var(--red);
	}
	.inc.amber {
		--c: var(--amber);
	}
	.inc.lav {
		--c: var(--lav);
	}
	.inc .halo {
		fill: none;
		stroke: transparent;
		stroke-width: 7;
	}
	.inc.hot .halo {
		stroke: color-mix(in srgb, var(--c) 22%, transparent);
	}
	.inc .ln2 {
		fill: none;
		stroke: var(--c);
		stroke-width: 2;
		stroke-linejoin: round;
	}
	.inc.closed .ln2 {
		stroke-width: 1.5;
		stroke-opacity: 0.55;
	}
	/* Step: lines grow rightward from where they opened (a clip, never a dash offset) */
	.inc .ln2.draw {
		animation: nj-step 900ms var(--ease-step) both;
	}
	@keyframes nj-step {
		from {
			clip-path: inset(0 100% 0 0);
		}
		to {
			clip-path: inset(0 0 0 0);
		}
	}
	.inc .st {
		fill: var(--pane);
		stroke: var(--c);
		stroke-width: 1.5;
	}
	.inc.dim {
		opacity: 0.22;
	}
	.end {
		font-size: 10.5px;
		fill: var(--muted);
	}
	.env {
		font-size: 9px;
		fill: var(--teal);
	}
	.hit {
		position: absolute;
		display: block;
		border-radius: 2px;
	}
	.hit:focus-visible {
		outline: 2px solid var(--teal);
		outline-offset: 1px;
	}
	.nowcol {
		border-left: 1px solid var(--line-strong);
	}
	.cell {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 4px 6px;
		padding: 0 12px;
		border-top: 1px solid var(--line);
		overflow: hidden;
		font-size: 11.5px;
	}
	.cell:first-child {
		border-top: 0;
	}
	.chip2 {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 22px;
		padding: 0 7px;
		border: 1px solid var(--line-strong);
		border-radius: 3px;
		font: 500 11.5px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--ink);
		text-decoration: none;
		background: var(--sunk);
	}
	.chip2 .g {
		color: var(--muted);
	}
	.chip2.red .g {
		color: var(--red);
	}
	.chip2.amber .g {
		color: var(--amber);
	}
	.chip2.lav {
		border-color: color-mix(in srgb, var(--lav) 50%, transparent);
		background: var(--lav-wash);
	}
	.chip2.lav .g {
		color: var(--lav);
	}
	.chip2.hot,
	.chip2:hover {
		border-color: var(--ink-2);
	}
	.phone {
		display: none;
	}
	.prow {
		display: grid;
		gap: 3px;
		padding: 10px 0;
		border-top: 1px solid var(--line);
		text-decoration: none;
		font-size: 13.5px;
	}
	.prow .mono {
		font-size: 11.5px;
	}
	.none {
		font-size: 12px;
		margin: 6px 0;
	}
	.phone :global(.acc) {
		border-top: 1px solid var(--line);
	}
	@media (max-width: 1100px) {
		.ln {
			padding: 0 6px;
			font-size: 10.5px;
		}
		.ln b {
			display: none;
		}
	}
	@media (max-width: 760px) {
		.chart:not(.compact) {
			display: none;
		}
		.chart.compact {
			grid-template-columns: 70px minmax(0, 1fr);
		}
		.phone {
			display: block;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.inc .ln2.draw {
			animation: none;
		}
	}
</style>
