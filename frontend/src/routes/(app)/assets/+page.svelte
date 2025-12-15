<script lang="ts">
	// Assets (DESIGN.md §6.13): a ledger led by the business role; an asset's detail rises as a bottom sheet.
	import { page } from '$app/state';
	import Plate from '$lib/components/ui/Plate.svelte';
	import Choice from '$lib/components/ui/Choice.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { AssetDoc } from '$lib/api/types';
	import { live } from '$lib/stores/live.svelte';

	let assets = $state<AssetDoc[]>([]);
	let err = $state('');
	let kind = $state<'all' | 'workstation' | 'server' | 'cloud'>('all');
	let site = $state('');
	let selected = $state<string | null>(null);
	$effect(() => {
		api.directory.assets().then((a) => (assets = a)).catch((x) => (err = problemMessage(x)));
	});
	$effect(() => {
		const n = page.url.searchParams.get('name');
		if (n) selected = n;
	});
	const sites = $derived([...new Set(assets.map((a) => a.site.split(' ')[0]!))].sort());
	const isCloud = (a: AssetDoc) => a.kind === 'cloud_account' || a.kind === 'saas';
	const shown = $derived(
		assets.filter((a) => (kind === 'all' || (kind === 'cloud' ? isCloud(a) : kind === 'server' ? ['server', 'network', 'ot_gateway'].includes(a.kind) : a.kind === kind)) && (!site || a.site.startsWith(site)) && (!page.url.searchParams.get('isolated') || a.isolated))
	);
	const sel = $derived(assets.find((a) => a.name === selected) ?? null);
	const incidentsFor = (name: string) => live.incidents.filter((i) => i.assets.includes(name));
	const role = (a: AssetDoc) => a.businessService ?? (a.kind === 'workstation' ? (a.owner ? 'Laptop' : 'Workstation') : a.kind.replace('_', ' '));
</script>

<svelte:head><title>Assets · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Plate title="The register" sub="{shown.length} of {assets.length}" quiet bare>
		{#snippet actions()}
			<Choice label="Kind" bind:value={kind} options={[{ value: 'workstation', label: 'laptops' }, { value: 'server', label: 'servers' }, { value: 'cloud', label: 'cloud' }, { value: 'all', label: 'all' }]} />
			<label class="sr" for="site">Site</label>
			<select id="site" class="select f" bind:value={site}><option value="">every site</option>{#each sites as s (s)}<option>{s}</option>{/each}</select>
		{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>Name</th><th>What it does</th><th>Weakness</th><th>State</th></tr></thead>
				<tbody>
					{#each shown as a (a.id)}
						<tr class:sel={selected === a.name} class={a.isolated ? 'h-person' : incidentsFor(a.name).length ? 'h-acc' : a.vulnerabilities.length ? 'h-soon' : ''}>
							<td><button class="rowbtn mono" type="button" onclick={() => (selected = selected === a.name ? null : a.name)} aria-expanded={selected === a.name}>{a.name}</button></td>
							<td>{role(a)}{#if a.criticality === 'crown_jewel'} <span class="cj">◆ crown jewel</span>{/if}</td>
							<td class="m">{#if a.vulnerabilities.length}<span class="alarm">▲</span> {a.vulnerabilities.length} exploited{:else}—{/if}</td>
							<td class="m">{#if a.isolated}<span class="person">‖ isolated</span>{:else if incidentsFor(a.name).length}<span class="acc">▸ in {incidentsFor(a.name).map((i) => i.reference).join(', ')}</span>{:else}✓ normal{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Plate>
	<Sheet kind="sheet" label={sel ? `Asset ${sel.name}` : 'Asset'} open={!!sel} onclose={() => (selected = null)}>
		{#if sel}
			<header class="sh"><span class="eng">Asset · <span class="mono">{sel.name}</span></span><button class="lnk" type="button" onclick={() => (selected = null)}>close <kbd>esc</kbd></button></header>
			<div class="det">
				<h2>{role(sel)}</h2>
				<p class="faint">{sel.name} · {sel.kind.replace('_', ' ')} · {sel.site} · {sel.jurisdiction}</p>
				<dl>
					<div><dt>owner</dt><dd>{sel.owner ?? 'shared'}</dd></div>
					<div><dt>importance</dt><dd>{sel.criticality === 'crown_jewel' ? '◆ crown jewel: actions need two people' : sel.criticality}</dd></div>
					<div><dt>addresses</dt><dd class="mono">{sel.ips.join(', ') || '—'}</dd></div>
					<div><dt>system</dt><dd>{sel.os || '—'}</dd></div>
					<div><dt>isolation</dt><dd>{sel.isolated ? '‖ isolated' : 'connected'}</dd></div>
				</dl>
				{#if sel.vulnerabilities.length}
					<h3 class="eng">Known exploited weaknesses</h3>
					{#each sel.vulnerabilities as v (v.cveId)}<p class="v"><span class="alarm mono">▲ {v.cveId}</span> {v.name}{v.knownRansomware ? ' · used in ransomware' : ''}</p>{/each}
				{/if}
				<h3 class="eng">Incidents ({incidentsFor(sel.name).length})</h3>
				{#each incidentsFor(sel.name) as i (i.id)}<a class="inc" href="/incidents/{i.reference}"><span class="mono">{i.reference}</span> {i.title}</a>{:else}<p class="faint">None open.</p>{/each}
				<a class="btn preset-outlined-surface-300-700 btn-sm" href="/hunt?ask={encodeURIComponent(`events on ${sel.name} today`)}">Hunt on this device</a>
			</div>
		{/if}
	</Sheet>
</div>

<style>
	.tiles {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.sh {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 14px 24px;
		border-bottom: 1px solid var(--rule);
	}
	.sh .eng {
		font-size: 15px;
		color: var(--ink);
	}
	.sh .mono {
		text-transform: none;
		letter-spacing: 0;
		color: var(--acc-hi);
	}
	.f {
		height: 30px;
		min-height: 30px;
		width: auto;
		font-size: 13px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.cj {
		margin-left: 6px;
		font: 700 11.5px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--person);
		white-space: nowrap;
	}
	.det {
		padding: 20px 24px 26px;
		max-width: 760px;
		display: grid;
		gap: 10px;
		justify-items: start;
	}
	h2 {
		margin: 0;
		font: 800 34px/0.95 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.det > p {
		margin: 0;
		font-size: 13.5px;
	}
	dl {
		margin: 4px 0;
		display: grid;
		gap: 0;
		width: 100%;
		border-top: 1px solid var(--rule);
	}
	dl div {
		display: grid;
		grid-template-columns: 120px 1fr;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--rule);
		font-size: 14px;
	}
	dt {
		font: 700 12px/1.6 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--faint);
	}
	dd {
		margin: 0;
		color: var(--ink-2);
	}
	h3 {
		margin: 12px 0 0;
		font-size: 14px;
		color: var(--dim);
	}
	.v {
		margin: 0;
		font-size: 14px;
	}
	.inc {
		text-decoration: none;
		font-size: 14px;
	}
	.inc:hover {
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	@media (max-width: 760px) {
		.tiles :global(.ph .choice) {
			display: none;
		}
	}
</style>
