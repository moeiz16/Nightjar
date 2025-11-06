<script lang="ts">
	// Invitation acceptance: name and password, then straight onto the watch.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import ClockBar from '$lib/components/shell/ClockBar.svelte';
	import StatusLine from '$lib/components/shell/StatusLine.svelte';
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
	<ClockBar idle>{#snippet right()}<a class="kbtn" href="/sign-in">Sign in</a>{/snippet}</ClockBar>
	<main class="center">
		<section class="tile" aria-labelledby="t">
			<div class="ph"><b id="t">join the watch</b>{#if inv}<span class="sp">{inv.orgName}</span>{/if}</div>
			{#if gone}
				<div class="body"><p>{gone}</p><p class="mono faint">Ask the person who invited you for a new link.</p><a class="btn" href="/sign-in">Go to sign-in</a></div>
			{:else if inv}
				<form class="body" onsubmit={accept} novalidate>
					<p class="lede">You are invited to <b>{inv.orgName}</b> as <b>{inv.roleLabel}</b>. The link expires {dueLabel(inv.expiresAt)}.</p>
					<label class="field"><span>Email</span><input class="input" value={inv.email} readonly /></label>
					<label class="field"><span>Your name</span><input class="input" bind:value={fullName} autocomplete="name" aria-invalid={!!fe.fullName} />{#if fe.fullName}<small class="ferr">{fe.fullName}</small>{/if}</label>
					<label class="field"><span>Choose a password</span><input class="input" type="password" bind:value={password} autocomplete="new-password" aria-describedby="pwh" aria-invalid={!!fe.password} />
						<small id="pwh" class="mono" class:teal={strong} class:faint={!strong}>{strong ? '✓ 12 characters or more' : '○ at least 12 characters, not a common word, not your name'}</small>
						{#if fe.password}<small class="ferr">{fe.password}</small>{/if}
					</label>
					{#if err && !Object.keys(fe).length}<p class="ferr" role="alert">{err}</p>{/if}
					<button class="btn teal big" type="submit" disabled={busy}>{busy ? 'Joining…' : 'Join and take the watch →'}</button>
				</form>
			{:else}
				<div class="body mono faint">Checking the invitation…</div>
			{/if}
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
	.big {
		height: 42px;
		justify-content: center;
	}
	small {
		font-size: 11.5px;
	}
</style>
