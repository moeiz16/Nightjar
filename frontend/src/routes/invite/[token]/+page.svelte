<script lang="ts">
	// Invitation acceptance: name and password, then straight onto the watch.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import ShiftAxis from '$lib/components/shell/ShiftAxis.svelte';
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
	<ShiftAxis idle />
	<main class="center">
		<section class="tile" aria-labelledby="t">
			<div class="ph"><b id="t">join the watch</b>{#if inv}<span class="sp">{inv.orgName}</span>{/if}</div>
			{#if gone}
				<div class="body"><p>{gone}</p><p class="mono faint">Ask the person who invited you for a new link.</p><a class="btn preset-outlined-surface-300-700" href="/sign-in">Go to sign-in</a></div>
			{:else if inv}
				<form class="body" onsubmit={accept} novalidate>
					<p class="lede">You are invited to <b>{inv.orgName}</b> as <b>{inv.roleLabel}</b>. The link expires {dueLabel(inv.expiresAt)}.</p>
					<label class="label"><span>Email</span><input class="input" value={inv.email} readonly /></label>
					<label class="label"><span>Your name</span><input class="input" bind:value={fullName} autocomplete="name" aria-invalid={!!fe.fullName} />{#if fe.fullName}<small class="ferr">{fe.fullName}</small>{/if}</label>
					<label class="label"><span>Choose a password</span><input class="input" type="password" bind:value={password} autocomplete="new-password" aria-describedby="pwh" aria-invalid={!!fe.password} />
						<small id="pwh" class="mono" class:teal={strong} class:faint={!strong}>{strong ? '✓ 12 characters or more' : '○ at least 12 characters, not a common word, not your name'}</small>
						{#if fe.password}<small class="ferr">{fe.password}</small>{/if}
					</label>
					{#if err && !Object.keys(fe).length}<p class="ferr" role="alert">{err}</p>{/if}
					<button class="btn preset-filled-primary-700-300 btn-lg" type="submit" disabled={busy}>{busy ? 'Joining…' : 'Join and take the watch →'}</button>
				</form>
			{:else}
				<div class="body mono faint">Checking the invitation…</div>
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
		padding: 30px 16px 40px calc(var(--gut) + 6px);
	}
	.tile {
		width: min(560px, 100%);
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
		padding: 0 18px;
		border-bottom: 1px solid var(--line);
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
	}
	.ph b {
		color: var(--ink);
		font-weight: 500;
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
		font-size: 14px;
		color: var(--ink-2);
	}
	small {
		font-size: 11.5px;
	}
	@media (max-width: 760px) {
		.center {
			padding: 20px 12px 30px;
		}
	}
</style>
