<script lang="ts">
	// Notice drafting and (mock) submission (DESIGN.md §4): the signal form. A pre-send checklist runs down the left
	// (each required field ✓ or ◐, jumping to its paragraph); the draft is a boxed form header (to, from, precedence,
	// due, reference) above numbered paragraphs; the evidence it cites is a strip bay below. "Review and submit" first
	// lists any required field still missing; only then can a person hold to submit.
	import { page } from '$app/state';
	import { tick } from 'svelte';
	import { Progress } from '@skeletonlabs/skeleton-svelte';
	import HoldButton from '$lib/components/console/HoldButton.svelte';
	import Untrusted from '$lib/components/console/Untrusted.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { DetectionDoc, Deadline, Notice, RegimeContacts, Regime } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { resolveIncidentId } from '$lib/util/incident';
	import { clockState, dueBoth, dueLabel, hm, nis2, REGIME_TZ, secondsLeft, STATE_GLYPH, STATE_WORD, timeOf, whenShort } from '$lib/time/clock';
	import { clockLongLabel, regimeShort } from '$lib/util/labels';

	const regime = $derived((page.params.regime ?? 'nis2') as Regime);
	let incidentId = $state('');
	let notice = $state<Notice | null>(null);
	let deadline = $state<Deadline | null>(null);
	let all = $state<Deadline[]>([]);
	let evidence = $state<NonNullable<DetectionDoc['evidence']>>([]);
	let summarised = $state(false); // the CISO's view: detection summaries, not events as received
	let contacts = $state<RegimeContacts>({});
	let bodies = $state<Record<string, string>>({});
	let err = $state('');
	let reviewing = $state(false);
	let saving = $state(false);
	let lit = $state('');
	let evOpen = $state(false);
	let savedAt = $state<string | null>(null);

	async function load() {
		try {
			incidentId = await resolveIncidentId(page.params.id ?? '');
			const rep = await api.incidents.reporting(incidentId);
			all = rep.deadlines;
			const want = page.url.searchParams.get('n');
			const d = (want ? rep.deadlines.find((x) => x.noticeId === want) : undefined) ?? rep.deadlines.find((x) => x.regime === regime && x.noticeId && x.state !== 'submitted') ?? rep.deadlines.find((x) => x.regime === regime && x.noticeId) ?? null;
			deadline = d;
			if (d?.noticeId) notice = await api.notices.get(d.noticeId);
			if (notice) {
				bodies = Object.fromEntries(notice.sections.map((s) => [s.key, s.body]));
				savedAt = notice.versions.at(-1)?.editedAt ?? null;
			}
			if (session.can('detections:read')) {
				const dets = await api.incidents.detections(incidentId).catch(() => []);
				evidence = dets.flatMap((x) => x.evidence ?? []);
			}
			summarised = !evidence.length;
			if (!evidence.length) {
				// Least privilege: the CISO reads the summarised evidence (rule, time span, people and devices), never
				// the matched fields or log text behind it.
				const sum = await api.incidents.evidence(incidentId).catch(() => null);
				evidence = (sum?.detections ?? []).map((d, k) => ({
					ref: `E-${String(k + 1).padStart(2, '0')}`,
					eventId: d.id,
					timestamp: d.firstSeen,
					dataset: `${d.eventCount} ${d.eventCount === 1 ? 'event' : 'events'}`,
					action: d.source,
					summary: { text: `${d.ruleTitle}${[...d.users, ...d.hosts].length ? ` · ${[...d.users, ...d.hosts].slice(0, 4).join(', ')}` : ''}`, field: 'summary', flags: [], truncated: false },
					host: null,
					user: null,
					fields: {}
				}));
			}
			contacts = await api.settings.regulators().catch(() => ({}));
		} catch (x) {
			err = problemMessage(x);
		}
	}
	$effect(() => {
		void page.params.id;
		void regime;
		load();
	});
	$effect(() => {
		if (page.url.searchParams.get('compare') && evidence.length) tick().then(() => document.getElementById('evidence')?.focus());
	});

	const dirty = $derived(!!notice && notice.sections.some((s) => (bodies[s.key] ?? '') !== s.body));
	const unfilled = (b: string | undefined) => !(b ?? '').trim() || (b ?? '').includes('[to be confirmed]');
	const missing = $derived(notice ? notice.sections.filter((s) => s.required && unfilled(bodies[s.key])) : []);
	const required = $derived(notice?.sections.filter((s) => s.required && s.key !== 'narrative') ?? []);
	const filledCount = $derived(required.filter((r) => !missing.some((m) => m.key === r.key)).length);
	const optional = $derived(notice?.sections.filter((s) => !s.required || s.key === 'narrative') ?? []);
	const st = $derived(deadline ? clockState(deadline, live.now) : 'on_track');
	const canSubmit = $derived(session.can('notices:submit'));
	const canEdit = $derived(session.can('notices:draft') && notice?.status !== 'submitted');
	const regimeContacts = $derived(contacts[regime]?.contacts ?? []);
	const next = $derived(all.filter((d) => d.regime === regime && d.id !== deadline?.id && d.state !== 'submitted'));

	async function save() {
		if (!notice) return;
		saving = true;
		try {
			notice = await api.notices.patch(notice.id, notice.sections.map((s) => ({ key: s.key, body: bodies[s.key] ?? '' })), notice.version);
			savedAt = notice.versions.at(-1)?.editedAt ?? null;
			ui.toast(`Saved draft ${notice.version}.`);
		} catch (x) {
			err = problemMessage(x);
		} finally {
			saving = false;
		}
	}
	async function submit() {
		if (!notice) throw new Error('No draft');
		if (dirty) await save();
		if (notice.status === 'draft') notice = await api.notices.approve(notice.id).catch((x) => {
			throw new Error(problemMessage(x));
		});
		notice = await api.notices.submit(notice.id, 'portal').catch((x) => {
			throw new Error(problemMessage(x));
		});
		live.loadClocks();
		return { label: `Recorded as submitted (demo) · ${timeOf(notice.submission!.submittedAt, undefined, true)}`, sub: `by ${notice.submission!.submittedBy.fullName} · ${notice.submission!.receipt}` };
	}
	async function goField(key: string) {
		await tick();
		const el = document.getElementById('f-' + key);
		el?.focus();
		el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
	}
	function cite(ref: string) {
		lit = ref;
		evOpen = true;
		document.getElementById('ev-' + ref)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		setTimeout(() => (lit = ''), 2400);
	}
	const finalDue = $derived(notice && notice.type === 'nis2.incident_notification' ? nis2.finalReportDue(new Date(notice.dueAt ?? live.now)) : null);
	$effect(() => {
		ui.setStation(page.url.pathname, 'Notice draft'); // the form's own header names the notice and the incident
	});
