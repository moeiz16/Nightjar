<script lang="ts">
	// The head of every page (DESIGN.md §3): the station plate (the brand, then the page's name as its h1 in tall
	// stencil capitals and a one-line summary), the lamp panel that is the navigation, and the chronometer: the
	// org's time, the watch it falls in, the most urgent regulator clock, ⌘K and you. It scrolls with the page;
	// nothing is pinned to an edge. Phones get one row and a Panel button that drops the lamps from the top.
	import type { Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { fly } from 'svelte/transition';
	import { backOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { Avatar, Popover, Switch } from '@skeletonlabs/skeleton-svelte';
	import Logo from './Logo.svelte';
	import Tick from './Tick.svelte';
	import LampPanel from './LampPanel.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
	import ClockList from '$lib/components/console/ClockList.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { ui, stationFor, stationLine } from '$lib/stores/ui.svelte';
	import { clockState, hms, secondsLeft, shortDay, REGIME_TZ, STATE_GLYPH, STATE_WORD, timeOf } from '$lib/time/clock';
	import { clockLabel, noticeHref, regimeShort } from '$lib/util/labels';
	import { watchOf } from '$lib/util/shift';

	let {
		title,
		sub,
		panel,
		right,
		extra,
		home = '/watch'
	}: {
		/** overrides the station name (public pages) */
		title?: string;
		sub?: string;
		/** replaces the lamp panel (public pages) */
		panel?: Snippet;
		/** replaces the chronometer and you (public pages) */
		right?: Snippet;
		/** a row of page tools under the summary line (the brief's editions) */
		extra?: Snippet;
		home?: string;
	} = $props();

	const path = $derived(page.url.pathname);
	const mine = $derived(ui.station && ui.station.path === path ? ui.station : null);
	const name = $derived(title ?? mine?.title ?? stationFor(path));
	// one short line at most (counts), never a description: what a page is for lives in the name's tooltip
	const line = $derived(sub ?? mine?.sub ?? '');
	const about = $derived(stationLine(path) || undefined);
	const tz = $derived(shift.tz);
	const t = $derived(timeOf(live.now, tz, true));
	const watch = $derived(watchOf(live.now, tz));
	const clocks = $derived(live.clocks.filter((c) => c.state !== 'not_required'));
	const urgent = $derived(clocks.find((c) => c.state !== 'not_started' && c.state !== 'submitted') ?? clocks[0] ?? null);
	const still = $derived(prefersReducedMotion.current);
	let me = $state(false);
	let clocksOpen = $state(false);
	let panelOpen = $state(false);
</script>

<header class="head" aria-label="Nightjar SOC">
	<div class="plate">
		<a class="brand" href={home} aria-label="Nightjar SOC, home"><Logo /><span>Nightjar <i>SOC</i></span></a>
		{#if session.org}<span class="org">{session.org.name}</span>{/if}
		<h1 class="station" title={about}>{name}</h1>
		{#if line}<p class="line">{line}</p>{/if}
		{#if extra}<div class="extra">{@render extra()}</div>{/if}
	</div>

	<div class="panel">
		{#if panel}{@render panel()}{:else}<LampPanel />{/if}
	</div>

	<div class="chrono">
		{#if right}
			{@render right()}
		{:else}
			<div class="time" aria-label="{t}, {watch.label} watch">
				<Tick class="hms" value={t} />
				<span class="date">{shortDay(live.now, tz)} · <b>{watch.label} watch</b></span>
			</div>
			<div class="clk">
				{#if urgent}
					{@const st = clockState(urgent, live.now)}
					{#key urgent.id}
						<a class="next {st}" href={noticeHref(urgent)} in:fly={{ y: still ? 0 : -18, duration: still ? 0 : 620, easing: backOut }} title="{clockLabel(urgent)} · {urgent.incidentReference}">
							<span class="g" aria-hidden="true">{STATE_GLYPH[st]}</span>
							<span class="l">{regimeShort(urgent.regime)}</span><span class="sr"> {clockLabel(urgent)} · {urgent.incidentReference}</span>
							<span class="v">{st === 'not_started' ? (urgent.decideBy ? `decide by ${shortDay(urgent.decideBy, REGIME_TZ[urgent.regime])}` : 'not started') : st === 'submitted' ? 'sent' : hms(secondsLeft(urgent.dueAt, live.now))}</span>
							<span class="sr">{STATE_WORD[st]}</span>
						</a>
					{/key}
					{#if clocks.length > 1}
						<Popover open={clocksOpen} onOpenChange={(e) => (clocksOpen = e.open)} positioning={{ placement: 'bottom-end' }} triggerBase="more" contentBase="nj-pop clockpop" triggerAriaLabel="All {clocks.length} regulator clocks">
							{#snippet trigger()}+{clocks.length - 1} clocks{/snippet}
							{#snippet content()}<div class="cp"><ClockList clocks={live.clocks} now={live.now} /></div>{/snippet}
						</Popover>
					{/if}
				{:else}
					<span class="idle">○ No regulator clocks running</span>
				{/if}
			</div>
			<div class="acts">
				<button class="find" type="button" onclick={() => ui.openPalette()} aria-label="Find anything (⌘K)"><span>Find</span><kbd>⌘K</kbd></button>
				<Popover open={me} onOpenChange={(e) => (me = e.open)} positioning={{ placement: 'bottom-end' }} triggerBase="youb" contentBase="nj-pop youp" triggerAriaLabel="You: {session.name}, {session.roleLabel}">
					{#snippet trigger()}
						<Avatar name={session.name || 'You'} size="size-8" font="text-[12px] font-bold" background="bg-[var(--raised)]" border="border border-[var(--rule-2)]" rounded="rounded-[2px]" />
					{/snippet}
					{#snippet content()}{@render you(() => (me = false))}{/snippet}
				</Popover>
			</div>
		{/if}
	</div>

	{#if !right}
		<button class="panelb" type="button" onclick={() => (panelOpen = true)} aria-haspopup="dialog" aria-label="Open the panel: stations, clocks and you"><span class="pt" aria-hidden="true">{t.slice(0, 5)}</span><span class="pl">Panel</span></button>
	{/if}
</header>

{#snippet you(close: () => void)}
	<div class="ph">
		<b>{session.name}</b>
		<span class="faint">{session.roleLabel} · {session.org?.name ?? ''}</span>
	</div>
	<div class="opt">
		<Switch name="theme" checked={ui.theme === 'light'} onCheckedChange={(e) => ui.setTheme(e.checked ? 'light' : 'dark')} classes="nj-switch" controlActive="bg-[var(--acc)]" controlInactive="bg-[var(--rule-2)]">Day presentation (light)</Switch>
	</div>
	<div class="opt">
		<Switch name="typing" checked={ui.holdByTyping} onCheckedChange={(e) => ui.setHoldByTyping(e.checked)} classes="nj-switch" controlActive="bg-[var(--acc)]" controlInactive="bg-[var(--rule-2)]">Confirm by typing the name instead of holding</Switch>
	</div>
	<a class="opt lnkrow" href="/settings/members" onclick={close}>Members and invitations</a>
	<a class="opt lnkrow" href="/settings/regimes" onclick={close}>Regulator contacts</a>
	<button class="opt lnkrow" type="button" onclick={async () => (close(), await session.logout(), goto('/sign-in'))}>Sign out</button>
{/snippet}

{#if !right}
	<Sheet kind="dialog" label="Stations, clocks and you" bind:open={panelOpen} width="420px">
		<div class="mpanel">
			<p class="eng mh">Stations</p>
			<LampPanel cols={2} onpick={() => (panelOpen = false)} />
			<p class="eng mh">Regulator clocks</p>
			<div class="mclk"><ClockList clocks={live.clocks} now={live.now} /></div>
			<button class="btn preset-outlined-surface-300-700 w-full" type="button" onclick={() => ((panelOpen = false), ui.openPalette())}>Find anything</button>
			<div class="youm">{@render you(() => (panelOpen = false))}</div>
		</div>
	</Sheet>
{/if}

<style>
	.head {
		display: grid;
		grid-template-columns: minmax(300px, 1fr) minmax(0, auto) minmax(250px, 300px);
		gap: 28px;
		align-items: stretch;
		padding: 18px var(--gut) 20px;
		border-bottom: 1px solid var(--rule);
		background:
			linear-gradient(180deg, color-mix(in srgb, var(--acc) 6%, var(--bg)), var(--bg) 70%);
		position: relative;
	}
	.plate {
		min-width: 0;
		display: grid;
		grid-template-columns: auto 1fr;
		align-content: start;
		column-gap: 14px;
		row-gap: 10px;
	}
	.brand {
		font-size: 13.5px;
		letter-spacing: 0.24em;
	}
	.brand span {
		padding-top: 1px;
	}
	.org {
		align-self: center;
		font: 600 12.5px/1 var(--ui);
		color: var(--faint);
		padding-left: 14px;
		border-left: 1px solid var(--rule-2);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.station {
		grid-column: 1 / -1;
		margin: 6px 0 0;
		font: 800 64px/0.88 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--ink);
		display: flex;
		align-items: center;
		gap: 14px;
		overflow-wrap: anywhere;
	}
	.station::before {
		content: '';
		width: 6px;
		align-self: stretch;
		margin: 6px 0;
		background: var(--acc);
		box-shadow: 0 0 18px var(--acc-glow);
		flex: none;
	}
	.line {
		grid-column: 1 / -1;
		margin: 0;
		font: 600 14.5px/1.4 var(--ui);
		color: var(--ink-2);
	}
	.extra {
		grid-column: 1 / -1;
		display: flex;
		flex-wrap: wrap;
		gap: 8px 18px;
		align-items: center;
	}
	.panel {
		align-self: center;
		min-width: 0;
		width: min(640px, 44vw);
	}
	.chrono {
		display: grid;
		align-content: center;
		gap: 10px;
		min-width: 0;
		overflow: hidden;
		padding-left: 22px;
		border-left: 1px solid var(--rule);
	}
	.time {
		display: grid;
		gap: 6px;
	}
	.time :global(.hms) {
		font: 400 40px/1 var(--code);
		color: var(--ink);
		letter-spacing: -0.01em;
	}
	.date {
		font: 700 12.5px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.18em;
		color: var(--faint);
	}
	.date b {
		color: var(--acc-hi);
		font-weight: 700;
	}
	.clk {
		min-width: 0;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 10px;
		min-height: 22px;
	}
	.next {
		display: inline-flex;
		align-items: baseline;
		gap: 7px;
		max-width: 100%;
		text-decoration: none;
		font: 500 12.5px/1.2 var(--code);
		color: var(--ink-2);
		min-width: 0;
	}
	.next .l {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.next .v {
		color: var(--ink);
		font-weight: 600;
	}
	.next.due_soon .g,
	.next.due_soon .v {
		color: var(--soon);
	}
	.next.overdue .v {
		background: var(--acc);
		color: var(--acc-ink);
		padding: 1px 4px;
	}
	.next.not_started .g,
	.next.not_started .v {
		color: var(--person);
	}
	.next:hover .l {
		color: var(--ink);
		text-decoration: underline;
	}
	.clk :global(.more) {
		font: 600 12px/1 var(--ui);
		color: var(--dim);
		border-bottom: 1px solid var(--rule-2);
		padding-bottom: 2px;
	}
	.idle {
		font: 600 12.5px/1 var(--ui);
		color: var(--faint);
	}
	.acts {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.find {
		min-width: 0;
		display: inline-flex;
		align-items: center;
		gap: 9px;
		height: 32px;
		padding: 0 6px 0 12px;
		border-radius: 2px;
		box-shadow: inset 0 0 0 1px var(--rule-2);
		font: 700 13.5px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--ink-2);
		flex: 1;
		justify-content: space-between;
	}
	.find:hover {
		background: var(--raised);
		color: var(--ink);
	}
	.acts :global(.youb) {
		display: flex;
		border-radius: 2px;
	}
	:global(.nj-pop) {
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-2);
		border-top: 2px solid var(--acc);
		box-shadow: var(--shadow);
		border-radius: 0;
		z-index: 40;
	}
	:global(.clockpop) {
		width: 360px;
	}
	.cp {
		padding: 4px 14px 8px;
	}
	:global(.youp) {
		width: 320px;
		font: 500 14px/1.4 var(--ui);
		white-space: normal;
	}
	.ph {
		display: grid;
		gap: 4px;
		padding: 14px 14px 12px;
		border-bottom: 1px solid var(--rule);
	}
	.ph .faint {
		font-size: 13px;
	}
	.opt {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		padding: 11px 14px;
		border-bottom: 1px solid var(--rule);
		text-decoration: none;
		color: var(--ink-2);
		text-align: left;
		width: 100%;
	}
	.lnkrow:hover {
		background: var(--raised);
		color: var(--ink);
	}
	:global(.nj-switch) {
		display: flex !important;
		flex-direction: row-reverse;
		justify-content: space-between;
		align-items: center;
		gap: 14px;
		width: 100%;
		cursor: pointer;
	}
	.opt:last-child {
		border-bottom: 0;
	}
	.panelb {
		display: none;
	}
	.mpanel {
		display: grid;
		gap: 12px;
		padding: 18px 16px;
	}
	.mh {
		margin: 4px 0 0;
		font-size: 13px;
		color: var(--dim);
	}
	.mclk {
		padding: 0 4px;
	}
	.youm {
		border: 1px solid var(--rule);
	}
	@media (max-width: 1280px) {
		.head {
			grid-template-columns: minmax(260px, 1fr) minmax(0, auto) 250px;
			gap: 20px;
		}
		.station {
			font-size: 52px;
		}
	}
	@media (max-width: 1080px) {
		.head {
			grid-template-columns: 1fr 250px;
		}
		.panel {
			grid-column: 1 / -1;
			grid-row: 2;
			width: 100%;
		}
	}
	@media (max-width: 760px) {
		.head {
			grid-template-columns: minmax(0, 1fr) auto;
			gap: 10px;
			padding: 12px var(--gut) 16px;
		}
		.panel,
		.chrono {
			display: none;
		}
		.org {
			display: none;
		}
		.station {
			font-size: 40px;
			margin-top: 8px;
		}
		.panelb {
			display: grid;
			grid-column: 2;
			grid-row: 1;
			align-self: start;
			justify-items: end;
			gap: 5px;
			padding: 6px 10px;
			border-radius: 2px;
			box-shadow: inset 0 0 0 1px var(--rule-2);
		}
		.pt {
			font: 500 14px/1 var(--code);
			color: var(--ink);
		}
		.pl {
			font: 700 12px/1 var(--display);
			text-transform: uppercase;
			letter-spacing: 0.2em;
			color: var(--acc-hi);
		}
		.plate {
			grid-row: 1;
		}
	}
</style>
