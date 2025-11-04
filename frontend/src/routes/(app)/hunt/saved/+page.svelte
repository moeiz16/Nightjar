<script lang="ts">
	// Saved searches (DESIGN.md §6.8): named, shareable hunts with a note on why we watch each one.
	import Pane from '$lib/components/shell/Pane.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { SavedSearch } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { whenShort } from '$lib/time/clock';

	let list = $state<SavedSearch[]>([]);
	let err = $state('');
	$effect(() => {
		api.hunting.saved().then((r) => (list = r)).catch((x) => (err = problemMessage(x)));
	});
	async function toggleAlert(s: SavedSearch) {
		try {
			const next = await api.hunting.updateSaved(s.id, { alert: !s.alert });
			list = list.map((x) => (x.id === s.id ? { ...x, ...next } : x));
			ui.toast(next.alert ? `“${s.name}” now alerts, with ${s.createdBy.fullName} as owner. It appears in Rules.` : `Alerts off for “${s.name}”.`);
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
	async function remove(s: SavedSearch) {
		if (!confirm(`Delete the saved search “${s.name}”?`)) return;
		try {
			await api.hunting.deleteSaved(s.id);
			list = list.filter((x) => x.id !== s.id);
			ui.toast('Deleted.');
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
</script>

<svelte:head><title>Saved searches · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Pane title="saved searches" sub={String(list.length)} focus>
		{#snippet actions()}<a class="btn sm" href="/hunt">New search</a><span class="hide-s">⌘K › “saved”</span>{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="tblwrap">
			<table class="ledger">
				<thead><tr><th>name</th><th>owner</th><th>runs</th><th>last result</th><th>alert me</th><th><span class="sr">actions</span></th></tr></thead>
				<tbody>
					{#each list as s (s.id)}
						<tr>
							<td><a class="rowbtn" href="/hunt?saved={s.id}">▸ {s.name}</a><small class="note">{s.description}</small></td>
							<td>{s.createdBy.fullName}</td>
							<td class="m">{s.schedule ?? 'on demand'}</td>
							<td class="m">{s.lastCount ?? '—'}{s.lastRunAt ? ` · ${whenShort(s.lastRunAt)}` : ''}{(s.lastCount ?? 0) > 0 && s.alert ? ' ◇' : ''}</td>
							<td class="m">
								<button class="tg" type="button" aria-pressed={!!s.alert} onclick={() => toggleAlert(s)} disabled={!session.can('hunting:write')}>{s.alert ? '● on' : '○ off'}</button>
							</td>
							<td class="m">{#if session.can('hunting:write')}<button class="lnk" type="button" onclick={() => remove(s)}>delete</button>{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<p class="foot mono faint">“Alert me” turns a saved search into a detection with an owner; it then appears in Rules.</p>
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		min-height: 0;
		background: var(--line);
	}
	.tblwrap {
		overflow-x: auto;
	}
	.rowbtn {
		text-decoration: none;
		display: block;
	}
	.rowbtn:hover {
		color: var(--teal);
	}
	.note {
		display: block;
		margin-top: 3px;
		font-size: 12px;
		color: var(--muted);
	}
	.tg {
		font: inherit;
		color: var(--muted);
	}
	.tg[aria-pressed='true'] {
		color: var(--teal);
	}
	.foot {
		padding: 14px;
		font-size: 11.5px;
	}
</style>
