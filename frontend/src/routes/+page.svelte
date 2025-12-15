<script lang="ts">
	// "Replay the night" (DESIGN.md §4): the landing is the product replaying one night at Halden Logistics on its
	// own watch dial. The hand sweeps toward 03:13, intake bars stand out from their ring, Joy's incident runs round
	// the triage ring and steps inward, the NIS2 clock drops in beside the chronometer, and the night log runs beside
	// the dial, ending on the stencil headline. Time-driven, not scroll-driven; `?still` and reduced motion show the
	// finished night at once.
	import { onMount, onDestroy } from 'svelte';
	import { fly } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import { backOut } from 'svelte/easing';
	import Tick from '$lib/components/shell/Tick.svelte';
	import Logo from '$lib/components/shell/Logo.svelte';
	import LampPanel from '$lib/components/shell/LampPanel.svelte';
	import WatchDial from '$lib/components/console/WatchDial.svelte';
	import HoldButton from '$lib/components/console/HoldButton.svelte';
	import type { Deadline, IncidentSummary, ShiftLogEntry } from '$lib/api/types';
	import { BRUSSELS, nightAt, nightOfLabel, now as clockNow, zonedParts } from '$lib/time/clock';
	import { shiftWindow, watchOf } from '$lib/util/shift';
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
	const hm = $derived(fmt(vNow).slice(0, 6));
	const ss = $derived(fmt(vNow).slice(6));
	const noise = Array.from({ length: 120 }, (_, i) => i === 37 || i === 91);
	const watch = $derived(watchOf(vNow, BRUSSELS));
	const PUBLIC = [
		{ n: 1, href: '#top', label: 'The night', lit: true },
		{ n: 2, href: '#round', label: 'The round' },
		{ n: 3, href: '#clocks', label: 'The clocks' },
		{ n: 4, href: '/sign-in', label: 'Sign on' }
	];
	const ROUND = [
		{ t: '22:00', label: 'The watch begins', line: 'Every event, all night.', more: 'Nightjar SOC takes in your security events as they happen: sign-ins, mail, endpoints, firewalls and cloud services across every office. It writes each one in plain words and folds away the routine, so the few that matter stand out. One minute at Halden Logistics: 1,284 events, 2 worth a look.' },
		{ t: '01:30', label: 'A false-alarm storm', line: '146 alerts. One note.', more: 'One backup update set off 146 alerts. Nightjar SOC groups related alerts into one incident, learns each person’s and laptop’s normal rhythm, and scores every alert for how likely it is to be real. It shows its reasons, so your team can disagree with it.' },
		{ t: '03:12', label: 'Reportable', line: 'The clock is ready first.', lit: true, more: 'Nightjar SOC checks each incident against where you operate and what happened, proposes which clocks apply, and drafts each notice with the required facts filled in from the evidence. A person decides, reviews and submits.' },
		{ t: '08:00', label: 'First light', line: 'One decision for the CISO.', more: 'The night watch hands over in plain words: what happened, which clocks are running, what was approved and by whom, and the one thing that needs the CISO today.' }
	];
	const REGIMES = [
		{ name: 'NIS2 · EU', clock: '24 h · 72 h', what: 'Early warning, notification', tone: 'h-acc', more: 'Early warning within 24 hours of becoming aware of a significant incident, notification within 72 hours, a final report within a month.' },
		{ name: 'SEC 8-K · US', clock: '4 days', what: 'After the materiality call', tone: 'h-person', more: 'Form 8-K Item 1.05, four business days after you decide an incident is material.' },
		{ name: 'SOCI · AU', clock: '12 h · 72 h', what: 'Significant · relevant', tone: 'h-soon', more: 'Report to the Australian Signals Directorate’s ACSC: 12 hours for a significant impact, 72 hours for a relevant one.' }
	];
	const shownLog = $derived(done ? log.slice(-2) : log);
</script>

<svelte:head>
	<title>Nightjar SOC · Someone is always on watch</title>
</svelte:head>

<a class="skip-link" href="#round">Skip to the round</a>
<header class="lhead" id="top">
	<a class="brand" href="/" aria-label="Nightjar SOC"><Logo /><span>Nightjar <i>SOC</i></span></a>
	<div class="lp"><LampPanel items={PUBLIC} /></div>
	<a class="btn preset-filled-primary-500 cta" href="/sign-in">Take the watch →</a>
</header>

