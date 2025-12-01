<script lang="ts">
	// Sign on to the watch (DESIGN.md §0.2): the idle shift axis on top, then one left-aligned band. A shift starts
	// with a sign-on that says which watch you are joining; the demo roles are a Segment, not a grid of cards.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import ShiftAxis from '$lib/components/shell/ShiftAxis.svelte';
	import Choice from '$lib/components/ui/Choice.svelte';
	import { problemMessage, ApiError } from '$lib/api';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { BRUSSELS, longDate, timeOf } from '$lib/time/clock';

	const DEMO_PASSWORD = 'Nightjar-demo-2025!';
	const ROLES = [
		{ key: 'analyst', label: 'Analyst', name: 'Lukas Peeters', email: 'lukas.peeters@halden.example', note: 'hunts, tunes rules and reads every investigation' },
		{ key: 'responder', label: 'Responder', name: 'Marcus Bell', email: 'marcus.bell@halden.example', note: 'holds to authorise containment and works the night' },
		{ key: 'ciso', label: 'CISO', name: 'Ingrid Vos', email: 'ingrid.vos@halden.example', note: 'lands on the morning brief and owns the regulator decisions' },
		{ key: 'auditor', label: 'Auditor', name: 'Samira Haddad', email: 'samira.haddad@halden.example', note: 'reads everything and checks the sealed activity log' }
	] as const;
	type RoleKey = (typeof ROLES)[number]['key'];
	let role = $state<RoleKey>('responder');
	const picked = $derived(ROLES.find((r) => r.key === role)!);
	let email = $state('');
	let password = $state('');
	let err = $state('');
	let fieldErr = $state<Record<string, string>>({});
	let busy = $state<string | null>(null);

	onMount(async () => {
		document.documentElement.dataset.theme = ui.theme;
		const me = await session.load(true);
		if (me) goto(next(me.membership?.role === 'executive' ? '/brief' : '/watch'), { replaceState: true });
	});
	function next(fallback: string) {
		const n = page.url.searchParams.get('next');
		return n && n.startsWith('/') && !n.startsWith('//') ? n : fallback;
	}
	async function signIn(e: string, p: string, key: string) {
		busy = key;
		err = '';
		fieldErr = {};
		try {
			await session.login(e, p);
			goto(next(session.home()));
		} catch (x) {
			err = problemMessage(x);
			if (x instanceof ApiError) fieldErr = x.fieldErrors();
		} finally {
			busy = null;
		}
	}
	function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!email.trim() || !password) {
			err = 'Enter your work email and password.';
			return;
		}
		signIn(email.trim(), password, 'form');
	}
</script>

<svelte:head><title>Take the watch · Nightjar SOC</title></svelte:head>

<div class="page">
	<ShiftAxis idle />
	<main class="work">
		<section class="band band-in" aria-labelledby="t">
			<header class="rule mono"><span class="dash" aria-hidden="true">──</span><h1 id="t">sign on</h1><span>· Halden Logistics demo</span><span class="run" aria-hidden="true"></span><a class="lnk" href="/">↺ replay the night</a></header>
			<div class="body">
				<p class="when mono">You take the watch at <b>{timeOf(live.now, BRUSSELS)}</b> · {longDate(live.now, BRUSSELS)}</p>
				<p class="lede">Three teammates are on shift: Triage, Enrichment and Response. Nothing is switched off until a person holds to authorise it.</p>

				<div class="demo">
					<span class="mono faint step">1 · choose a role</span>
					<Choice label="Demo role" size="md" bind:value={role} options={ROLES.map((r) => ({ value: r.key, label: r.label }))} />
					<p class="who"><b>{picked.name}</b> · {picked.note}</p>
					<button class="btn preset-filled-primary-700-300 btn-lg" type="button" onclick={() => signIn(picked.email, DEMO_PASSWORD, picked.key)} disabled={!!busy}>
						{busy === picked.key ? 'Signing on…' : `Sign on as ${picked.label.toLowerCase() === 'ciso' ? 'the CISO' : picked.label.toLowerCase()} →`}
					</button>
				</div>

				<div class="own">
					<span class="mono faint step">or with your own account</span>
					<form onsubmit={submit} novalidate>
						<label class="label"><span>Work email</span><input class="input" type="email" autocomplete="username" bind:value={email} placeholder="marcus.bell@halden.example" aria-invalid={!!fieldErr.email} /></label>
						<label class="label"><span>Password</span><input class="input" type="password" autocomplete="current-password" bind:value={password} aria-invalid={!!fieldErr.password} /></label>
						<button class="btn preset-filled-surface-950-50" type="submit" disabled={!!busy}>{busy === 'form' ? 'Taking the watch…' : 'Take the watch →'}</button>
					</form>
					{#if err}<p class="ferr" role="alert">{err}</p>{/if}
				</div>
				<p class="fine mono">Forgot your password? Ask your administrator · Sample company and data</p>
			</div>
		</section>
	</main>
</div>

<style>
	.page {
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr;
		background: var(--bg);
	}
	.work {
		min-width: 0;
	}
	.band {
		background: var(--pane);
		border-bottom: 1px solid var(--line);
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
	}
	.rule h1 {
		margin: 0;
		font-size: 12px;
		font-weight: 600;
		color: var(--ink);
	}
	.dash {
		color: var(--line-strong);
	}
	.run {
		flex: 1;
		height: 1px;
		background: repeating-linear-gradient(90deg, var(--line-strong) 0 6px, transparent 6px 9px);
	}
	.body {
		padding: 30px 0 34px calc(var(--gut) + 6px);
		max-width: calc(var(--gut) + 760px);
		display: grid;
		gap: 22px;
	}
	.when {
		margin: 0;
		font-size: 22px;
		line-height: 1.25;
		color: var(--ink-2);
	}
	.when b {
		color: var(--teal);
		font-weight: 600;
	}
	.lede {
		margin: -8px 0 0;
		max-width: 60ch;
		color: var(--ink-2);
		font-size: 15px;
		line-height: 1.55;
	}
	.demo,
	.own {
		display: grid;
		gap: 12px;
		justify-items: start;
		padding-top: 18px;
		border-top: 1px solid var(--line);
	}
	.step {
		font-size: 11.5px;
	}
	.who {
		margin: 0;
		font-size: 14px;
		color: var(--ink-2);
	}
	form {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
		gap: 12px;
		align-items: end;
		width: 100%;
	}
	form .btn {
		height: 38px;
	}
	.fine {
		margin: 0;
		font-size: 11.5px;
		color: var(--faint);
	}
	@media (max-width: 760px) {
		.body {
			padding: 22px 16px 28px;
		}
		.when {
			font-size: 18px;
		}
		form {
			grid-template-columns: minmax(0, 1fr);
		}
		.demo :global(.choice) {
			max-width: 100%;
			overflow-x: auto;
		}
		.rule .lnk {
			display: none;
		}
	}
</style>
