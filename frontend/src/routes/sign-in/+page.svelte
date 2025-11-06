<script lang="ts">
	// Sign-in (DESIGN.md §6.2): one centred tile on the night background; demo picker by role, not name.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import ClockBar from '$lib/components/shell/ClockBar.svelte';
	import StatusLine from '$lib/components/shell/StatusLine.svelte';
	import { problemMessage, ApiError } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';

	const DEMO_PASSWORD = 'Nightjar-demo-2025!';
	const ROLES = [
		{ key: 'analyst', label: 'Analyst', email: 'lukas.peeters@halden.example', note: 'hunts and tunes rules' },
		{ key: 'responder', label: 'Responder', email: 'marcus.bell@halden.example', note: 'authorises actions' },
		{ key: 'ciso', label: 'CISO', email: 'ingrid.vos@halden.example', note: 'lands on the brief' },
		{ key: 'auditor', label: 'Auditor', email: 'samira.haddad@halden.example', note: 'reads everything' }
	];
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
	<ClockBar idle>
		{#snippet right()}<a class="kbtn" href="/">↺ Replay the night</a>{/snippet}
	</ClockBar>
	<main class="center">
		<section class="tile" aria-labelledby="t">
			<div class="ph"><b id="t">take the watch</b><span class="sp">Halden Logistics demo</span></div>
			<form onsubmit={submit} novalidate>
				<label class="field"><span>Work email</span><input class="input" type="email" autocomplete="username" bind:value={email} placeholder="marcus.bell@halden.example" aria-invalid={!!fieldErr.email} /></label>
				<label class="field"><span>Password</span><input class="input" type="password" autocomplete="current-password" bind:value={password} aria-invalid={!!fieldErr.password} /></label>
				{#if err}<p class="ferr" role="alert">{err}</p>{/if}
				<button class="btn teal big" type="submit" disabled={!!busy}>{busy === 'form' ? 'Taking the watch…' : 'Take the watch →'}</button>
			</form>
			<div class="or mono"><span>or try the Halden Logistics demo as</span></div>
			<div class="roles">
				{#each ROLES as r (r.key)}
					<button class="role" type="button" onclick={() => signIn(r.email, DEMO_PASSWORD, r.key)} disabled={!!busy} aria-describedby="rn-{r.key}">
						<b class="mono">{busy === r.key ? '…' : r.label}</b><small id="rn-{r.key}">{r.note}</small>
					</button>
				{/each}
			</div>
			<p class="fine mono">Forgot your password? Ask your administrator · Sample company and data</p>
		</section>
	</main>
	<StatusLine idle />
</div>

<style>
	.page {
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr auto;
		background:
			radial-gradient(60% 50% at 50% 40%, color-mix(in srgb, var(--teal) 6%, transparent), transparent),
			var(--bg);
	}
	.center {
		display: grid;
		place-items: center;
		padding: 40px 16px;
	}
	.tile {
		width: min(460px, 100%);
		background: var(--pane);
		border: 1px solid var(--line);
		position: relative;
	}
	.tile::before {
		content: '';
		position: absolute;
		inset: -1px -1px auto;
		height: 2px;
		background: var(--teal);
	}
	.ph {
		height: 38px;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 0 18px;
		border-bottom: 1px solid var(--line);
		font: 400 12px/1 var(--mono);
		color: var(--muted);
	}
	.ph b {
		color: var(--ink);
		font-weight: 500;
	}
	.ph .sp {
		margin-left: auto;
	}
	form {
		display: grid;
		gap: 14px;
		padding: 22px 22px 6px;
	}
	.big {
		height: 42px;
		justify-content: center;
		font-size: 14.5px;
	}
	.or {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 18px 22px 10px;
		font-size: 11.5px;
		color: var(--faint);
	}
	.or::before,
	.or::after {
		content: '';
		flex: 1;
		height: 1px;
		background: var(--line);
	}
	.roles {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px;
		padding: 0 22px;
	}
	.role {
		display: grid;
		gap: 5px;
		padding: 10px 8px;
		border-radius: 6px;
		border: 1px solid var(--line-strong);
		text-align: center;
	}
	.role:hover:not(:disabled) {
		border-color: var(--teal-line);
		background: var(--teal-wash);
	}
	.role b {
		font-size: 12.5px;
		font-weight: 500;
	}
	.role small {
		font-size: 10.5px;
		color: var(--faint);
		line-height: 1.3;
	}
	.fine {
		padding: 18px 22px 20px;
		margin: 0;
		font-size: 11px;
		color: var(--faint);
	}
	@media (max-width: 480px) {
		.roles {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
