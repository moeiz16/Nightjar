<script lang="ts">
	// The CISO morning brief (DESIGN.md §0.2, §6.11): dawn bands, full width, finishable in two minutes. The headline,
	// then the one decision, then the same night the watch saw (the shift chart, read only), then a ruled band of
	// clocks · approved overnight · on watch now. Everything is composed from the API at runtime.
	import { api } from '$lib/api';
	import type { IdentityDoc, IncidentSummary, Member, MorningBrief, Posture, ProposalDoc, RegimeAssessment } from '$lib/api/types';
	import DecisionSheet from '$lib/components/console/DecisionSheet.svelte';
	import SevenNights from '$lib/components/brief/SevenNights.svelte';
	import ShiftChart from '$lib/components/console/ShiftChart.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { demoNight, dueLabel, elapsedFraction, hmParts, longDate, NY, REGIME_TZ, sec8k, secondsLeft, shortDay, timeOf, clockState, STATE_GLYPH, STATE_WORD } from '$lib/time/clock';
	import { briefHeadline, decisionQuestion } from '$lib/util/brief';
	import { actionTitle } from '$lib/util/approvals';
	import { clockLongLabel } from '$lib/util/labels';

	let posture = $state<Posture | null>(null);
	let incidents = $state<IncidentSummary[]>([]);
	let actions = $state<ProposalDoc[]>([]);
	let assessments = $state<{ a: RegimeAssessment; i: IncidentSummary }[]>([]);
	let members = $state<Member[]>([]);
	let people = $state<IdentityDoc[]>([]);
	let deciding = $state(false);
	let morning = $state<MorningBrief | null>(null);

	async function load() {
		api.executive.morning().then((m) => (morning = m)).catch(() => (morning = null));
		const [p, inc, ap, mem] = await Promise.all([
			api.executive.posture().catch(() => null),
			api.incidents.list({ status: 'all', pageSize: 60 }).then((r) => r.items).catch(() => [] as IncidentSummary[]),
			api.approvals.list({ status: 'all', pageSize: 100 }).then((r) => r.items).catch(() => [] as ProposalDoc[]),
			api.org.members().then((r) => r.items).catch(() => [] as Member[])
		]);
		api.directory.identities().then((r) => (people = r)).catch(() => undefined);
		posture = p;
		incidents = inc;
		actions = ap;
		members = mem;
		const open = inc.filter((i) => i.reportable === 'assess' && i.status !== 'closed');
		const reps = await Promise.all(open.map((i) => api.incidents.reporting(i.id).then((r) => r.assessments.filter((a) => a.proposal.applies === 'assess' && !a.determination).map((a) => ({ a, i }))).catch(() => [])));
		assessments = reps.flat().sort((x, y) => Number(y.a.regime === 'sec_8k') - Number(x.a.regime === 'sec_8k'));
	}
	$effect(() => {
		load();
		const offs = [live.on('clock.started', load), live.on('approval.decided', load), live.on('notice.updated', load)];
		return () => offs.forEach((f) => f());
	});

	const at = $derived(live.now);
	const night = $derived(demoNight(at));
	const since = $derived(new Date(+night - 30 * 3600e3)); // the last two nights
	const recent = $derived(incidents.filter((i) => +new Date(i.firstSeen) >= +since).sort((a, b) => a.firstSeen.localeCompare(b.firstSeen)));
	const reportable = $derived(recent.filter((i) => i.reportable === 'yes'));
	const contained = $derived(reportable.filter((i) => ['monitoring', 'resolved', 'closed'].includes(i.status)));
	const window = $derived(reportable.some((i) => +new Date(i.firstSeen) < +night - 6 * 3600e3) ? 'the last two nights' : 'overnight');
	const head = $derived(briefHeadline(reportable.length, contained.length, assessments.length, window));
	const decision = $derived(assessments[0] ?? null);
	const others = $derived(assessments.slice(1));
	const clocks = $derived(live.clocks.filter((c) => c.state !== 'not_started' && c.state !== 'submitted'));
	const allOnTrack = $derived(clocks.every((c) => clockState(c, at) === 'on_track'));
	const overnight = $derived(morning?.incidents?.length ? morning.incidents : recent.filter((i) => +new Date(i.firstSeen) >= +night - 6 * 3600e3));
	const approvedOvernight = $derived(actions.filter((p) => (p.status === 'executed' || p.status === 'approved') && p.approvals.some((a) => +new Date(a.decidedAt) >= +night - 6 * 3600e3)));
	const pendingNow = $derived(actions.filter((p) => p.status === 'pending'));
	const responder = $derived(members.find((m) => m.role === 'responder')?.user.fullName ?? 'the responder on call');
	const week = $derived(incidents.filter((i) => +new Date(i.firstSeen) >= +night - 7 * 864e5));
	const weekActions = $derived(actions.filter((p) => p.status === 'executed' && +new Date(p.createdAt) >= +night - 7 * 864e5));
	const prepared = $derived(morning ? timeOf(morning.asOf) : posture ? timeOf(posture.asOf) : '07:58');
	const secDue = $derived(sec8k.item105Due(at));
	function lede(): string {
		const parts = overnight
			.filter((i) => i.verdict === 'true_positive' && (i.severity === 'high' || i.severity === 'critical'))
			.slice(0, 2)
			.map((i) => `At ${timeOf(i.firstSeen)}: ${plainTitle(i.title)}.`);
		return parts.join(' ');
	}
	// incident titles are the plain story ("Cloud access key … used from an unfamiliar network"); the stored
	// summaries list detections and read like a log
	const plainTitle = (t: string) => t.replace(/\s*→\s*/g, ' leading to ').replace(/^(A|An|The) /, (w) => w.toLowerCase());
	const benign = $derived(overnight.filter((i) => i.verdict === 'benign'));
	function askLead() {
		ui.toast(`Asked ${responder}, the watch lead, to call you before 10:00.`, 'info');
	}
	const posturePts = $derived((posture?.history ?? []).map((v, i, a) => `${(i * 300) / Math.max(1, a.length - 1)},${44 - (v / 100) * 40}`).join(' '));
	const lastPt = $derived(posturePts.split(' ').at(-1)?.split(',') ?? ['300', '20']);
