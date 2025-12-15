<script lang="ts">
	// Saved searches (DESIGN.md §6.8): named, shareable hunts with a note on why we watch each one.
	import Plate from '$lib/components/ui/Plate.svelte';
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
<div class="tiles">
	<Plate title="The bay" sub="{list.length} searches" quiet bare>
		{#snippet actions()}<a class="btn preset-outlined-surface-300-700 btn-sm" href="/hunt">New search</a>{/snippet}
		{#if err}<p class="ferr">{err}</p>{/if}
		<div class="table-wrap">
			<table class="table">
				<thead><tr><th>Name</th><th>Owner</th><th>Runs</th><th>Last result</th><th title="Runs the search again every 15 minutes; a search with matches is marked ◇.">Alert me</th><th><span class="sr">Actions</span></th></tr></thead>
				<tbody>
					{#each list as s (s.id)}
						<tr class={s.alert ? ((s.lastCount ?? 0) > 0 ? 'h-soon' : 'h-acc') : ''}>
							<td><a class="rowbtn" href="/hunt?saved={s.id}" title={s.description}>▸ {s.name}</a></td>
							<td>{s.createdBy.fullName}</td>
							<td class="m">{s.schedule ?? 'on demand'}</td>
							<td class="m">{s.lastCount ?? '—'}{s.lastRunAt ? ` · ${whenShort(s.lastRunAt)}` : ''}{(s.lastCount ?? 0) > 0 && s.alert ? ' ◇' : ''}</td>
							<td class="m">
								<button class="tg" type="button" aria-pressed={!!s.alert} onclick={() => toggleAlert(s)} disabled={!session.can('hunting:write')}>{s.alert ? '● on' : '○ off'}</button>
							</td>
							<td class="m">{#if session.can('hunting:write')}<button class="lnk" type="button" onclick={() => (deleting = s)}>Delete</button>{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Plate>
</div>

<Sheet kind="dialog" label="Delete a saved search" width="460px" open={!!deleting} onclose={() => (deleting = null)}>
	{#if deleting}
		<div class="confirm" role="alertdialog" aria-labelledby="del-h">
			<p class="eng faint">Delete</p>
			<h2 id="del-h">Delete “{deleting.name}”?</h2>
			<p>The search and its alert stop for everyone. The activity log keeps a record that you deleted it.</p>
			<div class="row"><button class="btn preset-filled-primary-500" type="button" onclick={() => remove(deleting!)}>Delete</button><button class="btn preset-outlined-surface-300-700" type="button" onclick={() => (deleting = null)}>Keep it</button></div>
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
	.confirm .eng {
		font-size: 12.5px;
	}
	.confirm h2 {
		margin: 0;
		font: 800 30px/0.95 var(--display);
		text-transform: uppercase;
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
		text-decoration: underline;
		text-decoration-color: var(--acc);
	}
	.tg {
		font: inherit;
		color: var(--dim);
	}
	.tg[aria-pressed='true'] {
		color: var(--ok);
		font-weight: 600;
	}
</style>
