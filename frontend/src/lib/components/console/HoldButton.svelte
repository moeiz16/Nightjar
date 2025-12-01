<script lang="ts">
	// Hold to authorise: the visible proof that nothing is switched off without a person. The fill sweeps and
	// the ring arc fills for 1.2 s; releasing early springs back; completing stamps the button.
	import { Spring, prefersReducedMotion } from 'svelte/motion';
	import { onDestroy } from 'svelte';
	import { createHold, HOLD_MS, typedMatches } from '$lib/util/hold';
	import { ui } from '$lib/stores/ui.svelte';
	import { ProgressRing } from '@skeletonlabs/skeleton-svelte';

	interface Result {
		label: string;
		sub: string;
	}
	let {
		label = 'Hold to authorise',
		hint = 'or hold Space for 1.2 s',
		glyph = '‖',
		disabled = false,
		disabledReason = '',
		confirmName = '',
		onconfirm,
		done = null,
		id
	}: {
		label?: string;
		hint?: string;
		glyph?: string;
		disabled?: boolean;
		disabledReason?: string;
		/** for the typed alternative: the device or person name to type */
		confirmName?: string;
		onconfirm: () => Promise<Result>;
		/** already authorised (e.g. reloaded): show the stamp */
		done?: Result | null;
		id?: string;
	} = $props();

	const hintId = `hold-hint-${Math.random().toString(36).slice(2, 8)}`;
	let p = $state(0);
	const shown = new Spring(0, { stiffness: 0.18, damping: 0.75 });
	let busy = $state(false);
	let result = $state<Result | null>(null);
	let error = $state('');
	let typed = $state('');
	const finished = $derived(result ?? done);

	const hold = createHold({
		ms: HOLD_MS,
		onProgress: (v) => {
			p = v;
			if (v > 0) shown.set(v, { instant: true });
		},
		onCancel: () => {
			shown.set(0, { instant: prefersReducedMotion.current });
			ui.announce('Released early. Nothing was done.');
		},
		onComplete: () => complete()
	});
	onDestroy(() => hold.release());
	/** Start a hold that nobody releases (the landing's "Marcus, on call, holds"). */
	export function simulate() {
		if (!finished && !busy) hold.start();
	}

	async function complete() {
		busy = true;
		error = '';
		try {
			navigator.vibrate?.(10);
		} catch {
			/* no vibration */
		}
		try {
			result = await onconfirm();
			ui.announce(`${result.label}. ${result.sub}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'That did not go through.';
			hold.reset();
			shown.set(0);
		} finally {
			busy = false;
		}
	}
	function down(e: PointerEvent) {
		if (disabled || finished || busy || e.button > 0) return;
		(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
		hold.start();
	}
	function up() {
		hold.release();
	}
	function keydown(e: KeyboardEvent) {
		if (e.key !== ' ' && e.key !== 'Enter') return;
		e.preventDefault();
		if (e.repeat || disabled || finished || busy) return;
		hold.start();
	}
	function keyup(e: KeyboardEvent) {
		if (e.key !== ' ' && e.key !== 'Enter') return;
		e.preventDefault();
		hold.release();
	}
	async function confirmTyped(e: SubmitEvent) {
		e.preventDefault();
		if (!typedMatches(typed, confirmName)) {
			error = `Type ${confirmName} exactly to confirm.`;
			return;
		}
		await complete();
	}
	const pct = $derived(Math.round(p * 100));
	const fill = $derived(prefersReducedMotion.current ? 0 : shown.current);
</script>

{#if ui.holdByTyping && confirmName && !finished}
	<form class="typed" onsubmit={confirmTyped}>
		<label class="label"><span>Type <b class="mono">{confirmName}</b> to confirm</span>
			<input class="input mono" bind:value={typed} {disabled} autocomplete="off" spellcheck="false" aria-describedby={hintId} />
		</label>
		<button class="btn preset-filled-primary-700-300" type="submit" disabled={disabled || busy}>{busy ? 'Working…' : label.replace('Hold to', 'Confirm and')}</button>
		<span id={hintId} class="sr">Typed confirmation replaces holding. Change it in ⌘K.</span>
	</form>
{:else}
	<button
		{id}
		class="hold"
		class:done={!!finished}
		class:stamp={!!result}
		class:reduced={prefersReducedMotion.current}
		type="button"
		style="--p:{fill}"
		aria-describedby={hintId}
		aria-disabled={disabled || !!finished || busy}
		onpointerdown={down}
		onpointerup={up}
		onpointercancel={up}
		onpointerleave={up}
		onkeydown={keydown}
		onkeyup={keyup}
		onclick={(e) => e.preventDefault()}
		oncontextmenu={(e) => e.preventDefault()}
	>
		<span class="dial" aria-hidden="true">
			<ProgressRing
				value={finished ? 100 : prefersReducedMotion.current ? pct : Math.round(shown.current * 100)}
				max={100}
				size="size-[38px]"
				strokeWidth="9px"
				trackStroke={finished ? 'stroke-[color-mix(in_srgb,var(--teal-ink)_30%,transparent)]' : 'stroke-surface-300-700'}
				meterStroke={finished ? 'stroke-[var(--teal-ink)]' : 'stroke-primary-700-300'}
				meterTransition=""
				meterDuration=""
			/>
			<span class="glyph">{finished ? '✓' : glyph}</span>
		</span>
		<span class="txt">
			<b>{finished ? finished.label : busy ? 'Authorising…' : label}</b>
			<small>{finished ? finished.sub : disabled && disabledReason ? disabledReason : hint}</small>
		</span>
		<span class="pct mono" aria-hidden="true">{!finished && p > 0 ? pct + '%' : ''}</span>
	</button>
	<span id={hintId} class="sr">Press and hold for 1.2 seconds, or hold Space or Enter. Releasing early cancels.</span>
{/if}
{#if error}<p class="ferr" role="alert">{error}</p>{/if}

<style>
	.hold {
		position: relative;
		display: flex;
		align-items: center;
		gap: 14px;
		width: 100%;
		height: 56px;
		padding: 0 16px 0 10px;
		border-radius: 3px;
		border: 1px solid var(--teal-line);
		background: var(--teal-wash);
		color: var(--ink);
		overflow: hidden;
		user-select: none;
		-webkit-user-select: none;
		touch-action: none;
		-webkit-touch-callout: none;
	}
	.hold::before {
		content: '';
		position: absolute;
		inset: 0;
		background: var(--teal);
		transform-origin: left;
		transform: scaleX(var(--p));
		opacity: 0.22;
	}
	.hold[aria-disabled='true']:not(.done) {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.dial {
		position: relative;
		width: 38px;
		height: 38px;
		flex: none;
	}
	.glyph {
		position: absolute;
		z-index: 2;
		inset: 0;
		display: grid;
		place-items: center;
		font: 500 13px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--teal);
	}
	.txt {
		position: relative;
		display: grid;
		gap: 4px;
		text-align: left;
		min-width: 0;
	}
	.txt b {
		font-weight: 600;
		font-size: 15px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.txt small {
		font: 400 11.5px/1.2 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
	}
	.pct {
		position: relative;
		margin-left: auto;
		font: 500 13px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--teal);
		min-width: 3ch;
		text-align: right;
	}
	.hold.done {
		border-color: var(--teal);
		background: var(--teal);
		color: var(--teal-ink);
		cursor: default;
	}
	.hold.done::before {
		opacity: 0;
	}
	.hold.done .txt small,
	.hold.done .pct,
	.hold.done .glyph {
		color: var(--teal-ink);
	}
	.typed {
		display: grid;
		gap: 10px;
	}
	.ferr {
		margin: 8px 0 0;
	}
	@media (max-width: 760px) {
		.hold {
			height: 60px;
		}
	}
</style>
