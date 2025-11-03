<script lang="ts">
	// Record a regulatory determination (DESIGN.md §6.11): for the SEC, Material / Not material, with reasons /
	// Not yet: decide by… Each outcome is recorded with a timestamp; Material starts the 8-K clock, which drops
	// into the bar. The decision belongs to a person with reporting:assess, never to the watch.
	import { api, problemMessage } from '$lib/api';
	import type { RegimeAssessment } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { now, sec8k, shortDay, NY, timeOf } from '$lib/time/clock';
	import { regimeShort } from '$lib/util/labels';

	let { incidentId, reference, a, tone = 'console', onrecorded, oncancel }: { incidentId: string; reference: string; a: RegimeAssessment; tone?: 'console' | 'brief'; onrecorded?: () => void; oncancel?: () => void } = $props();

	const sec = $derived(a.regime === 'sec_8k');
	let outcome = $state<'yes' | 'no' | 'later' | ''>('');
	let rationale = $state('');
	let decideBy = $state('');
	let impact = $state<'significant' | 'relevant'>('relevant');
	let busy = $state(false);
	let err = $state('');
	const can = $derived(session.can('reporting:assess'));
	const dueIfMaterial = $derived(sec8k.item105Due(now()));
	$effect(() => {
		if (!decideBy) {
			const d = new Date(+now() + 2 * 864e5);
			decideBy = d.toISOString().slice(0, 10);
		}
	});

	async function record(e: SubmitEvent) {
		e.preventDefault();
		err = '';
		if (!outcome) {
			err = 'Choose one of the three outcomes.';
			return;
		}
		if (outcome !== 'later' && rationale.trim().length < 10) {
			err = 'Give the reasons in a sentence or two (at least 10 characters). They are kept with the decision.';
			return;
		}
		busy = true;
		try {
			if (outcome === 'later') {
				await api.incidents.note(incidentId, `Materiality decision for ${regimeShort(a.regime)} deferred: decide by ${decideBy}. ${rationale}`.trim());
				ui.toast(`Recorded: decide by ${shortDay(new Date(decideBy + 'T12:00:00Z'))}. The clock has not started.`, 'info');
			} else {
				const at = now().toISOString();
				await api.incidents.assess(incidentId, a.regime, {
					applies: outcome === 'yes',
					rationale: rationale.trim(),
					entityId: a.entityId,
					...(sec ? { material: outcome === 'yes', materialityDeterminedAt: at } : { awareAt: at, significant: outcome === 'yes', ...(a.regime === 'soci' ? { impactLevel: impact } : {}) })
				});
				ui.toast(
					outcome === 'yes'
						? sec
							? `Recorded as material at ${timeOf(at)}. The 8-K clock is running: due ${shortDay(dueIfMaterial, NY)} 17:30 New York time.`
							: `Recorded. The ${regimeShort(a.regime)} clock has started.`
						: `Recorded as ${sec ? 'not material' : 'not reportable'} at ${timeOf(at)}, with your reasons.`
				);
			}
			onrecorded?.();
		} catch (x) {
			err = problemMessage(x);
		} finally {
			busy = false;
		}
	}
	const words = $derived(
		sec
			? { yes: 'Material', yesNote: `starts the SEC clock · Form 8-K Item 1.05 due ${shortDay(dueIfMaterial, NY)}, 17:30 New York`, no: 'Not material, with reasons', noNote: 'no filing; the reasons are kept for the record', later: 'Not yet: decide by…', laterNote: 'records when you will decide; no clock yet' }
			: { yes: 'Reportable', yesNote: `starts the ${regimeShort(a.regime)} clock from now`, no: 'Not reportable, with reasons', noNote: 'no notice; the reasons are kept', later: 'Not yet: decide by…', laterNote: 'records when you will decide' }
	);
</script>

<form class="sheet {tone}" onsubmit={record} aria-label="Record the decision for {reference}">
	<p class="lbl mono">◆ {sec ? 'materiality decision · SEC Form 8-K, Item 1.05' : `${regimeShort(a.regime)} determination`} · {reference}</p>
	{#if !can}
		<p class="ro">Only the CISO or the disclosure committee can record this. You can read the watch’s view and the evidence.</p>
	{/if}
	<fieldset disabled={!can || busy}>
		<legend class="sr">Outcome</legend>
		<label class="opt" class:on={outcome === 'yes'}><input type="radio" bind:group={outcome} value="yes" /><span><b>{words.yes}</b><small>{words.yesNote}</small></span></label>
		<label class="opt" class:on={outcome === 'no'}><input type="radio" bind:group={outcome} value="no" /><span><b>{words.no}</b><small>{words.noNote}</small></span></label>
		<label class="opt" class:on={outcome === 'later'}><input type="radio" bind:group={outcome} value="later" /><span><b>{words.later}</b><small>{words.laterNote}</small></span></label>
		{#if outcome === 'later'}
			<label class="field"><span>decide by</span><input class="input" type="date" bind:value={decideBy} /></label>
		{/if}
		{#if a.regime === 'soci' && outcome === 'yes'}
			<label class="field"><span>impact on the critical asset</span><select class="select" bind:value={impact}><option value="significant">significant (12 h)</option><option value="relevant">relevant (72 h)</option></select></label>
		{/if}
		<label class="field"><span>{outcome === 'later' ? 'note (optional)' : 'reasons (kept with the decision)'}</span><textarea class="textarea" rows="3" bind:value={rationale} placeholder={sec ? 'e.g. One mailbox, no money moved, no customer data reached, contained in two minutes.' : ''}></textarea></label>
	</fieldset>
	{#if err}<p class="ferr" role="alert">{err}</p>{/if}
	<div class="row">
		<button class="btn pri" type="submit" disabled={!can || busy}>{busy ? 'Recording…' : 'Record the decision'}</button>
		{#if oncancel}<button class="lnk" type="button" onclick={oncancel}>not now</button>{/if}
	</div>
</form>

<style>
	.sheet {
		display: grid;
		gap: 12px;
	}
	.lbl {
		margin: 0;
		font-size: 11.5px;
		color: var(--lav);
	}
	.ro {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
	fieldset {
		border: 0;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 8px;
	}
	.opt {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		padding: 10px 12px;
		border-radius: 6px;
		border: 1px solid var(--line-strong);
		cursor: pointer;
	}
	.opt.on {
		border-color: color-mix(in srgb, var(--lav) 60%, transparent);
		background: var(--lav-wash);
	}
	.opt input {
		margin-top: 3px;
		accent-color: var(--lav);
	}
	.opt span {
		display: grid;
		gap: 2px;
	}
	.opt b {
		font-size: 14px;
		font-weight: 600;
	}
	.opt small {
		font: 400 11.5px/1.4 var(--mono);
		color: var(--muted);
	}
	.row {
		display: flex;
		gap: 14px;
		align-items: center;
	}
	.brief .opt {
		border-color: var(--rule-2, var(--line-strong));
	}
</style>
