<script lang="ts">
	// Settings › regime contacts (DESIGN.md §6.14): which rules apply to Halden, who is the contact, who submits.
	import Pane from '$lib/components/shell/Pane.svelte';
	import SettingsNav from '$lib/components/shell/SettingsNav.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { Member, OrgProfile, Regime, RegimeContacts } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';

	let profile = $state<OrgProfile | null>(null);
	let contacts = $state<RegimeContacts>({});
	let members = $state<Member[]>([]);
	let err = $state('');
	let busy = $state(false);
	let dirty = $state(false);
	$effect(() => {
		Promise.all([api.settings.profile(), api.settings.regulators(), api.org.members()])
			.then(([p, c, m]) => ((profile = p), (contacts = c), (members = m.items)))
			.catch((x) => (err = problemMessage(x)));
	});
	const can = $derived(session.can('settings:manage'));
	const ORDER: Regime[] = ['nis2', 'sec_8k', 'soci', 'gdpr_art33'];
	const title: Record<Regime, string> = { nis2: 'NIS2', sec_8k: 'SEC Form 8-K', soci: 'SOCI', gdpr_art33: 'GDPR Art. 33' };
	function why(r: Regime): string {
		const e = profile?.entities ?? [];
		if (r === 'nis2') {
			const x = e.find((y) => y.nis2?.applies);
			return x ? `${x.nis2!.sector.toLowerCase()} sector · ${x.nis2!.entityType} entity · ${x.name}` : 'not in scope';
		}
		if (r === 'sec_8k') {
			const x = e.find((y) => y.sec?.registrant);
			return x ? `listed company${x.sec!.ticker ? ` (${x.sec!.ticker})` : ''} · disclosure committee of ${x.sec!.disclosureCommittee.length}` : 'not listed';
		}
		if (r === 'soci') {
			const x = e.find((y) => y.soci?.responsibleEntity);
			return x ? `${x.soci!.assetClasses.join(', ')} · ${x.soci!.assets.join(', ')}` : 'not in scope';
		}
		const x = e.find((y) => y.gdpr?.controller);
		return x ? `controller · lead authority ${x.gdpr!.leadAuthorityName}` : 'not a controller';
	}
	async function save() {
		busy = true;
		try {
			contacts = await api.settings.saveRegulators(contacts);
			dirty = false;
			ui.toast('Regime contacts saved. New notices use them.');
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Regime contacts · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Pane title="settings" sub="regime contacts" focus>
		{#snippet actions()}{#if can}<button class="btn sm pri" type="button" onclick={save} disabled={!dirty || busy}>{busy ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}</button>{:else}<span>read only</span>{/if}{/snippet}
		<SettingsNav />
		{#if err}<p class="ferr pad">{err}</p>{/if}
		<div class="body">
			<h1>Which rules apply to Halden Logistics?</h1>
			<p class="mono faint sub">from your company profile · nothing here files anything; it decides who is asked and who submits</p>
			{#each ORDER as r (r)}
				{@const c = contacts[r]}
				{#if c}
					<section class="reg" aria-labelledby="r-{r}">
						<div class="h">
							<h2 id="r-{r}"><span class="teal mono">●</span> {title[r]} · {c.jurisdiction}</h2>
							<span class="why">{why(r)}</span>
							<span class="mono faint auth">authority: {c.authorityName}</span>
						</div>
						<div class="grid">
							<label class="field"><span>owner (submits)</span>
								<select class="select" disabled={!can} value={c.internalOwner.userId} onchange={(e) => { const m = members.find((x) => x.user.id === (e.currentTarget as HTMLSelectElement).value); if (m) { c.internalOwner = { userId: m.user.id, name: m.user.fullName }; dirty = true; } }}>
									{#each members as m (m.id)}<option value={m.user.id}>{m.user.fullName} · {m.roleLabel}</option>{/each}
								</select>
							</label>
							{#each c.contacts as ct, i (i)}
								<label class="field"><span>{ct.role.toLowerCase()}</span><input class="input" bind:value={ct.name} oninput={() => (dirty = true)} disabled={!can} /></label>
								<label class="field"><span>phone</span><input class="input mono" bind:value={ct.phone} oninput={() => (dirty = true)} disabled={!can} /></label>
							{/each}
						</div>
						<p class="mono ok">✓ verified · {c.contacts.length} {c.contacts.length === 1 ? 'contact' : 'contacts'}</p>
					</section>
				{/if}
			{/each}
			<p class="add mono faint">○ Add a regime · ask your administrator to extend the company profile</p>
		</div>
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		min-height: 0;
		background: var(--line);
	}
	.pad {
		padding: 22px;
	}
	.body {
		padding: 20px 22px 30px;
		max-width: 1080px;
	}
	h1 {
		margin: 0;
		font: 600 21px/1.25 var(--sans);
		letter-spacing: -0.01em;
	}
	.sub {
		margin: 6px 0 18px;
		font-size: 11.5px;
	}
	.reg {
		padding: 16px 0;
		border-top: 1px solid var(--line);
	}
	.h {
		display: flex;
		gap: 8px 16px;
		align-items: baseline;
		flex-wrap: wrap;
		margin-bottom: 12px;
	}
	h2 {
		margin: 0;
		font: 600 15px/1.3 var(--sans);
	}
	.why {
		font-size: 13px;
		color: var(--ink-2);
	}
	.auth {
		font-size: 11.5px;
		margin-left: auto;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 12px;
	}
	.ok {
		margin: 10px 0 0;
		font-size: 11.5px;
		color: var(--teal);
	}
	.add {
		padding-top: 14px;
		border-top: 1px solid var(--line);
		font-size: 12px;
	}
	@media (max-width: 760px) {
		.body {
			padding: 16px 14px;
		}
		.auth {
			margin-left: 0;
		}
	}
</style>
