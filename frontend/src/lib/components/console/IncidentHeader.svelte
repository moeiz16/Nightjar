<script lang="ts">
	// Incident header: severity bars, reportability, title, who and what, the ATT&CK chain and the stage rail.
	import type { Incident, Deadline } from '$lib/api/types';
	import { SEV_BARS, SEV_WORD, STAGES, STAGE_WORD, STATUS_WORD, regimeShort, plural } from '$lib/util/labels';
	import { timeOf } from '$lib/time/clock';

	let { incident, times, clocks = [], pending = 0, compact = false }: { incident: Incident; times: Record<string, string | null>; clocks?: Deadline[]; pending?: number; compact?: boolean } = $props();

	const reportable = $derived.by(() => {
		const yes = incident.regimes.filter((r) => r.determination?.applies);
		if (yes.length) return `Reportable: ${yes.map((r) => `${regimeShort(r.regime)} (${r.jurisdiction})`).join(', ')}`;
		const open = incident.regimes.filter((r) => r.proposal.applies === 'assess' && !r.determination);
		if (open.length) return `To assess: ${open.map((r) => regimeShort(r.regime)).join(', ')}`;
		return 'Not reportable';
	});
	const cur = $derived(STAGES.indexOf(incident.stage));
	const resolved = $derived(incident.status === 'resolved' || incident.status === 'closed');
	const people = $derived(incident.identitiesDetail.filter((i) => i.kind === 'person'));
	const running = $derived(clocks.filter((c) => c.incidentId === incident.id && c.state !== 'submitted').length);
</script>

<div class="head" class:compact>
	<div class="top">
		<span class="sev {incident.severity}"><span class="bars" aria-hidden="true">{SEV_BARS[incident.severity]}</span>{SEV_WORD[incident.severity]}</span>
		<span aria-hidden="true">·</span><span>{reportable}</span>
		<span aria-hidden="true">·</span><span>{plural(incident.detectionCount, 'alert')} grouped</span>
		<span aria-hidden="true" class="hide-s">·</span><span class="hide-s">{STATUS_WORD[incident.status]}</span>
	</div>
	<h1>{incident.title}</h1>
	<div class="meta">
		{#each people as p (p.id)}<span><b>{p.displayName}</b> · {p.department.toLowerCase()}, {p.jurisdiction === 'EU' ? 'Antwerp' : p.jurisdiction === 'US' ? 'Memphis' : 'Melbourne'}</span>{/each}
		{#each incident.identitiesDetail.filter((i) => i.kind === 'service') as s (s.id)}<span>Account <b class="mono">{s.username}</b></span>{/each}
		{#each incident.assetsDetail.slice(0, compact ? 2 : 4) as a (a.id)}<span>{a.kind === 'workstation' ? 'Laptop' : a.kind === 'cloud_account' ? 'Cloud' : a.kind === 'server' ? 'Server' : 'Asset'} <b class="mono">{a.name}</b></span>{/each}
		{#if incident.assignee}<span>with <b>{incident.assignee.fullName}</b></span>{/if}
	</div>
	{#if incident.attack.length}
		<div class="attck" aria-label="ATT&CK chain">
			<span>ATT&amp;CK</span>
			{#each incident.attack.slice(0, compact ? 3 : 6) as t, i (t.techniqueId)}
				{#if i > 0}<span aria-hidden="true">→</span>{/if}<span class="tag" title={t.tactics.join(', ')}>{t.techniqueId} {t.name}</span>
			{/each}
		</div>
	{/if}
	<ol class="stages" aria-label="Stage">
		{#each STAGES as s, i (s)}
			{@const done = resolved || i < cur || (s === 'report' && false)}
			{@const now = !resolved && i === cur}
			{#if i > 0}<li class="ln" class:done={done || now} aria-hidden="true"></li>{/if}
			<li class="stg" class:done class:now class:wait={now && pending > 0} aria-current={now ? 'step' : undefined}>
				<span class="k">{done ? '✓' : now && pending > 0 ? '‖' : now ? '●' : '○'}</span>{STAGE_WORD[s]}
				<small>
					{#if done && s !== 'report' && times[s]}{timeOf(times[s]!)}{:else if now && pending > 0}{plural(pending, 'approval')} waiting{:else if s === 'report'}{running ? plural(running, 'clock') : now ? 'nothing to file' : ''}{/if}
				</small>
			</li>
		{/each}
	</ol>
</div>

<style>
	.head {
		padding: 16px 22px 14px;
		border-bottom: 1px solid var(--line);
	}
	.top {
		display: flex;
		align-items: center;
		gap: 10px;
		font: 400 12px/1.3 var(--mono);
		color: var(--muted);
		flex-wrap: wrap;
	}
	h1 {
		margin: 10px 0 8px;
		font: 600 23px/1.2 var(--sans);
		letter-spacing: -0.015em;
		text-wrap: balance;
	}
	.compact h1 {
		font-size: 20px;
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 16px;
		font-size: 13px;
		color: var(--muted);
	}
	.meta b {
		color: var(--ink-2);
		font-weight: 500;
	}
	.attck {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin-top: 10px;
		align-items: center;
		font: 400 11.5px/1 var(--mono);
		color: var(--faint);
	}
	.stages {
		display: flex;
		align-items: center;
		margin: 14px 0 0;
		padding: 0;
		list-style: none;
		font: 400 12px/1 var(--mono);
	}
	.stg {
		display: flex;
		align-items: center;
		gap: 7px;
		color: var(--muted);
		white-space: nowrap;
	}
	.k {
		width: 14px;
		text-align: center;
	}
	.stg.done .k {
		color: var(--teal);
	}
	.stg.now {
		color: var(--ink);
	}
	.stg.now .k {
		color: var(--teal);
	}
	.stg.wait .k {
		color: var(--lav);
	}
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.ln {
		flex: 1;
		height: 1px;
		background: var(--line-strong);
		margin: 0 12px;
		min-width: 14px;
	}
	.ln.done {
		background: var(--teal-line);
	}
	@media (max-width: 760px) {
		.head {
			padding: 14px 14px 12px;
		}
		h1 {
			font-size: 19px;
		}
		.stages {
			flex-wrap: wrap;
			gap: 8px 0;
		}
		.stages small,
		.hide-s {
			display: none;
		}
		.ln {
			margin: 0 6px;
			min-width: 8px;
		}
	}
</style>
