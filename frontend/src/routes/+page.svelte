<script lang="ts">
	// "Replay the night" (DESIGN.md §0.2, §4): the landing is the product's own grammar replaying one night at Halden
	// Logistics. The shift axis fills toward 03:13, intake bars rise, the incident's line steps down the response
	// lanes, the NIS2 flag drops into the deadline gutter, and the night log types under it, ending on the headline.
	// Time-driven, not scroll-driven; `?still` and reduced motion show the finished night at once.
	import { onMount, onDestroy } from 'svelte';
	import { fly } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import ShiftAxis from '$lib/components/shell/ShiftAxis.svelte';
	import Tick from '$lib/components/shell/Tick.svelte';
	import ShiftChart from '$lib/components/console/ShiftChart.svelte';
	import HoldButton from '$lib/components/console/HoldButton.svelte';
	import type { Deadline, IncidentSummary, ShiftLogEntry } from '$lib/api/types';
	import { BRUSSELS, nightAt, nightOfLabel, now as clockNow, zonedParts } from '$lib/time/clock';
	import { shiftWindow, type AxisMark } from '$lib/util/shift';
	import { ui } from '$lib/stores/ui.svelte';

	const ROUTINE: [string, string][] = [
		['Backup of the Antwerp file server finished', 'server'],
		['Laptop mel-ws-007 checked in, patched', 'device'],
		['Firewall turned away 9 scans on the Antwerp gateway', 'network'],
		['Raphaël Dubois signed in from home, Ghent', 'sign-in'],
		['Mail scanned: 312 messages, 4 held', 'mail'],
		['Night shift badge-in at the Antwerp warehouse', 'building'],
		['Cloud storage: 2 files shared with a customer', 'cloud'],
		['Laptop ant-ws-022 installed updates', 'device'],
		['Nathan Kelly signed in at the Melbourne depot', 'sign-in'],
		['Payment run scheduled for 06:00', 'finance']
	];
	const ODD: [string, string][] = [
		['Joy Okafor signed in from a country she has never signed in from', 'sign-in'],
		['A new inbox rule forwards Joy’s invoices outside Halden', 'mail'],
		['Five refused sign-in prompts, then one accepted', 'identity']
	];

	interface Row { id: number; at: Date; text: string; src: string; odd: boolean }
	interface Line { id: number; ts: string; text: string; key: boolean }

	let reduced = $state(false);
	let vStart = nightAt(3, 11, 50);
	let vNow = $state(vStart);
	let speed = 6;
	let rows = $state<Row[]>([]);
	let log = $state<Line[]>([]);
	let alerts = $state<Row[]>([]);
	let clocks = $state<Deadline[]>([]);
	let asking = $state(false);
	let authorised = $state<{ by: string; at: Date } | null>(null);
	let done = $state(false);
	// the moments the incident moved, for its line on the chart
	let tOpen = $state<Date | null>(null);
	let tInvestigate = $state<Date | null>(null);
	let tClock = $state<Date | null>(null);
	let tAsk = $state<Date | null>(null);
	let rid = 0;
	let lid = 0;
	let timers: ReturnType<typeof setTimeout>[] = [];
	let tickTimer: ReturnType<typeof setInterval> | null = null;
	let riverTimer: ReturnType<typeof setInterval> | null = null;
	let hold = $state<HoldButton | null>(null);
	let userHeld = false;

	const fmt = (d: Date) => d.toLocaleTimeString('en-GB', { timeZone: 'Europe/Brussels', hour: '2-digit', minute: '2-digit', second: '2-digit' });
	const nightOf = nightOfLabel(clockNow());

	function addRow(text: string, src: string, odd = false) {
		const r = { id: ++rid, at: new Date(vNow), text, src, odd };
		rows = [r, ...rows].slice(0, 6);
		return r;
	}
	function say(ts: string, text: string, key = false) {
		log = [...log.filter((l) => l.ts !== 'intro'), { id: ++lid, ts, text, key }].slice(-6);
	}
	const at = (ms: number, fn: () => void) => timers.push(setTimeout(fn, ms));
	function clearAll() {
		timers.forEach(clearTimeout);
		timers = [];
		if (tickTimer) clearInterval(tickTimer);
		if (riverTimer) clearInterval(riverTimer);
		tickTimer = riverTimer = null;
	}
	function mkClock(id: string, label: string, hours: number, start: Date): Deadline {
		return { id, incidentId: 'replay', incidentReference: 'Joy Okafor', regime: 'nis2', regimeLabel: 'NIS2', noticeType: id, noticeLabel: label, jurisdiction: 'Belgium', startsAt: start.toISOString(), dueAt: new Date(+start + hours * 3600e3).toISOString(), dueAtLocal: null, businessDays: null, state: 'on_track', remainingSeconds: null, noticeId: null, basis: 'Halden Logistics NV, postal and courier services, Belgium: invoices redirected, possible considerable financial loss' };
	}
	function dropClocks() {
		const start = new Date(vNow);
		tClock = start;
		clocks = [mkClock('ew', 'early warning', 24, start), mkClock('nf', 'full notification', 72, start)];
		ui.announce('NIS2 clock started');
	}

	async function confirmHold() {
		authorised = { by: userHeld ? 'you' : 'Marcus Bell, on call', at: new Date(vNow) };
		say(fmt(vNow), `${userHeld ? 'You held' : 'Marcus, on call, held'} to authorise. Account turned off and Joy signed out everywhere.`, true);
		at(2600, finish);
		return { label: `Authorised · ${fmt(vNow)}`, sub: `by ${authorised.by}` };
	}

	function run() {
		tickTimer = setInterval(() => (vNow = new Date(+vNow + 100 * speed)), 100);
		riverTimer = setInterval(() => {
			const [t, s] = ROUTINE[rid % ROUTINE.length]!;
			addRow(t, s);
		}, 420);
		log = [{ id: ++lid, ts: 'intro', text: 'It is quiet at Halden Logistics. Events arrive by the thousand, nearly all of them routine.', key: false }];
		at(1700, () => {
			alerts = [...alerts, addRow(...ODD[0]!, true)];
			say('03:12:04', 'Joy in accounts payable signs in from a country she has never signed in from.', true);
		});
		at(3000, () => (alerts = [...alerts, addRow(...ODD[1]!, true)]));
		at(3600, () => (alerts = [...alerts, addRow(...ODD[2]!, true)]));
		at(4800, () => {
			tOpen = new Date(vNow);
			say(fmt(vNow), 'Three alerts become one incident.', true);
		});
		at(6200, () => say(fmt(vNow), 'Triage says it is likely real and shows why. Enrichment checks who is behind it. Response drafts a plan.', true));
		at(7300, () => (tInvestigate = new Date(vNow)));
		at(9800, () => {
			dropClocks();
			say(fmt(vNow), 'Likely reportable under NIS2. The 24-hour clock counts from now, and the early warning is drafted for the CISO.', true);
		});
		at(12000, () => {
			// the night moves on while the colleagues finish: the plan is ready at 03:40
			vNow = nightAt(3, 40, 50);
			tAsk = new Date(vNow);
			asking = true;
			speed = 2;
			say(fmt(vNow), 'The plan is ready: turn off Joy’s account and sign her out. Nothing happens until a person holds to authorise.');
		});
		at(15200, () => {
			if (!userHeld) hold?.simulate();
		});
	}
	function finish() {
		if (done) return;
		done = true;
		speed = 1;
		if (riverTimer) clearInterval(riverTimer);
		if (!reduced) riverTimer = setInterval(() => addRow(...ROUTINE[rid % ROUTINE.length]!), 1600);
	}
	function still() {
		clearAll();
		vNow = nightAt(3, 9, 30);
		rows = [];
		for (let i = 0; i < 6; i++) {
			vNow = new Date(+vNow + 13000);
			addRow(...ROUTINE[i % ROUTINE.length]!);
		}
		alerts = ODD.map(([t, s], i) => ({ id: ++rid, at: nightAt(3, 12, 4 + i * 9), text: t, src: s, odd: true }));
		tOpen = nightAt(3, 12, 31);
		tInvestigate = nightAt(3, 12, 58);
		vNow = nightAt(3, 13, 40);
		dropClocks();
		log = [
			['03:12:04', 'Joy in accounts payable signs in from a country she has never signed in from.'],
			['03:12:31', 'Three alerts become one incident.'],
			['03:12:58', 'Triage: likely real, 94 in 100. Enrichment: the sender’s domain is two days old.'],
			['03:13:40', 'Likely reportable under NIS2. The 24-hour clock counts from now; the early warning is drafted for the CISO.']
		].map(([ts, text]) => ({ id: ++lid, ts: ts!, text: text!, key: true }));
		vNow = nightAt(3, 41, 0);
		tAsk = nightAt(3, 40, 50);
		asking = true;
		authorised = { by: 'Marcus Bell, on call', at: new Date(vNow) };
		log = [...log, { id: ++lid, ts: fmt(vNow), text: 'Marcus, on call, held to authorise. Account turned off and Joy signed out everywhere.', key: true }];
		done = true;
		tickTimer = setInterval(() => (vNow = new Date(+vNow + 1000)), 1000);
	}
	function replay() {
		clearAll();
		vNow = vStart;
		rows = [];
		log = [];
		alerts = [];
		clocks = [];
		asking = false;
		authorised = null;
		done = false;
		userHeld = false;
		tOpen = tInvestigate = tClock = tAsk = null;
		speed = 6;
		if (reduced) still();
		else run();
	}

	onMount(() => {
		document.documentElement.dataset.theme = new URLSearchParams(location.search).get('theme') === 'light' ? 'light' : ui.theme;
		reduced = prefersReducedMotion.current || new URLSearchParams(location.search).has('still');
		vStart = nightAt(3, 11, 50);
		const go = () => (reduced ? still() : run());
		if (document.fonts) document.fonts.ready.then(go);
		else go();
	});
	onDestroy(clearAll);

	// ---------- the replay, drawn in the product's own grammar ----------
	const win = $derived(shiftWindow(vNow, BRUSSELS));
	const incident = $derived(
		tOpen
			? ({
					id: 'replay-joy',
					reference: 'Joy Okafor',
					title: 'Account takeover after a phishing email',
					kind: 'account_takeover',
					severity: 'high',
					status: authorised ? 'monitoring' : 'investigating',
					stage: tAsk ? 'contain' : tInvestigate ? 'investigate' : 'triage',
					assets: [],
					identities: [],
					attackTactics: [],
					detectionCount: 3,
					firstSeen: tOpen.toISOString(),
					lastSeen: vNow.toISOString(),
					assignee: null,
					verdict: null,
					likelyBenign: false,
					reportable: 'yes',
					nextDeadline: null,
					pendingApprovals: asking && !authorised ? 1 : 0
				} satisfies IncidentSummary)
			: null
	);
	const replayLog = $derived(
		[
			tInvestigate && { at: tInvestigate.toISOString(), text: 'Triage: likely real', kind: 'incident' as const, refId: 'replay-joy', severity: 'high' as const },
			tClock && { at: tClock.toISOString(), text: 'NIS2 clock started', kind: 'clock' as const, refId: 'replay-joy', severity: null },
			tAsk && { at: tAsk.toISOString(), text: 'Turn off Joy’s account: waiting for a person', kind: 'approval' as const, refId: 'replay-joy', severity: null }
		].filter(Boolean) as ShiftLogEntry[]
	);
	// hourly intake for the night: busy evening, a quiet night, the 01:00 backup storm; the current hour fills in
	const intake = $derived.by(() => {
		const out: { at: Date; count: number }[] = [];
		for (let t = +win.start; t <= +vNow; t += 3600e3) {
			const h = zonedParts(new Date(t), BRUSSELS).hour;
			const base = h >= 18 && h <= 21 ? 64_000 - (h - 18) * 9_000 : h === 1 ? 81_000 : h >= 22 || h <= 5 ? 28_000 + ((h * 7919) % 6000) : 52_000;
			const frac = Math.min(1, (+vNow - t) / 3600e3);
			out.push({ at: new Date(t), count: Math.round(base * frac) });
		}
		return out;
	});
	const marks = $derived<AxisMark[]>([
		...(tOpen ? [] : alerts).map((a) => ({ at: a.at, kind: 'approval' as const, glyph: '◇', text: a.text, refId: null })),
		...(tOpen ? [{ at: tOpen, kind: 'incident' as const, glyph: '▲', text: 'Account takeover after a phishing email', refId: null }] : []),
		...(tClock ? [{ at: tClock, kind: 'notice' as const, glyph: '✉', text: 'NIS2 early warning drafted', refId: null }] : []),
		...(tAsk ? [{ at: tAsk, kind: 'approval' as const, glyph: '‖', text: 'Turn off Joy’s account: waiting for a person', refId: null }] : [])
	]);
	const hm = $derived(fmt(vNow).slice(0, 6));
	const ss = $derived(fmt(vNow).slice(6));
	const noise = Array.from({ length: 120 }, (_, i) => i === 37 || i === 91);