</script>

<svelte:head><title>{notice?.typeLabel ?? 'Notice'} · {page.params.id} · Nightjar SOC</title></svelte:head>

<div class="signal rack">
	{#if notice}
		<aside class="check" aria-label="Pre-send checklist">
			<p class="eng ch">Pre-send checklist</p>
			<div class="rd">
				<span class="mono">{filledCount} of {required.length}</span><span>filled</span>
				<Progress value={filledCount} max={Math.max(1, required.length)} height="h-2" meterBg={missing.length ? 'bg-[var(--soon)]' : 'bg-[var(--ok)]'} trackBg="bg-[var(--rule-2)]" rounded="rounded-[0px]" />
			</div>
			<!-- only what is still to do: the filled fields are ticked in the form itself -->
			{#if missing.length}
				<ol class="items">
					{#each required as s, i (s.key)}
						{#if unfilled(bodies[s.key])}
							<li class="todo"><button type="button" onclick={() => goField(s.key)}><span class="k mono" aria-hidden="true">◐</span><span class="nn mono">{i + 1}</span><span>Confirm {s.title.toLowerCase()}</span><span class="sr"> (missing)</span></button></li>
						{/if}
					{/each}
				</ol>
			{/if}
			{#if !missing.length}<p class="okb">✓ Ready to review</p>{/if}
		</aside>
	{/if}
	<section class="form" aria-label="Notice draft">
		{#if err}<p class="ferr pad">{err}</p>{/if}
		{#if !notice && !err}
			<div class="pad faint">{deadline?.state === 'not_started' ? 'No clock yet: this notice waits for the materiality decision.' : 'Opening the draft…'}</div>
		{:else if notice}
			<div class="hdr">
				<div class="f to" title="From Settings › Regulator contacts"><span class="eng">To</span><b>{notice.recipient.name}</b></div>
				<div class="f" title={session.name}><span class="eng">From</span><b>{session.org?.name ?? 'Halden Logistics'}</b></div>
				<div class="f prec" title={deadline ? clockLongLabel(deadline) : regimeShort(regime)}><span class="eng">Precedence</span><b>{notice.typeLabel}</b></div>
				<div class="f due st-{st}"><span class="eng">Due</span>{#if deadline?.dueAt}<b class="mono">{hm(secondsLeft(deadline.dueAt, live.now))}</b><small title={dueBoth(deadline.dueAt, live.now, REGIME_TZ[deadline.regime])}><span class="g">{STATE_GLYPH[st]}</span> {STATE_WORD[st]}</small>{:else}<b>—</b>{/if}</div>
				<div class="f" title="{savedAt ? `Saved ${whenShort(savedAt)}` : ''}{notice.approvedBy ? ` · approved by ${notice.approvedBy.fullName}` : ''}"><span class="eng">Ref</span><b class="mono">{page.params.id}</b><small>{notice.status === 'submitted' ? 'submitted' : `draft ${notice.version}`}</small></div>
			</div>
			<form class="draft" onsubmit={(e) => (e.preventDefault(), save())}>
				<h2 class="sr">Required fields</h2>
				{#each required as s, i (s.key)}
					{@const has = !unfilled(bodies[s.key])}
					<div class="req" class:todo={!has}>
						<span class="k mono" aria-hidden="true">{i + 1}</span>
						<label class="lbl" for="f-{s.key}">{s.title}{#if !has}<span class="miss1" aria-hidden="true"> ◐</span><span class="sr"> (missing)</span>{/if}</label>
						<div class="val">
							{#if s.key === 'contact' && regimeContacts.length && canEdit}
								<select id="f-{s.key}" class="select" bind:value={bodies[s.key]}>
									<option value="">Confirm a contact person…</option>
									{#if bodies[s.key] && !regimeContacts.some((c) => bodies[s.key] === `${c.name}, ${c.role.toLowerCase()}, ${c.phone}`)}<option value={bodies[s.key]}>{bodies[s.key]}</option>{/if}
									{#each regimeContacts as c (c.email)}<option value="{c.name}, {c.role.toLowerCase()}, {c.phone}">{c.name} · {c.role}</option>{/each}
								</select>
							{:else if canEdit}
								<!-- a field grows with its sentence, so what the regulator reads is never cut off -->
								<textarea id="f-{s.key}" class="textarea fld" rows={Math.max(1, Math.ceil((bodies[s.key]?.length ?? 0) / 84))} bind:value={bodies[s.key]} placeholder={has ? '' : (s.hint ?? 'confirm…')}></textarea>
							{:else}
								<span id="f-{s.key}">{bodies[s.key] || '—'}</span>
							{/if}
						</div>
						<span class="refs">{#each s.evidence ?? [] as r (r)}<button type="button" class="ref mono" onclick={() => cite(r)}>[{r}]</button>{/each}</span>
					</div>
				{/each}
				{#each optional as s (s.key)}
					<details class="fold">
						<summary class="eng sec">{s.title}</summary>
						<textarea id="f-{s.key}" class="textarea narr" rows="6" bind:value={bodies[s.key]} readonly={!canEdit} aria-label={s.title}></textarea>
						<span class="refs">{#each s.evidence ?? [] as r (r)}<button type="button" class="ref mono" onclick={() => cite(r)}>[{r}]</button>{/each}</span>
					</details>
				{/each}
				{#if next.length || finalDue}
					<details class="fold">
						<summary class="eng sec">What comes next</summary>
						<ul class="next">
							{#each next as d (d.id)}<li><span class="mono">○</span> {d.noticeLabel} · {d.dueAt ? `due ${dueLabel(d.dueAt, live.now, REGIME_TZ[d.regime])}` : 'not started'}</li>{/each}
							{#if finalDue}<li><span class="mono">○</span> Final report · within one month of this notification</li>{/if}
						</ul>
					</details>
				{/if}
				{#if canEdit}<div class="row"><button class="btn preset-outlined-surface-300-700" type="submit" disabled={!dirty || saving}>{saving ? 'Saving…' : dirty ? 'Save draft' : 'Saved'}</button></div>{/if}
			</form>

			<div class="submit">
				{#if notice.status === 'submitted' && notice.submission}
					<p class="done">✓ Recorded as submitted (demo) {whenShort(notice.submission.submittedAt)} by {notice.submission.submittedBy.fullName} · reference <span class="mono">{notice.submission.receipt}</span> · the clock stopped</p>
				{:else if !reviewing}
					<button class="btn preset-filled-primary-500" type="button" onclick={() => (reviewing = true)}>Review and submit</button>
				{:else}
					<div class="review ticks" role="region" aria-label="Review before submitting">
						{#if missing.length}
							<p class="miss"><span class="mono">◐</span> Before this can go to {notice.recipient.name}, confirm:</p>
							<ul>{#each missing as m (m.key)}<li><button type="button" class="lnk" onclick={() => goField(m.key)}>{m.title} →</button></li>{/each}</ul>
						{:else}
							<p class="ok" title="Submitting records who, when and what was sent. Nothing is sent to a regulator from this demo."><span class="mono">✓</span> Ready to submit</p>
							{#if canSubmit}
								<div class="holdw"><HoldButton label="Hold to submit (demo)" hint="recorded, not sent" glyph="↗" onconfirm={submit} /></div>
							{:else}
								<p class="faint">Only the CISO can submit notices. Your draft is saved for Ingrid Vos.</p>
							{/if}
						{/if}
						<button class="lnk" type="button" onclick={() => (reviewing = false)}>Back to the draft</button>
					</div>
				{/if}
			</div>
		{/if}
	</section>
</div>
<details class="evw rack" bind:open={evOpen}>
	<summary class="eng evs">Evidence <span class="mono">{evidence.length}</span></summary>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<ol class="ev" id="evidence" tabindex="0" aria-label="Evidence cited by the draft">
		{#each evidence as e, i (e.eventId + i)}
			{@const ref = e.ref ?? `E-${String(i + 1).padStart(2, '0')}`}
			<li id="ev-{ref}" class:lit={lit === ref}>
				<span class="head"><b class="mono">{ref}</b><span class="mono">{timeOf(e.timestamp, undefined, true)}</span><span>{e.dataset}{e.host ? ` · ${e.host}` : ''}</span></span>
				<Untrusted value={e.summary} label={summarised ? null : undefined} />
			</li>
		{:else}
			<li class="faint">Click an [E-xx] in the draft to highlight it here.</li>
		{/each}
	</ol>
	<p class="foot faint">{regimeShort(regime)} facts are designed to support the notice, not legal advice.</p>
</details>

<style>
	.signal {
		display: grid;
		grid-template-columns: 290px minmax(0, 1fr);
		gap: 22px;
		margin: 24px var(--gut) 0;
		align-items: start;
	}
	.check {
		position: sticky;
		top: 16px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--soon);
		padding: 16px;
	}
	.ch {
		margin: 0 0 12px;
		font-size: 15px;
		color: var(--ink);
	}
	.rd {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 8px 8px;
		align-items: baseline;
		font-size: 13.5px;
		color: var(--dim);
		margin-bottom: 12px;
	}
	.rd .mono {
		font-size: 20px;
		color: var(--ink);
	}
	.rd :global([data-scope='progress']) {
		grid-column: 1 / -1;
	}
	.items {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.items li button {
		display: grid;
		grid-template-columns: 18px 18px minmax(0, 1fr);
		gap: 8px;
		width: 100%;
		text-align: left;
		padding: 8px 0;
		border-top: 1px solid var(--rule);
		font-size: 14px;
		color: var(--ink-2);
	}
	.items .k {
		color: var(--ok);
	}
	.items .nn {
		font-size: 12px;
		color: var(--faint);
		padding-top: 1px;
	}
	.items li.todo button {
		color: var(--ink);
		font-weight: 700;
	}
	.items li.todo .k {
		color: var(--soon);
	}
	.items li button:hover span:last-of-type {
		text-decoration: underline;
	}
	.okb {
		margin: 12px 0 0;
		font-weight: 700;
		color: var(--ok);
	}
	.form {
		background: var(--plate);
		border: 1px solid var(--rule-2);
		min-width: 0;
	}
	.pad {
		padding: 22px;
	}
	.hdr {
		display: grid;
		grid-template-columns: 1.3fr 1fr 1.2fr 1fr 1fr;
		border-bottom: 2px solid var(--rule-2);
	}
	.f {
		display: grid;
		gap: 6px;
		align-content: start;
		padding: 14px 16px;
		border-left: 1px solid var(--rule);
		min-width: 0;
	}
	.f:first-child {
		border-left: 0;
	}
	.f .eng {
		font-size: 12px;
		color: var(--faint);
	}
	.f b {
		font-size: 15.5px;
		font-weight: 700;
		color: var(--ink);
		overflow-wrap: anywhere;
	}
	.f b.mono {
		font-weight: 500;
		font-size: 17px;
	}
	.f small {
		font-size: 12.5px;
		color: var(--dim);
	}
	.due b {
		font-size: 24px !important;
	}
	.st-due_soon b,
	.st-due_soon .g {
		color: var(--soon);
	}
	.st-overdue b {
		background: var(--acc);
		color: var(--acc-ink);
		padding: 0 4px;
	}
	.draft {
		padding: 6px 22px 18px;
	}
	.sec {
		margin: 22px 0 10px;
		font-size: 15px;
		color: var(--ink);
		display: flex;
		gap: 10px;
		align-items: baseline;
	}
	.fold > summary {
		cursor: pointer;
		list-style: none;
		width: max-content;
	}
	.fold > summary::-webkit-details-marker {
		display: none;
	}
	.fold > summary::after {
		content: '▸';
		color: var(--faint);
	}
	.fold[open] > summary::after {
		content: '▾';
	}
	.evw {
		margin: 22px var(--gut) 0;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--rule-2);
		padding: 0 20px;
	}
	.evs {
		cursor: pointer;
		list-style: none;
		padding: 14px 0;
		font-size: 15px;
		color: var(--ink);
		display: flex;
		gap: 10px;
		align-items: baseline;
	}
	.evs::-webkit-details-marker {
		display: none;
	}
	.evs::after {
		content: '▸';
		color: var(--faint);
	}
	.evw[open] .evs::after {
		content: '▾';
	}
	.evs .mono {
		color: var(--faint);
		font-size: 13px;
	}
	.evw[open] .foot {
		margin-bottom: 16px;
	}
	.req {
		display: grid;
		grid-template-columns: 30px minmax(160px, 230px) minmax(0, 1fr) auto;
		gap: 12px;
		align-items: center;
		padding: 10px 0;
		border-top: 1px solid var(--rule);
	}
	.req .k {
		width: 24px;
		height: 24px;
		display: grid;
		place-items: center;
		font-size: 12px;
		color: var(--ink-2);
		box-shadow: inset 0 0 0 1px var(--rule-2);
	}
	.req.todo .k {
		color: var(--bg);
		background: var(--soon);
		box-shadow: none;
	}
	.lbl {
		font-size: 14.5px;
		font-weight: 700;
		color: var(--ink);
	}
	.miss1 {
		font-weight: 700;
		color: var(--soon);
	}
	.fld {
		width: 100%;
		resize: vertical;
		min-height: 0;
		line-height: 1.45;
	}
	.val :global(.input),
	.val :global(.select) {
		width: 100%;
	}
	.refs {
		display: flex;
		gap: 4px;
	}
	.ref {
		font-size: 12.5px;
		color: var(--acc-hi);
	}
	.ref:hover {
		text-decoration: underline;
	}
	.narr {
		width: 100%;
	}
	.next {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 6px;
		font-size: 14px;
		color: var(--ink-2);
	}
	.row {
		margin-top: 16px;
	}
	.submit {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 14px;
		padding: 16px 22px 20px;
		border-top: 2px solid var(--rule-2);
		background: var(--sunk);
	}
	.review {
		width: 100%;
		max-width: 620px;
		padding: 16px;
		display: grid;
		gap: 12px;
		justify-items: start;
		--tk: var(--acc);
	}
	.miss {
		margin: 0;
		font-weight: 700;
		color: var(--soon);
	}
	.review ul {
		margin: 0;
		padding-left: 18px;
	}
	.ok {
		margin: 0;
		color: var(--ink-2);
	}
	.ok .mono {
		color: var(--ok);
	}
	.holdw {
		width: 100%;
	}
	.done {
		margin: 0;
		font-weight: 600;
		color: var(--ok);
	}
	.ev {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 4px;
		outline: none;
	}
	.ev li {
		display: grid;
		gap: 6px;
		padding: 10px 14px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-left: 4px solid var(--rule-2);
		scroll-margin-top: 20px;
	}
	.ev li.lit {
		border-left-color: var(--acc);
		background: var(--sel);
	}
	.head {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 12px;
		align-items: baseline;
		font-size: 13px;
		color: var(--dim);
	}
	.head b {
		color: var(--acc-hi);
		font-weight: 600;
	}
	.foot {
		margin: 12px 0 0;
		font-size: 13px;
	}
	@media (max-width: 1100px) {
		.signal {
			grid-template-columns: minmax(0, 1fr);
		}
		.check {
			position: static;
		}
		.hdr {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.f:nth-child(odd) {
			border-left: 0;
		}
		.f:nth-child(n + 3) {
			border-top: 1px solid var(--rule);
		}
		.to {
			grid-column: 1 / -1;
		}
	}
	@media (max-width: 760px) {
		.req {
			grid-template-columns: 28px minmax(0, 1fr);
		}
		.req .val,
		.req .refs {
			grid-column: 2;
		}
		.draft,
		.submit {
			padding-inline: 14px;
		}
	}
</style>
