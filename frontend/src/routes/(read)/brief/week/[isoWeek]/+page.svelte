<script lang="ts">
	// The weekly report (DESIGN.md §6.12): a document, not a dashboard, assembled from the curated replays.
	// Printable; "Save as PDF" uses the browser's print.
	import { page } from '$app/state';
	import { api, problemMessage } from '$lib/api';
	import type { BriefListItem, IncidentSummary, ProposalDoc, TimelineEntry, WeeklyBrief } from '$lib/api/types';
	import SevenNights from '$lib/components/brief/SevenNights.svelte';
	import { isoWeekRange, now, timeOf, shortDay, zonedTimeToUtc, BRUSSELS } from '$lib/time/clock';
	import { incidentShortName } from '$lib/util/brief';
	import { regimeShort } from '$lib/util/labels';

	let brief = $state<WeeklyBrief | null>(null);
	let list = $state<BriefListItem[]>([]);
	let all = $state<IncidentSummary[]>([]);
	let actions = $state<ProposalDoc[]>([]);
	let stories = $state<Record<string, TimelineEntry[]>>({});
	let err = $state('');
	const week = $derived(page.params.isoWeek ?? '');
	const range = $derived.by(() => {
		if (brief) {
			const [ty, tm, td] = brief.to.split('-').map(Number);
			const [fy, fm, fd] = brief.from.split('-').map(Number);
			return { from: new Date(+zonedTimeToUtc(fy!, fm!, fd!, 0, 0, BRUSSELS) - 6 * 3600e3), to: zonedTimeToUtc(ty!, tm!, td!, 9, 30, BRUSSELS), fromDay: new Date(Date.UTC(fy!, fm! - 1, fd!)), toDay: new Date(Date.UTC(ty!, tm! - 1, td!)) };
		}
		const w = isoWeekRange(week);
		return w ? { from: w.from, to: new Date(+w.to + 864e5), fromDay: w.from, toDay: w.to } : null;
	});

	$effect(() => {
		void week;
		err = '';
		brief = null;
		api.executive.brief(week).then((b) => (brief = b)).catch((x) => (err = problemMessage(x)));
		api.executive.briefs().then((l) => (list = l)).catch(() => undefined);
		api.incidents.list({ status: 'all', pageSize: 80 }).then((r) => (all = r.items)).catch(() => undefined);
		api.approvals.list({ status: 'all', pageSize: 200 }).then((r) => (actions = r.items)).catch(() => undefined);
	});
	const inWeek = $derived(range ? all.filter((i) => +new Date(i.firstSeen) >= +range.from && +new Date(i.firstSeen) <= +range.to) : []);
	const notable = $derived(inWeek.filter((i) => i.reportable === 'yes' || i.severity === 'critical' || i.severity === 'high'));
	$effect(() => {
		for (const i of notable) if (!stories[i.id]) api.incidents.timeline(i.id, 'executive').then((t) => (stories[i.id] = t)).catch(() => undefined);
	});
	const actionsFor = (id: string) => actions.filter((p) => p.incidentId === id && (p.status === 'executed' || p.status === 'approved'));
	const contained = (i: IncidentSummary) => {
		const first = actionsFor(i.id).map((p) => p.result?.executedAt ?? p.approvals.at(-1)?.decidedAt).filter(Boolean).sort()[0];
		if (!first) return null;
		const m = Math.round((+new Date(first) - +new Date(i.firstSeen)) / 60000);
		return m < 90 ? `${m} min` : `${Math.round(m / 60)} h`;
	};
	const label = $derived(range ? `${range.fromDay.getUTCDate()}–${range.toDay.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })}` : week);
	const m = $derived(brief?.metrics);
	const dots = $derived(inWeek.map((i) => i));
	const noiseClosed = $derived(inWeek.filter((i) => i.verdict === 'benign').length);
	const reportableCount = $derived(inWeek.filter((i) => i.reportable === 'yes').length);
	/** 137.6 s → "about 2 minutes", 1,369 s → "about 23 minutes", 5,400 s → "about 1.5 hours". */
	function duration(seconds: number | null | undefined): string {
		if (seconds === null || seconds === undefined) return 'no time recorded';
		if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`;
		if (seconds < 90 * 60) return `about ${Math.round(seconds / 60)} minutes`;
		const h = seconds / 3600;
		return `about ${h < 10 ? h.toFixed(1).replace(/\.0$/, '') : Math.round(h)} hours`;
	}
</script>

<svelte:head><title>Weekly report {week} · Nightjar SOC</title></svelte:head>

<main class="doc">
	{#if err}
		<p class="err">{err}</p>
		<p><a href="/brief/week">Open this week’s report</a></p>
	{:else if brief && m}
		<p class="dateline mono">Week {week.split('-W')[1]} · {label} · Halden Logistics · prepared for the board</p>
		<h1>{brief.headline}</h1>
		<div class="tools mono"><button type="button" onclick={() => window.print()}>Save as PDF ⇩</button>
			{#each list.filter((l) => l.week !== week).slice(0, 3) as l (l.week)}<a href="/brief/week/{l.week}">week {l.week.split('-W')[1]}</a>{/each}
		</div>

		<section>
			<h2 class="mono">Seven nights <span>◆ incident · ● closed as noise</span></h2>
			<SevenNights incidents={inWeek} at={range ? range.to : now()} />
		</section>

		<section>
			<h2 class="mono">Incidents, as told by their replays</h2>
			{#each notable as i (i.id)}
				{@const st = stories[i.id] ?? []}
				<article class="story">
					<h3><span class="lav mono">◆</span> {i.reference} {i.title}</h3>
					<p class="meta mono">{i.reportable === 'yes' ? `reportable · ${i.nextDeadline ? regimeShort(i.nextDeadline.regime) : 'notices filed'}` : 'not reportable'} · {contained(i) ? `contained in ${contained(i)}` : 'no action needed'} · {actionsFor(i.id).length} {actionsFor(i.id).length === 1 ? 'action' : 'actions'} approved by a person</p>
					<dl>
						<div><dt>What happened</dt><dd>{incidentShortName(i).replace(/^./, (c) => c.toUpperCase())}, first seen {shortDay(i.firstSeen)} at {timeOf(i.firstSeen)}.</dd></div>
						<div><dt>What we did</dt><dd>{st.length ? st.slice(0, 4).map((e) => `${timeOf(e.at)} ${e.title.replace(/ · /g, ', ')}`).join('. ') + '.' : 'The watch triaged and grouped the alerts.'}</dd></div>
						<div><dt>What we changed</dt><dd>{i.kind === 'ransomware_precursor' ? 'Patched the remote-access gateway and reset its sessions; service accounts now need two people to re-enable.' : i.kind === 'credential_misuse' ? 'Blocked the look-alike domain and added a rule for outside forwarding.' : i.kind === 'cloud_credential_exposure' ? 'Deploy keys move to short-lived credentials; public-code scanning now alerts within minutes.' : 'Tuned the rule that fired.'}</dd></div>
					</dl>
				</article>
			{:else}
				<p class="muted">No incident this week needed more than the watch.</p>
			{/each}
		</section>

		<section class="facts">
			<h2 class="mono">The numbers, in sentences</h2>
			<p><b>Clocks:</b> {#if reportableCount}{reportableCount} reportable {reportableCount === 1 ? 'incident' : 'incidents'}; every notice due this week went out on time, recorded with who submitted it.{:else}no incident this week needed a regulator notice.{/if}</p>
			<p><b>Noise:</b> {m.alerts.last7d.toLocaleString('en-GB')} alerts became {m.alerts.incidentsLast7d} {m.alerts.incidentsLast7d === 1 ? 'incident' : 'incidents'} ({m.alerts.reductionPct}% fewer things to look at than rules alone){#if m.alerts.suppressedAsNoise}; triage set {m.alerts.suppressedAsNoise.toLocaleString('en-GB')} aside as noise{/if}, and {noiseClosed} {noiseClosed === 1 ? 'incident was' : 'incidents were'} closed as harmless.</p>
			<p><b>Speed:</b> the first verdict on an incident took {duration(m.meanTimeToTriage.last7dSeconds)} on average ({duration(m.meanTimeToTriage.last30dSeconds)} over 30 days); containment followed in {duration(m.meanTimeToContain.last7dSeconds)}.</p>
			<svg class="dotplot" viewBox="0 0 640 60" role="img" aria-label="Time to contain per incident this week">
				<line x1="0" y1="30" x2="640" y2="30" stroke="var(--rule-2)" />
				{#each [0, 15, 30, 45, 60] as t (t)}<text x={t * 10 + 10} y="54" font-size="11" font-family="var(--mono)" fill="var(--faint)">{t} min</text>{/each}
				{#each dots as i, k (i.id)}{@const c = contained(i)}{#if c && c.endsWith('min')}<circle cx={Math.min(620, parseInt(c) * 10 + 10)} cy={30 - (k % 3) * 6 + 6} r="5" fill="var(--teal)" opacity=".8"><title>{i.reference}: {c}</title></circle>{/if}{/each}
			</svg>
			<p><b>Next week:</b> {m.components[0]?.note ?? ''} The NIS2 final report for the dispatch incident is due one month after its incident notification.</p>
		</section>
		<p class="foot mono">Sample company and data · designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting; not legal advice.</p>
	{:else}
		<p class="mono muted">Assembling the week…</p>
	{/if}
</main>

<style>
	.doc {
		position: relative;
		max-width: calc(var(--gut) + 900px);
		margin: 0;
		padding: 44px 28px 100px calc(var(--gut) + 6px);
	}
	.dateline {
		font-size: 13px;
		color: var(--muted);
		margin: 0;
	}
	h1 {
		margin: 16px 0 14px;
		font: 500 30px/1.25 var(--sans);
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.02em;
		text-wrap: balance;
	}
	.tools {
		display: flex;
		gap: 16px;
		font-size: 12px;
		color: var(--muted);
	}
	.tools button,
	.tools a {
		color: var(--ink-2);
		font: inherit;
		text-decoration: none;
		border-bottom: 1px solid var(--rule-2);
	}
	section {
		margin-top: 52px;
	}
	h2 {
		display: flex;
		gap: 12px;
		align-items: baseline;
		margin: 0 0 6px;
		font-size: 13px;
		font-weight: 600;
		color: var(--ink);
	}
	h2 span {
		font-weight: 400;
		color: var(--muted);
	}
	.story {
		padding: 20px 0;
		border-top: 1px solid var(--rule);
	}
	.story h3 {
		margin: 0 0 4px;
		font: 600 17px/1.35 var(--sans);
		font-variation-settings: 'MONO' 0;
	}
	.meta {
		margin: 0 0 12px;
		font-size: 12px;
		color: var(--muted);
	}
	dl {
		margin: 0;
		display: grid;
		gap: 8px;
	}
	dl div {
		display: grid;
		grid-template-columns: 140px 1fr;
		gap: 16px;
	}
	dt {
		font: 400 12.5px/1.7 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--faint);
	}
	dd {
		margin: 0;
		font-size: 15.5px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.facts p {
		font-size: 16px;
		line-height: 1.7;
		color: var(--ink-2);
		margin: 10px 0;
	}
	.facts b {
		color: var(--ink);
	}
	.dotplot {
		width: 100%;
		height: auto;
		margin: 6px 0 10px;
	}
	.foot {
		margin-top: 60px;
		font-size: 11.5px;
		color: var(--faint);
	}
	.err {
		color: var(--ink-2);
	}
	@media (max-width: 760px) {
		.doc {
			padding: 28px 16px 70px;
		}
		h1 {
			font-size: 24px;
		}
		dl div {
			grid-template-columns: 1fr;
			gap: 2px;
		}
	}
	@media print {
		.tools {
			display: none;
		}
		.doc {
			padding-top: 0;
		}
	}
</style>
