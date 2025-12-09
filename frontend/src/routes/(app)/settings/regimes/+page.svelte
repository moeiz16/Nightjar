<script lang="ts">
	// Settings › regime contacts (DESIGN.md §6.14): which rules apply to Halden, who is the contact, who submits.
	import Plate from '$lib/components/ui/Plate.svelte';
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
	<Plate title="Which rules apply" sub="From your company profile" bare>
		{#snippet actions()}{#if can}<button class="btn preset-filled-surface-950-50 btn-sm" type="button" onclick={save} disabled={!dirty || busy}>{busy ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}</button>{:else}<span>read only</span>{/if}{/snippet}
		<SettingsNav />
		{#if err}<p class="ferr pad">{err}</p>{/if}
		<div class="body">
			<h2 class="q">Which rules apply to Halden Logistics?</h2>
			<p class="faint sub">Nothing here files anything; it decides who is asked and who submits.</p>
			{#each ORDER as r (r)}
				{@const c = contacts[r]}
				{#if c}
					<section class="reg" aria-labelledby="r-{r}">
						<div class="h">
							<h3 id="r-{r}"><span class="ok mono">●</span> {title[r]} · {c.jurisdiction}</h3>
							<span class="why">{why(r)}</span>
							<span class="faint auth">Authority: {c.authorityName}</span>
						</div>
						<div class="grid">
							<label class="label"><span>Owner (submits)</span>
								<select class="select" disabled={!can} value={c.internalOwner.userId} onchange={(e) => { const m = members.find((x) => x.user.id === (e.currentTarget as HTMLSelectElement).value); if (m) { c.internalOwner = { userId: m.user.id, name: m.user.fullName }; dirty = true; } }}>
									{#each members as m (m.id)}<option value={m.user.id}>{m.user.fullName} · {m.roleLabel}</option>{/each}
								</select>
							</label>
							{#each c.contacts as ct, i (i)}
								<label class="label"><span>{ct.role.toLowerCase()}</span><input class="input" bind:value={ct.name} oninput={() => (dirty = true)} disabled={!can} /></label>
								<label class="label"><span>Phone</span><input class="input mono" bind:value={ct.phone} oninput={() => (dirty = true)} disabled={!can} /></label>
							{/each}
						</div>
						<p class="okv">✓ Verified · {c.contacts.length} {c.contacts.length === 1 ? 'contact' : 'contacts'}</p>
					</section>
				{/if}
			{/each}
			<p class="add faint">○ Add a regime · ask your administrator to extend the company profile</p>
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
	.pad {
		padding: 22px;
	}
	.body {
		padding: 0;
		max-width: 1180px;
	}
	.q {
		margin: 0;
		font: 800 44px/0.92 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.02em;
	}
	.sub {
		margin: 8px 0 20px;
		font-size: 14px;
	}
	.reg {
		padding: 18px 20px 20px;
		margin-bottom: 6px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-left: 5px solid var(--acc);
	}
	.h {
		display: flex;
		gap: 8px 16px;
		align-items: baseline;
		flex-wrap: wrap;
		margin-bottom: 12px;
	}
	h3 {
		margin: 0;
		font: 800 26px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.why {
		font-size: 14px;
		color: var(--ink-2);
	}
	.auth {
		font-size: 13px;
		margin-left: auto;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 12px;
	}
	.okv {
		margin: 12px 0 0;
		font-size: 13px;
		font-weight: 700;
		color: var(--ok);
	}
	.add {
		padding-top: 14px;
		font-size: 13.5px;
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
