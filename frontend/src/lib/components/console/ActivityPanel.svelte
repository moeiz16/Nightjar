<script lang="ts">
	import Choice from '$lib/components/ui/Choice.svelte';
	import { Pagination } from '@skeletonlabs/skeleton-svelte';
	// The activity log (DESIGN.md §6.15): every sensitive action, newest first, each with a short seal.
	import { api, problemMessage } from '$lib/api';
	import type { AuditEntry, AuditVerify } from '$lib/api/types';
	import { ui } from '$lib/stores/ui.svelte';
	import { whenShort, timeOf, now } from '$lib/time/clock';

	let { compact = false, autoVerify = false, autoExport = false }: { compact?: boolean; autoVerify?: boolean; autoExport?: boolean } = $props();
	let rows = $state<AuditEntry[]>([]);
	let total = $state(0);
	let who = $state<'' | 'user' | 'agent' | 'system'>('');
	let area = $state('');
	let verify = $state<AuditVerify | null>(null);
	let verifiedAt = $state<Date | null>(null);
	let busy = $state(false);
	let err = $state('');
	const PAGE = 20;
	let pageNo = $state(1);
	const paged = $derived(compact ? rows.slice(0, 12) : rows.slice((pageNo - 1) * PAGE, pageNo * PAGE));

	async function load() {
		try {
			const r = await api.audit.list({ actorType: who || undefined, action: area || undefined, pageSize: 100 });
			rows = r.items;
			total = r.total;
		} catch (x) {
			err = problemMessage(x);
		}
	}
	$effect(() => {
		void who;
		void area;
		load();
	});
	async function doVerify() {
		busy = true;
		try {
			verify = await api.audit.verify();
			verifiedAt = now();
			ui.announce(verify.valid ? `Chain intact. Checked ${verify.checked} entries.` : `The chain is broken at entry ${verify.brokenAt}.`);
		} catch (x) {
			err = problemMessage(x);
		} finally {
			busy = false;
		}
	}
	function exportPack() {
		const head = 'time,who,actor,did,about,seal,previous';
		const lines = rows.map((r) => [r.createdAt, r.actorName, r.actorType, r.label, r.resourceId ?? '', r.hash, r.prevHash].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','));
		const blob = new Blob([[head, ...lines].join('\n')], { type: 'text/csv' });
		const a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = 'nightjar-evidence-pack.csv';
		a.click();
		URL.revokeObjectURL(a.href);
		ui.toast('Evidence pack exported (CSV with every seal).');
	}
	$effect(() => {
		if (autoVerify) doVerify();
	});
	$effect(() => {
		if (autoExport && rows.length) exportPack();
	});
	const AREAS: [string, string][] = [['', 'everything'], ['approval.', 'approvals'], ['notice.', 'notices'], ['reporting.', 'clocks'], ['settings.', 'settings']];
	// the investigation colleagues sign as "Triage agent" in the log; people read them as colleagues
	const actor = (r: AuditEntry) => (r.actorType === 'agent' ? r.actorName.replace(/\s*agent$/i, '') + ' colleague' : r.actorName);
	const TYPE_WORDS: Record<string, string> = { incident: 'incident', rule: 'detection rule', saved_search: 'saved search', member: 'member', invitation: 'invitation', ingest_key: 'event source key', notice: 'notice', approval: 'action', user: 'account', organization: 'organisation', settings: 'settings', model: 'model card', weekly_brief: 'weekly brief' };
	const about = (r: AuditEntry): string | null => {
		const ref = (r.metadata as Record<string, unknown> | null)?.reference;
		if (typeof ref === 'string' && ref.startsWith('NJ-')) return ref;
		return r.resourceId?.startsWith('NJ-') ? r.resourceId : null;
	};
</script>

