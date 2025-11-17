<script lang="ts">
	// Notice drafting and (mock) submission (DESIGN.md §6.6): draft | evidence. "Review and submit" first lists any
	// required field still missing (◐) and links each one to its field; only then can a person hold to submit.
	import { page } from '$app/state';
	import { tick } from 'svelte';
	import Pane from '$lib/components/shell/Pane.svelte';
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
	let contacts = $state<RegimeContacts>({});
	let bodies = $state<Record<string, string>>({});
	let err = $state('');
	let reviewing = $state(false);
	let saving = $state(false);
	let lit = $state('');
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
		document.getElementById('ev-' + ref)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		setTimeout(() => (lit = ''), 2400);
	}
	const finalDue = $derived(notice && notice.type === 'nis2.incident_notification' ? nis2.finalReportDue(new Date(notice.dueAt ?? live.now)) : null);
</script>

<svelte:head><title>{notice?.typeLabel ?? 'Notice'} · {page.params.id} · Nightjar SOC</title></svelte:head>

<div class="tiles">
	<Pane title="notice" sub={deadline ? `${clockLongLabel(deadline)} · ${deadline.incidentReference}` : ''} focus label="Notice draft">
		{#snippet actions()}
			{#if deadline?.dueAt}<span class="mono due st-{st}">due {dueBoth(deadline.dueAt, live.now, REGIME_TZ[deadline.regime])} · {hm(secondsLeft(deadline.dueAt, live.now))} <span class="g">{STATE_GLYPH[st]}</span> {STATE_WORD[st]}</span>{/if}
		{/snippet}
		{#if err}<p class="ferr pad">{err}</p>{/if}
		{#if !notice && !err}
			<div class="pad mono faint">{deadline?.state === 'not_started' ? 'No clock yet: this notice waits for the materiality decision.' : 'Opening the draft…'}</div>
		{:else if notice}
			<div class="to">
				<span>To <b>{notice.recipient.name}</b> <span class="faint">(from Settings › Regime contacts)</span></span>
				<span class="mono faint">{notice.status === 'submitted' ? 'submitted' : `draft ${notice.version}`}{savedAt ? ` · saved ${whenShort(savedAt)}` : ''}{notice.approvedBy ? ` · approved by ${notice.approvedBy.fullName}` : ''}</span>
			</div>
			<form class="draft" onsubmit={(e) => (e.preventDefault(), save())}>
				<h3 class="mono">required for {notice.typeLabel.toLowerCase().replace(/^(nis2|soci|sec)\s/i, (m) => m.toUpperCase())}</h3>
				{#each required as s (s.key)}
					{@const has = !unfilled(bodies[s.key])}
					<div class="req" class:todo={!has}>
						<span class="k mono" aria-hidden="true">{has ? '✓' : '◐'}</span>
						<label class="lbl" for="f-{s.key}">{s.title}{#if !has}<span class="sr"> (missing)</span>{/if}</label>
						<div class="val">
							{#if s.key === 'contact' && regimeContacts.length && canEdit}
								<select id="f-{s.key}" class="select" bind:value={bodies[s.key]}>
									<option value="">Confirm a contact person…</option>
									{#each regimeContacts as c (c.email)}<option value="{c.name}, {c.role.toLowerCase()}, {c.phone}">{c.name} · {c.role}</option>{/each}
								</select>
							{:else if canEdit}
								<input id="f-{s.key}" class="input" bind:value={bodies[s.key]} placeholder={has ? '' : (s.hint ?? 'confirm…')} />
							{:else}
								<span id="f-{s.key}">{bodies[s.key] || '—'}</span>
							{/if}
						</div>
						<span class="refs">{#each s.evidence ?? [] as r (r)}<button type="button" class="ref mono" onclick={() => cite(r)}>[{r}]</button>{/each}</span>
					</div>
				{/each}
				{#each optional as s (s.key)}
					<h3 class="mono sec">── {s.title.toLowerCase()} (editable, plain language) ──</h3>
					<textarea id="f-{s.key}" class="textarea narr" rows="6" bind:value={bodies[s.key]} readonly={!canEdit}></textarea>
					<span class="refs">{#each s.evidence ?? [] as r (r)}<button type="button" class="ref mono" onclick={() => cite(r)}>[{r}]</button>{/each}</span>
				{/each}
				{#if next.length || finalDue}
					<h3 class="mono sec">── what comes next ──</h3>
					<ul class="next mono">
						{#each next as d (d.id)}<li>○ {d.noticeLabel} · {d.dueAt ? `due ${dueLabel(d.dueAt, live.now, REGIME_TZ[d.regime])}` : 'not started'}</li>{/each}
						{#if finalDue}<li>○ final report · within one month of this notification</li>{/if}
					</ul>
				{/if}
				{#if canEdit}<div class="row"><button class="btn" type="submit" disabled={!dirty || saving}>{saving ? 'Saving…' : dirty ? 'Save draft' : 'Saved'}</button></div>{/if}
			</form>

			<div class="submit">
				{#if notice.status === 'submitted' && notice.submission}
					<p class="done mono">✓ Recorded as submitted (demo) {whenShort(notice.submission.submittedAt)} by {notice.submission.submittedBy.fullName} · reference {notice.submission.receipt} · the clock stopped</p>
				{:else if !reviewing}
					<button class="btn pri" type="button" onclick={() => (reviewing = true)}>Review and submit</button>
					<span class="mono faint">{missing.length ? `◐ ${missing.length} required ${missing.length === 1 ? 'field' : 'fields'} missing` : '✓ every required field is filled'}</span>
				{:else}
					<div class="review" role="region" aria-label="Review before submitting">
						{#if missing.length}
							<p class="miss"><span class="mono">◐</span> Before this can go to {notice.recipient.name}, confirm:</p>
							<ul>{#each missing as m (m.key)}<li><button type="button" class="lnk" onclick={() => goField(m.key)}>{m.title} →</button></li>{/each}</ul>
						{:else}
							<p class="ok"><span class="mono">✓</span> Every required field is filled. Submitting records who, when and what was sent. Nothing is sent to a regulator from this demo.</p>
							{#if canSubmit}
								<div class="holdw"><HoldButton label="Hold to submit (demo)" hint="or hold Space for 1.2 s · recorded, not sent" glyph="↗" onconfirm={submit} /></div>
							{:else}
								<p class="mono faint">Only the CISO can submit notices. Your draft is saved for Ingrid Vos.</p>
							{/if}
						{/if}
						<button class="lnk" type="button" onclick={() => (reviewing = false)}>back to the draft</button>
					</div>
				{/if}
			</div>
		{/if}
	</Pane>
	<Pane title="evidence" sub={String(evidence.length)} label="Evidence">
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<ol class="ev" id="evidence" tabindex="0" aria-label="Evidence cited by the draft">
			{#each evidence as e, i (e.eventId + i)}
				{@const ref = e.ref ?? `E-${String(i + 1).padStart(2, '0')}`}
				<li id="ev-{ref}" class:lit={lit === ref}>
					<span class="mono head"><b>{ref}</b> {timeOf(e.timestamp, undefined, true)} · {e.dataset}{e.host ? ` · ${e.host}` : ''}</span>
					<Untrusted value={e.summary} />
				</li>
			{:else}
				<li class="mono faint">Click an [E-xx] in the draft to highlight it here.</li>
			{/each}
		</ol>
		<p class="foot mono faint">{regimeShort(regime)} facts are designed to support the notice, not legal advice.</p>
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.9fr);
		gap: 1px;
		background: var(--line);
		min-height: 0;
	}
	.pad {
		padding: 22px;
	}
	.due {
		font-size: 11.5px;
	}
	.due .g {
		color: var(--teal);
	}
	.st-due_soon,
	.st-due_soon .g {
		color: var(--amber);
	}
	.st-overdue,
	.st-overdue .g {
		color: var(--red);
	}
	.to {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
		padding: 12px 22px;
		border-bottom: 1px solid var(--line);
		font-size: 13px;
	}
	.to .mono {
		font-size: 11.5px;
	}
	.draft {
		padding: 14px 22px 8px;
		display: grid;
		gap: 2px;
	}
	h3 {
		margin: 6px 0 8px;
		font-size: 11.5px;
		font-weight: 500;
		color: var(--faint);
		text-transform: none;
	}
	h3.sec {
		margin-top: 18px;
		font-weight: 400;
	}
	.req {
		display: grid;
		grid-template-columns: 18px 250px minmax(0, 1fr) 112px;
		gap: 10px;
		align-items: center;
		padding: 6px 0;
		font-size: 13px;
	}
	.req .k {
		color: var(--teal);
	}
	.req.todo .k {
		color: var(--amber);
	}
	.req.todo .lbl {
		color: var(--ink);
		font-weight: 500;
	}
	.lbl {
		color: var(--ink-2);
	}
	.val :global(.input),
	.val :global(.select) {
		height: 32px;
		font-size: 13px;
	}
	.refs {
		display: inline-flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.ref {
		font-size: 11px;
		color: var(--teal);
	}
	.ref:hover {
		text-decoration: underline;
	}
	.narr {
		font-size: 13.5px;
	}
	.next {
		list-style: none;
		margin: 0;
		padding: 0;
		font-size: 12px;
		color: var(--ink-2);
		display: grid;
		gap: 6px;
	}
	.row {
		margin-top: 12px;
	}
	.submit {
		display: flex;
		gap: 14px;
		align-items: center;
		flex-wrap: wrap;
		margin: 14px 22px 22px;
		padding-top: 16px;
		border-top: 1px solid var(--line);
	}
	.submit .mono {
		font-size: 11.5px;
	}
	.review {
		display: grid;
		gap: 10px;
		width: 100%;
		max-width: 560px;
	}
	.miss {
		margin: 0;
		color: var(--amber);
		font-size: 13.5px;
	}
	.review ul {
		margin: 0;
		padding-left: 22px;
	}
	.ok {
		margin: 0;
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.ok .mono {
		color: var(--teal);
	}
	.done {
		margin: 0;
		color: var(--teal);
		font-size: 12px !important;
	}
	.ev {
		list-style: none;
		margin: 0;
		padding: 14px 16px;
		display: grid;
		gap: 14px;
		outline: none;
	}
	.ev li {
		display: grid;
		gap: 5px;
		padding: 6px;
		margin: -6px;
		border-radius: 6px;
		transition: background 300ms;
	}
	.ev li.lit {
		background: var(--sel);
	}
	.head {
		font-size: 11.5px;
		color: var(--faint);
	}
	.head b {
		color: var(--teal);
		font-weight: 500;
	}
	.foot {
		padding: 0 16px 16px;
		font-size: 11px;
	}
	@media (max-width: 1000px) {
		.tiles {
			grid-template-columns: minmax(0, 1fr);
			overflow: auto;
		}
		.tiles > :global(.pane .scroll) {
			overflow: visible;
		}
		.req {
			grid-template-columns: 18px minmax(0, 1fr);
		}
		.req .val,
		.req .refs {
			grid-column: 2;
		}
		.to,
		.draft {
			padding-left: 14px;
			padding-right: 14px;
		}
		.submit {
			margin: 14px;
		}
	}
</style>
