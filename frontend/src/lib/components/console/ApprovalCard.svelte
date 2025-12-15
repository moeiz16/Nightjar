<script lang="ts">
	// An approval card (DESIGN.md §6.5): the action and Hold to authorise up front; does / why / undo one click
	// deeper under Details; Decline (with a reason) and Ask why underneath.
	import { api, problemMessage } from '$lib/api';
	import type { ProposalDoc, IdentityDoc } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { timeOf } from '$lib/time/clock';
	import { actionTitle, approvalGate } from '$lib/util/approvals';
	import HoldButton from './HoldButton.svelte';

	let {
		p = $bindable(),
		index = 1,
		total = 1,
		people = [],
		holdId,
		startDeclining = false,
		onwhy,
		onchange
	}: {
		p: ProposalDoc;
		index?: number;
		total?: number;
		people?: IdentityDoc[];
		holdId?: string;
		startDeclining?: boolean;
		onwhy?: () => void;
		onchange?: (p: ProposalDoc) => void;
	} = $props();

	const gate = $derived(approvalGate(p, session.me?.user.id ?? null, session.role, session.me?.membership?.permissions ?? []));
	const title = $derived(actionTitle(p, people));
	const person = $derived(p.targetType === 'identity' ? people.find((x) => x.username === p.target) : undefined);
	let declining = $state(false);
	$effect(() => {
		if (startDeclining) declining = true;
	});
	let reason = $state('');
	let other = $state('');
	let busy = $state(false);
	let err = $state('');
	let message = $state('');
	$effect(() => {
		if (person && !message) message = `Your account is paused for a security check. IT will contact you within the hour.`;
	});
	// phones have no Space bar: the hint says what a finger does
	const touch = typeof matchMedia !== 'undefined' && matchMedia('(hover: none)').matches;
	const REASONS = ['Wrong target', 'Business-critical right now', 'False positive', 'Other'];

	async function approve() {
		try {
			const next = await api.approvals.approve(p.id);
			p = next;
			onchange?.(next);
			const mine = next.approvals.at(-1);
			if (next.status === 'pending') return { label: `${next.approvals.length} of ${next.requiredRoles.length} held`, sub: 'waiting for a second person' };
			return { label: `Authorised · ${timeOf(mine?.decidedAt ?? new Date(), undefined, true)}`, sub: `by ${mine?.name ?? session.name}` };
		} catch (e) {
			throw new Error(problemMessage(e));
		}
	}
	async function decline(e: SubmitEvent) {
		e.preventDefault();
		const r = reason === 'Other' ? other.trim() : reason;
		if (!r) {
			err = 'Choose or write a reason. It goes back to Response.';
			return;
		}
		busy = true;
		err = '';
		try {
			p = await api.approvals.reject(p.id, r);
			onchange?.(p);
			ui.toast(`Declined. Response will see: “${r}”.`, 'info');
		} catch (x) {
			err = problemMessage(x);
		} finally {
			busy = false;
		}
	}
	const mineApproved = $derived(p.approvals.find((a) => a.userId === session.me?.user.id));
	const doneStamp = $derived(
		p.status === 'approved' || p.status === 'executed'
			? { label: `Authorised · ${timeOf(p.approvals.at(-1)?.decidedAt ?? p.createdAt, undefined, true)}`, sub: `by ${p.approvals.map((a) => a.name).join(' and ')}` }
			: mineApproved
				? { label: `${p.approvals.length} of ${p.requiredRoles.length} held`, sub: 'waiting for a second person' }
				: null
	);
</script>

