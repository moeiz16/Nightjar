<script lang="ts">
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
		<span class="mono">{total.toLocaleString('en-GB')} recent entries</span>
		<button class="btn sm" type="button" onclick={doVerify} disabled={busy}>{busy ? 'Checking…' : 'Verify the log'}</button>
		{#if verify}
			<span class="mono res" class:bad={!verify.valid} role="status">
				{verify.valid ? `✓ Chain intact · checked ${verify.checked.toLocaleString('en-GB')} entries · ${verifiedAt ? timeOf(verifiedAt) : ''}` : `● Chain broken at entry ${verify.brokenAt}`}
			</span>
		{/if}
		{#if !compact}<button class="btn sm right" type="button" onclick={exportPack}>Export evidence pack</button>{/if}
	</div>
	<div class="filters mono" role="group" aria-label="Filters">
		<span class="seg">
			<button type="button" aria-pressed={who === ''} onclick={() => (who = '')}>all</button>
			<button type="button" aria-pressed={who === 'user'} onclick={() => (who = 'user')}>people</button>
			<button type="button" aria-pressed={who === 'agent'} onclick={() => (who = 'agent')}>teammates</button>
			<button type="button" aria-pressed={who === 'system'} onclick={() => (who = 'system')}>system</button>
		</span>
		{#if !compact}
			<span class="seg">
				{#each AREAS as [k, l] (k)}<button type="button" aria-pressed={area === k} onclick={() => (area = k)}>{l}</button>{/each}
			</span>
		{/if}
	</div>
	{#if err}<p class="ferr">{err}</p>{/if}
	<div class="tblwrap">
		<table class="ledger">
			<thead><tr><th>time</th><th>who</th><th>did</th>{#if !compact}<th>about</th>{/if}<th>seal</th></tr></thead>
			<tbody>
				{#each rows as r (r.id)}
					<tr>
						<td class="m">{whenShort(r.createdAt)}<span class="sr"> {timeOf(r.createdAt, undefined, true)}</span></td>
						<td class="who" class:tm={r.actorType === 'agent'}>{actor(r)}</td>
						<td>{r.label}</td>
						{#if !compact}<td class="m">{#if about(r)}<a href="/incidents/{about(r)}">{about(r)}</a>{:else}{TYPE_WORDS[r.resourceType ?? ''] ?? (r.resourceType ? r.resourceType.replace(/_/g, ' ') : '—')}{/if}</td>{/if}
						<td class="m seal" title="Sealed to the entry before it; Verify the log checks every seal">·{r.hash.slice(0, 4)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<style>
	.top {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 16px;
		font-size: 12px;
		color: var(--ink-2);
		flex-wrap: wrap;
	}
	.res {
		color: var(--teal);
		font-size: 11.5px;
	}
	.res.bad {
		color: var(--red);
	}
	.right {
		margin-left: auto;
	}
	.filters {
		display: flex;
		gap: 10px;
		padding: 0 16px 10px;
		flex-wrap: wrap;
	}
	.tblwrap {
		overflow-x: auto;
	}
	.who {
		white-space: nowrap;
		color: var(--ink);
	}
	.who.tm {
		color: var(--teal);
	}
	.seal {
		color: var(--muted);
	}
	td a {
		text-decoration: none;
		color: var(--ink-2);
	}
	td a:hover {
		color: var(--teal);
	}
</style>