</script>

<svelte:head>
	<title>Nightjar SOC · Someone is always on watch</title>
</svelte:head>

<a class="skip-link" href="#log">Skip to the shift log</a>
<section class="stage" class:done aria-label="Replay of one night at Halden Logistics">
	<ShiftAxis {win} now={vNow} {clocks} {marks} fast={!done} />

	<div class="band">
		<header class="rule mono">
			<span class="dash" aria-hidden="true">──</span><span class="rec" aria-hidden="true">●</span>
			<b>replay</b><span class="hide-s">· Halden Logistics · the night of {nightOf}</span>
			<span class="run" aria-hidden="true"></span>
			<button class="lnk hide-s" type="button" onclick={replay}>↺ replay the night</button>
			<a class="btn btn-sm preset-filled-primary-700-300" href="/sign-in">Take the watch →</a>
		</header>
		<ShiftChart {win} incidents={incident ? [incident] : []} log={replayLog} {intake} tz={BRUSSELS} hrefFor={() => '/sign-in'} />
	</div>

	<div class="lower">
		<div class="nightlog-band">
			<div class="bigclock mono" aria-hidden="true"><Tick value={hm} fast={!done} /><span class="sec"><Tick value={ss} fast={!done} /></span></div>
			<ol class="nightlog" aria-live="polite">
				{#each log as l, i (l.id)}
					<li class:now={i === log.length - 1 && !done} class:key={l.key} class:resolve={l.key && !reduced} in:fly={{ y: 6, duration: reduced ? 0 : 420 }}>
						<span class="ts mono">{l.ts === 'intro' ? fmt(vNow) : l.ts}</span><span>{l.text}</span>
					</li>
				{/each}
			</ol>
			{#if done}
				<div class="final" in:fly={{ y: 8, duration: reduced ? 0 : 600 }}>
					<h1>Someone is always on watch.</h1>
					<p>Nightjar SOC watches your company’s security through the night, separates real threats from noise, investigates with three AI teammates, starts the regulator’s clock and drafts the notice. Nothing is switched off or locked out until a person says so.</p>
					<div class="ctas"><a class="btn btn-lg preset-filled-primary-700-300" href="/sign-in">Take the watch →</a><button class="btn btn-lg preset-outlined-surface-300-700" type="button" onclick={replay}>↺ Replay the night</button></div>
				</div>
			{:else}
				<button class="skip mono" type="button" onclick={still}>skip replay →</button>
			{/if}
		</div>
		{#if asking}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="askbox" in:fly={{ y: 6, duration: reduced ? 0 : 380 }} onpointerdown={() => (userHeld = true)} onkeydown={() => (userHeld = true)}>
				<p class="lbl mono">‖ waiting on a person · proposed by Response · reversible · logged</p>
				<p class="q">Turn off Joy’s account and sign her out everywhere</p>
				<HoldButton bind:this={hold} hint="try it · or hold Space" done={authorised && done ? { label: `Authorised · ${fmt(authorised.at)}`, sub: `by ${authorised.by}` } : null} onconfirm={confirmHold} />
				<p class="try mono">{authorised ? 'Logged with who, when and why.' : 'If you don’t, Marcus on call will.'}</p>
			</div>
		{/if}
	</div>

	<nav class="hours mono" aria-label="The night, hour by hour">
		<a href="#t2200"><b>22:00</b> takes in everything</a>
		<a href="#t0130"><b>01:30</b> sorts real from noise</a>
		<a href="#t0312"><b>03:12</b> starts the regulator’s clock</a>
		<a href="#t0800"><b>08:00</b> briefs the CISO</a>
		<a class="end" href="/sign-in">sign in</a>
	</nav>
</section>

<main class="log" id="log">
	<header class="rule mono"><span class="dash" aria-hidden="true">──</span><b>the shift log</b><span>· one night, four entries</span><span class="run" aria-hidden="true"></span></header>
	<div class="entries">
		<article class="entry" id="t2200">
			<p class="when mono">22:00 <small>the watch begins</small></p>
			<h2>Every sign-in, email, laptop and cloud account, all night.</h2>
			<p class="lede">Nightjar SOC takes in your security events as they happen: sign-ins, mail, endpoints, firewalls and cloud services across every office. It writes each one in plain words and folds away the routine, so the few that matter stand out.</p>
			<div class="noise" aria-hidden="true">{#each noise as k, i (i)}<i class:k></i>{/each}</div>
			<p class="cap mono">One minute at Halden Logistics: 1,284 events · 1,282 routine, folded · ◇ 2 worth a look</p>
		</article>
		<article class="entry" id="t0130">
			<p class="when mono">01:30 <small>a false-alarm storm</small></p>
			<h2>146 alerts from one backup update. One note, not 146 pages.</h2>
			<p class="lede">Rules match patterns; patterns are noisy. Nightjar SOC groups related alerts into one incident, learns each person’s and laptop’s normal rhythm, and scores every alert for how likely it is to be real. It shows its reasons, so your team can disagree with it.</p>
		</article>
		<article class="entry" id="t0312">
			<p class="when mono">03:12 <small>reportable</small></p>
			<h2>When an incident may be reportable, the clock is ready before you are.</h2>
			<p class="lede">Nightjar SOC checks each incident against where you operate and what happened, proposes which clocks apply, and drafts each notice with the required facts filled in from the evidence. A person decides, reviews and submits.</p>
		</article>
		<article class="entry dawn" id="t0800">
			<p class="when mono">08:00 <small>first light</small></p>
			<h2>One incident from overnight is reportable. It is contained, and one decision is yours.</h2>
			<p class="lede">The night watch hands over in plain words: what happened, which clocks are running, what was approved and by whom, and the one thing that needs the CISO today.</p>
		</article>
	</div>
	<section class="clocksband" aria-labelledby="ck">
		<header class="rule mono"><span class="dash" aria-hidden="true">──</span><b id="ck">the clocks it keeps</b><span class="run" aria-hidden="true"></span></header>
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>regime</th><th>what is due</th><th>clock</th><th>counts from</th></tr></thead>
				<tbody>
					<tr><td><b>NIS2 · EU</b></td><td>Early warning, then full notification, then a final report</td><td class="m">24 h · 72 h</td><td class="m">becoming aware of a significant incident; final report within a month</td></tr>
					<tr><td><b>SEC Form 8-K · US</b></td><td>Item 1.05, once you decide an incident is material</td><td class="m">4 business days</td><td class="m">the materiality decision</td></tr>
					<tr><td><b>SOCI · Australia</b></td><td>Report to the Australian Signals Directorate’s ACSC</td><td class="m">12 h · 72 h</td><td class="m">significant · relevant impact</td></tr>
				</tbody>
			</table>
		</div>
	</section>
</main>
<footer class="fin">
	<h2>Take the watch at Halden Logistics.</h2>
	<a class="btn btn-lg preset-filled-primary-700-300" href="/sign-in">Take the watch →</a>
	<p class="fine mono">Sample company and data · For mid-size companies with operations in the EU, US and Australia · designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting; it does not submit anything without a person.</p>
</footer>

<style>
	.stage {
		position: relative;
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto auto minmax(0, 1fr) auto;
		background: var(--bg);
	}
	.band {
		background: var(--pane);
		border-bottom: 1px solid var(--line-strong);
		position: relative;
	}
	.rule {
		height: 34px;
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 16px;
		font-size: 12px;
		color: var(--muted);
		background: var(--bg);
		border-bottom: 1px solid var(--line);
		white-space: nowrap;
		min-width: 0;
	}
	.rule b {
		color: var(--ink);
		font-weight: 600;
	}
	.dash {
		color: var(--line-strong);
	}
	.rec {
		color: var(--red);
		font-size: 9px;
	}
	.run {
		flex: 1;
		min-width: 12px;
		height: 1px;
		background: repeating-linear-gradient(90deg, var(--line-strong) 0 6px, transparent 6px 9px);
	}
	.lower {
		display: grid;
		grid-template-columns: minmax(0, 1fr) var(--dl);
		min-height: 0;
		border-bottom: 1px solid var(--line);
	}
	.nightlog-band {
		padding: 22px 28px 24px calc(var(--gut) + 6px);
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.bigclock {
		display: flex;
		align-items: baseline;
		font-size: clamp(44px, 5.4vw, 76px);
		font-weight: 300;
		line-height: 1;
		letter-spacing: -0.03em;
		color: var(--ink);
	}
	.bigclock .sec {
		color: var(--teal);
		font-size: 0.62em;
		margin-left: 2px;
	}
	.nightlog {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
		max-width: 860px;
	}
	.nightlog li {
		display: grid;
		grid-template-columns: 76px minmax(0, 1fr);
		gap: 14px;
		font-size: 16px;
		line-height: 1.45;
		color: var(--muted);
		font-variation-settings: 'MONO' 0;
	}
	.nightlog li.key {
		color: var(--ink-2);
	}
	.nightlog li.now {
		color: var(--ink);
		font-size: 19px;
	}
	.nightlog .ts {
		font-size: 12.5px;
		color: var(--teal);
		padding-top: 3px;
	}
	.final {
		display: grid;
		gap: 12px;
		max-width: 760px;
		padding-top: 10px;
		border-top: 1px solid var(--line);
	}
	.final h1 {
		margin: 0;
		font-size: clamp(32px, 4vw, 52px);
		line-height: 1.05;
		letter-spacing: -0.025em;
		font-weight: 650;
	}
	.final p {
		margin: 0;
		color: var(--ink-2);
		font-size: 15.5px;
		line-height: 1.55;
	}
	.ctas {
		display: flex;
		gap: 10px;
		flex-wrap: wrap;
	}
	.skip {
		align-self: flex-start;
		font-size: 12px;
		color: var(--muted);
		border-bottom: 1px solid var(--line-strong);
	}
	.askbox {
		border-left: 1px solid var(--line-strong);
		background: var(--lav-wash);
		padding: 22px 20px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.askbox .lbl {
		margin: 0;
		font-size: 11.5px;
		color: var(--lav);
	}
	.askbox .q {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
		line-height: 1.3;
	}
	.askbox .try {
		margin: 0;
		font-size: 11.5px;
		color: var(--muted);
	}
	.hours {
		display: flex;
		height: 40px;
		border-top: 1px solid var(--line);
		background: var(--bg);
		font-size: 12px;
		overflow-x: auto;
		scrollbar-width: none;
	}
	.hours a {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 16px;
		border-right: 1px solid var(--line);
		color: var(--muted);
		text-decoration: none;
		white-space: nowrap;
	}
	.hours a:hover {
		color: var(--ink);
	}
	.hours b {
		color: var(--ink-2);
		font-weight: 600;
	}
	.hours .end {
		margin-left: auto;
		border-right: 0;
		border-left: 1px solid var(--line);
	}
	.log {
		background: var(--pane);
	}
	.entries {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
	}
	.entry {
		padding: 26px 24px 34px;
		border-left: 1px solid var(--line);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.entry:first-child {
		border-left: 0;
		padding-left: calc(var(--gut) + 6px);
	}
	.when {
		margin: 0;
		font-size: 22px;
		font-weight: 600;
		color: var(--teal);
	}
	.when small {
		font-size: 11.5px;
		font-weight: 400;
		color: var(--faint);
		margin-left: 6px;
	}
	.entry h2 {
		margin: 0;
		font-size: 19px;
		line-height: 1.3;
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.entry .lede {
		margin: 0;
		font-size: 14.5px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.entry.dawn {
		background: linear-gradient(180deg, #cbe5de, #f7f9f8);
		color: #0b141b;
	}
	.entry.dawn .when {
		color: #087565;
	}
	.entry.dawn .when small {
		color: #4a5a62;
	}
	.entry.dawn .lede {
		color: #2b3943;
	}
	.noise {
		display: grid;
		grid-template-columns: repeat(24, 1fr);
		gap: 3px;
		max-width: 360px;
	}
	.noise i {
		height: 8px;
		border-radius: 1px;
		background: var(--line-strong);
	}
	.noise i.k {
		background: var(--amber);
	}
	.cap {
		margin: 0;
		font-size: 11.5px;
		color: var(--faint);
	}
	.clocksband {
		border-top: 1px solid var(--line);
	}
	.clocksband .table-wrap {
		padding: 0 16px 0 calc(var(--gut) + 6px);
	}
	.clocksband .table th {
		background: var(--pane);
	}
	.fin {
		padding: 46px 16px 54px calc(var(--gut) + 6px);
		display: grid;
		gap: 16px;
		justify-items: start;
		border-top: 1px solid var(--line-strong);
		background: var(--bg);
	}
	.fin h2 {
		margin: 0;
		font-size: clamp(26px, 3vw, 36px);
		letter-spacing: -0.02em;
		font-weight: 650;
	}
	.fine {
		margin: 0;
		font-size: 11.5px;
		color: var(--faint);
		max-width: 80ch;
	}
	@media (max-width: 1180px) {
		.entries {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.entry:nth-child(3) {
			border-left: 0;
			padding-left: calc(var(--gut) + 6px);
		}
		.entry:nth-child(n + 3) {
			border-top: 1px solid var(--line);
		}
	}
	@media (max-width: 760px) {
		.lower {
			grid-template-columns: minmax(0, 1fr);
		}
		.askbox {
			border-left: 0;
			border-top: 1px solid var(--line-strong);
		}
		.nightlog-band {
			padding: 18px 16px 20px;
		}
		.nightlog li {
			grid-template-columns: 64px minmax(0, 1fr);
			font-size: 14.5px;
		}
		.nightlog li.now {
			font-size: 16px;
		}
		.entries {
			grid-template-columns: minmax(0, 1fr);
		}
		.entry,
		.entry:first-child,
		.entry:nth-child(3) {
			border-left: 0;
			padding: 22px 16px 26px;
			border-top: 1px solid var(--line);
		}
		.clocksband .table-wrap,
		.fin {
			padding-left: 16px;
		}
		:global(.hide-s) {
			display: none;
		}
		.hours a:not(.end) {
			display: none;
		}
	}
</style>
