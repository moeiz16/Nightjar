<script lang="ts">
	// One regulator clock in the top bar: label, rolling digits, glyph + word, and the elapsed rule.
	import type { Deadline } from '$lib/api/types';
	import Tick from './Tick.svelte';
	import { clockState, elapsedFraction, hms, secondsLeft, shortDay, STATE_GLYPH, STATE_WORD, dueLabel, REGIME_TZ } from '$lib/time/clock';
	import { clockLabel, noticeHref, clockLongLabel } from '$lib/util/labels';

	let { d, now, href, fast = false }: { d: Deadline; now: Date; href?: string; fast?: boolean } = $props();
	const state = $derived(clockState(d, now));
	const left = $derived(secondsLeft(d.dueAt, now));
	const elapsed = $derived(elapsedFraction(d.startsAt, d.dueAt, now));
	const cls = $derived(state === 'on_track' ? 'ok' : state === 'due_soon' ? 'soon' : state === 'overdue' ? 'over' : state === 'not_started' ? 'decide' : state === 'submitted' ? 'sent' : '');
	const aria = $derived(
		state === 'not_started'
			? `${clockLongLabel(d)}, ${d.incidentReference}: not started, ${d.decideBy ? `decision due ${shortDay(d.decideBy, REGIME_TZ[d.regime])}` : 'materiality decision needed'}. ${d.basis}`
			: `${clockLongLabel(d)}, ${d.incidentReference}: ${STATE_WORD[state]}, ${hms(left)} left, due ${d.dueAt ? dueLabel(d.dueAt, now, REGIME_TZ[d.regime]) : ''} regime time. ${d.basis}`
	);
</script>

<a class="clock {cls}" href={href ?? noticeHref(d)} style="--elapsed:{elapsed.toFixed(4)}" aria-label={aria} title={d.basis}>
	<span class="lbl">{clockLabel(d)}</span>
	<span class="row">
		{#if state === 'not_started'}
			<span class="t ns">{d.decideBy ? `decide by ${shortDay(d.decideBy, REGIME_TZ[d.regime])}` : 'not started'}</span>
		{:else if state === 'submitted'}
			<span class="t">sent</span>
		{:else}
			<Tick class="t" value={hms(left)} {fast} />
		{/if}
		<span class="st"><span class="g">{STATE_GLYPH[state]}</span>{STATE_WORD[state]}</span>
	</span>
</a>

<style>
	.clock {
		display: grid;
		grid-template-rows: auto auto;
		align-content: center;
		gap: 4px;
		padding: 0 18px;
		border-right: 1px solid var(--line);
		text-decoration: none;
		position: relative;
		flex: none;
		height: 100%;
	}
	.clock:hover {
		background: var(--raised);
	}
	.lbl {
		font: 400 11px/1 var(--mono);
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
		font: 500 19px/1 var(--mono);
		letter-spacing: -0.02em;
		color: var(--ink);
	}
	.t.ns {
		color: var(--muted);
		font-weight: 400;
		font-size: 15px;
	}
	.st {
		font: 400 12px/1 var(--sans);
		color: var(--ink-2);
	}
	.g {
		font-family: var(--mono);
		margin-right: 4px;
	}
	.ok .g,
	.sent .g {
		color: var(--teal);
	}
	.soon .g,
	.soon .row :global(.t) {
		color: var(--amber);
	}
	.over .g,
	.over .row :global(.t) {
		color: var(--red);
	}
	.decide .g {
		color: var(--lav);
	}
	.decide .st {
		text-decoration: underline dashed color-mix(in srgb, var(--lav) 60%, transparent);
		text-underline-offset: 4px;
	}
	.clock::after {
		content: '';
		position: absolute;
		left: 0;
		bottom: -1px;
		height: 2px;
		width: calc(var(--elapsed, 0) * 100%);
		background: var(--teal-line);
	}
	.soon::after {
		background: color-mix(in srgb, var(--amber) 60%, transparent);
	}
	.over::after {
		background: var(--red);
	}
	.decide::after {
		background: repeating-linear-gradient(90deg, var(--lav) 0 4px, transparent 4px 8px);
		width: 100%;
		opacity: 0.5;
	}
</style>
