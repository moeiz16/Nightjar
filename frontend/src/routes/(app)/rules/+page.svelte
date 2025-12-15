<script lang="ts">
	// Detection rules (DESIGN.md §6.10): a ledger with a 7-day spark, the calibrated "likely real", and state.
	// Noisy is ◐ + the word, never colour alone.
	import Plate from '$lib/components/ui/Plate.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { RuleInfo } from '$lib/api/types';
	import { goto } from '$app/navigation';

	let rules = $state<RuleInfo[]>([]);
	let q = $state('');
	let err = $state('');
	$effect(() => {
		api.rules.list().then((r) => (rules = r)).catch((x) => (err = problemMessage(x)));
	});
	const noisy = (r: RuleInfo) => r.enabled && (r.precisionEstimate ?? 1) < 0.1 && r.hits7d > 50;
	const shown = $derived(rules.filter((r) => !q || (r.title + r.attack.map((a) => a.techniqueId).join(' ')).toLowerCase().includes(q.toLowerCase())));
	const on = $derived(rules.filter((r) => r.enabled).length);
	const noisyCount = $derived(rules.filter(noisy).length);
	// rules that fired this week come first; the quiet ones are one click away (a filter always searches all)
	let showQuiet = $state(false);
	const quiet = $derived(shown.filter((r) => !r.hits7d));
	const listed = $derived(q || showQuiet ? shown : shown.filter((r) => r.hits7d));
	function spark(days: number[] | undefined): string {
		if (!days?.length) return '';
		const max = Math.max(1, ...days);
		return days.map((d, i) => `${i * 9},${18 - (d / max) * 16}`).join(' ');
	}
</script>

<svelte:head><title>Rules · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Plate title="The bay" sub="{on} on · {noisyCount} noisy" quiet bare>
		{#snippet actions()}
			<label class="sr" for="rq">Filter rules</label><input id="rq" class="input f" bind:value={q} placeholder="filter…" />
		{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>Rule</th><th>ATT&amp;CK</th><th>Last 7 days</th><th>Likely real</th><th>State</th></tr></thead>
				<tbody>
					{#each listed as r (r.id)}
						<tr class="click {noisy(r) ? 'h-soon' : ''}" onclick={() => goto(`/rules/${r.id}`)}>
							<td><a class="rowbtn" href="/rules/{r.id}" title="{r.source === 'sigma' ? 'Sigma' : r.source === 'ueba' ? 'behaviour' : 'Nightjar'} · {r.level}">{r.title}</a></td>
							<td class="m">{r.attack[0]?.techniqueId ?? '—'}</td>
							<td class="m spk"><span>{r.hits7d.toLocaleString('en-GB')}</span>
								{#if r.hits7dDaily}<svg viewBox="0 0 56 20" width="56" height="20" aria-hidden="true"><polyline points={spark(r.hits7dDaily)} /></svg>{/if}
							</td>
							<td class="m">{r.precisionEstimate === null ? '—' : `${Math.round(r.precisionEstimate * 100)} in 100`}{#if noisy(r)} <span class="soon">◐</span>{/if}</td>
							<td class="m st">{#if !r.enabled}○ off{:else if noisy(r)}<span class="soon">◐ noisy</span>{:else}<span class="ok">● on</span>{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if !q && !showQuiet && quiet.length}<button class="lnk morer" type="button" onclick={() => (showQuiet = true)}>Show {quiet.length} quiet rules</button>{/if}
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
		width: 220px;
		font-size: 13px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	tr.click {
		cursor: pointer;
	}
	.rowbtn {
		text-decoration: none;
		color: var(--ink);
		font-weight: 700;
	}
	.morer {
		display: block;
		margin: 14px auto 0;
	}
	.spk {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.spk span {
		min-width: 3ch;
		text-align: right;
	}
	polyline {
		fill: none;
		stroke: var(--acc);
		stroke-width: 1.4;
	}
	@media (max-width: 760px) {
		.f {
			width: 120px;
		}
	}
</style>