<section class="stage" class:done aria-label="Replay of one night at Halden Logistics">
	<div class="dialcol">
		<WatchDial {win} incidents={incident ? [incident] : []} log={replayLog} {intake} tz={BRUSSELS} compact max={660} hrefFor={() => '/sign-in'} />
		<ol class="ticker" aria-hidden="true">
			{#each rows.slice(0, 3) as r (r.id)}
				<li class:odd={r.odd} in:fly={{ y: -6, duration: reduced ? 0 : 260 }}><span class="mono t">{fmt(r.at)}</span><span class="g">{r.odd ? '◇' : '·'}</span><span class="x">{r.text}</span></li>
			{/each}
		</ol>
	</div>

	<div class="logcol">
		<p class="kicker eng" title="The night of {nightOf}"><span class="rec" aria-hidden="true">●</span> Replay · Halden Logistics</p>
		<div class="chrono">
			<div class="bigclock mono" aria-hidden="true"><Tick value={hm} fast={!done} /><span class="sec"><Tick value={ss} fast={!done} /></span></div>
			<span class="eng wt">{watch.label} watch</span>
		</div>
		<div class="lamps2" aria-live="polite">
			{#each clocks as c, i (c.id)}
				<span class="clk" in:fly={{ y: reduced ? 0 : -26, duration: reduced ? 0 : 620, delay: i * 120, easing: backOut }}>
					<span class="eng">NIS2 · {c.noticeLabel}</span>
					<span class="mono">{c.id === 'ew' ? '24:00:00' : '72:00:00'}</span>
					<span class="st">○ on track</span>
				</span>
			{/each}
		</div>
		<ol class="nightlog" aria-live="polite">
			{#each shownLog as l, i (l.id)}
				<li class:now={i === shownLog.length - 1 && !done} class:key={l.key} class:resolve={l.key && !reduced} in:fly={{ y: 6, duration: reduced ? 0 : 420 }}>
					<span class="ts mono">{l.ts === 'intro' ? fmt(vNow) : l.ts}</span><span>{l.text}</span>
				</li>
			{/each}
		</ol>
		{#if asking && !done}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="askbox ticks" in:fly={{ y: 6, duration: reduced ? 0 : 380 }} onpointerdown={() => (userHeld = true)} onkeydown={() => (userHeld = true)}>
				<p class="lbl eng" title="Proposed by Response · reversible · logged">‖ Waiting on a person</p>
				<p class="q">Turn off Joy’s account and sign her out everywhere</p>
				<HoldButton bind:this={hold} hint="try it · or hold Space" done={authorised && done ? { label: `Authorised · ${fmt(authorised.at)}`, sub: `by ${authorised.by}` } : null} onconfirm={confirmHold} />
				<p class="try">{authorised ? 'Logged with who, when and why.' : 'If you don’t, Marcus on call will.'}</p>
			</div>
		{/if}
		{#if done}
			<div class="final" in:fly={{ y: 10, duration: reduced ? 0 : 600 }}>
				<h1>Someone is always on watch.</h1>
				<p title="Nightjar SOC watches your company’s security through the night, separates real threats from noise, investigates with three AI teammates, starts the regulator’s clock and drafts the notice. Nothing is switched off or locked out until a person says so.">Threats found, regulator clocks kept, and a person decides.</p>
				<div class="ctas"><a class="btn btn-lg preset-filled-primary-500" href="/sign-in">Take the watch →</a><button class="btn btn-lg preset-outlined-surface-300-700" type="button" onclick={replay}>↺ Replay the night</button></div>
			</div>
		{:else}
			<button class="skip lnk" type="button" onclick={still}>Skip the replay →</button>
		{/if}
	</div>
</section>

<main class="round" id="round">
	<h2 class="sr">The round: one night, four stations</h2>
	<!-- each station is one line; the paragraph behind it opens on click -->
	<div class="stations">
		{#each ROUND as st (st.t)}
			<details class="stn" class:lit={st.lit} id="t{st.t.replace(':', '')}">
				<summary>
					<span class="when"><span class="mono">{st.t}</span><span class="eng">{st.label}</span></span>
					<span class="big">{st.line}</span>
					{#if st.t === '22:00'}<span class="noise" aria-hidden="true">{#each noise as k, i (i)}<i class:k></i>{/each}</span>{/if}
					<span class="more">More</span>
				</summary>
				<p class="lede">{st.more}</p>
			</details>
		{/each}
	</div>

	<section class="clocksband" id="clocks" aria-labelledby="ck">
		<h2 id="ck" class="sr">The clocks it keeps</h2>
		<div class="regs">
			{#each REGIMES as r (r.name)}
				<div class="reg {r.tone}" title={r.more}>
					<span class="eng">{r.name}</span>
					<b class="mono">{r.clock}</b>
					<span class="faint">{r.what}</span>
				</div>
			{/each}
		</div>
	</section>
</main>
<footer class="fin">
	<div class="horizon" aria-hidden="true"></div>
	<p class="eng fk">08:00 · First light</p>
	<a class="btn btn-lg preset-filled-primary-500" href="/sign-in">Take the watch →</a>
	<p class="fine" title="For mid-size companies with operations in the EU, US and Australia. Designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting; it does not submit anything without a person.">Sample company and data</p>
</footer>

<style>
	.lhead {
		display: grid;
		grid-template-columns: auto minmax(0, 560px) auto;
		align-items: center;
		justify-content: space-between;
		gap: 24px;
		padding: 14px var(--gut);
		border-bottom: 1px solid var(--rule);
		background: linear-gradient(180deg, color-mix(in srgb, var(--acc) 7%, var(--bg)), var(--bg));
	}
	.brand span {
		letter-spacing: 0.24em;
	}
	.stage {
		display: grid;
		grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
		gap: 40px;
		padding: 26px var(--gut) 40px;
		min-height: calc(100vh - 82px);
		align-items: start;
	}
	.dialcol {
		display: grid;
		gap: 10px;
		justify-items: center;
		position: sticky;
		top: 12px;
	}
	.ticker {
		list-style: none;
		margin: 0;
		padding: 0;
		width: min(100%, 620px);
		display: grid;
		gap: 3px;
		min-height: 66px;
	}
	.ticker li {
		display: grid;
		grid-template-columns: 70px 14px minmax(0, 1fr);
		gap: 8px;
		font: 400 12.5px/1.45 var(--code);
		color: var(--faint);
		white-space: nowrap;
	}
	.ticker .x {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.ticker li.odd {
		color: var(--soon);
	}
	.logcol {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 16px;
		padding-top: 18px;
		max-width: 660px;
	}
	.kicker {
		margin: 0;
		font-size: 13.5px;
		color: var(--dim);
		display: flex;
		align-items: center;
		gap: 9px;
	}
	.rec {
		color: var(--acc);
		animation: nj-pulse 2.4s ease-out infinite;
		border-radius: 50%;
		font-size: 10px;
	}
	.chrono {
		display: flex;
		align-items: baseline;
		gap: 18px;
		flex-wrap: wrap;
	}
	.bigclock {
		font-size: 104px;
		line-height: 0.9;
		font-weight: 300;
		color: var(--ink);
		letter-spacing: -0.04em;
		transition: font-size 0.5s var(--ease-tick);
	}
	.bigclock .sec {
		color: var(--acc);
	}
	.wt {
		font-size: 15px;
		color: var(--acc-hi);
	}
	.done .bigclock {
		font-size: 60px;
		color: var(--dim);
	}
	.lamps2 {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		min-height: 0;
	}
	.lamps2 .clk {
		display: grid;
		grid-template-columns: auto auto;
		gap: 5px 14px;
		padding: 9px 12px;
		background: var(--plate);
		box-shadow:
			inset 0 0 0 1px var(--acc-line),
			0 0 22px -10px var(--acc-glow);
		border-top: 2px solid var(--acc);
	}
	.lamps2 .eng {
		font-size: 12.5px;
		color: var(--ink);
		grid-column: 1 / -1;
	}
	.lamps2 .mono {
		font-size: 18px;
		color: var(--ink);
	}
	.lamps2 .st {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--ink-2);
		align-self: center;
	}
	.nightlog {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}
	.nightlog li {
		display: grid;
		grid-template-columns: 78px minmax(0, 1fr);
		gap: 12px;
		padding: 7px 0;
		border-top: 1px solid var(--rule);
		font-size: 16px;
		line-height: 1.45;
		color: var(--ink-2);
		transition: font-size 0.4s var(--ease-tick);
	}
	.nightlog li.key {
		color: var(--ink);
	}
	.nightlog li.now {
		font-size: 22px;
		font-weight: 600;
		line-height: 1.3;
		color: var(--ink);
	}
	.nightlog .ts {
		font-size: 13px;
		padding-top: 4px;
		color: var(--faint);
	}
	.done .nightlog li {
		font-size: 14.5px;
		padding: 5px 0;
	}
	.askbox {
		padding: 18px;
		background-color: var(--plate);
		--tk: var(--acc);
		--tl: 14px;
		display: grid;
		gap: 10px;
	}
	.askbox .lbl {
		margin: 0;
		font-size: 12.5px;
		color: var(--person);
	}
	.askbox .q {
		margin: 0 0 4px;
		font: 800 28px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.askbox .try {
		margin: 0;
		font-size: 13px;
		color: var(--faint);
	}
	.final h1 {
		margin: 6px 0 16px;
		font: 800 92px/0.86 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.01em;
		color: var(--ink);
	}
	.final p {
		margin: 0 0 22px;
		font-size: 17.5px;
		line-height: 1.55;
		color: var(--ink-2);
		max-width: 58ch;
	}
	.ctas {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
	}
	.skip {
		align-self: start;
	}
	.round {
		padding: 40px var(--gut) 56px;
		border-top: 1px solid var(--rule);
	}
	.stations {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 6px;
		padding: 6px;
		background: var(--sunk);
		border: 1px solid var(--rule);
	}
	.stn {
		padding: 22px 22px 24px;
		background: linear-gradient(180deg, var(--raised), var(--plate));
		box-shadow: inset 0 0 0 1px var(--rule);
		scroll-margin-top: 20px;
	}
	.stn.lit {
		background: radial-gradient(130% 90% at 50% 0%, color-mix(in srgb, var(--acc) 22%, var(--plate)), var(--plate) 70%);
		box-shadow:
			inset 0 0 0 1px var(--acc-line),
			0 0 30px -12px var(--acc-glow);
	}
	.when {
		display: flex;
		align-items: baseline;
		gap: 12px;
	}
	.when .mono {
		font-size: 34px;
		color: var(--ink);
	}
	.when .eng {
		font-size: 13px;
		color: var(--acc-hi);
	}
	.stn summary {
		display: grid;
		gap: 12px;
		cursor: pointer;
		list-style: none;
	}
	.stn summary::-webkit-details-marker {
		display: none;
	}
	.stn .big {
		font: 800 30px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--ink);
	}
	.stn .more {
		width: max-content;
		font: 600 13px/1 var(--ui);
		color: var(--dim);
		border-bottom: 1px solid var(--rule-2);
		padding-bottom: 2px;
	}
	.stn .more::after {
		content: ' ▸';
	}
	.stn[open] .more::after {
		content: ' ▾';
	}
	.stn summary:hover .more {
		color: var(--ink);
	}
	.stn .lede {
		margin: 14px 0 0;
		font-size: 15px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	.noise {
		display: grid;
		grid-template-columns: repeat(30, 1fr);
		gap: 2px;
		margin: 2px 0;
	}
	.noise i {
		height: 9px;
		background: var(--rule-2);
		opacity: 0.6;
	}
	.noise i.k {
		background: var(--soon);
		opacity: 1;
	}
	.clocksband {
		margin-top: 6px;
		scroll-margin-top: 20px;
	}
	.regs {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 6px;
	}
	.reg {
		display: grid;
		gap: 10px;
		padding: 18px 22px 20px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-left: 4px solid var(--acc);
		cursor: help;
	}
	.reg.h-person {
		border-left-color: var(--person);
	}
	.reg.h-soon {
		border-left-color: var(--soon);
	}
	.reg .eng {
		font-size: 15px;
		color: var(--ink-2);
	}
	.reg b {
		font: 400 34px/1 var(--code);
		color: var(--ink);
	}
	.reg .faint {
		font-size: 13.5px;
	}
	.fin {
		position: relative;
		overflow: hidden;
		padding: 120px var(--gut) 56px;
		background: #f3eee8;
		color: #1d1311;
		display: grid;
		justify-items: start;
		gap: 18px;
	}
	.horizon {
		position: absolute;
		inset: 0 0 auto 0;
		height: 110px;
		background: linear-gradient(180deg, var(--bg) 0%, #5a160d 30%, #b84a2a 62%, #e9b49c 85%, #f3eee8 100%);
	}
	.fin > :not(.horizon) {
		position: relative;
	}
	.fk {
		margin: 0;
		font-size: 13px;
		color: #8a1a12;
	}
	.fin .btn {
		background: #b3241a;
		color: #fff;
		box-shadow: none;
	}
	.fine {
		margin: 6px 0 0;
		max-width: 90ch;
		font-size: 13.5px;
		color: #5a4740;
	}
	@media (max-width: 1180px) {
		.stage {
			grid-template-columns: minmax(0, 1fr);
		}
		.dialcol {
			position: static;
			max-width: 560px;
			justify-self: center;
			width: 100%;
		}
		.stations {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.final h1 {
			font-size: 88px;
		}
	}
	@media (max-width: 860px) {
		.lhead {
			grid-template-columns: auto auto;
		}
		.lp {
			grid-column: 1 / -1;
			grid-row: 2;
		}
	}
	@media (max-width: 760px) {
		.stage {
			padding: 16px var(--gut) 28px;
			gap: 18px;
			min-height: 0;
		}
		.bigclock {
			font-size: 64px;
		}
		.done .bigclock {
			font-size: 44px;
		}
		.nightlog li {
			grid-template-columns: 64px minmax(0, 1fr);
			font-size: 14.5px;
		}
		.nightlog li.now {
			font-size: 18px;
		}
		.final h1 {
			font-size: 58px;
		}
		.stations {
			grid-template-columns: minmax(0, 1fr);
		}
		.regs {
			grid-template-columns: minmax(0, 1fr);
		}
			.ticker li {
			grid-template-columns: 62px 12px minmax(0, 1fr);
			font-size: 11.5px;
		}
		.lp :global(.lbl) {
			font-size: 12px;
			letter-spacing: 0.1em;
		}
		.lp :global(.win) {
			padding: 0 4px;
		}
	}
</style>
