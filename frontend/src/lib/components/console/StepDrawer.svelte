<script lang="ts">
	// The step drawer (DESIGN.md §6.4): the evidence a step cited, its reasons in plain words, and Disagree.
	import { slide } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import { api, problemMessage } from '$lib/api';
	import type { IncidentSummary } from '$lib/api/types';
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
	let verdict = $state('Likely noise');
	let note = $state('');
	let busy = $state(false);

	async function disagree(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		try {
			await api.incidents.note(incident.id, `Disagreed with ${row.who} at ${timeOf(row.at, undefined, true)} (“${row.title}”): ${verdict}. ${note}`.trim());
			ui.toast(`Recorded your verdict: ${verdict.toLowerCase()}. It also feeds the triage check set.`);
			disagreeing = false;
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<section class="drawer" transition:slide={{ duration: prefersReducedMotion.current ? 0 : 200 }} aria-label="Step detail">
	<header>
		<span class="mono faint">{row.who} · {timeOf(row.at, undefined, true)}{row.step ? ` · step ${row.step.seq}` : ''}</span>
		<button class="lnk" type="button" onclick={onclose}>close <kbd>esc</kbd></button>
	</header>
	<h3>{row.title}</h3>
	{#if row.why}<p class="why"><span class="mono faint">why</span> {row.why}</p>{/if}
	{#if confidence !== null}<p class="why"><span class="mono faint">confidence</span> {confidence} in 100</p>{/if}

	{#if reasons.length}
		<div class="blk">
			<h4>reasons, by weight</h4>
			<ul class="reasons">
				{#each reasons as r, i (i)}
					<li><span class="w mono {r.weight}">{r.weight === 'strong' ? '▮▮▮' : r.weight === 'medium' ? '▮▮▯' : '▮▯▯'} {r.weight}</span>{r.text}</li>
				{/each}
			</ul>
		</div>
	{/if}
	{#if row.lookedAt.length}
		<div class="blk">
			<h4>looked at</h4>
			<p class="looked">{row.lookedAt.join(' · ')}</p>
		</div>
	{/if}
	{#if evidence.length}
		<div class="blk">
			<h4>evidence cited</h4>
			<ul class="ev">
				{#each evidence as e (e.ref)}
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
			<div class="row"><button class="btn sm" type="button" onclick={() => (disagreeing = true)}>Disagree</button><span class="mono faint">records a person’s verdict</span></div>
		{:else}
			<form class="dis" onsubmit={disagree}>
				<label class="field"><span>your verdict</span>
					<select class="select" bind:value={verdict}><option>Likely noise</option><option>Likely real</option><option>Needs more evidence</option></select>
				</label>
				<label class="field"><span>why (optional)</span><input class="input" bind:value={note} /></label>
				<div class="row"><button class="btn pri sm" type="submit" disabled={busy}>Record verdict</button><button class="lnk" type="button" onclick={() => (disagreeing = false)}>cancel</button></div>
			</form>
		{/if}
	{/if}
</section>

<style>
	.drawer {
		position: relative;
		z-index: 3;
		margin: 4px 14px 10px var(--tcol, 64px);
		padding: 14px 18px 16px;
		border-radius: 10px;
		border: 1px solid var(--line-strong);
		background: var(--raised);
		box-shadow: var(--shadow);
		display: grid;
		gap: 12px;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 11.5px;
	}
	h3 {
		margin: 0;
		font: 600 15px/1.3 var(--sans);
	}
	h4 {
		margin: 0 0 7px;
		font: 400 11px/1 var(--mono);
		color: var(--faint);
	}
	.why {
		margin: 0;
		font-size: 13px;
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
		font-size: 13px;
		color: var(--ink-2);
	}
	.w {
		font-size: 11.5px;
		color: var(--muted);
	}
	.w.strong {
		color: var(--teal);
	}
	.looked {
		margin: 0;
		font: 400 12px/1.5 var(--mono);
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
			margin: 4px 8px 10px;
		}
		.reasons li {
			grid-template-columns: 1fr;
			gap: 2px;
		}
	}
</style>
