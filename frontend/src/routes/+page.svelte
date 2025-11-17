<script lang="ts">
	// "Replay the night" (DESIGN.md §4): the landing is the console replaying one night at Halden Logistics. It is
	// time-driven, not scroll-driven; the headline lands as the last log entry.
	import { onMount, onDestroy } from 'svelte';
	import { crossfade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { Spring, Tween, prefersReducedMotion } from 'svelte/motion';
	import { backOut, cubicInOut } from 'svelte/easing';
	import Logo from '$lib/components/shell/Logo.svelte';
	import Tick from '$lib/components/shell/Tick.svelte';
	import ClockCell from '$lib/components/shell/ClockCell.svelte';
	import HoldButton from '$lib/components/console/HoldButton.svelte';
	import type { Deadline } from '$lib/api/types';
	import { nightAt, nightOfLabel, now as clockNow } from '$lib/time/clock';
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
	const STILL_LOG: [string, string][] = [
		['03:12:04', 'Joy in accounts payable signs in from a country she has never signed in from.'],
		['03:12:31', 'Three alerts become one incident.'],
		['03:12:58', 'Triage: likely real, 94 in 100. Enrichment: the sender’s domain is two days old.'],
		['03:13:40', 'Likely reportable under NIS2. The 24-hour clock counts from now; the early warning is drafted for the CISO.']
	];

	interface Row { id: number; at: Date; text: string; src: string; odd: boolean }
	interface Line { id: number; ts: string; text: string; key: boolean }

	const [send, receive] = crossfade({ duration: 560, easing: cubicInOut, fallback: () => ({ duration: 120, css: (t) => `opacity:${t}` }) });
	let reduced = $state(false);
	let vStart = nightAt(3, 11, 50);
	let vNow = $state(vStart);
	let speed = 6;
	let rows = $state<Row[]>([]);
	let log = $state<Line[]>([]);
	let alerts = $state<Row[]>([]);
	let coalesced = $state(false);
	let mini = $state(0); // mini baton steps shown (0..4)
	let clocks = $state<Deadline[]>([]);
	let asking = $state(false);
	let authorised = $state<{ by: string; at: Date } | null>(null);
	let done = $state(false);
	let dropReady = false;
	let rid = 0;
	let generation = $state(0);
	let lid = 0;
	let timers: ReturnType<typeof setTimeout>[] = [];
	let tickTimer: ReturnType<typeof setInterval> | null = null;
	let riverTimer: ReturnType<typeof setInterval> | null = null;
	let hold = $state<HoldButton | null>(null);
	let userHeld = false;
	const strip = new Spring(0.94, { stiffness: 0.15, damping: 0.55 });
	const bead = new Tween(0, { duration: 900, easing: cubicInOut });
	let beadPath = $state('');

	const fmt = (d: Date) => d.toLocaleTimeString('en-GB', { timeZone: 'Europe/Brussels', hour: '2-digit', minute: '2-digit', second: '2-digit' });
	const nightOf = nightOfLabel(clockNow());

	function addRow(text: string, src: string, odd = false) {
		const r = { id: ++rid, at: new Date(vNow), text, src, odd };
		rows = [r, ...rows].slice(0, 24);
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
		return { id, incidentId: 'replay', incidentReference: 'replay', regime: 'nis2', regimeLabel: 'NIS2', noticeType: id, noticeLabel: label, jurisdiction: 'Belgium', startsAt: start.toISOString(), dueAt: new Date(+start + hours * 3600e3).toISOString(), dueAtLocal: null, businessDays: null, state: 'on_track', remainingSeconds: null, noticeId: null, basis: 'Halden Logistics NV, postal and courier services, Belgium: invoices redirected, possible considerable financial loss' };
	}
	function dropClocks() {
		dropReady = !reduced;
		const start = new Date(vNow);
		clocks = [mkClock('ew', 'early warning', 24, start), mkClock('nf', 'full notification', 72, start)];
		ui.announce('NIS2 clock started');
	}
	function drop(_n: Element, { delay = 0 } = {}) {
		if (!dropReady) return { duration: 0 };
		return { delay, duration: 620, easing: backOut, css: (t: number, u: number) => `transform: translateY(${-56 * u}px); opacity:${Math.min(1, t / 0.6)}` };
	}
	function miniStep(n: number) {
		mini = n;
		if (reduced || n < 2) return;
		const p = MINI_PATHS[n - 2];
		if (!p) return;
		beadPath = p;
		bead.set(0, { duration: 0 }).then(() => bead.set(1));
	}
	const LX = [15, 45, 75, 105];
	const MY = [9, 55, 101, 147];
	const MINI_PATHS = [1, 2, 3].map((i) => `M${LX[i - 1]},${MY[i - 1]} C${LX[i - 1]},${(MY[i - 1]! + MY[i]!) / 2} ${LX[i]},${(MY[i - 1]! + MY[i]!) / 2} ${LX[i]},${MY[i]}`);

	async function confirmHold() {
		authorised = { by: userHeld ? 'you' : 'Marcus Bell, on call', at: new Date(vNow) };
		mini = 4;
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
			coalesced = true;
			strip.set(1);
			say(fmt(vNow), 'Three alerts become one incident.', true);
		});
		at(6200, () => {
			miniStep(1);
			say(fmt(vNow), 'Triage says it is likely real and shows why. Enrichment checks who is behind it. Response drafts a plan.', true);
		});
		at(7300, () => miniStep(2));
		at(8400, () => miniStep(3));
		at(9800, () => {
			dropClocks();
			say(fmt(vNow), 'Likely reportable under NIS2. The 24-hour clock counts from now, and the early warning is drafted for the CISO.', true);
		});
		at(12000, () => {
			// the night moves on while the colleagues finish: the plan is ready at 03:40
			vNow = nightAt(3, 40, 50);
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
		dropReady = false;
		vNow = nightAt(3, 9, 30);
		rows = [];
		for (let i = 0; i < 12; i++) {
			vNow = new Date(+vNow + 13000);
			addRow(...ROUTINE[i % ROUTINE.length]!);
		}
		alerts = [];
		coalesced = true;
		strip.set(1, { instant: true });
		mini = 4;
		vNow = nightAt(3, 13, 40);
		dropClocks();
		log = STILL_LOG.map(([ts, text]) => ({ id: ++lid, ts, text, key: true }));
		vNow = nightAt(3, 41, 0);
		asking = true;
		authorised = { by: 'Marcus Bell, on call', at: new Date(vNow) };
		log = [...log, { id: ++lid, ts: fmt(vNow), text: 'Marcus, on call, held to authorise. Account turned off and Joy signed out everywhere.', key: true }];
		done = true;
		tickTimer = setInterval(() => (vNow = new Date(+vNow + 1000)), 1000);
	}
	function replay() {
		clearAll();
		generation++;
		vNow = vStart;
		rows = [];
		log = [];
		alerts = [];
		coalesced = false;
		strip.set(0.94, { instant: true });
		mini = 0;
		clocks = [];
		asking = false;
		authorised = null;
		done = false;
		userHeld = false;
		speed = 6;
		dropReady = false;
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

	const hm = $derived(fmt(vNow).slice(0, 6));
	const ss = $derived(fmt(vNow).slice(6));
	const noise = Array.from({ length: 120 }, (_, i) => i === 37 || i === 91);
</script>

<svelte:head>
	<title>Nightjar SOC · Someone is always on watch</title>
</svelte:head>

<a class="skip-link" href="#log">Skip to the shift log</a>
<section class="stage" class:done aria-label="Replay of one night at Halden Logistics">
	<header class="clockbar">
		<a class="brand" href="/" aria-label="Nightjar SOC"><Logo /><b>nightjar</b><i>soc</i></a>
		<div class="clocks" aria-live="polite" aria-label="Regulator clocks">
			{#if !clocks.length}<div class="idle"><i></i>No regulator clocks running</div>{/if}
			{#each clocks as d, i (d.id)}
				<div class="cw" in:drop={{ delay: i * 120 }} animate:flip={{ duration: 300 }}><ClockCell {d} now={vNow} href="/sign-in" fast={!done} /></div>
			{/each}
		</div>
		<div class="right">
			<button class="cta gh hide-s" type="button" onclick={replay}>↺ Replay the night</button>
			<a class="cta pri" href="/sign-in">Take the watch →</a>
		</div>
	</header>

	<div class="floor">
		<aside class="col l" aria-label="Events coming in">
			<div class="ph"><b>events</b><span>Halden Logistics</span><span class="rate teal">1,284/min</span></div>
			<div class="river">
				<!-- a replay starts a fresh river: the old rows go at once instead of 24 crossfades -->
				{#key generation}
					{#each rows as r (r.id)}
						<div class="ev" class:odd={r.odd} in:fly={{ y: -6, duration: reduced ? 0 : 320 }} out:send={{ key: r.id }}>
							<span class="tm mono">{r.odd ? '◇ ' : ''}{fmt(r.at)}</span><span>{r.text}<br /><span class="src mono">{r.src}</span></span>
						</div>
					{/each}
				{/key}
			</div>
		</aside>

		<div class="center">
			<div class="eyebrow mono"><span class="rec" aria-hidden="true">●</span> replay · Halden Logistics · the night of {nightOf}</div>
			<div class="bigclock mono" aria-hidden="true"><Tick value={hm} fast={!done} /><span class="sec"><Tick value={ss} fast={!done} /></span></div>
			<ol class="nightlog" aria-live="polite">
				{#each log as l, i (l.id)}
					<li class:now={i === log.length - 1 && !done} class:key={l.key} in:fly={{ y: 6, duration: reduced ? 0 : 420 }}>
						<span class="ts mono">{l.ts === 'intro' ? fmt(vNow) : l.ts}</span><span>{l.text}</span>
					</li>
				{/each}
			</ol>
			{#if done}
				<div class="final" in:fly={{ y: 8, duration: reduced ? 0 : 600 }}>
					<h1>Someone is always<br />on watch.</h1>
					<p>Nightjar SOC watches your company’s security through the night, separates real threats from noise, investigates with three AI teammates, starts the regulator’s clock and drafts the notice. Nothing is switched off or locked out until a person says so.</p>
					<div class="ctas"><a class="cta pri big" href="/sign-in">Take the watch →</a><button class="cta gh big" type="button" onclick={replay}>↺ Replay the night</button></div>
					<p class="fine mono">For mid-size companies with operations in the EU, US and Australia · designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting</p>
				</div>
			{:else}
				<button class="skip mono" type="button" onclick={still}>skip replay →</button>
			{/if}
		</div>

		<aside class="col r" aria-label="What the watch is doing">
			<div class="ph"><b>incident</b><span>{coalesced ? 'Joy Okafor · mailbox' : 'none open'}</span></div>
			<div class="slot">
				{#if !coalesced}
					<div class="empty mono">Nothing needs a person.<br />The watch is quiet.</div>
				{:else}
					<div class="strip" style="transform: scale({strip.current})">
						<div class="top">
							<div class="k mono"><span class="sev high"><span class="bars">▮▮▮▯</span>High</span><span>{authorised ? 'contained' : 'investigating'}</span></div>
							<b>Account takeover after a phishing email</b>
							<small>Joy Okafor · accounts payable, Antwerp</small>
							<div class="chips">
								{#each alerts as a (a.id)}<span class="achip mono" in:receive={{ key: a.id }}>◇ {a.src}</span>{/each}
							</div>
						</div>
						<div class="mini">
							<div class="lanes mono" aria-hidden="true"><span>T</span><span>E</span><span>R</span><span class="p">P</span></div>
							<svg aria-hidden="true" viewBox="0 0 120 160">
								{#each LX as x (x)}<line class="g" x1={x} y1="0" x2={x} y2="160" />{/each}
								{#each MINI_PATHS as d, i (i)}<path class:on={mini >= i + 2} {d} />{/each}
								{#each LX as x, i (i)}<circle class:on={mini >= i + 1} class:p={i === 3} cx={x} cy={MY[i]} r="4.5" />{/each}
							</svg>
							{#if beadPath && bead.current > 0 && bead.current < 1}<i class="bead" style="offset-path: path('{beadPath}'); offset-distance:{bead.current * 100}%"></i>{/if}
							<div class="st" class:on={mini >= 1}><div>Likely real: 94 in 100<small class="mono">Triage · new device, new rule</small></div></div>
							<div class="st" class:on={mini >= 2}><div>Sender domain is 2 days old<small class="mono">Enrichment · mail, domain records</small></div></div>
							<div class="st" class:on={mini >= 3}><div>Turn off the account?<small class="mono">Response · hands to a person</small></div></div>
							<div class="st" class:on={mini >= 3}><div>{authorised ? `${authorised.by === 'you' ? 'You' : 'Marcus'} held to authorise` : 'Waiting for a person'}<small class="mono">{authorised ? `People · account turned off · ${fmt(authorised.at)}` : 'People · nothing happens until then'}</small></div></div>
						</div>
					</div>
				{/if}
				{#if asking}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="askbox" in:fly={{ y: 6, duration: reduced ? 0 : 380 }} onpointerdown={() => (userHeld = true)} onkeydown={() => (userHeld = true)}>
						<p class="q">Turn off Joy’s account and sign her out everywhere<span class="mono">proposed by Response · reversible · logged</span></p>
						<HoldButton bind:this={hold} hint="try it · or hold Space" done={authorised && done ? { label: `Authorised · ${fmt(authorised.at)}`, sub: `by ${authorised.by}` } : null} onconfirm={confirmHold} />
						<div class="try mono">{authorised ? 'Logged with who, when and why.' : 'If you don’t, Marcus on call will.'}</div>
					</div>
				{/if}
			</div>
		</aside>
	</div>

	<nav class="statusline" aria-label="The night, hour by hour">
		<span class="live"><i class="pulse" aria-hidden="true"></i>replay</span>
		<a href="#t2200"><b>22:00</b>&nbsp;takes in everything</a>
		<a href="#t0130"><b>01:30</b>&nbsp;sorts real from noise</a>
		<a href="#t0312"><b>03:12</b>&nbsp;starts the regulator’s clock</a>
		<a href="#t0800"><b>08:00</b>&nbsp;briefs the CISO</a>
		<span class="end"><a href="/sign-in">sign in</a></span>
	</nav>
</section>

<main class="log" id="log">
	<article class="entry" id="t2200">
		<div class="when mono">22:00<small>the watch begins</small></div>
		<div>
			<h2>Every sign-in, email, laptop and cloud account, all night.</h2>
			<p class="lede">Nightjar SOC takes in your security events as they happen: sign-ins, mail, endpoints, firewalls and cloud services across every office. It writes each one in plain words and folds away the routine, so the few that matter stand out.</p>
			<div class="noise" aria-hidden="true">{#each noise as k, i (i)}<i class:k></i>{/each}</div>
			<p class="noise-cap mono">One minute at Halden Logistics: 1,284 events · 1,282 routine, folded · ◇ 2 worth a look</p>
		</div>
	</article>
	<article class="entry" id="t0130">
		<div class="when mono">01:30<small>a false-alarm storm</small></div>
		<div>
			<h2>146 alerts from one backup update. One note, not 146 pages.</h2>
			<p class="lede">Rules match patterns; patterns are noisy. Nightjar SOC groups related alerts into one incident, learns each person’s and laptop’s normal rhythm, and scores every alert for how likely it is to be real. It shows its reasons, so your team can disagree with it.</p>
		</div>
	</article>
	<article class="entry" id="t0312">
		<div class="when mono">03:12<small>reportable</small></div>
		<div>
			<h2>When an incident may be reportable, the clock is ready before you are.</h2>
			<p class="lede">Nightjar SOC checks each incident against where you operate and what happened, proposes which clocks apply, and drafts each notice with the required facts filled in from the evidence. A person decides, reviews and submits.</p>
			<div class="regs">
				<div class="reg"><b>NIS2 · EU</b><span class="w">Early warning, then full notification, then a final report</span><span class="t mono">24 h · 72 h<small>final report within a month</small></span></div>
				<div class="reg"><b>SEC Form 8-K · US</b><span class="w">Item 1.05, once you decide an incident is material</span><span class="t mono">4 business days<small>from the materiality decision</small></span></div>
				<div class="reg"><b>SOCI · Australia</b><span class="w">Report to the Australian Signals Directorate’s ACSC</span><span class="t mono">12 h · 72 h<small>significant · relevant impact</small></span></div>
			</div>
		</div>
	</article>
	<section class="dawn">
		<article class="entry" id="t0800">
			<div class="when mono">08:00<small>first light</small></div>
			<div class="lightcard">
				<div class="dl mono">Morning brief · for the CISO · 2-minute read</div>
				<h3>One incident from overnight is reportable. It is contained, and one decision is yours.</h3>
				<p>The night watch hands over in plain words: what happened, which clocks are running, what was approved and by whom, and the one thing that needs you today.</p>
			</div>
		</article>
	</section>
</main>
<footer class="fin">
	<h2>Take the watch at Halden Logistics.</h2>
	<a class="cta pri big" href="/sign-in">Take the watch →</a>
	<p class="fine mono">Sample company and data · Nightjar SOC is designed to support your reporting duties; it does not submit anything without a person.</p>
</footer>

<style>
	.stage {
		position: relative;
		height: 100vh;
		min-height: 760px;
		display: grid;
		grid-template-rows: 56px minmax(0, 1fr) 28px;
		overflow: hidden;
	}
	:global(:root[data-theme='light']) .stage {
		background: linear-gradient(180deg, #e4ebee, #f3f6f7);
	}
	.clockbar {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: stretch;
		height: 56px;
		border-bottom: 1px solid var(--line);
	}
	.clockbar .brand {
		padding: 0 20px;
		border-right: 1px solid var(--line);
	}
	.clocks {
		display: flex;
		min-width: 0;
		overflow: hidden;
	}
	.cw {
		display: flex;
		flex: none;
	}
	.idle {
		display: flex;
		align-items: center;
		padding: 0 18px;
		font: 400 12px/1 var(--mono);
		color: var(--faint);
		gap: 8px;
		white-space: nowrap;
	}
	.idle i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		border: 1px solid var(--faint);
	}
	.right {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 14px;
		border-left: 1px solid var(--line);
	}
	.cta {
		display: inline-flex;
		align-items: center;
		gap: 10px;
		height: 36px;
		padding: 0 14px;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		text-decoration: none;
		white-space: nowrap;
	}
	.cta.pri {
		background: var(--teal);
		color: var(--teal-ink);
	}
	.cta.pri:hover {
		filter: brightness(1.08);
	}
	.cta.gh {
		border: 1px solid var(--line-strong);
		color: var(--ink);
	}
	.cta.gh:hover {
		border-color: var(--muted);
	}
	.cta.big {
		height: 46px;
		padding: 0 18px;
		font-size: 15px;
		border-radius: 8px;
	}
	.floor {
		display: grid;
		grid-template-columns: 300px minmax(0, 1fr) 360px;
		min-height: 0;
	}
	.col {
		min-height: 0;
		position: relative;
		display: flex;
		flex-direction: column;
	}
	.col > .ph {
		height: 34px;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 0 16px;
		font: 400 12px/1 var(--mono);
		color: var(--faint);
		border-bottom: 1px solid var(--line);
		flex: none;
		white-space: nowrap;
	}
	.col > .ph b {
		color: var(--ink-2);
		font-weight: 500;
	}
	.rate {
		margin-left: auto;
	}
	.col.l {
		border-right: 1px solid var(--line);
	}
	.col.r {
		border-left: 1px solid var(--line);
	}
	.river {
		flex: 1;
		overflow: hidden;
		position: relative;
		-webkit-mask-image: linear-gradient(#000 55%, transparent);
		mask-image: linear-gradient(#000 55%, transparent);
	}
	.ev {
		display: grid;
		grid-template-columns: 62px 1fr;
		gap: 6px;
		padding: 7px 16px;
		font-size: 12.5px;
		line-height: 17px;
		color: var(--muted);
		border-left: 2px solid transparent;
	}
	.ev .tm {
		font-size: 11px;
		line-height: 17px;
		color: var(--faint);
		white-space: nowrap;
	}
	.ev .src {
		font-size: 11px;
		color: var(--faint);
	}
	.ev.odd {
		color: var(--ink);
		border-left-color: var(--amber);
		background: color-mix(in srgb, var(--amber) 6%, transparent);
	}
	.ev.odd .tm {
		color: var(--amber);
	}
	.center {
		display: flex;
		flex-direction: column;
		justify-content: center;
		align-items: flex-start;
		padding: 0 64px 36px 72px;
		position: relative;
		min-width: 0;
	}
	.center::before {
		content: '';
		position: absolute;
		left: 30%;
		top: 34%;
		width: 760px;
		height: 460px;
		transform: translate(-50%, -50%);
		background: radial-gradient(closest-side, color-mix(in srgb, var(--teal) 8%, transparent), transparent);
		pointer-events: none;
	}
	.eyebrow {
		font-size: 12.5px;
		color: var(--muted);
		display: flex;
		gap: 10px;
		align-items: center;
		position: relative;
	}
	.rec {
		color: var(--red);
	}
	.bigclock {
		margin: 14px 0 22px -6px;
		font-size: 112px;
		font-weight: 250;
		line-height: 1;
		letter-spacing: -0.045em;
		color: var(--ink);
		position: relative;
		transition:
			font-size 500ms cubic-bezier(0.2, 0.9, 0.1, 1),
			color 500ms;
		display: flex;
	}
	.bigclock .sec {
		color: var(--teal);
	}
	.nightlog {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 10px;
		max-width: 760px;
		position: relative;
	}
	.nightlog li {
		display: grid;
		grid-template-columns: 74px 1fr;
		gap: 14px;
		align-items: baseline;
		font: 400 16px/1.45 var(--sans);
		color: var(--muted);
		transition:
			color 400ms,
			font-size 400ms cubic-bezier(0.2, 0.9, 0.1, 1);
	}
	.nightlog .ts {
		font-size: 12.5px;
		line-height: 1;
		color: var(--faint);
	}
	.nightlog li.now {
		font-size: 24px;
		line-height: 1.35;
		color: var(--ink);
		letter-spacing: -0.012em;
	}
	.nightlog li.now .ts,
	.nightlog li.key .ts {
		color: var(--teal);
	}
	.final {
		position: relative;
		margin-top: 30px;
		padding-top: 30px;
		border-top: 1px solid var(--line);
		width: 100%;
		max-width: 960px;
	}
	.final h1 {
		margin: 0;
		font: 600 60px/1.02 var(--sans);
		letter-spacing: -0.035em;
	}
	.final p {
		max-width: 640px;
		margin: 16px 0 26px;
		font-size: 17.5px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	.ctas {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
	}
	.final .fine {
		margin: 22px 0 0;
		font-size: 12px;
		line-height: 1.6;
		color: var(--faint);
	}
	.done .bigclock {
		font-size: 64px;
		margin: 12px 0 18px -3px;
		color: var(--muted);
	}
	.done .nightlog li {
		font-size: 15px;
		line-height: 1.45;
		color: var(--muted);
	}
	.done .nightlog li.key {
		color: var(--ink-2);
	}
	.skip {
		position: absolute;
		bottom: 18px;
		right: 22px;
		font-size: 12px;
		color: var(--faint);
	}
	.skip:hover {
		color: var(--ink);
	}
	.slot {
		padding: 16px;
		display: grid;
		gap: 14px;
		align-content: start;
		flex: 1;
		min-height: 0;
	}
	.empty {
		border: 1px dashed var(--line-strong);
		border-radius: 8px;
		padding: 22px 16px;
		font-size: 12px;
		line-height: 1.5;
		color: var(--faint);
		text-align: center;
	}
	.strip {
		border: 1px solid var(--line-strong);
		border-radius: 8px;
		background: var(--pane);
		overflow: hidden;
		transform-origin: top center;
	}
	.strip .top {
		padding: 12px 14px 10px;
		border-bottom: 1px solid var(--line);
	}
	.strip .k {
		display: flex;
		gap: 10px;
		font-size: 11.5px;
		color: var(--muted);
	}
	.strip b {
		display: block;
		margin-top: 8px;
		font-size: 15px;
		font-weight: 600;
	}
	.strip small {
		display: block;
		margin-top: 3px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin-top: 8px;
	}
	.achip {
		font-size: 10.5px;
		color: var(--amber);
		border: 1px solid color-mix(in srgb, var(--amber) 40%, transparent);
		border-radius: 4px;
		padding: 2px 6px;
	}
	.mini {
		position: relative;
		padding: 8px 0 6px;
	}
	.mini .lanes {
		display: grid;
		grid-template-columns: repeat(4, 30px) 1fr;
		padding: 0 0 4px 8px;
		font-size: 10px;
		font-weight: 500;
	}
	.mini .lanes span {
		text-align: center;
		color: var(--teal);
	}
	.mini .lanes .p {
		color: var(--lav);
	}
	.mini svg {
		position: absolute;
		left: 8px;
		top: 22px;
		width: 120px;
		height: 160px;
		overflow: visible;
		pointer-events: none;
	}
	.mini .g {
		stroke: var(--line);
		stroke-dasharray: 2 4;
	}
	.mini path {
		fill: none;
		stroke: var(--teal);
		stroke-width: 1.6;
		opacity: 0;
		transition: opacity 360ms ease-out 380ms;
	}
	.mini path.on {
		opacity: 0.75;
	}
	.mini circle {
		fill: var(--pane);
		stroke: var(--teal);
		stroke-width: 1.6;
		opacity: 0;
		transition: opacity 200ms;
	}
	.mini circle.p {
		stroke: var(--lav);
	}
	.mini circle.on {
		opacity: 1;
	}
	.bead {
		position: absolute;
		left: 8px;
		top: 22px;
		width: 14px;
		height: 6px;
		border-radius: 3px;
		background: var(--teal);
		box-shadow: 0 0 10px var(--teal);
		offset-rotate: auto;
		pointer-events: none;
	}
	.mini .st {
		display: grid;
		grid-template-columns: 120px 1fr;
		min-height: 46px;
		padding-right: 12px;
		opacity: 0;
		transform: translateY(-4px);
		transition:
			opacity 300ms,
			transform 300ms cubic-bezier(0.2, 0.9, 0.1, 1);
	}
	.mini .st div {
		grid-column: 2;
		font-size: 12.5px;
		line-height: 16px;
		color: var(--ink);
	}
	.mini .st small {
		margin-top: 1px;
		font-size: 11px;
		line-height: 15px;
	}
	.mini .st.on {
		opacity: 1;
		transform: none;
	}
	.askbox .q {
		font-size: 14px;
		margin: 0 0 10px;
	}
	.askbox .q span {
		display: block;
		font-size: 11.5px;
		line-height: 1.5;
		color: var(--muted);
	}
	.try {
		margin-top: 8px;
		font-size: 11.5px;
		line-height: 1.4;
		color: var(--faint);
		text-align: center;
	}
	.statusline {
		height: 28px;
		display: flex;
		align-items: center;
		border-top: 1px solid var(--line);
		font: 400 11.5px/1 var(--mono);
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
	}
	.statusline > span,
	.statusline > a {
		padding: 0 14px;
		border-right: 1px solid var(--line);
		height: 100%;
		display: flex;
		align-items: center;
		gap: 7px;
		text-decoration: none;
		color: var(--muted);
	}
	.statusline a:hover {
		color: var(--ink);
	}
	.statusline a b {
		color: var(--ink-2);
		font-weight: 500;
	}
	.statusline .live {
		color: var(--teal);
	}
	.statusline .end {
		margin-left: auto;
		border-right: 0;
		border-left: 1px solid var(--line);
	}
	.log {
		border-top: 1px solid var(--line);
	}
	.entry {
		display: grid;
		grid-template-columns: 220px minmax(0, 1fr);
		max-width: 1240px;
		margin: 0 auto;
		padding: 88px 40px;
		border-bottom: 1px solid var(--line);
		gap: 40px;
	}
	.when {
		font-size: 54px;
		font-weight: 300;
		line-height: 1;
		letter-spacing: -0.03em;
		color: var(--muted);
		position: sticky;
		top: 40px;
		align-self: start;
	}
	.when small {
		display: block;
		margin-top: 10px;
		font-size: 12px;
		letter-spacing: 0;
		color: var(--teal);
	}
	.entry h2 {
		margin: 0 0 14px;
		font: 600 36px/1.1 var(--sans);
		letter-spacing: -0.025em;
		max-width: 720px;
	}
	.entry .lede {
		margin: 0 0 30px;
		max-width: 640px;
		font-size: 17px;
		line-height: 1.6;
		color: var(--ink-2);
	}
	.regs {
		display: grid;
		border-top: 1px solid var(--line);
		max-width: 860px;
	}
	.reg {
		display: grid;
		grid-template-columns: 200px 1fr 220px;
		gap: 20px;
		padding: 18px 0;
		border-bottom: 1px solid var(--line);
		align-items: baseline;
	}
	.reg b {
		font-weight: 600;
	}
	.reg .w {
		font-size: 14px;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.reg .t {
		font-size: 22px;
		color: var(--ink);
		text-align: right;
	}
	.reg .t small {
		display: block;
		margin-top: 6px;
		font-size: 11.5px;
		color: var(--muted);
	}
	.noise {
		display: grid;
		grid-template-columns: repeat(40, 1fr);
		gap: 3px;
		max-width: 860px;
	}
	.noise i {
		height: 18px;
		border-radius: 2px;
		background: var(--line-strong);
	}
	.noise i.k {
		background: var(--amber);
	}
	.noise-cap {
		margin-top: 14px;
		font-size: 12.5px;
		line-height: 1.6;
		color: var(--muted);
	}
	.dawn {
		background: linear-gradient(180deg, var(--bg), #0f2830 30%, #cfe7e1 75%, #eef5f2);
	}
	.dawn .entry {
		border-bottom: 0;
		padding-bottom: 120px;
	}
	.dawn .when,
	.dawn .when small {
		color: #9fe3d4;
	}
	.lightcard {
		background: #ffffff;
		color: #0b1a21;
		border-radius: 10px;
		padding: 34px 40px;
		max-width: 760px;
		box-shadow: 0 30px 80px rgba(8, 30, 40, 0.25);
	}
	.lightcard .dl {
		font-size: 12px;
		color: #4e6069;
	}
	.lightcard h3 {
		margin: 12px 0 10px;
		font: 500 28px/1.25 var(--sans);
		letter-spacing: -0.02em;
	}
	.lightcard p {
		margin: 0;
		font-size: 15.5px;
		line-height: 1.6;
		color: #2a3a42;
	}
	.fin {
		padding: 80px 40px 60px;
		text-align: center;
		background: #eef5f2;
		color: #0b1a21;
	}
	.fin h2 {
		margin: 0 0 22px;
		font: 600 40px/1.1 var(--sans);
		letter-spacing: -0.03em;
	}
	.fin .fine {
		margin-top: 26px;
		font-size: 12px;
		line-height: 1.6;
		color: #4e6069;
	}
	.fin .cta.pri {
		background: #0a7f6d;
		color: #fff;
	}
	@media (max-width: 1180px) {
		.floor {
			grid-template-columns: 260px minmax(0, 1fr) 320px;
		}
		.center {
			padding: 0 32px 36px 36px;
		}
		.bigclock {
			font-size: 88px;
		}
		.final h1 {
			font-size: 48px;
		}
	}
	@media (max-width: 900px) {
		.stage {
			height: auto;
			min-height: 100vh;
			overflow: visible;
			overflow-x: clip;
		}
		.log,
		.fin {
			overflow-x: clip;
		}
		.floor {
			grid-template-columns: minmax(0, 1fr);
		}
		.col.l {
			display: none;
		}
		.col.r {
			border-left: 0;
			border-top: 1px solid var(--line);
		}
		.center {
			padding: 40px 16px 56px;
			justify-content: flex-start;
		}
		.center::before {
			left: 50%;
			width: 120%;
		}
		.bigclock {
			font-size: 64px;
		}
		.done .bigclock {
			font-size: 44px;
		}
		.nightlog li {
			grid-template-columns: 64px 1fr;
			gap: 10px;
		}
		.nightlog li.now {
			font-size: 19px;
		}
		.final h1 {
			font-size: 40px;
		}
		.final p {
			font-size: 16px;
		}
		.clockbar .brand {
			padding: 0 12px;
		}
		.clockbar :global(.brand b),
		.clockbar :global(.brand i),
		.hide-s {
			display: none;
		}
		.clocks {
			overflow-x: auto;
			scrollbar-width: none;
		}
		.right {
			padding: 0 10px;
		}
		.cta {
			padding: 0 12px;
			font-size: 13px;
		}
		.statusline {
			overflow-x: auto;
			scrollbar-width: none;
		}
		.entry {
			grid-template-columns: minmax(0, 1fr);
			padding: 56px 16px;
			gap: 16px;
		}
		.when {
			position: static;
			font-size: 40px;
		}
		.entry h2 {
			font-size: 28px;
		}
		.reg {
			grid-template-columns: 1fr;
			gap: 6px;
		}
		.reg .t {
			text-align: left;
			font-size: 18px;
		}
		.noise {
			grid-template-columns: repeat(20, 1fr);
		}
		.lightcard {
			padding: 24px 20px;
		}
		.lightcard h3 {
			font-size: 22px;
		}
		.fin {
			padding: 60px 16px 48px;
		}
		.fin h2 {
			font-size: 30px;
		}
	}
</style>
