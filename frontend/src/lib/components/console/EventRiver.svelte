<script lang="ts">
	// The event river (DESIGN.md §5): grouped by minute, unusual behaviour ◇ with an amber rule, incident-related ▸
	// with a teal rule, routine events folded into a dashed block.
	import { fly } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import { live, groupByMinute, minuteTotal } from '$lib/stores/live.svelte';
	import { timeOf } from '$lib/time/clock';
	import { plural } from '$lib/util/labels';

	let { landing = false }: { landing?: boolean } = $props();
	let expanded = $state<Record<string, boolean>>({});
	const ROUTINE_SHOWN = 3;

	$effect(() => {
		live.holdRiver = prefersReducedMotion.current;
	});
	const groups = $derived(groupByMinute(live.river).slice(0, 8));
	const refs = $derived(Object.fromEntries(live.incidents.map((i) => [i.id, i.reference])));
	const DS: Record<string, string> = {
		'okta.system': 'sign-in',
		'mail.gateway': 'mail',
		'm365.audit': 'cloud app',
		'edr.alert': 'device',
		'windows.security': 'server',
		'windows.sysmon': 'device',
		'vpn.syslog': 'network',
		'aws.cloudtrail': 'cloud',
		'aws.s3access': 'cloud',
		'nightjar.synthetic': 'nightjar'
	};
</script>

<div class="river" aria-live="off">
	{#if live.held.length}
		<button class="newer mono" type="button" onclick={() => live.showHeld()}>{live.held.length} new · show</button>
	{/if}
	{#each groups as g (g.key)}
		{@const notable = g.lines.filter((l) => l.anomaly || l.incidentId)}
		{@const routine = g.lines.filter((l) => !l.anomaly && !l.incidentId)}
		{@const total = minuteTotal(live.counts[g.key], g.lines.length)}
		{@const shown = expanded[g.key] ? routine : routine.slice(0, ROUTINE_SHOWN)}
		<section aria-label="Minute {g.label}">
			<h3 class="minute mono">{g.label} <span>{total.toLocaleString('en-GB')} in · {notable.length ? `${notable.length} to look at` : 'all routine'}</span></h3>
			{#each [...notable, ...shown].sort((a, b) => b.at.localeCompare(a.at)) as l (l.eventId)}
				<div class="ev" class:odd={l.anomaly} class:inc={!!l.incidentId} in:fly={{ y: -6, duration: prefersReducedMotion.current ? 0 : 320 }}>
					<span class="g mono" aria-hidden="true">{l.anomaly ? '◇' : l.incidentId ? '▸' : '·'}</span>
					<p class:raw={!l.incidentId && !l.anomaly} class:resolve={!!l.incidentId}>
						{#if l.incidentId && !landing}<a href="/incidents/{refs[l.incidentId] ?? l.incidentId}">{l.text}</a>{:else}{l.text}{/if}
						<small>{timeOf(l.at, undefined, true)} · {l.anomaly ? 'unusual behaviour' : l.incidentId ? `${refs[l.incidentId] ?? 'incident'} · awaiting you` : (DS[l.dataset] ?? l.dataset)}</small>
					</p>
					{#if l.anomaly}<span class="sr">unusual behaviour</span>{/if}
				</div>
			{/each}
			{#if total - shown.length - notable.length > 0}
				<button class="fold mono" type="button" onclick={() => (expanded[g.key] = !expanded[g.key])} aria-expanded={!!expanded[g.key]}>
					+ {plural(Math.max(0, total - shown.length - notable.length), 'routine event')} folded · {expanded[g.key] ? 'fold' : 'show'}
				</button>
			{/if}
		</section>
	{:else}
		<p class="emptyr faint">Waiting for the first events…</p>
	{/each}
</div>

<style>
	/* routine lines read as the log they are; a line that joins an incident resolves into plain words */
	.ev p.raw {
		font: 400 12.5px/18px var(--code);
		color: var(--dim);
	}
	.river {
		position: relative;
	}
	.minute {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 0;
		padding: 12px 14px 6px;
		font-size: 12px;
		font-weight: 500;
		line-height: 1;
		color: var(--ink);
		white-space: nowrap;
	}
	.minute::after {
		content: '';
		flex: 1;
		height: 1px;
		background: var(--rule);
	}
	.minute span {
		color: var(--faint);
		font-weight: 400;
	}
	.ev {
		display: grid;
		grid-template-columns: 14px minmax(0, 1fr);
		gap: 8px;
		padding: 6px 14px 6px 12px;
		border-left: 2px solid transparent;
	}
	.ev .g {
		font-size: 12px;
		line-height: 18px;
		color: var(--faint);
		text-align: center;
	}
	.ev p {
		margin: 0;
		font-size: 14px;
		line-height: 19px;
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}
	.ev p a {
		text-decoration: none;
	}
	.ev p a:hover {
		text-decoration: underline;
	}
	.ev small {
		display: block;
		font-family: var(--code);
		font-size: 11.5px;
		line-height: 16px;
		color: var(--faint);
	}
	.ev.odd {
		border-left-color: var(--soon);
		background: color-mix(in srgb, var(--soon) 5%, transparent);
	}
	.ev.odd .g {
		color: var(--soon);
	}
	.ev.odd p {
		color: var(--ink);
	}
	.ev.inc {
		border-left-color: var(--acc);
	}
	.ev.inc .g {
		color: var(--acc-hi);
	}
	.ev.inc p {
		color: var(--ink);
		font-weight: 600;
	}
	.fold {
		display: block;
		width: calc(100% - 40px);
		margin: 2px 14px 2px 26px;
		padding: 6px 10px;
		border: 1px dashed var(--rule-2);
		border-radius: 2px;
		font-size: 12px;
		line-height: 1.3;
		color: var(--faint);
		text-align: left;
	}
	.fold:hover {
		color: var(--ink-2);
	}
	.newer {
		position: sticky;
		top: 6px;
		z-index: 2;
		display: block;
		margin: 6px auto;
		padding: 5px 10px;
		border-radius: 2px;
		background: var(--raised);
		border: 1px solid var(--acc-line);
		color: var(--acc-hi);
		font-size: 11.5px;
	}
	.emptyr {
		padding: 20px 14px;
		font-size: 12px;
	}
</style>
