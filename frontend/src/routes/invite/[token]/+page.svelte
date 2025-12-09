<script lang="ts">
	// Invitation acceptance: name and password, then straight onto the watch.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import StationHead from '$lib/components/shell/StationHead.svelte';
	import LampPanel from '$lib/components/shell/LampPanel.svelte';
	import { api, problemMessage, ApiError } from '$lib/api';
	import type { InvitationPreview } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { dueLabel } from '$lib/time/clock';

	let inv = $state<InvitationPreview | null>(null);
	let gone = $state('');
	let fullName = $state('');
	let password = $state('');
	let err = $state('');
	let fe = $state<Record<string, string>>({});
	let busy = $state(false);

	onMount(async () => {
		document.documentElement.dataset.theme = ui.theme;
		try {
			inv = await api.invitations.preview(page.params.token ?? '');
		} catch (x) {
			gone = problemMessage(x);
		}
	});
	async function accept(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		err = '';
		fe = {};
		try {
			await api.auth.csrf().catch(() => undefined);
			const me = await api.invitations.accept(page.params.token ?? '', { fullName: fullName.trim(), password });
			session.set(me);
			goto(session.home());
		} catch (x) {
			err = problemMessage(x);
			if (x instanceof ApiError) fe = x.fieldErrors();
		} finally {
			busy = false;
		}
	}
	const strong = $derived(password.length >= 12);
</script>

<svelte:head><title>Join the watch · Nightjar SOC</title></svelte:head>

<div class="page">
	<StationHead title="Join the watch" sub={inv ? `${inv.orgName} · an invitation` : 'An invitation'} home="/">
		{#snippet panel()}<LampPanel items={[{ n: 1, href: '/', label: 'The night' }, { n: 2, href: '/sign-in', label: 'Sign on' }, { n: 3, href: '#', label: 'Join', lit: true }]} cols={3} />{/snippet}
		{#snippet right()}<span class="eng faint">Sample company and data</span>{/snippet}
	</StationHead>
	<main class="center">
		<section class="tile ticks" aria-labelledby="t">
			<div class="ph"><h2 id="t">Your place on the watch bill</h2>{#if inv}<span class="sp">{inv.orgName}</span>{/if}</div>
			{#if gone}
				<div class="body"><p>{gone}</p><p class="faint">Ask the person who invited you for a new link.</p><a class="btn preset-outlined-surface-300-700" href="/sign-in">Go to sign-in</a></div>
			{:else if inv}
				<form class="body" onsubmit={accept} novalidate>
					<p class="lede">You are invited to <b>{inv.orgName}</b> as <b>{inv.roleLabel}</b>. The link expires {dueLabel(inv.expiresAt)}.</p>
					<label class="label"><span>Email</span><input class="input" value={inv.email} readonly /></label>
					<label class="label"><span>Your name</span><input class="input" bind:value={fullName} autocomplete="name" aria-invalid={!!fe.fullName} />{#if fe.fullName}<small class="ferr">{fe.fullName}</small>{/if}</label>
					<label class="label"><span>Choose a password</span><input class="input" type="password" bind:value={password} autocomplete="new-password" aria-describedby="pwh" aria-invalid={!!fe.password} />
						<small id="pwh" class:acc={strong} class:faint={!strong}>{strong ? '✓ 12 characters or more' : '○ at least 12 characters, not a common word, not your name'}</small>
						{#if fe.password}<small class="ferr">{fe.password}</small>{/if}
					</label>
					{#if err && !Object.keys(fe).length}<p class="ferr" role="alert">{err}</p>{/if}
					<button class="btn preset-filled-primary-500 btn-lg" type="submit" disabled={busy}>{busy ? 'Joining…' : 'Join and take the watch →'}</button>
				</form>
			{:else}
				<div class="body faint">Checking the invitation…</div>
			{/if}
		</section>
	</main>
</div>

<style>
	.page {
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr;
	}
	.center {
		display: block;
		padding: 34px var(--gut) 40px;
	}
	.tile {
		width: min(620px, 100%);
		background-color: var(--plate);
		--tk: var(--acc);
		--tl: 14px;
		position: relative;
	}
	.tile::before {
		content: '';
		position: absolute;
		inset: -1px -1px auto;
		height: 2px;
		background: var(--acc);
	}
	.ph {
		display: flex;
		align-items: baseline;
		gap: 12px;
		padding: 18px 22px 14px;
		border-bottom: 1px solid var(--rule);
		color: var(--dim);
		font-weight: 600;
	}
	.ph h2 {
		margin: 0;
		font: 800 30px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--ink);
	}
	.ph .sp {
		margin-left: auto;
	}
	.body {
		display: grid;
		gap: 14px;
		padding: 22px;
	}
	.body p {
		margin: 0;
	}
	.lede {
		font-size: 16px;
		color: var(--ink-2);
	}
	small {
		font-size: 13px;
	}
	@media (max-width: 760px) {
		.center {
			padding: 20px 12px 30px;
		}
	}
</style>
