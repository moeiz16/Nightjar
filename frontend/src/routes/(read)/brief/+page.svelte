<script lang="ts">
	// The CISO morning brief (DESIGN.md §4): the morning report, finishable in two minutes. A stencil headline across
	// the page, then three columns: the one decision (the only boxed thing on the page) · the night the watch saw (the
	// same dial, read only) · clocks, approved overnight and who is on watch. Then overnight in plain words and seven
	// nights. Everything is composed from the API at runtime.
	import { api } from '$lib/api';
	import type { IdentityDoc, IncidentSummary, Member, MorningBrief, Posture, ProposalDoc, RegimeAssessment } from '$lib/api/types';
	import DecisionSheet from '$lib/components/console/DecisionSheet.svelte';
	import SevenNights from '$lib/components/brief/SevenNights.svelte';
	import WatchDial from '$lib/components/console/WatchDial.svelte';
	import { shift } from '$lib/stores/shift.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { demoNight, dueLabel, elapsedFraction, hmParts, longDate, NY, REGIME_TZ, sec8k, secondsLeft, shortDay, timeOf, clockState, STATE_GLYPH, STATE_WORD } from '$lib/time/clock';
	import { briefHeadline, decisionQuestion, numberWord } from '$lib/util/brief';
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
	// where the night's real incidents stand, counted from the same list the page shows below (never a blanket claim)
	function nightState(): string {
		const real = overnight.filter((i) => i.verdict !== 'benign');
		const isContained = (i: IncidentSummary) => ['monitoring', 'resolved', 'closed'].includes(i.status);
		const c = real.filter(isContained).length;
		const w = real.filter((i) => !isContained(i) && i.pendingApprovals).length;
		const r = real.filter((i) => !isContained(i) && !i.pendingApprovals).length;
		const parts = [c && `${numberWord(c)} ${c === 1 ? 'is' : 'are'} contained`, w && `${numberWord(w, !c)} ${w === 1 ? 'waits' : 'wait'} on an approval`, r && `${numberWord(r, !c && !w)} ${r === 1 ? 'is' : 'are'} in review`].filter(Boolean) as string[];
		const lead = `${head.lead} Of ${numberWord(real.length, false)} real ${real.length === 1 ? 'incident' : 'incidents'} overnight,`;
		return parts.length ? `${lead} ${parts.join(', ').replace(/, ([^,]*)$/, ' and $1').replace(/^./, (x) => x.toLowerCase())}.` : head.lead;
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

<main class="report rack">
	<section class="lead" aria-labelledby="bh">
		<p class="dateline" title="Prepared {prepared} for {session.name}, {session.role === 'executive' ? 'CISO' : session.roleLabel.toLowerCase()} · a 2-minute read"><b class="eng">{longDate(at)}</b></p>
		<!-- the headline is the one thing to act on; what was reportable and contained opens beneath it -->
		<h2 id="bh" class="headline">{#if head.em}<em>{head.em.replace(/^./, (c) => c.toUpperCase())}</em>{head.end}{:else}{head.lead}{/if}</h2>
		<!-- the story in sentences is one click deeper; the headline and the plates carry the morning -->
		<details class="lede">
			<summary>The night in a paragraph</summary>
			<p>
				{nightState()}
				{#if benign.length} A software update set off {benign.reduce((n, i) => n + i.detectionCount, 0)} alerts; the watch grouped them into one note and closed it as noise.{/if}
				{#if clocks[0]} The nearest regulator deadline is the {clockLongLabel(clocks[0]).split(' · ')[0]}, due {dueLabel(clocks[0].dueAt!, at, REGIME_TZ[clocks[0].regime])}.{/if}
			</p>
		</details>
	</section>

	<div class="cols">
		{#if decision}
			<section class="decision ticks" aria-labelledby="dq">
				<div class="lbl eng" title={decision.a.regime === 'sec_8k' ? 'SEC Form 8-K, Item 1.05' : decision.a.regimeLabel}>◆ Your decision · {decision.a.regime === 'sec_8k' ? 'SEC 8-K' : decision.a.regimeLabel}</div>
				<h3 id="dq">{decisionQuestion(decision.a, decision.i)}</h3>
				<details class="view">
					<summary>Why it is yours · {decision.a.proposal.reasons.length} reasons</summary>
					<ul>{#each decision.a.proposal.reasons as r (r)}<li>{r}</li>{/each}</ul>
				</details>
				{#if deciding}
					<DecisionSheet incidentId={decision.i.id} reference={decision.i.reference} a={decision.a} tone="brief" onrecorded={() => ((deciding = false), load(), live.loadClocks())} oncancel={() => (deciding = false)} />
				{:else}
					<div class="row">
						<button class="btn preset-filled-primary-500" type="button" onclick={() => (deciding = true)}>Record the decision</button>
						<button class="btn preset-outlined-surface-300-700" type="button" onclick={askLead}>Ask the watch lead</button>
					</div>
					{#if decision.a.regime === 'sec_8k'}<p class="if">If material: 8-K due <b class="mono">{shortDay(secDue, NY)}</b></p>{/if}
				{/if}
				{#if others.length}
					<details class="view also"><summary>Also yours · {others.length}</summary><ul>{#each others as o (o.i.id + o.a.regime)}<li><a href="/incidents/{o.i.reference}?decide={o.a.regime}">{decisionQuestion(o.a, o.i)}</a></li>{/each}</ul></details>
				{/if}
			</section>
		{/if}

		<section class="col nightcol" aria-labelledby="nb">
			<h3 id="nb" class="ch" title="Each line is an incident moving inward through the response; the hand is now.">The night</h3>
			<WatchDial win={shift.win} incidents={shift.incidents} log={shift.log} intake={shift.intake} tz={shift.tz} clocks={live.clocks} compact max={400} />
		</section>

		<section class="col" aria-label="The handover">
			<h3 id="ck" class="ch">Clocks <span>{clocks.length ? (allOnTrack ? 'all on track' : 'one needs attention') : 'none'}</span></h3>
			{#each clocks as c (c.id)}
				{@const st = clockState(c, at)}
				{@const left = hmParts(secondsLeft(c.dueAt, at))}
				<div class="clk {st}" style="--elapsed:{elapsedFraction(c.startsAt, c.dueAt, at)}">
					<div class="what" title="Due {dueLabel(c.dueAt!, at)}{c.regime === 'nis2' ? '' : ` · ${timeOf(c.dueAt!, REGIME_TZ[c.regime])} ${c.regime === 'sec_8k' ? 'New York' : 'Melbourne'}`}"><b>{clockLongLabel(c).split(' · ')[0]}</b> <span class="mono ref">{c.incidentReference}</span></div>
					<div class="left"><div class="n mono">{left.h}<span class="u">h</span>{left.m}<span class="u">m</span></div>{#if st !== 'on_track'}<small><span class="g">{STATE_GLYPH[st]}</span> {STATE_WORD[st]}</small>{/if}</div>
					<i class="bar2" aria-hidden="true"></i>
				</div>
			{/each}

			<h3 class="ch" title="Every response action was authorised by a person">Actions</h3>
			<ul class="acts">
				{#each approvedOvernight.slice(0, 3) as p (p.id)}<li title="{p.approvals.map((a) => a.name).join(' + ')} · {timeOf(p.approvals.at(-1)!.decidedAt)}"><span class="okg mono">✓</span><span>{actionTitle(p, people)}</span></li>{/each}
				{#if approvedOvernight.length > 3}<li class="more">+ {approvedOvernight.length - 3} more, all logged</li>{/if}
				{#each pendingNow as p (p.id)}<li title="Waiting for {p.requiredRoles.includes('executive') ? 'you and a responder' : responder}"><span class="wait mono">‖</span><span>{actionTitle(p, people)}</span></li>{/each}
				{#if !approvedOvernight.length && !pendingNow.length}<li class="faint">Nothing needed a person overnight.</li>{/if}
			</ul>

			{#if posture}
				<div class="posture" title={posture.highlights[0] ?? posture.components[0]?.note}><span class="eng">Posture</span> <b>{posture.scoreBand === 'steady' ? 'Steady' : posture.scoreBand === 'watch' ? 'Watch' : 'Strained'}</b> <small>{posture.meanTimeToTriage.trend === 'better' ? '▲ better' : '= same'}</small><span class="onw" title="With Triage, Enrichment and Response. Next brief tomorrow at 08:00.">On watch: <b>{responder}</b></span></div>
				{#if posturePts}
					<svg class="spark" viewBox="0 0 300 46" width="300" height="46" role="img" aria-label="Posture over the last 8 weeks: {posture.scoreBand}"><polyline fill="none" stroke="var(--acc)" stroke-width="1.8" points={posturePts} /><circle cx={lastPt[0]} cy={lastPt[1]} r="3" fill="var(--acc)" /><line x1="0" y1="45" x2="300" y2="45" stroke="var(--rule-2)" /></svg>
				{/if}
			{/if}
		</section>
	</div>

	<div class="two">
		<section class="col" aria-labelledby="ov">
			<h3 id="ov" class="ch big">Overnight</h3>
			<ol class="night">
				{#each overnight as i (i.id)}
					<li><span class="t mono">{timeOf(i.firstSeen)}</span><p><a href="/incidents/{i.reference}">{plainTitle(i.title).replace(/^./, (c) => c.toUpperCase())}</a> {#if i.verdict === 'benign'}<b class="tag">Noise</b>{:else if i.status === 'monitoring' || i.status === 'resolved'}<b class="tag ok">Contained</b>{:else if i.pendingApprovals}<b class="tag per">‖ Waiting</b>{:else if i.verdict === 'needs_review'}<b class="tag">In review</b>{/if}</p></li>
				{/each}
			</ol>
		</section>

		<section class="col" aria-labelledby="wk">
			<h3 id="wk" class="ch big" title="18:00 to 08:00 · ◆ incident · ● closed as noise">Seven nights</h3>
			<SevenNights incidents={week} {at} />
			{#if posture}
				<dl class="stats">
					<div><dt>Alerts looked at</dt><dd class="mono">{posture.alerts.last7d.toLocaleString('en-GB')}</dd></div>
					<div><dt>Incidents</dt><dd class="mono">{week.length}</dd></div>
					<div><dt>Closed as noise</dt><dd class="mono">{posture.alerts.suppressedAsNoise.toLocaleString('en-GB')}</dd></div>
				</dl>
				<details class="lede"><summary>The week in a paragraph</summary><p class="facts">This week the watch looked closely at <b>{posture.alerts.last7d.toLocaleString('en-GB')}</b> alerts and opened <b>{week.length}</b> incidents, <b>{week.filter((i) => i.reportable === 'yes').length}</b> of them reportable. It closed <b>{posture.alerts.suppressedAsNoise.toLocaleString('en-GB')}</b> alerts as noise. Median time to a triage verdict was <b>{posture.meanTimeToTriage.last7dSeconds} s</b>; median time to contain after a person approved was <b>{Math.round(posture.meanTimeToContain.last7dSeconds / 60)} min</b>. Every one of the <b>{weekActions.length}</b> response actions was authorised by a person.</p></details>
			{/if}
		</section>
	</div>
</main>

<style>
	.report {
		padding: 30px var(--gut) 0;
		max-width: 1480px;
		margin: 0 auto;
	}
	.lead {
		display: grid;
		grid-template-columns: minmax(0, 1.75fr) minmax(0, 1fr);
		column-gap: 44px;
		align-items: end;
	}
	.dateline {
		grid-column: 1 / -1;
		display: flex;
		flex-wrap: wrap;
		gap: 6px 16px;
		align-items: baseline;
		margin: 0 0 16px;
		font-size: 14px;
		color: var(--dim);
	}
	.dateline b {
		font-size: 15px;
		color: var(--acc);
	}
	.headline {
		margin: 0;
		font: 800 76px/0.9 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.015em;
		color: var(--ink);
		text-wrap: balance;
	}
	.headline em {
		font-style: normal;
		color: var(--acc);
	}
	.lede {
		margin: 14px 0 0;
		grid-column: 1 / -1;
	}
	.lede summary,
	.view summary {
		cursor: pointer;
		list-style: none;
		width: max-content;
		max-width: 100%;
		font: 600 14px/1.2 var(--ui);
		color: var(--dim);
		border-bottom: 1px solid var(--rule-2);
		padding-bottom: 2px;
	}
	.lede summary::-webkit-details-marker,
	.view summary::-webkit-details-marker {
		display: none;
	}
	.lede summary::after,
	.view summary::after {
		content: ' ▸';
	}
	.lede[open] summary::after,
	.view[open] summary::after {
		content: ' ▾';
	}
	.lede summary:hover,
	.view summary:hover {
		color: var(--ink);
	}
	.lede p {
		margin: 12px 0 0;
		padding-left: 18px;
		border-left: 3px solid var(--acc);
		font-size: 17px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.lede b {
		color: var(--ink);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 6px;
		margin: 18px 0 14px;
	}
	.stats div {
		padding: 12px 14px;
		background: var(--raised);
	}
	.stats dt {
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--faint);
	}
	.stats dd {
		margin: 8px 0 0;
		font-size: 30px;
		line-height: 1;
		color: var(--ink);
	}
	.what .ref {
		font-size: 12px;
		color: var(--faint);
		white-space: nowrap;
	}
	.tag {
		margin-left: 6px;
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--dim);
		white-space: nowrap;
	}
	.tag.ok {
		color: var(--ok);
	}
	.tag.per {
		color: var(--person);
	}
	.night a {
		text-decoration: none;
		color: inherit;
	}
	.night a:hover {
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	.cols {
		display: grid;
		grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.95fr) minmax(0, 1fr);
		gap: 34px;
		margin-top: 28px;
		padding-top: 26px;
		border-top: 3px double var(--rule-2);
		align-items: start;
	}
	.decision {
		padding: 22px 24px 24px;
		background-color: var(--plate);
		box-shadow: inset 4px 0 0 var(--person);
		--tk: var(--acc);
		--tl: 16px;
	}
	.decision .lbl {
		font-size: 13px;
		color: var(--ink);
		margin-bottom: 12px;
	}
	.decision h3 {
		margin: 0 0 12px;
		font: 800 28px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.02em;
	}
	.decision p {
		margin: 0 0 14px;
		font-size: 16px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.view {
		margin: 4px 0 18px;
	}
	.view[open] {
		padding-bottom: 4px;
	}
	.view ul {
		margin: 8px 0 0;
		padding-left: 18px;
		font-size: 15px;
		line-height: 1.55;
		color: var(--ink);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}
	.if {
		margin: 12px 0 0 !important;
		font-size: 14px !important;
	}
	.also {
		margin-top: 14px !important;
		font-size: 14px !important;
	}
	.also a {
		color: var(--ink);
	}
	.col {
		min-width: 0;
	}
	.ch {
		margin: 0 0 12px;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 12px;
		font: 700 20px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		padding-bottom: 9px;
		border-bottom: 2px solid var(--ink);
	}
	.ch + * {
		margin-top: 0;
	}
	.col .ch:not(:first-child) {
		margin-top: 28px;
	}
	.ch span {
		font: 600 13px/1 var(--ui);
		letter-spacing: 0;
		text-transform: none;
		color: var(--dim);
	}
	.ch.big {
		font-size: 26px;
	}
	.nightcol :global(.wd) {
		margin-top: 4px;
	}
	.clk {
		position: relative;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 12px;
		align-items: center;
		padding: 12px 0 14px;
		border-bottom: 1px solid var(--rule);
	}
	.what b {
		display: block;
		font-weight: 700;
		font-size: 15px;
	}
	.left {
		text-align: right;
	}
	.n {
		font-size: 26px;
		font-weight: 400;
		color: var(--ink);
		line-height: 1;
	}
	.n .u {
		font-size: 13px;
		color: var(--dim);
		margin: 0 2px;
	}
	.left small {
		display: block;
		margin-top: 4px;
		font-size: 13px;
		font-weight: 600;
		color: var(--ink-2);
	}
	.clk.due_soon .left small {
		color: var(--soon);
	}
	.bar2 {
		position: absolute;
		left: 0;
		bottom: -1px;
		height: 2px;
		width: calc(var(--elapsed) * 100%);
		background: var(--acc);
	}
	.acts {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.acts li {
		display: grid;
		grid-template-columns: 22px minmax(0, 1fr);
		gap: 6px;
		padding: 9px 0;
		border-bottom: 1px solid var(--rule);
		font-size: 15px;
	}
	.okg {
		color: var(--ok);
	}
	.wait {
		color: var(--person);
		font-weight: 700;
	}
	.more {
		display: block !important;
		color: var(--dim);
		font-size: 13.5px !important;
	}
	.posture .onw b {
		font: 700 14px/1 var(--ui);
		letter-spacing: 0;
		text-transform: none;
		color: var(--ink);
	}
	.onw {
		margin: 0 0 0 auto;
		font-size: 14px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	.posture {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 10px;
		margin: 14px 0 6px;
	}
	.posture .eng {
		font-size: 13px;
		color: var(--dim);
	}
	.posture b {
		font: 800 26px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.posture small {
		font-size: 13px;
		color: var(--dim);
	}
	.spark {
		display: block;
		max-width: 100%;
		height: auto;
		margin-bottom: 8px;
	}
	.two {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: 40px;
		margin-top: 44px;
	}
	.night {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.night li {
		display: grid;
		grid-template-columns: 56px minmax(0, 1fr);
		gap: 12px;
		padding: 12px 0;
		border-bottom: 1px solid var(--rule);
	}
	.night .t {
		font-size: 14px;
		color: var(--acc);
		padding-top: 3px;
	}
	.night p {
		margin: 0;
		font-size: 16.5px;
		line-height: 1.55;
	}
	.facts {
		margin: 16px 0 0;
		font-size: 15.5px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.facts b {
		color: var(--ink);
	}
	@media (max-width: 1180px) {
		.cols {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		}
		.decision {
			grid-column: 1 / -1;
		}
		.headline {
			font-size: 58px;
		}
		.lead {
			grid-template-columns: minmax(0, 1fr);
		}
		.lede {
			margin-top: 20px;
		}
	}
	@media (max-width: 760px) {
		.report {
			padding-top: 20px;
		}
		.headline {
			font-size: 42px;
		}
		.lede {
			font-size: 16.5px;
		}
		.cols,
		.two {
			grid-template-columns: minmax(0, 1fr);
			gap: 26px;
		}
		.decision {
			padding: 18px 16px;
		}
		.decision h3 {
			font-size: 28px;
		}
		.nightcol {
			display: none;
		}
	}
</style>
