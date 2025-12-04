<script lang="ts">
	// The watch dial (DESIGN.md §8): a 24-hour face like a watchman's clock dial, midnight at the top. The shift is
	// the lit sector and the hand is now. Intake stands out from its ring as hourly bars; each incident runs clockwise
	// along its stage ring and steps inward (triage → investigate → contain → report) at the times its log records.
	// Regulator deadlines due within the next day sit on the bezel ahead of the hand.
	import { scale } from 'svelte/transition';
	import { backOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { Accordion } from '@skeletonlabs/skeleton-svelte';
	import type { Deadline, IncidentSummary, ShiftLogEntry } from '$lib/api/types';
	import { timeOf } from '$lib/time/clock';
	import { regimeShort } from '$lib/util/labels';
	import {
		dialAngle,
		dialAngleOn,
		dialPath,
		incidentPaths,
		intakeBars,
		LANES,
		LANE_LABEL,
		polar,
		sectorPath,
		watchOf,
		WATCHES,
		type IncidentPath,
		type ShiftWindow
	} from '$lib/util/shift';

	let {
		win,
		incidents,
		log,
		intake = [],
		tz,
		clocks = [],
		compact = false,
		max = 600,
		hrefFor = (ref: string) => `/incidents/${ref}`
	}: {
		win: ShiftWindow;
		incidents: IncidentSummary[];
		log: ShiftLogEntry[];
		intake?: { at: Date; count: number }[];
		tz: string;
		/** running regulator clocks: the ones due within a day are marked on the bezel */
		clocks?: Deadline[];
		/** the brief's read-only dial: no lists, no lane counts */
		compact?: boolean;
		/** the largest the face is drawn, in px */
		max?: number;
		hrefFor?: (ref: string) => string;
	} = $props();

	let box = $state(480);
	const S = $derived(Math.max(240, Math.min(max, box - 64)));
	const C = $derived(S / 2);
	const R = $derived({
		face: S * 0.43,
		bezel: S * 0.48,
		hours: S * 0.408,
		barIn: S * 0.33,
		barOut: S * 0.39,
		hub: S * 0.13,
		lanes: [0, S * 0.29, S * 0.245, S * 0.2, S * 0.155]
	});

	const paths = $derived(incidentPaths(incidents, log, win));
	const bars = $derived(intakeBars(intake, win));
	const n = $derived(Math.max(1, paths.length));
	const ang = (t: Date | string) => dialAngleOn(t, win, tz);
	const rad = (lane: number, track: number) => {
		const base = R.lanes[lane] ?? R.lanes[1]!;
		if (n < 2) return base;
		const step = Math.min(4.5, (S * 0.032) / (n - 1));
		return base + (track - (n - 1) / 2) * step;
	};
	const a0 = $derived(dialAngle(win.start, tz));
	const aNow = $derived(ang(win.end));
	const nowWatch = $derived(watchOf(win.end, tz));
	const byLane = $derived(LANES.map((_, i) => paths.filter((p) => p.open && p.lane === i)));
	const openCount = $derived(paths.filter((p) => p.open).length);
	const waiting = $derived(paths.filter((p) => p.open && p.waiting).length);
	let hot = $state<string | null>(null);
	const still = $derived(prefersReducedMotion.current);

	// the unlit sector (now → the window's start, a day on) holds the lane names, written level
	const labelAngle = $derived(aNow + (a0 + 360 - aNow) / 2);
	const pt = (r: number, a: number) => polar(C, C, r, a);
	const intakeAt = $derived(pt((R.barIn + R.barOut) / 2, labelAngle));

	// hour ticks and labels around the face, every hour / every two hours
	const hours = Array.from({ length: 24 }, (_, h) => h);
	// the six watches on the bezel; the lower ones run anticlockwise so they read upright
	const watchArcs = $derived(
		WATCHES.map((w) => {
			const a1 = (w.from / 24) * 360 + 1.5;
			const a2 = (w.to / 24) * 360 - 1.5;
			const mid = (a1 + a2) / 2;
			const r = (R.face + R.bezel) / 2;
			const lower = mid > 90 && mid < 270;
			const rr = lower ? r + S * 0.009 : r - S * 0.009;
			const [x1, y1] = pt(rr, lower ? a2 : a1);
			const [x2, y2] = pt(rr, lower ? a1 : a2);
			return { ...w, d: `M${x1} ${y1} A${rr} ${rr} 0 0 ${lower ? 0 : 1} ${x2} ${y2}`, on: w.key === nowWatch.key };
		})
	);
	const handovers = $derived(win.handovers.map((h) => ang(h)));
	const nights = $derived(win.nights.map((nt) => ({ a1: ang(nt.from), a2: ang(nt.to) })));
	const hourW = 15;
	const ahead = $derived(
		clocks
			.filter((c) => c.dueAt && c.state !== 'submitted' && c.state !== 'not_required' && c.state !== 'not_started')
			.map((c) => ({ c, dt: +new Date(c.dueAt!) - +win.end }))
			.filter((x) => x.dt > 0 && x.dt < 23.5 * 3600_000)
			.map((x) => ({ ...x, a: aNow + (x.dt / 86_400_000) * 360 }))
	);
	const SEV: Record<string, string> = { critical: 'hi', high: 'hi', medium: 'mid', low: 'lo', info: 'lo' };
	const tone = (p: IncidentPath) => (p.waiting ? 'person' : (SEV[p.severity] ?? 'lo'));
	const glyph = (p: IncidentPath) => (p.waiting ? '‖' : !p.open ? '■' : p.severity === 'critical' || p.severity === 'high' ? '▲' : '△');
	const headAngle = (p: IncidentPath) => Math.max(ang(p.steps.at(-1)!.at), ang(p.until));
	const summary = $derived(
		`${paths.length} incidents on this shift: ` +
			LANES.slice(1)
				.map((l, i) => `${byLane[i + 1]!.length} open in ${l}`)
				.join(', ')
	);
	let phoneOpen = $state<string[]>(['contain']);
</script>

<div class="wd" class:compact>
	<div class="dial ticks" bind:clientWidth={box} role="figure" aria-label={summary} style="--S:{S}px">
		<div class="face" style="width:{S}px;height:{S}px">
			<!-- persistence: a soft wedge trailing the hand -->
			<div class="trail" aria-hidden="true" style="--a:{aNow}deg; inset:{C - R.face}px"></div>
			<svg width={S} height={S} viewBox="0 0 {S} {S}" aria-hidden="true">
				<circle class="bez" cx={C} cy={C} r={(R.face + R.bezel) / 2} stroke-width={R.bezel - R.face} />
				<circle class="facebg" cx={C} cy={C} r={R.face} />
				<!-- the shift: the lit sector, night shaded inside it -->
				<path class="lit" d={sectorPath(C, C, R.hub, R.face, a0, aNow)} />
				{#each nights as nt, i (i)}
					<path class="night" d={sectorPath(C, C, R.hub, R.face, nt.a1, nt.a2)} />
				{/each}
				{#each [1, 2, 3, 4] as l (l)}
					<circle class="ring" cx={C} cy={C} r={R.lanes[l]} />
				{/each}
				<circle class="ring base" cx={C} cy={C} r={R.barIn} />
				<!-- hours -->
				{#each hours as h (h)}
					{@const a = (h / 24) * 360}
					{@const [x1, y1] = pt(R.face, a)}
					{@const [x2, y2] = pt(R.face - (h % 6 === 0 ? S * 0.022 : S * 0.011), a)}
					<line class="hr" class:maj={h % 6 === 0} {x1} {y1} {x2} {y2} />
					{#if h % 2 === 0}
						{@const [tx, ty] = pt(R.hours - S * 0.012, a)}
						<text class="hl" x={tx} y={ty} dy="0.35em" text-anchor="middle">{String(h).padStart(2, '0')}</text>
					{/if}
				{/each}
				<!-- the watches, engraved on the bezel -->
				<defs>
					{#each watchArcs as w (w.key)}<path id="wa-{w.key}-{S}" d={w.d} />{/each}
				</defs>
				{#each watchArcs as w (w.key)}
					<text class="wn" class:on={w.on}><textPath href="#wa-{w.key}-{S}" startOffset="50%" text-anchor="middle">{w.label}</textPath></text>
				{/each}
				{#each [0, 4, 8, 12, 16, 20] as h (h)}
					{@const [x1, y1] = pt(R.face, (h / 24) * 360)}
					{@const [x2, y2] = pt(R.bezel, (h / 24) * 360)}
					<line class="wsep" {x1} {y1} {x2} {y2} />
				{/each}
				<!-- handovers -->
				{#each handovers as a, i (i)}
					{@const [x1, y1] = pt(R.hub, a)}
					{@const [x2, y2] = pt(R.face, a)}
					<line class="ho" {x1} {y1} {x2} {y2} />
				{/each}
				<!-- intake: hourly bars standing out from the intake ring -->
				{#each bars as b (b.at.toISOString())}
					{@const a1 = ang(b.at)}
					{#if a1 < aNow && +b.at >= +win.start}
						<path class="bar" d={sectorPath(C, C, R.barIn, R.barIn + Math.max(1.5, b.h * (R.barOut - R.barIn)), a1 + 0.8, Math.min(aNow, a1 + hourW - 0.8))} />
					{/if}
				{/each}
				<!-- incidents -->
				{#each paths as p (p.id)}
					<g class="inc {tone(p)}" class:dim={hot && hot !== p.id} class:hot={hot === p.id} class:closed={!p.open}>
						<path class="halo" d={dialPath(p, ang, rad, C, C)} />
						<path class="ln" d={dialPath(p, ang, rad, C, C)} />
						{#each p.steps as s, si (si)}
							{@const [x, y] = pt(rad(s.lane, p.track), ang(s.at))}
							<circle class="st" cx={x} cy={y} r={si === 0 ? 3.4 : 2.4} />
						{/each}
						{#each p.notices as t (t.toISOString())}
							{@const ln = p.steps.filter((s) => +s.at <= +t).at(-1)?.lane ?? p.lane}
							{@const [x, y] = pt(rad(ln, p.track) + S * 0.022, ang(t))}
							<text class="env" {x} {y} dy="0.35em" text-anchor="middle">✉</text>
						{/each}
					</g>
				{/each}
				<!-- regulator deadlines ahead of the hand -->
				{#each ahead as d (d.c.id)}
					{@const [x1, y1] = pt(R.bezel + S * 0.004, d.a)}
					{@const [x2, y2] = pt(R.face - S * 0.006, d.a)}
					{@const [lx, ly] = pt(R.bezel + S * 0.03, d.a)}
					<line class="due" {x1} {y1} {x2} {y2} />
					<text class="duel" x={lx} y={ly} dy="0.35em" text-anchor={d.a % 360 < 180 ? 'start' : 'end'}>▼ {regimeShort(d.c.regime)} {timeOf(d.c.dueAt!, tz)}</text>
				{/each}
				<!-- lane names, level, in the unlit sector -->
				{#each [1, 2, 3, 4] as l (l)}
					{@const [x, y] = pt(R.lanes[l]!, labelAngle)}
					<text class="lane" {x} {y} dy="0.35em" text-anchor="middle">{LANE_LABEL[LANES[l]!]}{#if !compact && byLane[l]!.length} · {byLane[l]!.length}{/if}</text>
				{/each}
				<text class="lane" x={intakeAt[0]} y={intakeAt[1]} dy="0.35em" text-anchor="middle">intake</text>
				<!-- the hand -->
				<g class="hand" class:still style="transform: rotate({aNow}deg); transform-origin: {C}px {C}px">
					<line x1={C} y1={C - R.hub} x2={C} y2={C - R.bezel - S * 0.012} />
					<path d="M{C} {C - R.bezel - S * 0.004} l-5 -9 h10 z" />
				</g>
				<circle class="hub" cx={C} cy={C} r={R.hub} />
			</svg>
			<!-- the accessible layer: every incident's head is a link -->
			{#each paths as p (p.id)}
				{@const [hx, hy] = pt(rad(p.lane, p.track), headAngle(p))}
				<a
					class="head {tone(p)}"
					class:closed={!p.open}
					class:hot={hot === p.id}
					href={hrefFor(p.reference)}
					style="left:{hx}px; top:{hy}px"
					in:scale={{ start: 1.8, duration: still ? 0 : 420, easing: backOut }}
					onpointerenter={() => (hot = p.id)}
					onpointerleave={() => (hot = null)}
					onfocus={() => (hot = p.id)}
					onblur={() => (hot = null)}
					aria-label="{p.reference} {p.title}: opened {timeOf(p.steps[0]!.at, tz)}, now in {LANE_LABEL[LANES[p.lane]!]}{p.open ? '' : ', finished'}{p.waiting ? ', waiting for a person' : ''}"
				>
					<span class="g" aria-hidden="true">{glyph(p)}</span>
					{#if !compact && !p.open}<span class="ref" aria-hidden="true">{p.reference}</span>{/if}
				</a>
			{/each}
			<div class="centre" aria-hidden="true">
				<b class="t">{timeOf(win.end, tz)}</b>
				<span class="w">{nowWatch.label} watch</span>
				{#if !compact}<span class="c">{openCount} open{waiting ? ` · ${waiting} waiting` : ''}</span>{/if}
			</div>
		</div>
	</div>

	{#if !compact}
		<ul class="open" aria-label="Open incidents, by stage">
			{#each paths.filter((p) => p.open) as p (p.id)}
				<li>
					<a class="tag {tone(p)}" class:hot={hot === p.id} href={hrefFor(p.reference)} onpointerenter={() => (hot = p.id)} onpointerleave={() => (hot = null)} title={p.title}>
						<span class="g" aria-hidden="true">{glyph(p)}</span><span class="r">{p.reference}</span><span class="s">{LANE_LABEL[LANES[p.lane]!]}</span>
						{#if p.waiting}<span class="sr">waiting for a person</span>{/if}
					</a>
				</li>
			{:else}
				<li class="none">Nothing open. The watch is quiet.</li>
			{/each}
		</ul>

		<!-- phones: the same rings as a list, Contain open first -->
		<div class="phone">
			<Accordion value={phoneOpen} onValueChange={(e) => (phoneOpen = e.value)} multiple collapsible spaceY="space-y-0" rounded="rounded-none">
				{#each LANES.slice(1) as l, i (l)}
					{@const list = paths.filter((p) => p.lane === i + 1)}
					<Accordion.Item value={l} controlPadding="py-3 px-4" controlRounded="rounded-none" controlHover="hover:bg-[var(--raised)]" panelPadding="px-4 pb-3" classes="acc">
						{#snippet control()}<span class="eng">{LANE_LABEL[l]}</span> <span class="faint">· {list.filter((p) => p.open).length} open{list.some((p) => !p.open) ? `, ${list.filter((p) => !p.open).length} finished` : ''}</span>{/snippet}
						{#snippet panel()}
							{#each list as p (p.id)}
								<a class="prow" href={hrefFor(p.reference)}>
									<span class="mono {tone(p)}">{glyph(p)} {p.reference}</span>
									<span>{p.title}</span>
									<span class="mono faint">since {timeOf(p.steps.at(-1)!.at, tz)}{p.open ? '' : ' · finished'}</span>
								</a>
							{:else}
								<p class="faint none">Nothing in {l} on this shift.</p>
							{/each}
						{/snippet}
					</Accordion.Item>
				{/each}
			</Accordion>
		</div>
	{/if}
</div>

<style>
	.wd {
		width: 100%;
		min-width: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 14px;
	}
	.dial {
		width: 100%;
		display: flex;
		justify-content: center;
		padding: 22px 0;
		--tl: 16px;
	}
	.face {
		position: relative;
		flex: none;
	}
	svg {
		display: block;
		overflow: visible;
	}
	.trail {
		position: absolute;
		border-radius: 50%;
		background: conic-gradient(from calc(var(--a) - 46deg), transparent 0deg, var(--acc-glow) 46deg, transparent 46.2deg);
		pointer-events: none;
		opacity: 0.85;
	}
	.bez {
		fill: none;
		stroke: var(--plate);
	}
	.facebg {
		fill: var(--sunk);
		stroke: var(--rule-2);
		stroke-width: 1;
	}
	.lit {
		fill: var(--acc-wash);
	}
	.night {
		fill: var(--night);
	}
	.ring {
		fill: none;
		stroke: var(--rule);
		stroke-width: 1;
	}
	.ring.base {
		stroke: var(--rule-2);
	}
	.hr {
		stroke: var(--rule-2);
		stroke-width: 1;
	}
	.hr.maj {
		stroke: var(--dim);
		stroke-width: 1.5;
	}
	.hl {
		font: 500 calc(var(--S) * 0.018) / 1 var(--code);
		fill: var(--faint);
	}
	.wn {
		font: 700 calc(var(--S) * 0.019) / 1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.22em;
		fill: var(--faint);
	}
	.wn.on {
		fill: var(--acc-hi);
	}
	.wsep {
		stroke: var(--rule-2);
		stroke-width: 1;
	}
	.ho {
		stroke: var(--soon);
		stroke-width: 1;
		stroke-dasharray: 2 4;
		opacity: 0.7;
	}
	.bar {
		fill: var(--dim);
		opacity: 0.55;
	}
	.lane {
		font: 700 calc(var(--S) * 0.018) / 1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		fill: var(--dim);
		paint-order: stroke;
		stroke: var(--sunk);
		stroke-width: 4px;
	}
	.inc .ln {
		fill: none;
		stroke-width: 2.2;
		stroke-linejoin: round;
		stroke-linecap: round;
		transition: opacity 0.18s;
	}
	.inc .halo {
		fill: none;
		stroke: transparent;
		stroke-width: 9;
	}
	.inc.hot .halo {
		stroke: var(--acc-wash);
	}
	.inc .st {
		stroke: var(--sunk);
		stroke-width: 1.5;
	}
	.inc.hi .ln {
		stroke: var(--acc);
	}
	.inc.hi .st {
		fill: var(--acc);
	}
	.inc.mid .ln {
		stroke: var(--soon);
	}
	.inc.mid .st {
		fill: var(--soon);
	}
	.inc.lo .ln {
		stroke: var(--dim);
	}
	.inc.lo .st {
		fill: var(--dim);
	}
	.inc.person .ln {
		stroke: var(--person);
		stroke-width: 2.6;
	}
	.inc.person .st {
		fill: var(--person);
	}
	.inc.closed .ln {
		stroke-dasharray: 5 3;
		opacity: 0.75;
	}
	.inc.dim {
		opacity: 0.3;
	}
	.env {
		font: 400 11px/1 var(--code);
		fill: var(--ink-2);
	}
	.due {
		stroke: var(--acc);
		stroke-width: 2;
	}
	.duel {
		font: 500 calc(var(--S) * 0.019) / 1 var(--code);
		fill: var(--acc-hi);
	}
	.hand line {
		stroke: var(--acc);
		stroke-width: 2;
	}
	.hand path {
		fill: var(--acc);
	}
	.hand {
		transition: transform 900ms var(--ease-tick);
	}
	.hand.still {
		transition: none;
	}
	.hub {
		fill: var(--plate);
		stroke: var(--rule-2);
	}
	.centre {
		position: absolute;
		left: 50%;
		top: 50%;
		transform: translate(-50%, -50%);
		display: grid;
		justify-items: center;
		gap: 5px;
		text-align: center;
		pointer-events: none;
	}
	.centre .t {
		font: 500 calc(var(--S) * 0.058) / 1 var(--code);
		font-feature-settings: 'tnum', 'zero';
		color: var(--ink);
	}
	.centre .w {
		font: 700 calc(var(--S) * 0.021) / 1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.2em;
		color: var(--acc-hi);
	}
	.centre .c {
		font: 600 calc(var(--S) * 0.02) / 1 var(--ui);
		color: var(--dim);
	}
	.head {
		position: absolute;
		transform: translate(-50%, -50%);
		display: flex;
		align-items: center;
		gap: 4px;
		text-decoration: none;
		min-width: 22px;
		min-height: 22px;
		justify-content: center;
		border-radius: 2px;
		z-index: 2;
	}
	.head .g {
		font: 400 13px/1 var(--code);
		width: 18px;
		height: 18px;
		display: grid;
		place-items: center;
		background: var(--sunk);
		border-radius: 50%;
	}
	.head .ref {
		position: absolute;
		left: 21px;
		top: 50%;
		transform: translateY(-50%);
		font: 500 11px/1 var(--code);
		color: var(--ink-2);
		background: color-mix(in srgb, var(--sunk) 85%, transparent);
		padding: 2px 3px;
		white-space: nowrap;
	}
	.head.closed .g {
		font-size: 9px;
	}
	.head.closed .ref {
		color: var(--faint);
	}
	.head.hi .g {
		color: var(--acc-hi);
	}
	.head.mid .g {
		color: var(--soon);
	}
	.head.lo .g {
		color: var(--dim);
	}
	.head.person .g {
		color: var(--person);
		box-shadow: 0 0 0 1.5px var(--person);
	}
	.head.hot .ref,
	.head:hover .ref {
		color: var(--ink);
	}
	.open {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		justify-content: center;
	}
	.tag {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		height: 28px;
		padding: 0 10px 0 8px;
		border: 1px solid var(--rule-2);
		border-radius: 2px;
		background: var(--plate);
		text-decoration: none;
		font: 500 12.5px/1 var(--code);
		color: var(--ink);
	}
	.tag .s {
		font: 700 11.5px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--dim);
	}
	.tag.hi .g {
		color: var(--acc-hi);
	}
	.tag.mid .g {
		color: var(--soon);
	}
	.tag.person {
		border-color: var(--person);
		box-shadow: inset 0 0 0 1px var(--person);
	}
	.tag.person .g {
		color: var(--person);
	}
	.tag:hover,
	.tag.hot {
		background: var(--raised);
	}
	.none {
		color: var(--faint);
		font-size: 13.5px;
	}
	.phone {
		display: none;
		width: 100%;
	}
	.phone :global(.acc) {
		border-top: 1px solid var(--rule);
		background: var(--plate);
	}
	.prow {
		display: grid;
		gap: 3px;
		padding: 9px 0;
		border-top: 1px solid var(--rule);
		text-decoration: none;
		font-size: 14px;
	}
	.prow .mono {
		font-size: 12px;
	}
	.prow .hi {
		color: var(--acc-hi);
	}
	.prow .person {
		color: var(--person);
	}
	.compact .dial {
		padding: 8px 0;
	}
	@media (max-width: 760px) {
		.phone {
			display: block;
		}
		.open {
			display: none;
		}
		.dial {
			padding: 14px 0;
		}
	}
</style>