<article class="ask" aria-labelledby="ap-{p.id}">
	<div class="top">
		{#if p.status === 'rejected'}
			<span class="badge st dec">✕ Declined · {p.rejection?.reason}</span>
		{:else if p.status === 'approved' || p.status === 'executed'}
			<span class="badge st okb">✓ {p.status === 'executed' ? 'Done' : 'Authorised'}</span>
		{:else if p.approvals.length && p.requiredRoles.length > 1}
			<span class="badge st per">‖ {p.approvals.length} of {p.requiredRoles.length} held · waiting for a second person</span>
		{:else if !gate.canHold}
			<!-- "waiting on you" is already said by the plate around the card; only someone else's turn needs a badge -->
			<span class="badge st per">‖ Awaiting a person’s approval</span>
		{/if}
		{#if total > 1}<span class="mono faint n">{index} of {total}</span>{/if}
	</div>
	<h2 id="ap-{p.id}">{title}</h2>
	{#if p.crownJewel}<p class="cj" title="Crown jewel: needs a responder and the CISO, two different people.">◆ Crown jewel · two people</p>{/if}
	{#if person && p.status === 'pending'}
		<label class="who" for="msg-{p.id}"><span class="eng">{person.displayName.split(' ')[0]} will see</span>
			<textarea id="msg-{p.id}" class="textarea msg" rows="2" bind:value={message}></textarea></label>
	{/if}
	<!-- whether it can be undone is said up front, before the hold; what it does and why are one click deeper -->
	<div class="irrev">
		<span class="cq" class:hard={!p.reversible}>{p.reversible ? '↺ Reversible' : '✕ Cannot be undone'}</span>
	</div>
	<details class="more">
		<summary>Details</summary>
		<div class="by">{p.kind.startsWith('quarantine') || p.targetType === 'message' || p.targetType === 'email' ? 'the phishing email' : p.target} · proposed by {p.proposedBy.name.replace(/\s*(colleague|agent)$/i, '')} at {timeOf(p.createdAt)} · {p.incidentReference}</div>
		<dl class="facts">
			<div><dt>does</dt><dd>{p.expectedEffect}</dd></div>
			<div><dt>why</dt><dd>{p.rationale} {#if onwhy}<button class="lnk" type="button" onclick={onwhy}>why ↗</button>{/if}</dd></div>
			{#if p.undo}<div><dt>undo</dt><dd>{p.undo}</dd></div>{/if}
		</dl>
	</details>

	{#if p.status === 'pending' || doneStamp}
		<HoldButton
			id={holdId}
			done={doneStamp}
			disabled={!gate.canHold}
			disabledReason={gate.reason}
			hint={gate.canHold ? (touch ? 'press and hold' : 'or hold Space') : gate.reason}
			confirmName={p.target}
			onconfirm={approve}
		/>
	{/if}

	{#if p.status === 'pending' && !declining}
		<div class="alt">
			<button class="lnk" type="button" onclick={() => (declining = true)} disabled={!gate.canDecide}>Decline</button>
			{#if onwhy}<button class="lnk" type="button" onclick={onwhy}>Ask why</button>{/if}
		</div>
	{:else if p.status === 'pending' && declining}
		<form class="decline" onsubmit={decline}>
			<fieldset>
				<legend class="eng faint">decline · the reason goes back to Response</legend>
				{#each REASONS as r (r)}
					<label class="rad"><input type="radio" name="reason-{p.id}" value={r} bind:group={reason} /> {r}</label>
				{/each}
				{#if reason === 'Other'}<input class="input" bind:value={other} placeholder="Say why in a few words" aria-label="Other reason" />{/if}
			</fieldset>
			{#if err}<p class="ferr" role="alert">{err}</p>{/if}
			<div class="row"><button class="btn preset-outlined-surface-300-700" type="submit" disabled={busy || !gate.canDecide}>Decline</button><button class="lnk" type="button" onclick={() => (declining = false)}>Keep it waiting</button></div>
		</form>
	{/if}
</article>

<style>
	.ask {
		padding: 18px 18px 20px;
		border-bottom: 1px solid var(--rule);
	}
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		margin-bottom: 12px;
	}
	.st {
		height: auto;
		padding: 5px 8px;
		line-height: 1.15;
		white-space: normal;
		text-align: left;
	}
	.per {
		color: var(--person);
		box-shadow: inset 0 0 0 1px var(--person);
		background: var(--person-wash);
	}
	.okb {
		color: var(--ok-ink);
		background: var(--ok);
	}
	.dec {
		color: var(--dim);
		box-shadow: inset 0 0 0 1px var(--rule-2);
	}
	.top .n {
		font-size: 12px;
		white-space: nowrap;
	}
	h2 {
		margin: 0 0 12px;
		font: 800 25px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--ink);
	}
	.by {
		font: 500 12.5px/1.5 var(--ui);
		color: var(--dim);
		overflow-wrap: anywhere;
	}
	.cj {
		margin: 0 0 12px;
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--person);
	}
	.who {
		display: grid;
		gap: 6px;
		margin: 0 0 14px;
	}
	.who .eng {
		font-size: 12px;
		color: var(--faint);
	}
	.irrev {
		display: flex;
		align-items: center;
		gap: 12px;
		margin: 0 0 10px;
	}
	.cq {
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--dim);
	}
	.cq.hard {
		color: var(--soon);
	}
	.more {
		margin: 0 0 14px;
	}
	.more summary {
		cursor: pointer;
		width: max-content;
		font: 600 13px/1 var(--ui);
		color: var(--dim);
		border-bottom: 1px solid var(--rule-2);
		padding-bottom: 2px;
		list-style: none;
	}
	.more summary::-webkit-details-marker {
		display: none;
	}
	.more summary::after {
		content: ' ▸';
	}
	.more[open] summary::after {
		content: ' ▾';
	}
	.more summary:hover {
		color: var(--ink);
	}
	.more .by {
		margin-top: 12px;
	}
	.facts {
		margin: 10px 0 4px;
		display: grid;
		gap: 9px;
	}
	.facts div {
		display: grid;
		grid-template-columns: 52px minmax(0, 1fr);
		gap: 10px;
		font-size: 14px;
		line-height: 1.45;
		color: var(--ink-2);
	}
	dt {
		font: 700 12px/1.7 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--faint);
	}
	dd {
		margin: 0;
	}
	.msg {
		margin-top: 6px;
		font-size: 13.5px;
	}
	.alt {
		display: flex;
		justify-content: space-between;
		margin-top: 14px;
		gap: 12px;
	}
	.decline {
		margin-top: 14px;
		display: grid;
		gap: 10px;
	}
	fieldset {
		border: 1px solid var(--rule-2);
		border-radius: 2px;
		padding: 10px 12px;
		margin: 0;
		display: grid;
		gap: 8px;
	}
	legend {
		font-size: 12px;
		padding: 0 4px;
	}
	.rad {
		display: flex;
		gap: 8px;
		align-items: center;
		font-size: 14px;
		color: var(--ink-2);
	}
	.rad input {
		accent-color: var(--acc);
	}
	.row {
		display: flex;
		gap: 14px;
		align-items: center;
	}
</style>