<div class="act" class:compact>
	<div class="top">
		<span class="mono">{total.toLocaleString('en-GB')} entries</span>
		<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={doVerify} disabled={busy}>{busy ? 'Checking…' : 'Verify the log'}</button>
		{#if verify}
			<span class="mono res" class:bad={!verify.valid} role="status" title={verify.valid ? `Checked ${verify.checked.toLocaleString('en-GB')} entries${verifiedAt ? ` at ${timeOf(verifiedAt)}` : ''}` : undefined}>
				{verify.valid ? '✓ Chain intact' : `● Chain broken at entry ${verify.brokenAt}`}<span class="sr">{verify.valid ? ` · checked ${verify.checked} entries` : ''}</span>
			</span>
		{/if}
		{#if !compact}<button class="btn preset-outlined-surface-300-700 btn-sm right" type="button" onclick={exportPack} title="Download the log and its seals as an evidence pack">Export</button>{/if}
	</div>
	<div class="filters mono" role="group" aria-label="Filters">
		<Choice label="Who" value={who || 'all'} options={[{ value: 'all', label: 'all' }, { value: 'user', label: 'people' }, { value: 'agent', label: 'teammates' }, { value: 'system', label: 'system' }]} onchange={(v) => ((who = v === 'all' ? '' : (v as typeof who)), (pageNo = 1))} />
		{#if !compact}
			<Choice label="Area" value={area || 'all'} options={AREAS.map(([k, l]) => ({ value: k || 'all', label: l }))} onchange={(v) => ((area = v === 'all' ? '' : v), (pageNo = 1))} />
		{/if}
	</div>
	{#if err}<p class="ferr">{err}</p>{/if}
	<div class="table-wrap">
		<table class="table">
			<thead><tr><th>Time</th><th>Who</th><th>Did</th>{#if !compact}<th>About</th>{/if}<th>Seal</th></tr></thead>
			<tbody>
				{#each paged as r (r.id)}
					<tr class={r.actorType === 'user' ? 'h-person' : r.actorType === 'agent' ? 'h-acc' : ''}>
						<td class="m">{whenShort(r.createdAt)}<span class="sr"> {timeOf(r.createdAt, undefined, true)}</span></td>
						<td class="who" class:tm={r.actorType === 'agent'}>{actor(r)}</td>
						<td>{r.label}</td>
						{#if !compact}<td class="m">{#if about(r)}<a href="/incidents/{about(r)}">{about(r)}</a>{:else}{TYPE_WORDS[r.resourceType ?? ''] ?? (r.resourceType ? r.resourceType.replace(/_/g, ' ') : '—')}{/if}</td>{/if}
						<td class="m seal" title="Sealed to the entry before it ({r.hash.slice(0, 4)}); Verify the log checks every seal"><span aria-hidden="true">⛓</span><span class="sr">sealed</span></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if !compact && rows.length > PAGE}
		<div class="pager">
			<span></span>
			<Pagination data={rows} count={rows.length} pageSize={PAGE} page={pageNo} onPageChange={(e) => (pageNo = e.page)} siblingCount={1} buttonActive="bg-[var(--acc)] text-[var(--acc-ink)]" buttonInactive="preset-outlined-surface-300-700" buttonHover="hover:bg-[var(--raised)]" gap="gap-1" padding="p-0" background="" border="" classes="pg" />
		</div>
	{/if}
</div>

<style>
	.top {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 0 0 12px;
		font-size: 14px;
		color: var(--ink-2);
		flex-wrap: wrap;
	}
	.top > .mono {
		font-size: 13px;
	}
	.res {
		font: 600 13px/1.2 var(--ui);
		color: var(--ok-ink);
		background: var(--ok);
		padding: 6px 10px;
	}
	.res.bad {
		background: var(--acc);
		color: var(--acc-ink);
	}
	.right {
		margin-left: auto;
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 14px 0 6px;
		flex-wrap: wrap;
		font-size: 13px;
	}
	.pager :global(.pg button) {
		height: 30px;
		min-width: 30px;
		font: 500 12.5px/1 var(--code);
		border-radius: 2px;
	}
	.filters {
		display: flex;
		gap: 10px;
		padding: 0 0 6px;
		flex-wrap: wrap;
	}
	.compact .top,
	.compact .filters {
		padding: 12px 14px 8px;
	}
	.compact .table-wrap {
		padding: 0 10px 10px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.who {
		white-space: nowrap;
		color: var(--ink);
		font-weight: 600;
	}
	.who.tm {
		color: var(--acc-hi);
	}
	.seal {
		color: var(--dim);
		position: relative;
	}
	td a {
		color: var(--ink-2);
	}
	td a:hover {
		color: var(--ink);
	}
</style>
