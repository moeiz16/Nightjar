<script lang="ts">
	const MFA_WORDS: Record<string, string> = { push: 'phone approval', totp: 'authenticator code', fido2: 'security key', sms: 'text message code', none: 'password only' };
	// People and service accounts (DESIGN.md §6.13): a ledger of who signs in where, and what is unusual.
	import { page } from '$app/state';
	import Plate from '$lib/components/ui/Plate.svelte';
	import Choice from '$lib/components/ui/Choice.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { AnomalyDoc, IdentityDoc } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';

	let people = $state<IdentityDoc[]>([]);
	let anomalies = $state<AnomalyDoc[]>([]);
	let err = $state('');
	let kind = $state<'person' | 'service' | 'all'>('all');
	let q = $state('');
	$effect(() => {
		api.directory.identities().then((r) => (people = r)).catch((x) => (err = problemMessage(x)));
		if (session.can('ueba:read')) api.ueba.anomalies({ entityType: 'user' }).then((r) => (anomalies = r.items)).catch(() => undefined);
	});
	$effect(() => {
		const u = page.url.searchParams.get('u');
		if (u) q = u;
	});
	const SITE: Record<string, string> = { EU: 'Antwerp', US: 'Memphis', AU: 'Melbourne' };
	const shown = $derived(people.filter((p) => (kind === 'all' || (kind === 'service') === (p.kind === 'service')) && (!q || (p.displayName + p.username + p.department).toLowerCase().includes(q.toLowerCase()))));
	const unusual = (u: string) => anomalies.filter((a) => a.entityId === u);
	const persons = $derived(people.filter((p) => p.kind !== 'service').length);
</script>

<svelte:head><title>People · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Plate title="The register" sub="{persons} people · {people.length - persons} accounts" quiet bare>
		{#snippet actions()}
			<Choice label="Kind" bind:value={kind} options={[{ value: 'person', label: 'people' }, { value: 'service', label: 'accounts' }, { value: 'all', label: 'all' }]} />
			<label class="sr" for="iq">Filter people</label><input id="iq" class="input f" bind:value={q} placeholder="filter…" />
		{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>Person</th><th>Team</th><th title="Unusual in the last 14 days">Unusual</th><th>Access</th></tr></thead>
				<tbody>
					{#each shown as p (p.id)}
						{@const u = unusual(p.username)}
						<tr class={p.disabled ? 'h-person' : u.length ? 'h-soon' : ''}>
							<td title="{p.username} · {p.kind === 'service' ? 'machine sign-in only' : `signs in from ${p.usualCountries.join(', ') || '—'} with ${MFA_WORDS[p.mfa] ?? p.mfa}`}"><b class="nm">{p.displayName}</b></td>
							<td title={SITE[p.jurisdiction] ?? p.jurisdiction}>{p.department}</td>
							<td class="m">{#if u.length}<span class="soon" title={u[0]?.headline}>◇ {u.length}</span>{:else}—{/if}</td>
							<td class="m">{#if p.disabled}<span class="person">turned off</span>{:else if p.privileged}<span>◆ privileged</span>{:else}normal{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Plate>
</div>

<style>
	.tiles {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.f {
		height: 30px;
		min-height: 30px;
		width: 180px;
		max-width: 100%;
		font-size: 13px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.nm {
		color: var(--ink);
		font-weight: 700;
	}
	@media (max-width: 760px) {
		.tiles :global(.ph .seg) {
			display: none;
		}
	}
</style>
