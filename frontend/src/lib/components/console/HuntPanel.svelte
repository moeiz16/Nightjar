<script lang="ts">
	import Choice from '$lib/components/ui/Choice.svelte';
	// Hunting (DESIGN.md §6.7): plain words or filters, suggestions, List | Timeline over one search, Save as…
	import { untrack } from 'svelte';
	import { api, problemMessage } from '$lib/api';
	import type { HuntQueryDoc, HuntResultDoc, IncidentSummary, SavedSearch, Suggestion, UntrustedText } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { now, whenShort, timeOf } from '$lib/time/clock';
	import { parseHunt, chipsFromQuery, type Chip } from '$lib/util/hunt';
	import Untrusted from './Untrusted.svelte';

	let { initial = '', savedId = '', compact = false }: { initial?: string; savedId?: string; compact?: boolean } = $props();

	let text = $state('');
	let chips = $state<Chip[]>([]);
	let range = $state('last 7 days');
	let query = $state<HuntQueryDoc | null>(null);
	let result = $state<HuntResultDoc | null>(null);
	let view = $state<'list' | 'timeline'>('list');
	let suggestions = $state<Suggestion[]>([]);
	let busy = $state(false);
	let err = $state('');
	let saving = $state(false);
	let saveName = $state('');
	let saveNote = $state('');
	let fromSaved = $state<SavedSearch | null>(null);

	$effect(() => {
		api.hunting.suggestions().then((s) => (suggestions = s)).catch(() => undefined);
	});
	$effect(() => {
		const sid = savedId;
		const init = initial;
		untrack(() => start(sid, init));
	});
	function start(savedId: string, initial: string) {
		if (savedId) {
			api.hunting.saved().then((all) => {
				const s = all.find((x) => x.id === savedId);
				if (!s) return;
				fromSaved = s;
				text = s.query.text ?? s.name;
				chips = chipsFromQuery(s.query);
				query = s.query;
				runSaved(s);
			});
		} else if (initial) {
			text = initial;
			run();
		}
	}

	function interpret() {
		const p = parseHunt(text, now());
		chips = p.chips;
		range = p.range;
		query = p.query;
		fromSaved = null;
	}
	async function run(e?: SubmitEvent) {
		e?.preventDefault();
		if (!text.trim()) return;
		interpret();
		await exec();
	}
	async function exec() {
		if (!query) return;
		busy = true;
		err = '';
		try {
			result = await api.hunting.search({ ...query, filters: chips.map((c) => c.filter) });
		} catch (x) {
			err = problemMessage(x);
		} finally {
			busy = false;
		}
	}
	async function runSaved(s: SavedSearch) {
		busy = true;
		try {
			result = await api.hunting.runSaved(s.id);
		} catch (x) {
			err = problemMessage(x);
		} finally {
			busy = false;
		}
	}
	function removeChip(i: number) {
		chips = chips.filter((_, k) => k !== i);
		exec();
	}
	function useSuggestion(s: Suggestion) {
		text = s.title;
		query = s.query;
		chips = chipsFromQuery(s.query);
		exec();
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!query) interpret();
		try {
			const s = await api.hunting.save(saveName.trim(), saveNote.trim(), { ...query!, filters: chips.map((c) => c.filter) });
			ui.toast(`Saved “${s.name}”. Find it in ⌘K › saved searches.`);
			saving = false;
		} catch (x) {
			err = problemMessage(x);
		}
	}
	async function addTo(eventId: string) {
		const target = ui.scope ?? (live.incidents[0] ? { id: live.incidents[0].id, reference: live.incidents[0].reference } : null);
		if (!target) return;
		try {
			await api.incidents.addHuntMatch(target.id, eventId);
			ui.toast(`Added to ${target.reference}'s evidence, with your name.`);
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
	// Rows come back as named columns (contract §1.13); summary is the event's own text: untrusted, plain only.
	let known = $state<IncidentSummary[]>([]);
	let names = $state<Record<string, string>>({});
	$effect(() => {
		api.incidents.list({ status: 'all', pageSize: 100 }).then((r) => (known = r.items)).catch(() => undefined);
		api.directory.identities().then((ids) => (names = Object.fromEntries(ids.map((i) => [i.username, i.displayName])))).catch(() => undefined);
	});
	type Row = { at: string; who: string; device: string; what: UntrustedText; incident: string | null; eventId: string };
	function incidentFor(user: string | null, host: string | null, at: string): string | null {
		const t = +new Date(at);
		const hit = known.find((i) => ((user && i.identities.includes(user)) || (host && i.assets.includes(host))) && t >= +new Date(i.firstSeen) - 30 * 60e3 && t <= +new Date(i.lastSeen) + 30 * 60e3);
		return hit?.reference ?? null;
	}
	const rows = $derived.by<Row[]>(() => {
		if (!result) return [];
		const c = (...n: string[]) => result!.columns.findIndex((x) => n.includes(x));
		const [iT, iE, iH, iU, iS, iI] = [c('timestamp', 'time'), c('eventId'), c('host.name', 'device'), c('user.name', 'who'), c('summary', 'what', 'message'), c('incident')];
		return result.rows.map((r, k) => {
			const user = iU >= 0 ? ((r[iU] as string | null) ?? null) : null;
			const host = iH >= 0 ? ((r[iH] as string | null) ?? null) : null;
			const at = String(r[iT] ?? '');
			const raw = iS >= 0 ? r[iS] : '';
			return {
				at,
				eventId: iE >= 0 ? String(r[iE]) : String(k),
				who: user ? (names[user] ?? user) : '—',
				device: host ?? '—',
				what: typeof raw === 'object' && raw ? (raw as UntrustedText) : { text: String(raw ?? ''), flags: [] },
				incident: iI >= 0 ? ((r[iI] as string | null) ?? null) : incidentFor(user, host, at)
			};
		});
	});
	// grouped searches (saved searches and suggestions often count by person, device or address) come back as
	// columns + a count, not events: shown as a small ledger with plain column names
	const grouped = $derived(!!result && !result.columns.includes('timestamp'));
	const COL_WORDS: Record<string, string> = { 'user.name': 'person', 'host.name': 'device', 'source.ip': 'from address', 'destination.ip': 'to address', 'cloud.accessKeyId': 'access key', 'process.name': 'program', action: 'what happened', dataset: 'source', bucket: 'when', count: 'events', outcome: 'outcome' };
	const colWord = (c: string) => COL_WORDS[c] ?? (c.startsWith('distinct ') ? `different ${COL_WORDS[c.slice(9)] ?? c.slice(9)}` : c.replace(/[._]/g, ' '));
	const cell = (c: string, v: unknown) => (v === null || v === undefined || v === '' ? '—' : c === 'bucket' ? whenShort(String(v)) : c === 'user.name' ? (names[String(v)] ?? String(v)) : typeof v === 'number' ? v.toLocaleString('en-GB') : String(v));
	const devices = $derived(new Set(rows.map((r) => r.device)).size);
	const people = $derived(new Set(rows.map((r) => r.who)).size);
	const addTarget = $derived(ui.scope?.reference ?? live.incidents[0]?.reference ?? null);
</script>

<div class="hunt" class:compact>
	<form class="bar" onsubmit={run} role="search">
		<label class="sr" for="huntq">Search events in plain words</label>
		<span class="ask eng" aria-hidden="true">Ask</span>
		<input id="huntq" class="q" bind:value={text} placeholder="devices that uploaded to upload.files-sync.example in the last 7 days" autocomplete="off" />
		<span class="kb mono" aria-hidden="true">⏎</span>
		<div class="acts">
			<Choice label="View" bind:value={view} options={[{ value: 'list', label: 'List' }, { value: 'timeline', label: 'Timeline' }]} />
			{#if session.can('hunting:write')}<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={() => ((saving = !saving), (saveName ||= text.slice(0, 60)))} disabled={!text.trim()}>Save as…</button>{/if}
			<button class="btn preset-filled-primary-500 btn-sm" type="submit" disabled={busy}>▶ Run</button>
		</div>
	</form>

	{#if saving}
		<form class="save" onsubmit={save}>
			<label class="label"><span>Name</span><input class="input" bind:value={saveName} required /></label>
			<label class="label"><span>Why we watch this (a short note)</span><input class="input" bind:value={saveNote} /></label>
			<div class="row"><button class="btn preset-filled-surface-950-50 btn-sm" type="submit">Save search</button><button class="lnk" type="button" onclick={() => (saving = false)}>cancel</button></div>
		</form>
	{/if}

	<div class="filters mono" aria-label="Filters">
		<span class="eng faint fl">Filters</span>
		{#each chips as c, i (c.label)}
			<span class="fchip">{c.label}<button type="button" aria-label="Remove {c.label}" onclick={() => removeChip(i)}>×</button></span>
		{/each}
		<span class="fchip dim">{range} ▾</span>
		{#if fromSaved}<span class="faint">· saved search “{fromSaved.name}”</span>{/if}
	</div>

	{#if suggestions.length}
		<div class="sugg">
			<span class="eng faint fl">Worth a look</span>
			{#each suggestions.slice(0, compact ? 2 : 3) as s (s.id)}
				<button type="button" class="sg" title={s.why} onclick={() => useSuggestion(s)}>▸ {s.title}</button>
			{/each}
		</div>
	{/if}

	{#if err}<p class="ferr" role="alert">{err}</p>{/if}

	{#if result}
		<div class="sum mono" aria-live="polite">
			<span><b>{result.totalMatched.toLocaleString('en-GB')}</b> matches{#if grouped} · {result.rows.length} {result.rows.length === 1 ? 'group' : 'groups'}{:else} · {devices} devices · {people} people{/if}</span>
			<span class="faint">{#if result.searched}searched {(result.searched / 1e6).toFixed(1)} m events · {/if}answered in {Math.max(1, Math.round(result.elapsedMs))} ms</span>
		</div>
		{#if grouped}
			<div class="table-wrap">
				<table class="table">
					<thead><tr>{#each result.columns as c (c)}<th class:m={c === 'count' || c.startsWith('distinct ')}>{colWord(c)}</th>{/each}</tr></thead>
					<tbody>
						{#each result.rows as r, k (k)}
							<tr>{#each result.columns as c, i (c)}<td class:m={c === 'count' || c.startsWith('distinct ') || c === 'bucket'}>{cell(c, r[i])}</td>{/each}</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if view === 'list'}
			<div class="table-wrap">
				<table class="table">
					<thead><tr><th>Time</th><th>Who</th><th>Device</th><th>What</th><th>Incident</th></tr></thead>
					<tbody>
						{#each rows as r (r.eventId + r.at)}
							<tr class={r.incident ? 'h-acc' : ''}>
								<td class="m">{whenShort(r.at)}</td>
								<td>{r.who}</td>
								<td class="m">{r.device}</td>
								<td class="what">{#if r.what.text?.trim()}<Untrusted value={r.what} inline />{:else}<span class="faint">no message</span>{/if}</td>
								<td class="m">
									{#if r.incident}<a href="/incidents/{r.incident}">{r.incident}</a>{:else if addTarget && session.can('incidents:write')}<button class="lnk" type="button" onclick={() => addTo(r.eventId)}>— add to {addTarget} +</button>{:else}—{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<ol class="ladder" aria-label="Matches as a minute ladder">
				{#each rows as r (r.eventId + r.at)}
					<li class:inc={!!r.incident}>
						<span class="t mono">{whenShort(r.at)}<small>{timeOf(r.at, undefined, true).slice(6)}s</small></span>
						<span class="dot" aria-hidden="true"></span>
						<div><b>{r.who}</b> <span class="mono faint">{r.device}</span><Untrusted value={r.what} inline />{#if r.incident}<a class="mono inc" href="/incidents/{r.incident}">{r.incident}</a>{/if}</div>
					</li>
				{/each}
			</ol>
		{/if}
		{#if !result.rows.length}<p class="none">Nothing matched. Try fewer filters or a longer time range.</p>{/if}
	{:else if !busy}
		<p class="none">Ask in plain words: who, which device, what happened, and when. Field names are optional.</p>
	{/if}
</div>

<style>
	.hunt {
		display: grid;
		align-content: start;
	}
	.bar {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto auto;
		gap: 0 12px;
		align-items: center;
		padding: 18px 18px 12px;
	}
	.ask {
		height: 56px;
		display: grid;
		place-items: center;
		padding: 0 16px;
		font-size: 17px;
		background: var(--acc);
		color: var(--acc-ink);
		margin-right: -12px;
		z-index: 1;
	}
	.q {
		height: 56px;
		padding: 0 16px;
		border-radius: 0;
		border: 1px solid var(--rule-2);
		background: var(--sunk);
		font-size: 18px;
		font-weight: 500;
		min-width: 0;
	}
	.q:focus {
		outline: 2px solid var(--acc);
		outline-offset: -1px;
	}
	.kb {
		color: var(--faint);
		font-size: 12px;
	}
	.acts {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.save {
		display: grid;
		grid-template-columns: 1fr 1.4fr;
		gap: 10px;
		padding: 4px 18px 12px;
		align-items: end;
	}
	.save .row {
		grid-column: 1 / -1;
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
		padding: 0 18px 10px;
		font: 500 12.5px/1 var(--code);
	}
	.fl {
		font-size: 12.5px;
		margin-right: 4px;
	}
	.fchip {
		display: inline-flex;
		gap: 6px;
		align-items: center;
		height: 26px;
		padding: 0 4px 0 9px;
		border-radius: 1px;
		background: var(--raised);
		border: 1px solid var(--rule-2);
		color: var(--ink-2);
	}
	.fchip.dim {
		padding-right: 8px;
		color: var(--dim);
	}
	.fchip button {
		color: var(--faint);
		padding: 0 4px;
	}
	.fchip button:hover {
		color: var(--ink);
	}
	.sugg {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		padding: 0 18px 12px;
		font-size: 12.5px;
		align-items: center;
	}
	.sg {
		color: var(--acc-hi);
		font-weight: 600;
		text-align: left;
	}
	.sg:hover {
		text-decoration: underline;
	}
	.sum {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 18px;
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
		font: 500 13px/1.3 var(--code);
		color: var(--ink-2);
		background: var(--sunk);
	}
	.sum b {
		color: var(--ink);
	}
	.table-wrap {
		overflow-x: auto;
	}
	.what {
		min-width: 260px;
	}
	.ladder {
		list-style: none;
		margin: 0;
		padding: 8px 18px 18px;
		position: relative;
	}
	.ladder::before {
		content: '';
		position: absolute;
		left: 112px;
		top: 0;
		bottom: 0;
		width: 1px;
		background: var(--rule-2);
	}
	.ladder li {
		display: grid;
		grid-template-columns: 80px 28px minmax(0, 1fr);
		gap: 0;
		padding: 9px 0;
		align-items: start;
		font-size: 13px;
	}
	.ladder .t {
		font-size: 11.5px;
		color: var(--faint);
	}
	.ladder .t small {
		display: block;
		font-size: 10.5px;
	}
	.ladder .dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		border: 1.6px solid var(--dim);
		background: var(--plate);
		margin: 4px 0 0 9px;
		position: relative;
	}
	.ladder li.inc .dot {
		border-color: var(--acc);
	}
	.ladder li div {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 8px;
		align-items: baseline;
	}
	.ladder a.inc {
		font-size: 11.5px;
		color: var(--acc);
	}
	.none {
		padding: 18px;
		color: var(--dim);
		font-size: 13px;
		margin: 0;
	}
	.compact .bar {
		grid-template-columns: minmax(0, 1fr) auto;
	}
	.compact .kb {
		display: none;
	}
	.compact .acts {
		grid-column: 1 / -1;
	}
	@media (max-width: 760px) {
		.bar {
			grid-template-columns: minmax(0, 1fr);
			padding: 12px;
		}
		.kb {
			display: none;
		}
		.acts {
			flex-wrap: wrap;
		}
		.save {
			grid-template-columns: 1fr;
		}
	}
</style>
