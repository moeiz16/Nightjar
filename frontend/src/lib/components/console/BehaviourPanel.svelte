<script lang="ts">
	// Unusual behaviour (DESIGN.md §6.9): each person or device is a rhythm strip, 24 h × 14 days, toned by how far
	// from their own normal. ◇ marks the outlier; reasons in plain words. No map, no score without reasons.
	import { api, problemMessage } from '$lib/api';
	import type { AnomalyDoc } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { live } from '$lib/stores/live.svelte';
	import { timeOf } from '$lib/time/clock';

	let { compact = false }: { compact?: boolean } = $props();
	let kind = $state<'user' | 'host'>('user');
	let items = $state<AnomalyDoc[]>([]);
	let err = $state('');
	let expecting = $state<string | null>(null);
	let drafting = $state<string | null>(null);
	let draft = $state('');
	let expected = $state<Record<string, string>>({});
	let howLong = $state('until the end of the month');

	$effect(() => {
		api.ueba
			.anomalies({ entityType: kind })
			.then((r) => (items = r.items))
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
	function markExpected(a: AnomalyDoc) {
		expected[a.id] = howLong;
		expecting = null;
		ui.toast(`Marked as expected ${howLong}. The change is in the activity log.`);
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
		<span class="mono faint">last 14 days ·</span>
		<span class="seg" role="group" aria-label="Show">
			<button type="button" aria-pressed={kind === 'user'} onclick={() => (kind = 'user')}>people</button>
			<button type="button" aria-pressed={kind === 'host'} onclick={() => (kind = 'host')}>devices</button>
		</span>
		<span class="mono faint sort">sorted by: most unusual</span>
	</div>
	{#if err}<p class="ferr">{err}</p>{/if}
	{#each items as a (a.id)}
		{@const ref = refOf(a.incidentId)}
		<article class="who" aria-labelledby="b-{a.id}">
			<header>
				<h3 id="b-{a.id}">{a.label ?? a.entityId}</h3>
				<p class="head"><span class="g" aria-hidden="true">◇</span><span class="sr">unusual behaviour:</span> {a.headline}{#if expected[a.id]} <span class="chip">expected · {expected[a.id]}</span>{/if}</p>
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
				<li class="mono faint">reasons:</li>
				{#each a.reasons as r, i (i)}<li class:lower={r.contribution < 0}>{r.text}{i < a.reasons.length - 1 ? ' ·' : ''}</li>{/each}
			</ul>
			<div class="acts">
				{#if ref}<a class="btn sm" href="/incidents/{ref}">Open {ref}</a>{:else}<button class="btn sm" type="button" onclick={() => ui.toast('Opening an incident by hand needs a person with incident rights; ask the watch lead.', 'info')} disabled={!session.can('incidents:write')}>Open as incident</button>{/if}
				<button class="btn sm" type="button" onclick={() => openDraft(a)}>Ask the manager (draft)</button>
				<button class="btn sm" type="button" onclick={() => (expecting = expecting === a.id ? null : a.id)} disabled={!session.can('incidents:write')}>Mark as expected</button>
				<span class="mono faint win">{timeOf(a.windowStart)}–{timeOf(a.windowEnd)}</span>
			</div>
			{#if expecting === a.id}
				<form class="inline" onsubmit={(e) => (e.preventDefault(), markExpected(a))}>
					<label class="field"><span>expected for how long?</span>
						<select class="select" bind:value={howLong}><option>for today</option><option>for this week</option><option>until the end of the month</option><option>from now on</option></select>
					</label>
					<button class="btn pri sm" type="submit">Mark as expected</button>
				</form>
			{/if}
			{#if drafting === a.id}
				<form class="inline col" onsubmit={(e) => (e.preventDefault(), send(a))}>
					<label class="field"><span>draft message to the manager</span><textarea class="textarea" rows="3" bind:value={draft}></textarea></label>
					<div class="row"><button class="btn pri sm" type="submit">Record and send</button><button class="lnk" type="button" onclick={() => (drafting = null)}>cancel</button></div>
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
		padding: 12px 18px;
		border-bottom: 1px solid var(--line);
		font-size: 12px;
	}
	.sort {
		margin-left: auto;
	}
	.who {
		padding: 16px 18px 18px;
		border-bottom: 1px solid var(--line);
		display: grid;
		gap: 10px;
	}
	h3 {
		margin: 0;
		font: 600 14.5px/1.3 var(--sans);
	}
	.head {
		margin: 4px 0 0;
		font-size: 13px;
		color: var(--ink);
	}
	.head .g {
		color: var(--amber);
		font-family: var(--mono);
		margin-right: 4px;
	}
	.strip {
		display: grid;
		gap: 2px;
		max-width: 640px;
	}
	.hours {
		display: flex;
		justify-content: space-between;
		margin-left: 44px;
		margin-right: 18px;
		font-size: 10px;
		color: var(--faint);
	}
	.day {
		display: grid;
		grid-template-columns: 40px repeat(24, minmax(0, 1fr)) 14px;
		gap: 2px;
		align-items: center;
	}
	.dl {
		font-size: 10px;
		color: var(--faint);
	}
	.day i {
		height: 9px;
		border-radius: 1px;
		background: var(--raised);
	}
	.day i.l1 {
		background: color-mix(in srgb, var(--teal) 10%, var(--raised));
	}
	.day i.l2 {
		background: color-mix(in srgb, var(--teal) 22%, var(--raised));
	}
	.day i.l3 {
		background: color-mix(in srgb, var(--teal) 38%, var(--raised));
	}
	.day i.warm {
		background: color-mix(in srgb, var(--amber) 55%, var(--raised));
	}
	.day i.hot {
		background: var(--amber);
	}
	.mk {
		color: var(--amber);
		font-size: 10px;
		font-family: var(--mono);
	}
	.reasons {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 6px;
		font-size: 12.5px;
		color: var(--ink-2);
	}
	.reasons .mono {
		font-size: 11.5px;
	}
	.reasons .lower {
		color: var(--muted);
	}
	.acts {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
	}
	.win {
		margin-left: auto;
		font-size: 11.5px;
	}
	.inline {
		display: flex;
		gap: 10px;
		align-items: end;
		max-width: 520px;
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
		padding: 18px;
		color: var(--muted);
	}
	@media (max-width: 760px) {
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
