<script lang="ts">
	// An approval card (DESIGN.md §6.5): does / why / undo, Hold to authorise, Decline with a reason, Ask Response why.
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
			<span class="badge preset-outlined-surface-300-700">✕ Declined · {p.rejection?.reason}</span>
		{:else if p.status === 'approved' || p.status === 'executed'}
			<span class="badge preset-tonal-success">✓ {p.status === 'executed' ? 'Done' : 'Authorised'}</span>
		{:else if p.approvals.length && p.requiredRoles.length > 1}
			<span class="badge preset-tonal-secondary">‖ {p.approvals.length} of {p.requiredRoles.length} held · waiting for a second person</span>
		{:else}
			<span class="badge preset-tonal-secondary">‖ Awaiting {gate.canHold ? 'your' : 'a person’s'} approval</span>
		{/if}
		{#if total > 1}<span class="mono faint n">{index} of {total}</span>{/if}
	</div>
	<h2 id="ap-{p.id}">{title}</h2>
	<div class="by mono">{p.kind.startsWith('quarantine') || p.targetType === 'message' || p.targetType === 'email' ? 'the phishing email' : p.target} · proposed by {p.proposedBy.name} at {timeOf(p.createdAt)} · {p.incidentReference}</div>
	<dl class="facts">
		<div><dt>does</dt><dd>{p.expectedEffect}</dd></div>
		<div><dt>why</dt><dd>{p.rationale} {#if onwhy}<button class="lnk" type="button" onclick={onwhy}>why ↗</button>{/if}</dd></div>
		<div><dt>undo</dt><dd>{p.undo ?? (p.reversible ? 'Reversible at any time. Logged either way.' : 'Cannot be undone. Logged either way.')}</dd></div>
		{#if person && p.status === 'pending'}
			<div><dt>who</dt><dd>{person.displayName} will see:
				<label class="sr" for="msg-{p.id}">Message {person.displayName} will see</label>
				<textarea id="msg-{p.id}" class="textarea msg" rows="2" bind:value={message}></textarea></dd></div>
		{/if}
		{#if p.crownJewel}<div><dt>rule</dt><dd>Crown jewel: needs a responder and the CISO, two different people.</dd></div>{/if}
	</dl>

	{#if p.status === 'pending' || doneStamp}
		<HoldButton
			id={holdId}
			done={doneStamp}
			disabled={!gate.canHold}
			disabledReason={gate.reason}
			hint={gate.canHold ? 'or hold Space for 1.2 s' : gate.reason}
			confirmName={p.target}
			onconfirm={approve}
		/>
	{/if}

	{#if p.status === 'pending' && !declining}
		<div class="alt">
			<button class="lnk" type="button" onclick={() => (declining = true)} disabled={!gate.canDecide}>Decline with a reason</button>
			{#if onwhy}<button class="lnk" type="button" onclick={onwhy}>Ask Response why</button>{/if}
		</div>
	{:else if p.status === 'pending' && declining}
		<form class="decline" onsubmit={decline}>
			<fieldset>
				<legend class="mono faint">decline · the reason goes back to Response</legend>
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
		border-bottom: 1px solid var(--line);
	}
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		margin-bottom: 12px;
	}
	.top .n {
		font-size: 11.5px;
	}
	h2 {
		margin: 0 0 4px;
		font: 600 19px/1.25 var(--sans);
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.01em;
	}
	.by {
		font: 400 11.5px/1.5 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		overflow-wrap: anywhere;
	}
	.facts {
		margin: 14px 0 16px;
		display: grid;
		gap: 10px;
	}
	.facts div {
		display: grid;
		grid-template-columns: 46px minmax(0, 1fr);
		gap: 8px;
		font-size: 13px;
		line-height: 1.45;
		color: var(--ink-2);
	}
	dt {
		font: 400 11.5px/1.65 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--faint);
	}
	dd {
		margin: 0;
	}
	.msg {
		margin-top: 6px;
		font-size: 12.5px;
	}
	.alt {
		display: flex;
		justify-content: space-between;
		margin-top: 12px;
		gap: 12px;
	}
	.decline {
		margin-top: 14px;
		display: grid;
		gap: 10px;
	}
	fieldset {
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 10px 12px;
		margin: 0;
		display: grid;
		gap: 7px;
	}
	legend {
		font-size: 11.5px;
		padding: 0 4px;
	}
	.rad {
		display: flex;
		gap: 8px;
		align-items: center;
		font-size: 13px;
		color: var(--ink-2);
	}
	.rad input {
		accent-color: var(--teal);
	}
	.row {
		display: flex;
		gap: 14px;
		align-items: center;
	}
</style>