</script>

<svelte:head><title>Morning brief · Nightjar SOC</title></svelte:head>

<main class="wrap">
	<section class="bb head" aria-labelledby="bh">
		<p class="dateline mono"><b>{longDate(at)}</b> · prepared {prepared} for {session.name}, {session.role === 'executive' ? 'CISO' : session.roleLabel.toLowerCase()} · a 2-minute read</p>
		<h1 id="bh">{head.lead}{head.tail}{#if head.em}<em>{head.em}</em>{/if}{head.end}</h1>
		<p class="lede">
			{lede()}
			{#if benign.length} A software update set off {benign.reduce((n, i) => n + i.detectionCount, 0)} alerts; the watch grouped them into one note and closed it as noise. <b>No one was woken.</b>{/if}
			{#if clocks[0]} The nearest regulator deadline is the {clockLongLabel(clocks[0]).split(' · ')[0]}, due {dueLabel(clocks[0].dueAt!, at, REGIME_TZ[clocks[0].regime])}.{/if}
		</p>
	</section>

	{#if decision}
		<section class="bb decide" aria-labelledby="dq">
			<div class="decision">
				<div class="lbl mono">◆ Needs your decision · {decision.a.regime === 'sec_8k' ? 'SEC Form 8-K, Item 1.05' : decision.a.regimeLabel}</div>
				<h2 id="dq">{decisionQuestion(decision.a, decision.i)}</h2>
				{#if decision.a.regime === 'sec_8k'}
					<p>Halden’s shares are listed in the US, so a cybersecurity incident it judges material must be disclosed within four business days of that decision. The decision belongs to you and the disclosure committee, not to the watch.</p>
				{:else}
					<p>The watch cannot decide this for {decision.a.entityName}. It belongs to you, with legal and data protection.</p>
				{/if}
				<div class="view"><span class="mono">the watch’s view · {decision.a.proposal.reasons.length} reasons</span>
					<ul>{#each decision.a.proposal.reasons as r (r)}<li>{r}</li>{/each}</ul>
				</div>
				{#if deciding}
					<DecisionSheet incidentId={decision.i.id} reference={decision.i.reference} a={decision.a} tone="brief" onrecorded={() => ((deciding = false), load(), live.loadClocks())} oncancel={() => (deciding = false)} />
				{:else}
					<div class="row">
						<button class="b ink" type="button" onclick={() => (deciding = true)}>Record the decision</button>
						<button class="b" type="button" onclick={askLead}>Ask the watch lead</button>
						{#if decision.a.regime === 'sec_8k'}<span class="if mono">If material: 8-K due <b>{shortDay(secDue, NY)}</b></span>{/if}
					</div>
				{/if}
				{#if others.length}
					<p class="also">Also yours: {#each others as o, k (o.i.id + o.a.regime)}{k ? ' · ' : ''}<a href="/incidents/{o.i.reference}?decide={o.a.regime}">{decisionQuestion(o.a, o.i).replace(/\?$/, '')}</a>{/each}.</p>
				{/if}
			</div>
		</section>
	{/if}

	<section class="bb nightband" aria-labelledby="nb">
		<h3 id="nb" class="rl mono">── the night the watch saw <span>· response stages down, hours across · the same chart the watch works from</span></h3>
		<ShiftChart win={shift.win} incidents={shift.incidents} log={shift.log} intake={shift.intake} tz={shift.tz} compact dawn />
	</section>

	<section class="bb three" aria-label="The handover">
		<section class="part" aria-labelledby="ck">
			<h3 id="ck" class="mono">Clocks running <span>{clocks.length} · {clocks.length ? (allOnTrack ? 'all on track' : 'one needs attention') : 'none'}</span></h3>
			{#each clocks as c (c.id)}
				{@const st = clockState(c, at)}
				{@const left = hmParts(secondsLeft(c.dueAt, at))}
				<div class="clk" style="--elapsed:{elapsedFraction(c.startsAt, c.dueAt, at)}">
					<div class="what"><b>{clockLongLabel(c)}</b><small>{c.incidentReference} · {c.basis}</small></div>
					<div class="due mono">due<b>{dueLabel(c.dueAt!, at)}</b>{c.regime === 'nis2' ? '' : `${timeOf(c.dueAt!, REGIME_TZ[c.regime])} ${c.regime === 'sec_8k' ? 'New York' : 'Melbourne'}`}</div>
					<div class="left"><div class="n mono">{left.h}<span class="u">h</span>{left.m}<span class="u">m</span></div><small class:soon={st !== 'on_track'}><span class="g mono">{STATE_GLYPH[st]}</span> {STATE_WORD[st]}</small></div>
					<i class="bar2"></i>
				</div>
			{/each}
		</section>
		<section class="part side" aria-label="Approved overnight">
			<h3 class="mono">Approved overnight · by a person</h3>
			<ul>
				{#each approvedOvernight.slice(0, 4) as p (p.id)}<li><span class="ok mono">✓</span> {actionTitle(p, people)}<small class="mono">{p.approvals.map((a) => a.name).join(' + ')} · {timeOf(p.approvals.at(-1)!.decidedAt)}</small></li>{/each}
				{#if approvedOvernight.length > 4}<li class="more mono">+ {approvedOvernight.length - 4} more, all logged</li>{/if}
				{#each pendingNow as p (p.id)}<li><span class="wait mono">‖</span> {actionTitle(p, people)}<small class="mono">waiting for {p.requiredRoles.includes('executive') ? 'you and a responder' : responder}</small></li>{/each}
				{#if !approvedOvernight.length && !pendingNow.length}<li class="faint">Nothing needed a person overnight.</li>{/if}
			</ul>
		</section>
		<section class="part side" aria-label="On watch now and posture">
			<h3 class="mono">On watch now</h3>
			<p class="onw">{responder}, responder, with Triage, Enrichment and Response. Next brief tomorrow at 08:00.</p>
			{#if posture}
				<h3 class="mono pt">Posture</h3>
				<div class="posture">{posture.scoreBand === 'steady' ? 'Steady' : posture.scoreBand === 'watch' ? 'Watch' : 'Strained'} <small class="mono">— {posture.meanTimeToTriage.trend === 'better' ? 'better than last week' : 'same as last week'}</small></div>
				{#if posturePts}
					<svg class="spark" viewBox="0 0 300 46" width="300" height="46" role="img" aria-label="Posture over the last 8 weeks: {posture.scoreBand}"><polyline fill="none" stroke="var(--teal)" stroke-width="1.6" points={posturePts} /><circle cx={lastPt[0]} cy={lastPt[1]} r="3" fill="var(--teal)" /><line x1="0" y1="45" x2="300" y2="45" stroke="var(--rule-2)" /></svg>
				{/if}
				<p class="onw">{posture.highlights[0] ?? posture.components[0]?.note}</p>
			{/if}
		</section>
	</section>

	<section class="bb two" aria-label="Overnight and the week">
		<section class="part" aria-labelledby="ov">
			<h3 id="ov" class="mono">Overnight, in plain words</h3>
			<ol class="night">
				{#each overnight as i (i.id)}
					<li><span class="t mono">{timeOf(i.firstSeen)}</span><p>{plainTitle(i.title).replace(/^./, (c) => c.toUpperCase())}. {#if i.verdict === 'benign'}<b>Closed as noise.</b>{:else if i.status === 'monitoring' || i.status === 'resolved'}<b>Contained.</b>{:else if i.pendingApprovals}Response is waiting for a person to authorise the next step.{:else if i.verdict === 'needs_review'}A person is looking at it this morning.{/if}</p></li>
				{/each}
			</ol>
			<p class="sign mono">— the night watch: Triage, Enrichment and Response, with {responder} on call</p>
		</section>

		<section class="part week" aria-labelledby="wk">
			<h3 id="wk" class="mono">Seven nights <span>18:00 to 08:00 · ◆ incident · ● closed as noise</span></h3>
			<SevenNights incidents={week} {at} />
			{#if posture}
				<p class="facts">This week the watch looked closely at <b>{posture.alerts.last7d.toLocaleString('en-GB')}</b> alerts and opened <b>{week.length}</b> incidents, <b>{week.filter((i) => i.reportable === 'yes').length}</b> of them reportable. It closed <b>{posture.alerts.suppressedAsNoise.toLocaleString('en-GB')}</b> alerts as noise. Median time to a triage verdict was <b>{posture.meanTimeToTriage.last7dSeconds} s</b>; median time to contain after a person approved was <b>{Math.round(posture.meanTimeToContain.last7dSeconds / 60)} min</b>. Every one of the <b>{weekActions.length}</b> response actions was authorised by a person.</p>
			{/if}
		</section>
	</section>
</main>

<style>
	.wrap {
		position: relative;
		padding: 0 0 80px;
	}
	/* dawn bands: full width, hard-edged, content starts on the shift grid's lane-label line */
	.bb {
		position: relative;
		padding: 34px 28px 36px calc(var(--gut) + 6px);
		border-bottom: 1px solid var(--rule);
	}
	.bb.head {
		padding-top: 48px;
	}
	.bb.head h1,
	.bb.head .lede {
		max-width: 920px;
	}
	.bb.decide {
		background: color-mix(in srgb, var(--lav) 7%, transparent);
		border-top: 1px solid color-mix(in srgb, var(--lav) 30%, transparent);
	}
	.bb.decide .decision {
		max-width: 920px;
	}
	.bb.nightband {
		padding: 18px 0 8px;
		background: color-mix(in srgb, var(--page) 70%, transparent);
	}
	.rl {
		margin: 0 0 6px calc(var(--gut) + 6px);
		font-size: 12px;
		font-weight: 600;
		color: var(--ink);
	}
	.rl span {
		font-weight: 400;
		color: var(--muted);
	}
	.bb.three,
	.bb.two {
		display: grid;
		gap: 0;
		padding-top: 0;
		padding-bottom: 0;
	}
	.bb.three {
		grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr);
	}
	@media (min-width: 761px) {
		.bb.three .clk {
			grid-template-columns: minmax(0, 1fr) auto auto;
			gap: 22px;
		}
	}
	.bb.two {
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
	}
	.bb.three > .part,
	.bb.two > .part {
		margin: 0;
		padding: 28px 28px 30px 0;
	}
	.bb.three > .part + .part,
	.bb.two > .part + .part {
		padding-left: 28px;
		border-left: 1px solid var(--rule);
	}
	.onw {
		margin: 0 0 12px;
		font-size: 15px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	.pt {
		margin-top: 18px !important;
	}
	.dateline {
		margin: 0;
		font-size: 13px;
		line-height: 1.5;
		color: var(--muted);
	}
	.dateline b {
		color: var(--ink);
		font-weight: 500;
	}
	h1 {
		margin: 18px 0 22px;
		font: 500 46px/1.12 var(--sans);
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.03em;
		text-wrap: balance;
	}
	h1 em {
		font-style: normal;
		color: var(--teal);
	}
	.lede {
		margin: 0;
		font-size: 19px;
		line-height: 1.62;
		color: var(--ink-2);
		max-width: 680px;
	}
	.lede b {
		color: var(--ink);
		font-weight: 600;
	}
	.decision {
		padding: 0 0 0 22px;
		border-left: 3px solid var(--lav);
	}
	.decision .lbl {
		font-size: 12px;
		font-weight: 500;
		color: var(--lav);
	}
	.decision h2 {
		margin: 12px 0 10px;
		font: 600 23px/1.3 var(--sans);
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.015em;
	}
	.decision p {
		margin: 0 0 10px;
		font-size: 15.5px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.view {
		margin: 16px 0 20px;
		padding: 14px 16px;
		border-radius: 4px;
		background: var(--lav-wash);
		font-size: 15px;
		line-height: 1.55;
		color: var(--ink);
	}
	.view span {
		display: block;
		font-size: 12px;
		color: var(--muted);
		margin-bottom: 8px;
	}
	.view ul {
		margin: 0;
		padding-left: 20px;
	}
	.row {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
	}
	.if {
		margin-left: 8px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.also {
		margin-top: 16px !important;
		font-size: 14px !important;
	}
	.also a {
		color: var(--lav);
	}
	.b {
		height: 40px;
		padding: 0 16px;
		border-radius: 6px;
		font-size: 14.5px;
		font-weight: 500;
		display: inline-flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--rule-2);
		color: var(--ink);
		text-decoration: none;
	}
	.b.ink {
		background: var(--ink);
		color: var(--page);
		border-color: var(--ink);
	}
	.decision :global(.btn.preset-filled-surface-950-50) {
		background: var(--ink);
		color: var(--page);
		border-color: var(--ink);
		height: 40px;
		font-size: 14.5px;
	}
	.decision :global(.textarea),
	.decision :global(.input),
	.decision :global(.select) {
		background: var(--page);
		border-color: var(--rule-2);
		color: var(--ink);
	}
	.part {
		margin-top: 64px;
	}
	.part > h3 {
		display: flex;
		align-items: baseline;
		gap: 12px;
		margin: 0 0 6px;
		font-size: 13px;
		font-weight: 600;
		color: var(--ink);
	}
	.part > h3 span {
		font-weight: 400;
		color: var(--muted);
	}
	.clk {
		display: grid;
		grid-template-columns: 1fr 150px 168px;
		gap: 20px;
		align-items: center;
		padding: 20px 0 22px;
		border-bottom: 1px solid var(--rule);
		position: relative;
	}
	.clk:first-of-type {
		border-top: 1px solid var(--rule-2);
	}
	.what b {
		display: block;
		font-size: 16.5px;
		font-weight: 600;
	}
	.what small {
		display: block;
		margin-top: 4px;
		font-size: 14px;
		color: var(--muted);
	}
	.due {
		font-size: 13px;
		line-height: 1.45;
		color: var(--muted);
	}
	.due b {
		display: block;
		color: var(--ink);
		font-weight: 500;
	}
	.left {
		text-align: right;
	}
	.n {
		font-size: 34px;
		font-weight: 300;
		line-height: 1;
		letter-spacing: -0.03em;
		color: var(--ink);
	}
	.n .u {
		font-size: 15px;
		color: var(--muted);
		letter-spacing: 0;
		margin: 0 6px 0 2px;
	}
	.left small {
		display: block;
		margin-top: 6px;
		font-size: 12.5px;
		color: var(--teal);
	}
	.left small.soon {
		color: var(--amber);
	}
	.bar2 {
		position: absolute;
		left: 0;
		right: 0;
		bottom: -1px;
		height: 2px;
		background: linear-gradient(90deg, var(--teal) calc(var(--elapsed) * 100%), transparent 0);
		opacity: 0.55;
	}
	.night {
		list-style: none;
		margin: 18px 0 0;
		padding: 0;
	}
	.night li {
		display: grid;
		grid-template-columns: 76px 1fr;
		gap: 18px;
		padding: 14px 0;
		border-bottom: 1px solid var(--rule);
	}
	.night .t {
		font-size: 13px;
		line-height: 1.7;
		color: var(--muted);
	}
	.night p {
		margin: 0;
		font-size: 16px;
		line-height: 1.62;
		color: var(--ink-2);
	}
	.night p b {
		color: var(--ink);
		font-weight: 600;
	}
	.sign {
		margin-top: 18px;
		font-size: 13px;
		line-height: 1.6;
		color: var(--muted);
	}
	.facts {
		margin-top: 18px;
		font-size: 16px;
		line-height: 1.75;
		color: var(--ink-2);
	}
	.facts b {
		font-family: var(--mono);
		font-variation-settings: 'MONO' 1;
		font-weight: 500;
		color: var(--ink);
		font-size: 15px;
	}
	.side {
		padding-top: 6px;
	}
	.posture {
		font: 500 28px/1 var(--sans);
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.02em;
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
	}
	.posture small {
		font-size: 12.5px;
		color: var(--teal);
		letter-spacing: 0;
	}
	.spark {
		display: block;
		margin-top: 12px;
		max-width: 100%;
		height: auto;
	}
	.side p {
		margin: 10px 0 0;
		font-size: 14px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	.side ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 12px;
	}
	.side li {
		font-size: 14px;
		line-height: 1.45;
		color: var(--ink-2);
	}
	.side li small {
		display: block;
		font-size: 12px;
		line-height: 1.4;
		color: var(--muted);
	}
	.ok {
		color: var(--teal);
	}
	.more {
		font-size: 12px;
		color: var(--muted) !important;
	}
	.wait {
		color: var(--lav);
	}
	@media (max-width: 1100px) {
		.bb.three {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		}
		.bb.three > .part:first-child {
			grid-column: 1 / -1;
			border-bottom: 1px solid var(--rule);
		}
		.bb.three > .part:nth-child(2) {
			padding-left: 0;
			border-left: 0;
		}
	}
	@media (max-width: 760px) {
		.bb {
			padding: 26px 16px 28px;
		}
		.bb.nightband {
			padding: 14px 0 6px;
		}
		.rl {
			margin-left: 16px;
		}
		.bb.three,
		.bb.two {
			grid-template-columns: minmax(0, 1fr);
			padding: 0 16px;
		}
		.bb.three > .part,
		.bb.two > .part,
		.bb.three > .part + .part,
		.bb.two > .part + .part,
		.bb.three > .part:nth-child(2) {
			padding: 22px 0;
			border-left: 0;
			border-top: 1px solid var(--rule);
		}
		.bb.three > .part:first-child,
		.bb.two > .part:first-child {
			border-top: 0;
		}
		h1 {
			font-size: 31px;
		}
		.lede {
			font-size: 17px;
		}
		.decision {
			padding: 20px 16px 20px 18px;
		}
		.decision h2 {
			font-size: 20px;
		}
		.clk {
			grid-template-columns: 1fr auto;
			gap: 10px;
		}
		.clk .due {
			grid-column: 1;
			grid-row: 2;
		}
		.clk .left {
			grid-row: 1 / 3;
			grid-column: 2;
		}
		.n {
			font-size: 26px;
		}
		.night li {
			grid-template-columns: 52px 1fr;
			gap: 12px;
		}
		.if {
			margin-left: 0;
		}
	}
</style>
