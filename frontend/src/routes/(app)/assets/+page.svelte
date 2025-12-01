<script lang="ts">
	// Assets (DESIGN.md §6.13): a ledger led by the business role; an asset's detail rises as a bottom sheet.
	import { page } from '$app/state';
	import Band from '$lib/components/ui/Band.svelte';
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
<h1 class="sr">Assets</h1>
<div class="tiles">
	<Band title="assets" sub={String(assets.length)} fill>
		{#snippet actions()}
			<Choice label="Kind" bind:value={kind} options={[{ value: 'workstation', label: 'laptops' }, { value: 'server', label: 'servers' }, { value: 'cloud', label: 'cloud' }, { value: 'all', label: 'all' }]} />
			<label class="sr" for="site">Site</label>
			<select id="site" class="select f" bind:value={site}><option value="">every site</option>{#each sites as s (s)}<option>{s}</option>{/each}</select>
		{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>name</th><th>what it does</th><th>where</th><th>owner</th><th>known weakness</th><th>state</th></tr></thead>
				<tbody>
					{#each shown as a (a.id)}
						<tr class:sel={selected === a.name}>
							<td><button class="rowbtn mono" type="button" onclick={() => (selected = selected === a.name ? null : a.name)} aria-expanded={selected === a.name}>{a.name}</button></td>
							<td>{role(a)}{#if a.criticality === 'crown_jewel'} <span class="cj mono">◆ crown jewel</span>{/if}</td>
							<td>{a.site}</td>
							<td>{a.owner ?? '—'}</td>
							<td class="m">{#if a.vulnerabilities.length}<span class="red">▲</span> {a.vulnerabilities.length} exploited{:else}—{/if}</td>
							<td class="m">{#if a.isolated}<span class="lav">‖ isolated</span>{:else if incidentsFor(a.name).length}<span class="teal">▸ in {incidentsFor(a.name).map((i) => i.reference).join(', ')}</span>{:else}✓ normal{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Band>
	<Sheet kind="sheet" label={sel ? `Asset ${sel.name}` : 'Asset'} open={!!sel} onclose={() => (selected = null)}>
		{#if sel}
			<header class="sh mono"><span>── asset · {sel.name}</span><button class="lnk" type="button" onclick={() => (selected = null)}>close <kbd>esc</kbd></button></header>
			<div class="det">
				<h2>{role(sel)}</h2>
				<p class="mono faint">{sel.name} · {sel.kind.replace('_', ' ')} · {sel.site} · {sel.jurisdiction}</p>
				<dl>
					<div><dt>owner</dt><dd>{sel.owner ?? 'shared'}</dd></div>
					<div><dt>importance</dt><dd>{sel.criticality === 'crown_jewel' ? '◆ crown jewel: actions need two people' : sel.criticality}</dd></div>
					<div><dt>addresses</dt><dd class="mono">{sel.ips.join(', ') || '—'}</dd></div>
					<div><dt>system</dt><dd>{sel.os || '—'}</dd></div>
					<div><dt>isolation</dt><dd>{sel.isolated ? '‖ isolated' : 'connected'}</dd></div>
				</dl>
				{#if sel.vulnerabilities.length}
					<h3 class="mono">known exploited weaknesses</h3>
					{#each sel.vulnerabilities as v (v.cveId)}<p class="v"><span class="red mono">▲ {v.cveId}</span> {v.name}{v.knownRansomware ? ' · used in ransomware' : ''}</p>{/each}
				{/if}
				<h3 class="mono">incidents ({incidentsFor(sel.name).length})</h3>
				{#each incidentsFor(sel.name) as i (i.id)}<a class="inc" href="/incidents/{i.reference}"><span class="mono">{i.reference}</span> {i.title}</a>{:else}<p class="mono faint">None open.</p>{/each}
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
		padding: 12px 24px;
		border-bottom: 1px solid var(--line);
		font-size: 12px;
		color: var(--muted);
	}
	.f {
		height: 28px;
		width: auto;
		font-size: 12px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.cj {
		font-size: 11px;
		color: var(--lav);
		white-space: nowrap;
	}
	.det {
		padding: 18px 24px 24px;
		max-width: 760px;
		display: grid;
		gap: 10px;
		justify-items: start;
	}
	h2 {
		margin: 0;
		font: 600 19px/1.25 var(--sans);
		font-variation-settings: 'MONO' 0;
	}
	.det > p {
		margin: 0;
		font-size: 11.5px;
	}
	dl {
		margin: 0;
		display: grid;
		gap: 6px;
		width: 100%;
	}
	dl div {
		display: grid;
		grid-template-columns: 90px 1fr;
		gap: 8px;
		font-size: 13px;
	}
	dt {
		font: 400 11.5px/1.6 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--faint);
	}
	dd {
		margin: 0;
		color: var(--ink-2);
	}
	h3 {
		margin: 8px 0 0;
		font-size: 11.5px;
		font-weight: 400;
		color: var(--faint);
	}
	.v {
		margin: 0;
		font-size: 13px;
	}
	.inc {
		text-decoration: none;
		font-size: 13px;
	}
	.inc:hover {
		color: var(--teal);
	}
	@media (max-width: 760px) {
		.tiles :global(.rule .choice) {
			display: none;
		}
	}
</style>
