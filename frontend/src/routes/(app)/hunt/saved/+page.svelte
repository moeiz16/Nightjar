<script lang="ts">
	// Saved searches (DESIGN.md §6.8): named, shareable hunts with a note on why we watch each one.
	import Band from '$lib/components/ui/Band.svelte';
	import Sheet from '$lib/components/ui/Sheet.svelte';
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
	let deleting = $state<SavedSearch | null>(null);
	async function remove(s: SavedSearch) {
		deleting = null;
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
<h1 class="sr">Saved searches</h1>
<div class="tiles">
	<Band title="saved searches" sub={String(list.length)} fill>
		{#snippet actions()}<a class="btn preset-outlined-surface-300-700 btn-sm" href="/hunt">New search</a><span class="hide-s">⌘K › “saved”</span>{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
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
							<td class="m">{#if session.can('hunting:write')}<button class="lnk" type="button" onclick={() => (deleting = s)}>delete</button>{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<p class="foot mono faint">“Alert me” runs the search again every 15 minutes; a search with matches is marked ◇.</p>
	</Band>
</div>

<Sheet kind="dialog" label="Delete a saved search" width="460px" open={!!deleting} onclose={() => (deleting = null)}>
	{#if deleting}
		<div class="confirm" role="alertdialog" aria-labelledby="del-h">
			<p class="mono faint">── delete</p>
			<h2 id="del-h">Delete “{deleting.name}”?</h2>
			<p>The search and its alert stop for everyone. The activity log keeps a record that you deleted it.</p>
			<div class="row"><button class="btn preset-filled-error-700-300" type="button" onclick={() => remove(deleting!)}>Delete</button><button class="btn preset-outlined-surface-300-700" type="button" onclick={() => (deleting = null)}>Keep it</button></div>
		</div>
	{/if}
</Sheet>

<style>
	.confirm {
		padding: 18px 22px 20px;
		display: grid;
		gap: 10px;
	}
	.confirm p {
		margin: 0;
		color: var(--ink-2);
		font-size: 13.5px;
	}
	.confirm .mono {
		font-size: 11.5px;
	}
	.confirm h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
	}
	.confirm .row {
		display: flex;
		gap: 8px;
		margin-top: 6px;
	}
	.tiles {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.table-wrap {
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
