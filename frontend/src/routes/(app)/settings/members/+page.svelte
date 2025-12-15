<script lang="ts">
	// Settings › members (DESIGN.md §6.14): who can authorise what, invitations, and the two-person rule.
	import Plate from '$lib/components/ui/Plate.svelte';
	import SettingsNav from '$lib/components/shell/SettingsNav.svelte';
	import { api, problemMessage, ApiError } from '$lib/api';
	import type { Invitation, Member, Role } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { whenShort, dueLabel } from '$lib/time/clock';

	let members = $state<Member[]>([]);
	let invites = $state<Invitation[]>([]);
	let err = $state('');
	let inviting = $state(false);
	let email = $state('');
	let role = $state<Role>('analyst');
	let fe = $state<Record<string, string>>({});
	let link = $state('');
	const canManage = $derived(session.can('members:manage'));
	async function load() {
		try {
			members = (await api.org.members()).items;
			if (canManage) {
				const r = await api.org.invitations();
				invites = Array.isArray(r) ? r : r.items;
			}
		} catch (x) {
			err = problemMessage(x);
		}
	}
	$effect(() => {
		load();
	});
	const CAN: Record<Role, string> = {
		responder: 'isolate · turn off · sign out · block',
		analyst: 'propose actions · tune rules · hunt',
		executive: 'submit notices · materiality · crown jewels (second person)',
		admin: 'runs the platform · cannot authorise actions',
		auditor: '—'
	};
	const ROLES: [Role, string][] = [['analyst', 'Security analyst'], ['responder', 'Incident responder'], ['executive', 'CISO / executive'], ['auditor', 'Auditor (read only)'], ['admin', 'Administrator']];
	async function invite(e: SubmitEvent) {
		e.preventDefault();
		fe = {};
		try {
			const inv = await api.org.invite(email.trim(), role);
			link = inv.acceptUrl ?? '';
			ui.toast(`Invitation for ${inv.email} created. The link is shown once.`);
			email = '';
			load();
		} catch (x) {
			if (x instanceof ApiError) fe = x.fieldErrors();
			if (!Object.keys(fe).length) ui.toast(problemMessage(x), 'bad');
		}
	}
	async function changeRole(m: Member, r: Role) {
		try {
			await api.org.setRole(m.id, r);
			ui.toast(`${m.user.fullName} is now ${ROLES.find((x) => x[0] === r)?.[1]}. Logged.`);
			load();
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
			load();
		}
	}
	async function revoke(i: Invitation) {
		try {
			await api.org.revokeInvitation(i.id);
			load();
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
</script>

<svelte:head><title>Members · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Plate title="The roster" sub="{members.length} members · {invites.length} invited" quiet bare>
		{#snippet actions()}{#if canManage}<button class="btn preset-filled-surface-950-50 btn-sm" type="button" onclick={() => (inviting = !inviting)}>Invite someone</button>{/if}{/snippet}
		<SettingsNav />
		{#if err}<p class="ferr pad">{err}</p>{/if}
		{#if inviting}
			<form class="inv" onsubmit={invite}>
				<label class="label"><span>Work email</span><input class="input" type="email" bind:value={email} required aria-invalid={!!fe.email} />{#if fe.email}<small class="ferr">{fe.email}</small>{/if}</label>
				<label class="label"><span>Role</span><select class="select" bind:value={role}>{#each ROLES as [r, l] (r)}<option value={r}>{l}</option>{/each}</select></label>
				<button class="btn preset-filled-primary-500" type="submit">Send invitation</button>
			</form>
			{#if link}<p class="link mono">Copy this link now (shown once): <a href={link}>{location.origin}{link}</a></p>{/if}
		{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>Name</th><th>Role</th><th>Can authorise</th><th>Joined</th></tr></thead>
				<tbody>
					{#each members as m (m.id)}
						<tr class={m.role === 'executive' || m.role === 'responder' ? 'h-person' : ''}>
							<td title={m.user.email}><b class="nm">{m.user.fullName}</b></td>
							<td>
								{#if canManage && m.user.id !== session.me?.user.id}
									<label class="sr" for="role-{m.id}">Role for {m.user.fullName}</label>
									<select id="role-{m.id}" class="select rs" value={m.role} onchange={(e) => changeRole(m, (e.currentTarget as HTMLSelectElement).value as Role)}>{#each ROLES as [r, l] (r)}<option value={r}>{l}</option>{/each}</select>
								{:else}{m.roleLabel}{/if}
							</td>
							<td class="m">{CAN[m.role]}</td>
							<td class="m">{whenShort(m.joinedAt)}</td>
						</tr>
					{/each}
					{#each invites as i (i.id)}
						<tr class="h-soon">
							<td><span class="faint">Invited</span> <span class="mono">{i.email}</span></td>
							<td>{i.roleLabel}</td>
							<td class="m">expires {dueLabel(i.expiresAt)}</td>
							<td class="m"><button class="lnk" type="button" onclick={() => revoke(i)}>Revoke</button></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<section class="rules">
			<div class="r"><span>Two-person rule for crown jewels and privileged accounts</span><span class="ok st" role="status">● on</span><small>a responder and the CISO, two different people</small></div>
			<div class="r"><span>Nobody authorises their own request</span><span class="ok st">● always</span><small>enforced on every action</small></div>
			<div class="r">
				<span>Confirm actions by typing the name instead of holding</span>
				<button class="tg" type="button" aria-pressed={ui.holdByTyping} onclick={() => ui.setHoldByTyping(!ui.holdByTyping)}>{ui.holdByTyping ? '● on' : '○ off'}</button>
				<small>your personal setting · for people who cannot hold a key</small>
			</div>
		</section>
	</Plate>
</div>

<style>
	.tiles {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.pad {
		padding: 0;
	}
	.inv {
		display: grid;
		grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) auto;
		gap: 12px;
		align-items: end;
		padding: 16px 18px;
		margin-bottom: 10px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-top: 2px solid var(--acc);
	}
	.link {
		margin: 0 0 10px;
		padding: 10px 14px;
		font-size: 13px;
		background: var(--sunk);
		overflow-wrap: anywhere;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.nm {
		display: block;
		color: var(--ink);
		font-weight: 700;
	}
	small {
		font-size: 12px;
		color: var(--faint);
	}
	.rs {
		height: 30px;
		min-height: 30px;
		font-size: 13px;
		width: auto;
	}
	.rules {
		margin-top: 22px;
		padding: 6px 18px 10px;
		display: grid;
		gap: 0;
		background: var(--plate);
		border: 1px solid var(--rule);
	}
	.rules .r {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 2px 16px;
		padding: 12px 0;
		border-top: 1px solid var(--rule);
		font-size: 14.5px;
	}
	.rules .r:first-child {
		border-top: 0;
	}
	.st {
		font-weight: 700;
		font-size: 13px;
	}
	.rules small {
		grid-column: 1;
	}
	.tg {
		color: var(--dim);
		font-size: 13px;
		font-weight: 700;
	}
	.tg[aria-pressed='true'] {
		color: var(--ok);
	}
	@media (max-width: 760px) {
		.inv {
			grid-template-columns: 1fr;
		}
	}
</style>
