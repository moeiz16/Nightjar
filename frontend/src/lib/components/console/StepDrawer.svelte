<script lang="ts">
	// The step drawer (DESIGN.md §6.4): the evidence a step cited, its reasons in plain words, and Disagree.
	import { api, problemMessage } from '$lib/api';
	import type { IncidentSummary, Verdict } from '$lib/api/types';
	import type { BatonRow } from '$lib/util/baton';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import Untrusted from './Untrusted.svelte';

	let { row, incident, onclose }: { row: BatonRow; incident: IncidentSummary; onclose: () => void } = $props();
	const d = $derived(row.step?.detail ?? {});
	const reasons = $derived(Array.isArray(d.reasons) ? d.reasons : []);
	const confidence = $derived(typeof d.confidence === 'number' ? Math.round(d.confidence * 100) : null);
	const evidence = $derived(row.evidence ?? (Array.isArray(d.evidence) ? d.evidence : []));
	let disagreeing = $state(false);
	let verdict = $state<Verdict>('false_positive');
	const VERDICT_WORDS: Record<Verdict, string> = { false_positive: 'a false alarm', benign: 'expected activity', true_positive: 'a real attack', needs_review: 'not settled yet' };
	let note = $state('');
	let busy = $state(false);

	async function disagree(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		try {
			if (row.runId && row.step) {
				const fb = await api.investigations.disagree(row.runId, row.step.seq, verdict, note.trim());
				ui.toast(`Recorded: ${VERDICT_WORDS[verdict]}. Kept with ${fb.labelledDetections} alerts for the next triage review.`);
			} else {
				await api.incidents.note(incident.id, `Disagreed with ${row.who} (“${row.title}”): this is ${VERDICT_WORDS[verdict]}. ${note}`.trim());
				ui.toast(`Recorded: ${VERDICT_WORDS[verdict]}.`);
			}
			disagreeing = false;
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<section class="drawer" aria-label="Step detail">
	<header>
		<span class="mono faint">{row.who} · {timeOf(row.at, undefined, true)}{row.step ? ` · step ${row.step.seq}` : ''}</span>
		<button class="lnk" type="button" onclick={onclose}>close <kbd>esc</kbd></button>
	</header>
	<h3>{row.title}</h3>
	{#if row.why}<p class="why"><span class="eng faint">Why</span> {row.why}</p>{/if}
	{#if confidence !== null}<p class="why"><span class="eng faint">Confidence</span> {confidence} in 100</p>{/if}

	{#if reasons.length}
		<div class="blk">
			<h4>Reasons, by weight</h4>
			<ul class="reasons">
				{#each reasons as r, i (i)}
					<li><span class="w mono {r.weight}">{r.weight === 'strong' ? '▮▮▮' : r.weight === 'medium' ? '▮▮▯' : '▮▯▯'} {r.weight}</span>{r.text}</li>
				{/each}
			</ul>
		</div>
	{/if}
	{#if row.lookedAt.length}
		<div class="blk">
			<h4>Looked at</h4>
			<p class="looked">{row.lookedAt.join(' · ')}</p>
		</div>
	{/if}
	{#if evidence.length}
		<div class="blk evb">
			<h4>Evidence cited</h4>
			<ul class="ev">
				{#each evidence as e, i (i)}
					<li>
						<span class="mono faint">{e.ref} · {timeOf(e.at, undefined, true)} · {e.source}</span>
						<Untrusted value={e.text} label={null} />
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if session.can('incidents:write') && row.step}
		{#if !disagreeing}
			<div class="row"><button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={() => (disagreeing = true)}>Disagree</button><span class="mono faint">records a person’s verdict</span></div>
		{:else}
			<form class="dis" onsubmit={disagree}>
				<label class="label"><span>your verdict</span>
					<select class="select" bind:value={verdict}><option value="false_positive">A false alarm</option><option value="benign">Expected activity</option><option value="true_positive">A real attack</option><option value="needs_review">Needs more evidence</option></select>
				</label>
				<label class="label"><span>why (optional)</span><input class="input" bind:value={note} /></label>
				<div class="row"><button class="btn preset-filled-surface-950-50 btn-sm" type="submit" disabled={busy}>Record verdict</button><button class="lnk" type="button" onclick={() => (disagreeing = false)}>cancel</button></div>
			</form>
		{/if}
	{/if}
</section>

<style>
	.drawer {
		position: relative;
		padding: 18px 28px 22px;
		display: grid;
		gap: 12px;
		align-content: start;
	}
	/* wide sheets: the step and the verdict on the left, the evidence it cited on the right */
	@media (min-width: 1000px) {
		.drawer {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
			column-gap: 32px;
		}
		.drawer > :global(*) {
			grid-column: 1;
		}
		.drawer > .evb {
			grid-column: 2;
			grid-row: 1 / span 12;
			max-height: calc(min(82vh, 820px) - 60px);
			overflow: auto;
		}
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 12.5px;
	}
	h3 {
		margin: 0;
		font: 800 30px/0.95 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	h4 {
		margin: 0 0 8px;
		font: 700 13px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--dim);
	}
	.why {
		margin: 0;
		font-size: 14.5px;
		color: var(--ink-2);
	}
	.reasons,
	.ev {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 7px;
	}
	.reasons li {
		display: grid;
		grid-template-columns: 112px 1fr;
		gap: 10px;
		font-size: 14px;
		color: var(--ink-2);
	}
	.w {
		font-size: 12px;
		color: var(--dim);
	}
	.w.strong {
		color: var(--acc-hi);
	}
	.looked {
		margin: 0;
		font: 400 12px/1.5 var(--code);
		color: var(--ink-2);
	}
	.ev li {
		display: grid;
		gap: 4px;
		font-size: 11.5px;
	}
	.row {
		display: flex;
		gap: 12px;
		align-items: center;
		font-size: 11.5px;
	}
	.dis {
		display: grid;
		gap: 10px;
		max-width: 420px;
	}
	@media (max-width: 760px) {
		.drawer {
			padding: 16px 16px 20px;
		}
		.reasons li {
			grid-template-columns: 1fr;
			gap: 2px;
		}
	}
</style>
