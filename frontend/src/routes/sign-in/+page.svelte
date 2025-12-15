<script lang="ts">
	// Sign on to the watch (DESIGN.md §4): the watch bill. The idle dial shows the time on the left; on the right
	// the roster of demo roles (station, who, a two-word note, Take). Your own account is folded under it.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import StationHead from '$lib/components/shell/StationHead.svelte';
	import LampPanel from '$lib/components/shell/LampPanel.svelte';
	import WatchDial from '$lib/components/console/WatchDial.svelte';
	import { problemMessage, ApiError } from '$lib/api';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { BRUSSELS, longDate, timeOf } from '$lib/time/clock';
	import { shiftWindow, watchOf } from '$lib/util/shift';

	const DEMO_PASSWORD = 'Nightjar-demo-2025!';
	const ROLES = [
		{ key: 'analyst', label: 'Analyst', name: 'Lukas Peeters', email: 'lukas.peeters@halden.example', note: 'hunts and tunes rules' },
		{ key: 'responder', label: 'Responder', name: 'Marcus Bell', email: 'marcus.bell@halden.example', note: 'holds to authorise' },
		{ key: 'ciso', label: 'CISO', name: 'Ingrid Vos', email: 'ingrid.vos@halden.example', note: 'reads the brief' },
		{ key: 'auditor', label: 'Auditor', name: 'Samira Haddad', email: 'samira.haddad@halden.example', note: 'checks the log' }
	] as const;
	const win = $derived(shiftWindow(live.now, BRUSSELS));
	const watch = $derived(watchOf(live.now, BRUSSELS));
	const PUBLIC = [
		{ n: 1, href: '/', label: 'The night' },
		{ n: 2, href: '/#round', label: 'The round' },
		{ n: 3, href: '/#clocks', label: 'The clocks' },
		{ n: 4, href: '/sign-in', label: 'Sign on', lit: true }
	];
	let email = $state('');
	let password = $state('');
	let err = $state('');
	let fieldErr = $state<Record<string, string>>({});
	let busy = $state<string | null>(null);
	const roleWord = (r: (typeof ROLES)[number]) => (r.key === 'ciso' ? 'the CISO' : r.label.toLowerCase());

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
	<StationHead title="Sign on" sub="Demo company" home="/">
		{#snippet panel()}<LampPanel items={PUBLIC} />{/snippet}
		{#snippet right()}
			<div class="pr">
				<span class="t mono" title="{watch.label} watch · Antwerp">{timeOf(live.now, BRUSSELS)}</span>
				<a class="lnk" href="/">↺ Replay the night</a>
			</div>
		{/snippet}
	</StationHead>
	<main class="bill rack" id="main">
		<div class="dialw" aria-hidden="true">
			<WatchDial {win} incidents={[]} log={[]} tz={BRUSSELS} compact max={460} />
		</div>
		<section class="on" aria-labelledby="t">
			<h2 id="t" class="eng kicker" title="The watch bill · {longDate(live.now, BRUSSELS)}">Pick a station</h2>

			<table class="roster">
				<caption class="sr">Demo roles</caption>
				<thead class="sr"><tr><th scope="col">Station</th><th scope="col">On watch</th><th scope="col">Does</th><th scope="col">Sign on</th></tr></thead>
				<tbody>
					{#each ROLES as r (r.key)}
						<tr>
							<th scope="row" class="eng rl">{r.label}</th>
							<td class="nm">{r.name}</td>
							<td class="nt">{r.note}</td>
							<td class="go">
								<button class="btn btn-sm {r.key === 'responder' ? 'preset-filled-primary-500' : 'preset-outlined-surface-300-700'}" type="button" onclick={() => signIn(r.email, DEMO_PASSWORD, r.key)} disabled={!!busy} aria-label="Sign on as {roleWord(r)}">
									{busy === r.key ? 'Signing on…' : 'Take →'}
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>

			<details class="own" open={!!err}>
				<summary class="step">Your own account</summary>
				<form onsubmit={submit} novalidate>
					<label class="label"><span>Work email</span><input class="input" type="email" autocomplete="username" bind:value={email} placeholder="marcus.bell@halden.example" aria-invalid={!!fieldErr.email} /></label>
					<label class="label" title="Forgot it? Ask your administrator."><span>Password</span><input class="input" type="password" autocomplete="current-password" bind:value={password} aria-invalid={!!fieldErr.password} /></label>
					<button class="btn preset-filled-surface-950-50" type="submit" disabled={!!busy}>{busy === 'form' ? 'Taking the watch…' : 'Take the watch →'}</button>
				</form>
			</details>
			{#if err}<p class="ferr" role="alert">{err}</p>{/if}
		</section>
	</main>
</div>

<style>
	.page {
		min-height: 100vh;
		display: flex;
		flex-direction: column;
	}
	.pr {
		display: grid;
		gap: 8px;
		justify-items: start;
	}
	.pr .t {
		font-size: 40px;
		color: var(--ink);
	}
	.bill {
		display: grid;
		grid-template-columns: minmax(300px, 520px) minmax(0, 760px);
		gap: 56px;
		padding: 36px var(--gut) 56px;
		align-items: start;
		justify-content: center;
	}
	.dialw {
		position: sticky;
		top: 24px;
	}
	.on {
		align-self: center;
	}
	.kicker {
		margin: 0 0 10px;
		font-size: 15px;
		font-weight: 700;
		color: var(--acc-hi);
	}
	.roster {
		width: 100%;
		border-collapse: separate;
		border-spacing: 0 5px;
	}
	.roster tbody th,
	.roster td {
		background: var(--plate);
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
		padding: 12px 14px;
		text-align: left;
		vertical-align: middle;
	}
	.roster tbody th {
		border-left: 5px solid var(--rule-2);
		font-size: 17px;
		color: var(--ink);
		white-space: nowrap;
	}
	.roster tbody tr:nth-child(2) th {
		border-left-color: var(--acc);
	}
	.roster td:last-child {
		border-right: 1px solid var(--rule);
		text-align: right;
	}
	.nm {
		font-weight: 700;
		color: var(--ink);
		white-space: nowrap;
	}
	.nt {
		color: var(--dim);
		font-size: 14px;
	}
	.roster tbody tr:hover th,
	.roster tbody tr:hover td {
		background: var(--raised);
	}
	.own {
		margin-top: 24px;
		padding-top: 18px;
		border-top: 1px solid var(--rule);
	}
	.step {
		cursor: pointer;
		width: max-content;
		font: 600 14px/1 var(--ui);
		color: var(--dim);
		border-bottom: 1px solid var(--rule-2);
		padding-bottom: 2px;
		list-style: none;
	}
	.step::-webkit-details-marker {
		display: none;
	}
	.step::after {
		content: ' ▸';
	}
	.own[open] .step {
		margin-bottom: 14px;
	}
	.own[open] .step::after {
		content: ' ▾';
	}
	.step:hover {
		color: var(--ink);
	}
	.ferr {
		margin: 12px 0 0;
	}
	form {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
		gap: 12px;
		align-items: end;
	}
	@media (max-width: 1100px) {
		.bill {
			grid-template-columns: minmax(0, 1fr);
			gap: 24px;
		}
		.dialw {
			position: static;
			max-width: 380px;
			justify-self: center;
			width: 100%;
		}
	}
	@media (max-width: 760px) {
		.bill {
			padding: 20px var(--gut) 40px;
		}
		.dialw {
			display: none;
		}
		.roster tr {
			display: grid;
			grid-template-columns: 1fr auto;
			margin-bottom: 6px;
			background: var(--plate);
			border: 1px solid var(--rule);
			border-left: 5px solid var(--rule-2);
		}
		.roster tbody tr:nth-child(2) {
			border-left-color: var(--acc);
		}
		.roster tbody th,
		.roster td {
			border: 0 !important;
			padding: 8px 12px;
			background: none;
		}
		.roster .nm {
			grid-column: 1;
			padding-top: 0;
		}
		.roster .nt {
			grid-column: 1 / -1;
			padding-top: 0;
		}
		.roster .go {
			grid-row: 1;
			grid-column: 2;
		}
		form {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
