<script lang="ts">
	import Choice from '$lib/components/ui/Choice.svelte';
	// Unusual behaviour (DESIGN.md §6.9): each person or device is a rhythm strip, 24 h × 14 days, toned by how far
	// from their own normal. ◇ marks the outlier; reasons in plain words. No map, no score without reasons.
	import { api, problemMessage } from '$lib/api';
	import type { AnomalyDoc } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { now, timeOf } from '$lib/time/clock';

	let { compact = false }: { compact?: boolean } = $props();
	let kind = $state<'user' | 'host'>('user');
	let items = $state<AnomalyDoc[]>([]);
	let err = $state('');
	let expecting = $state<string | null>(null);
	let drafting = $state<string | null>(null);
	let draft = $state('');
	let expected = $state<Record<string, string>>({});
	let howLong = $state('until the end of the month');
	let expectWhy = $state('');

	$effect(() => {
		api.ueba
			.anomalies({ entityType: kind })
			.then((r) => {
				// one rhythm strip per person or device: the most unusual window (the list comes highest first)
				items = r.items.filter((a, i, all) => all.findIndex((b) => b.entityId === a.entityId) === i);
			})
			.catch((x) => (err = problemMessage(x)));
	});
	const refOf = (id: string | null) => live.incidents.find((i) => i.id === id)?.reference ?? null;
	function tone(v: number): string {
		if (v >= 0.95) return 'hot';
		if (v >= 0.6) return 'warm';
		if (v >= 0.3) return 'l3';
		if (v >= 0.15) return 'l2';
		if (v >= 0.06) return 'l1';
		return 'l0';
	}
	function daysFor(choice: string): number | undefined {
		const t = now();
		if (choice === 'for today') return 1;
		if (choice === 'for this week') return 7;
		if (choice === 'until the end of the month') return Math.max(1, new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate() - t.getDate() + 1);
		return undefined;
	}
	async function markExpected(a: AnomalyDoc) {
		try {
			await api.exceptions.add({ ruleId: `ueba:${a.entityType}:*`, field: a.entityType === 'user' ? 'user' : 'host', value: a.entityId, reason: expectWhy.trim(), expiresInDays: daysFor(howLong) });
			expected[a.id] = howLong;
			expecting = null;
			expectWhy = '';
			ui.toast(`Marked as expected ${howLong}. New alerts like this stay quiet; the change is in the activity log.`);
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
	function openDraft(a: AnomalyDoc) {
		const name = (a.label ?? a.entityId).split(' · ')[0];
		draft = `Hi, the watch noticed ${a.headline?.toLowerCase() ?? 'unusual activity'} for ${name}. Is this expected work? Nothing has been blocked. — ${session.name}`;
		drafting = a.id;
	}
	async function send(a: AnomalyDoc) {
		drafting = null;
		const ref = refOf(a.incidentId);
		if (ref && a.incidentId) {
			try {
				await api.incidents.note(a.incidentId, `Asked the manager about ${a.entityId}: ${draft}`);
			} catch (x) {
				ui.toast(problemMessage(x), 'bad');
				return;
			}
		}
		ui.toast('Message drafted and recorded. Their reply will appear here.');
	}
	const days = 14;
</script>

<div class="beh" class:compact>
	<div class="top">
		<span class="eng faint">Last 14 days</span>
		<Choice label="Show" bind:value={kind} options={[{ value: 'user', label: 'people' }, { value: 'host', label: 'devices' }]} />
		<span class="faint sort">Sorted by most unusual first</span>
	</div>
	{#if err}<p class="ferr">{err}</p>{/if}
	{#each items as a (a.id)}
		{@const ref = refOf(a.incidentId)}
		<article class="who" aria-labelledby="b-{a.id}">
			<header>
				<h3 id="b-{a.id}">{a.label ?? a.entityId}</h3>
				<p class="head"><span class="g" aria-hidden="true">◇</span><span class="sr">unusual behaviour:</span> {a.headline}{#if expected[a.id]} <span class="badge exp">expected · {expected[a.id]}</span>{/if}</p>
			</header>
			{#if a.rhythm}
				<div class="strip" role="img" aria-label="Activity by hour for 14 days; the outlier is {a.headline}">
					<div class="hours mono" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>
					{#each a.rhythm as row, d (d)}
						<div class="day">
							<span class="dl mono" aria-hidden="true">{d === days - 1 ? 'today' : d === 0 ? '14 d' : ''}</span>
							{#each row as v, h (h)}<i class={tone(v)}></i>{/each}
							{#if row.some((v) => v >= 0.95)}<span class="mk" aria-hidden="true">◇</span>{/if}
						</div>
					{/each}
				</div>
			{/if}
			<ul class="reasons">
				<li class="eng faint">Why</li>
				{#each a.reasons as r, i (i)}<li class:lower={r.contribution < 0}>{r.text}{i < a.reasons.length - 1 ? ' ·' : ''}</li>{/each}
			</ul>
			<div class="acts">
				{#if ref}<a class="btn preset-outlined-surface-300-700 btn-sm" href="/incidents/{ref}">Open {ref}</a>{:else}<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={() => ui.toast('Opening an incident by hand needs a person with incident rights; ask the watch lead.', 'info')} disabled={!session.can('incidents:write')}>Open as incident</button>{/if}
				<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={() => openDraft(a)}>Ask the manager (draft)</button>
				<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={() => (expecting = expecting === a.id ? null : a.id)} disabled={!session.can('rules:manage')} title={session.can('rules:manage') ? undefined : 'Analysts mark behaviour as expected'}>Mark as expected</button>
				<span class="mono faint win">{timeOf(a.windowStart)}–{timeOf(a.windowEnd)}</span>
			</div>
			{#if expecting === a.id}
				<form class="inline" onsubmit={(e) => (e.preventDefault(), markExpected(a))}>
					<label class="label"><span>Expected for how long?</span>
						<select class="select" bind:value={howLong}><option>for today</option><option>for this week</option><option>until the end of the month</option><option>from now on</option></select>
					</label>
					<label class="label wide"><span>Why (kept with the exception)</span><input class="input" bind:value={expectWhy} required minlength="10" placeholder="e.g. quarter-end reporting, agreed with the manager" /></label>
					<button class="btn preset-filled-surface-950-50 btn-sm" type="submit">Mark as expected</button>
				</form>
			{/if}
			{#if drafting === a.id}
				<form class="inline col" onsubmit={(e) => (e.preventDefault(), send(a))}>
					<label class="label"><span>Draft message to the manager</span><textarea class="textarea" rows="3" bind:value={draft}></textarea></label>
					<div class="row"><button class="btn preset-filled-surface-950-50 btn-sm" type="submit">Record and send</button><button class="lnk" type="button" onclick={() => (drafting = null)}>Cancel</button></div>
				</form>
			{/if}
		</article>
	{:else}
		<p class="none">Nothing unusual in the last 14 days.</p>
	{/each}
</div>

<style>
	.top {
		display: flex;
		gap: 12px;
		align-items: center;
		padding: 0 0 12px;
		font-size: 14px;
	}
	.top .eng {
		font-size: 13px;
	}
	.sort {
		margin-left: auto;
	}
	/* one tape per person or device: who and why on the left, their fortnight punched on the right */
	.who {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 600px);
		grid-template-areas: 'h s' 'r s' 'a s' 'f f';
		align-content: start;
		gap: 10px 28px;
		padding: 18px 20px 20px;
		margin-bottom: 6px;
		background: var(--plate);
		border: 1px solid var(--rule);
		border-left: 5px solid var(--soon);
	}
	.who > header {
		grid-area: h;
	}
	.strip {
		grid-area: s;
	}
	.reasons {
		grid-area: r;
	}
	.acts {
		grid-area: a;
	}
	.inline {
		grid-area: f;
	}
	h3 {
		margin: 0;
		font: 800 28px/0.95 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.head {
		margin: 8px 0 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--ink);
	}
	.head .g {
		color: var(--soon);
		font-family: var(--code);
		margin-right: 4px;
	}
	.exp {
		color: var(--ok);
		box-shadow: inset 0 0 0 1px var(--ok);
	}
	.strip {
		display: grid;
		gap: 2px;
		padding: 10px;
		background: var(--sunk);
		align-self: start;
	}
	.hours {
		display: flex;
		justify-content: space-between;
		margin-left: 44px;
		margin-right: 18px;
		font-size: 10.5px;
		color: var(--faint);
	}
	.day {
		display: grid;
		grid-template-columns: 40px repeat(24, minmax(0, 1fr)) 14px;
		gap: 2px;
		align-items: center;
	}
	.dl {
		font: 500 10.5px/1 var(--code);
		color: var(--faint);
	}
	.day i {
		height: 10px;
		border-radius: 0;
		background: color-mix(in srgb, var(--rule) 70%, transparent);
	}
	.day i.l1 {
		background: color-mix(in srgb, var(--acc) 22%, var(--sunk));
	}
	.day i.l2 {
		background: color-mix(in srgb, var(--acc) 42%, var(--sunk));
	}
	.day i.l3 {
		background: color-mix(in srgb, var(--acc) 66%, var(--sunk));
	}
	.day i.warm {
		background: color-mix(in srgb, var(--soon) 60%, var(--sunk));
	}
	.day i.hot {
		background: var(--soon);
		box-shadow: 0 0 0 1px var(--soon);
	}
	.mk {
		color: var(--soon);
		font-size: 10.5px;
		font-family: var(--code);
	}
	.reasons {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 6px;
		font-size: 14px;
		color: var(--ink-2);
		align-items: baseline;
	}
	.reasons .eng {
		font-size: 12px;
		margin-right: 4px;
	}
	.reasons .lower {
		color: var(--dim);
	}
	.acts {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
	}
	.win {
		margin-left: auto;
		font-size: 12.5px;
	}
	.inline {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		align-items: end;
		max-width: 760px;
	}
	.inline .wide {
		flex: 1 1 240px;
	}
	.inline.col {
		display: grid;
	}
	.row {
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.none {
		padding: 18px 0;
		color: var(--dim);
	}
	.compact .who {
		grid-template-columns: minmax(0, 1fr);
		grid-template-areas: 'h' 's' 'r' 'a' 'f';
		margin: 0;
		border-width: 0 0 1px 3px;
	}
	.compact .top {
		padding: 12px 14px;
	}
	@media (max-width: 1100px) {
		.who {
			grid-template-columns: minmax(0, 1fr);
			grid-template-areas: 'h' 's' 'r' 'a' 'f';
		}
	}
	@media (max-width: 760px) {
		.who {
			padding: 14px;
		}
		.day {
			grid-template-columns: 30px repeat(24, minmax(0, 1fr)) 10px;
			gap: 1px;
		}
		.hours {
			margin-left: 32px;
		}
		.sort,
		.win {
			display: none;
		}
	}
</style>
