<script lang="ts">
	// The shift axis (DESIGN.md §0.1): a time ruler across the top of every signed-in screen. The night is shaded, the
	// 08:00 and 20:00 shift changes are ruled, incidents (▲), approvals (‖) and notices (✉) sit where they happened,
	// and the now line advances each minute. Past now the axis folds into the deadline gutter: one flag per running
	// regulator clock, with rolling digits. A new clock drops onto the axis.
	import { goto } from '$app/navigation';
	import { flip } from 'svelte/animate';
	import { backOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { Popover, Tooltip } from '@skeletonlabs/skeleton-svelte';
	import Logo from './Logo.svelte';
	import Tick from './Tick.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { clockState, hms, secondsLeft, shortDay, STATE_GLYPH, STATE_WORD, REGIME_TZ, timeOf, elapsedFraction } from '$lib/time/clock';
	import { clockLabel, clockLongLabel, noticeHref, regimeShort } from '$lib/util/labels';
	import { axisMarks, xOf, type AxisMark, type ShiftWindow } from '$lib/util/shift';
	import type { Deadline } from '$lib/api/types';

	// The landing drives the axis from its replay (win, now, clocks, marks); everywhere else it reads the shift.
	let {
		idle = false,
		win,
		now,
		clocks: clocksIn,
		marks: marksIn,
		home = '/',
		fast = false
	}: { idle?: boolean; win?: ShiftWindow; now?: Date; clocks?: Deadline[]; marks?: AxisMark[]; home?: string; fast?: boolean } = $props();

	const w = $derived(win ?? shift.win);
	const at = $derived(now ?? live.now);
	const clocks = $derived(clocksIn ?? (idle ? [] : live.clocks.filter((c) => c.state !== 'not_required')));
	const marks = $derived(marksIn ?? (idle ? [] : axisMarks(shift.log, shift.incidents, w)));
	const urgent = $derived(clocks.find((c) => c.state !== 'not_started' && c.state !== 'submitted') ?? clocks[0]);
	let ready = $state(false);
	$effect(() => {
		if ((clocksIn || live.clocksLoaded) && !ready) setTimeout(() => (ready = true), 400);
	});
	function drop(_node: Element, { delay = 0 }: { delay?: number } = {}) {
		if (!ready || prefersReducedMotion.current) return { duration: 0 };
		return { delay, duration: 620, easing: backOut, css: (t: number, u: number) => `transform: translateY(${-40 * u}px); opacity: ${Math.min(1, t / 0.6)}` };
	}
	const nowLabel = $derived(timeOf(at, shift.tz));
	const incRef = (id: string | null) => shift.incidents.find((i) => i.id === id)?.reference ?? null;
	function openMark(id: string | null) {
		const ref = incRef(id);
		if (ref) goto(`/incidents/${ref}`);
	}
	let flagsOpen = $state(false);
</script>

<header class="axis" aria-label="The shift">
	{#if idle || win}
		<a class="brand" href={home} aria-label="Nightjar SOC"><Logo /><b>nightjar</b><i>soc</i></a>
	{:else}
		<button class="brand" type="button" onclick={() => ui.openPalette('all', '')} aria-label="Nightjar SOC: go to anything">
			<Logo /><b>nightjar</b><i>soc</i>
		</button>
	{/if}

	<div class="track" role="group" aria-label="The shift from {timeOf(w.start, shift.tz)} to now, {nowLabel}">
		{#each w.nights as n (n.from.toISOString())}
			<span class="night" style="left:{xOf(n.from, w) * 100}%; width:{(xOf(n.to, w) - xOf(n.from, w)) * 100}%" aria-hidden="true"></span>
		{/each}
		{#each w.ticks as t (t.at.toISOString())}
			{@const tx = xOf(t.at, w)}
			<span class="tk" class:major={t.major} style="left:{tx * 100}%" aria-hidden="true">{#if tx > 0.025 && tx < 0.965}<i>{t.label}</i>{/if}</span>
		{/each}
		{#each w.handovers as h (h.toISOString())}
			<span class="ho" style="left:{xOf(h, w) * 100}%" aria-hidden="true">{#if xOf(h, w) < 0.84}<i>handover {timeOf(h, shift.tz)}</i>{/if}</span>
		{/each}
		{#each marks as m, i (m.kind + m.at.toISOString() + i)}
			<span class="mk {m.kind}" style="left:{xOf(m.at, w) * 100}%">
				<Tooltip
					positioning={{ placement: 'bottom' }}
					openDelay={120}
					triggerBase="mkb"
					contentBase="nj-tip"
					triggerAriaLabel="{timeOf(m.at, shift.tz)} {m.text}"
					onclick={() => !win && openMark(m.refId)}
				>
					{#snippet trigger()}<span aria-hidden="true">{m.glyph}</span>{/snippet}
					{#snippet content()}<span class="mono">{timeOf(m.at, shift.tz)}</span> {m.text}{/snippet}
				</Tooltip>
			</span>
		{/each}
		<span class="now" aria-hidden="true"><i class="mono">now {nowLabel}</i></span>
	</div>

	<div class="gutter" aria-label="Regulator clocks">
		{#if !clocks.length}
			<div class="idle mono"><i></i>No regulator clocks running</div>
		{/if}
		{#each clocks as d, i (d.id)}
			{@const st = clockState(d, at)}
			{@const left = secondsLeft(d.dueAt, at)}
			<a
				class="flag {st}"
				href={noticeHref(d)}
				in:drop={{ delay: i * 120 }}
				animate:flip={{ duration: prefersReducedMotion.current ? 0 : 300 }}
				style="--elapsed:{elapsedFraction(d.startsAt, d.dueAt, at).toFixed(4)}"
				title={d.basis}
				aria-label={st === 'not_started'
					? `${clockLongLabel(d)}, ${d.incidentReference}: not started, ${d.decideBy ? `decision due ${shortDay(d.decideBy, REGIME_TZ[d.regime])}` : 'materiality decision needed'}`
					: `${clockLongLabel(d)}, ${d.incidentReference}: ${STATE_WORD[st]}, ${hms(left)} left`}
			>
				<span class="lbl">{clockLabel(d)} · {d.incidentReference}</span>
				<span class="row">
					{#if st === 'not_started'}
						<span class="t ns">{d.decideBy ? `decide by ${shortDay(d.decideBy, REGIME_TZ[d.regime])}` : 'not started'}</span>
					{:else if st === 'submitted'}
						<span class="t">sent</span>
					{:else}
						<Tick class="t" value={hms(left)} {fast} />
					{/if}
					<span class="st"><span class="g">{STATE_GLYPH[st]}</span>{STATE_WORD[st]}</span>
				</span>
			</a>
		{/each}
	</div>

	{#if urgent}
		<span class="mflags">
			<Popover
				open={flagsOpen}
				onOpenChange={(e) => (flagsOpen = e.open)}
				positioning={{ placement: 'bottom-end' }}
				triggerBase="mchip mono"
				contentBase="nj-pop"
				triggerAriaLabel="Regulator clocks: {clocks.length}"
			>
				{#snippet trigger()}
					<span class="g">{STATE_GLYPH[clockState(urgent, at)]}</span>
					{regimeShort(urgent.regime)}
					{urgent.state === 'not_started' ? 'decide' : hms(secondsLeft(urgent.dueAt, at)).slice(0, 5)}
					{#if clocks.length > 1}<span class="faint">+{clocks.length - 1}</span>{/if}
				{/snippet}
				{#snippet content()}
					{#each clocks as d (d.id)}
						<a class="pl" href={noticeHref(d)} onclick={() => (flagsOpen = false)}>
							<span class="mono faint">{clockLabel(d)} · {d.incidentReference}</span>
							<span class="mono">{d.state === 'not_started' ? '◆ decide' : d.state === 'submitted' ? '✓ sent' : hms(secondsLeft(d.dueAt, at))}</span>
						</a>
					{/each}
				{/snippet}
			</Popover>
		</span>
	{/if}
</header>

<style>
	.axis {
		position: relative;
		z-index: 5;
		display: grid;
		grid-template-columns: var(--gut) minmax(0, 1fr) var(--dl);
		height: 64px;
		overflow: hidden;
		scrollbar-gutter: stable;
		scrollbar-width: thin;
		border-bottom: 1px solid var(--line-strong);
		background: var(--bg);
	}
	.brand {
		padding: 0 12px;
		gap: 7px;
		border-right: 1px solid var(--line);
		overflow: hidden;
	}
	.track {
		position: relative;
		min-width: 0;
		margin: 0 14px 0 6px;
	}
	.night {
		position: absolute;
		top: 0;
		bottom: 0;
		background: var(--night);
		border-left: 1px dashed var(--line);
		border-right: 1px dashed var(--line);
	}
	.tk {
		position: absolute;
		bottom: 0;
		width: 1px;
		height: 7px;
		background: var(--line-strong);
	}
	.tk.major {
		height: 12px;
		background: var(--muted);
	}
	.tk i {
		position: absolute;
		bottom: 13px;
		left: 0;
		transform: translateX(-50%);
		font: 400 11px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		font-style: normal;
		color: var(--faint);
	}
	.ho {
		position: absolute;
		top: 6px;
		bottom: 0;
		width: 0;
		border-left: 1px solid var(--amber);
		opacity: 0.8;
	}
	.ho i {
		position: absolute;
		top: 0;
		left: 4px;
		font: 400 10.5px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		font-style: normal;
		color: var(--amber);
		white-space: nowrap;
	}
	.mk {
		position: absolute;
		top: 20px;
		transform: translateX(-50%);
	}
	.mk :global(.mkb) {
		display: grid;
		place-items: center;
		width: 18px;
		height: 18px;
		font: 600 11px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		border-radius: 2px;
		color: var(--ink-2);
	}
	.mk.incident :global(.mkb) {
		color: var(--red);
	}
	.mk.approval :global(.mkb) {
		color: var(--lav);
	}
	.mk.notice :global(.mkb) {
		color: var(--teal);
	}
	.mk :global(.mkb:hover) {
		background: var(--raised);
	}
	:global(.nj-tip) {
		background: var(--raised);
		color: var(--ink);
		border: 1px solid var(--line-strong);
		box-shadow: var(--shadow);
		padding: 7px 10px;
		font-size: 12.5px;
		max-width: 340px;
		border-radius: 3px;
	}
	:global(.nj-pop) {
		background: var(--pane);
		color: var(--ink);
		border: 1px solid var(--line-strong);
		box-shadow: var(--shadow);
		border-radius: 3px;
		min-width: 260px;
		display: grid;
	}
	.now {
		position: absolute;
		right: 0;
		top: 0;
		bottom: 0;
		width: 2px;
		background: var(--teal);
		box-shadow: 0 0 0 0 var(--teal-line);
		animation: nj-now 2.4s ease-out infinite;
	}
	@keyframes nj-now {
		0% {
			box-shadow: 0 0 0 0 var(--teal-line);
		}
		70% {
			box-shadow: 0 0 0 5px transparent;
		}
		100% {
			box-shadow: 0 0 0 0 transparent;
		}
	}
	.now i {
		position: absolute;
		right: 6px;
		top: 6px;
		font-size: 11px;
		font-style: normal;
		color: var(--teal);
		white-space: nowrap;
	}
	.gutter {
		display: flex;
		min-width: 0;
		overflow-x: auto;
		overflow-y: hidden;
		scrollbar-width: none;
		border-left: 1px solid var(--line-strong);
		background: linear-gradient(90deg, color-mix(in srgb, var(--teal) 6%, transparent), transparent 40%);
	}
	.gutter {
		-webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 36px), transparent);
		mask-image: linear-gradient(90deg, #000 calc(100% - 36px), transparent);
	}
	.gutter::-webkit-scrollbar {
		display: none;
	}
	.idle {
		display: flex;
		align-items: center;
		padding: 0 16px;
		font-size: 12px;
		color: var(--faint);
		gap: 8px;
		white-space: nowrap;
	}
	.idle i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		border: 1px solid var(--faint);
	}
	.flag {
		position: relative;
		flex: none;
		display: grid;
		align-content: center;
		gap: 5px;
		padding: 0 16px 0 14px;
		border-right: 1px solid var(--line);
		text-decoration: none;
	}
	.flag::before {
		content: '';
		position: absolute;
		left: 0;
		top: 10px;
		bottom: 10px;
		width: 2px;
		background: var(--teal);
	}
	.flag.due_soon::before {
		background: var(--amber);
	}
	.flag.overdue::before {
		background: var(--red);
	}
	.flag.not_started::before {
		background: repeating-linear-gradient(180deg, var(--lav) 0 3px, transparent 3px 6px);
	}
	.flag.submitted::before {
		background: var(--line-strong);
	}
	.flag::after {
		content: '';
		position: absolute;
		left: 0;
		bottom: -1px;
		height: 2px;
		width: calc(var(--elapsed, 0) * 100%);
		background: var(--teal-line);
	}
	.flag:hover {
		background: var(--raised);
	}
	.lbl {
		font: 400 11px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		white-space: nowrap;
	}
	.row {
		display: flex;
		align-items: baseline;
		gap: 10px;
		white-space: nowrap;
	}
	.row :global(.t) {
		font: 600 19px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		letter-spacing: -0.02em;
		color: var(--ink);
	}
	.t.ns {
		color: var(--muted);
		font-weight: 400;
		font-size: 14px;
	}
	.st {
		font-size: 12px;
		color: var(--ink-2);
	}
	.g {
		font-family: var(--mono);
		font-variation-settings: 'MONO' 1;
		margin-right: 4px;
	}
	.on_track .g,
	.submitted .g {
		color: var(--teal);
	}
	.due_soon .g,
	.due_soon .row :global(.t) {
		color: var(--amber);
	}
	.overdue .g,
	.overdue .row :global(.t) {
		color: var(--red);
	}
	.not_started .g {
		color: var(--lav);
	}
	.mflags {
		display: none;
	}
	.pl {
		display: flex;
		justify-content: space-between;
		gap: 14px;
		padding: 12px 14px;
		border-top: 1px solid var(--line);
		text-decoration: none;
		font-size: 12.5px;
	}
	.pl:first-child {
		border-top: 0;
	}
	@media (max-width: 1100px) {
		.brand :global(b),
		.brand :global(i) {
			display: none;
		}
		.tk i {
			font-size: 10px;
		}
	}
	@media (max-width: 760px) {
		.axis {
			grid-template-columns: auto minmax(0, 1fr) auto;
			height: 52px;
		}
		.brand {
			padding: 0 12px;
			border-right: 0;
		}
		.gutter,
		.ho i,
		.mk {
			display: none;
		}
		.track {
			margin: 0 10px;
		}
		.mflags {
			display: flex;
			align-items: center;
			padding-right: 10px;
		}
		.mflags :global(.mchip) {
			display: inline-flex;
			align-items: center;
			gap: 6px;
			height: 32px;
			padding: 0 10px;
			border: 1px solid var(--line-strong);
			border-radius: 3px;
			font-size: 12.5px;
			color: var(--ink);
			white-space: nowrap;
		}
		.mflags :global(.mchip .g) {
			color: var(--teal);
		}
		.now i {
			display: none;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.now {
			animation: none;
		}
	}
</style>
