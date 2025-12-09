<script lang="ts">
	// The incident as one flight strip (DESIGN.md §4): a holder edge for severity, the reference and the severity in
	// the first box, the title with who and what, reportability and the count of alerts, then the stage rail and the
	// ATT&CK chain along the strip's lower edge.
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
	const people = $derived(incident.identitiesDetail.filter((i) => i.kind !== 'service'));
	const running = $derived(clocks.filter((c) => c.incidentId === incident.id && c.state !== 'submitted').length);
</script>

<div class="strip sv-{incident.severity}" class:compact>
	<div class="holder" aria-hidden="true"></div>
	<div class="box id">
		<span class="ref mono">{incident.reference}</span>
		<span class="sev {incident.severity}"><span class="bars" aria-hidden="true">{SEV_BARS[incident.severity]}</span>{SEV_WORD[incident.severity]}</span>
	</div>
	<div class="box what">
		<h2 class="title">{incident.title}</h2>
		<div class="meta">
			{#each people as p (p.id)}<span><b>{p.displayName}</b> · {p.department.toLowerCase()}, {p.jurisdiction === 'EU' ? 'Antwerp' : p.jurisdiction === 'US' ? 'Memphis' : 'Melbourne'}</span>{/each}
			{#each incident.identitiesDetail.filter((i) => i.kind === 'service') as s (s.id)}<span>Account <b class="mono">{s.username}</b></span>{/each}
			{#each incident.assetsDetail.slice(0, compact ? 2 : 4) as a (a.id)}<span>{a.kind === 'workstation' ? 'Laptop' : a.kind === 'cloud_account' ? 'Cloud' : a.kind === 'server' ? 'Server' : 'Asset'} <b class="mono">{a.name}</b></span>{/each}
			{#if incident.assignee}<span>with <b>{incident.assignee.fullName}</b></span>{/if}
		</div>
	</div>
	<div class="box rep">
		<span class="k">Reporting</span>
		<span class="v">{reportable}</span>
	</div>
	<div class="box cnt">
		<span class="k">{STATUS_WORD[incident.status]}</span>
		<span class="v mono">{plural(incident.detectionCount, 'alert')} grouped</span>
	</div>
	<ol class="stages" aria-label="Stage">
		{#each STAGES as s, i (s)}
			{@const done = resolved || i < cur}
			{@const now = !resolved && i === cur}
			<li class="stg" class:done class:now class:wait={now && pending > 0} aria-current={now ? 'step' : undefined}>
				<span class="g" aria-hidden="true">{done ? '✓' : now && pending > 0 ? '‖' : now ? '●' : '○'}</span>
				<span class="nm">{STAGE_WORD[s]}</span>
				<small>
					{#if done && s !== 'report' && times[s]}{timeOf(times[s]!)}{:else if now && pending > 0}{plural(pending, 'approval')} waiting{:else if s === 'report'}{running ? plural(running, 'clock') : now ? 'nothing to file' : ''}{/if}
				</small>
			</li>
		{/each}
	</ol>
	{#if incident.attack.length}
		<div class="attck" aria-label="ATT&CK chain">
			<span class="k">ATT&amp;CK</span>
			{#each incident.attack.slice(0, compact ? 3 : 6) as t, i (t.techniqueId)}
				{#if i > 0}<span class="arr" aria-hidden="true">→</span>{/if}<span class="tq" title={t.tactics.join(', ')}><b class="mono">{t.techniqueId}</b> {t.name}</span>
			{/each}
		</div>
	{/if}
</div>

<style>
	.strip {
		display: grid;
		grid-template-columns: 10px minmax(150px, auto) minmax(0, 1fr) minmax(160px, 240px) minmax(130px, 170px);
		background: var(--plate);
		border: 1px solid var(--rule-2);
		--hold: var(--dim);
	}
	.sv-critical,
	.sv-high {
		--hold: var(--acc);
	}
	.sv-medium {
		--hold: var(--soon);
	}
	.holder {
		grid-row: 1 / span 3;
		background: var(--hold);
	}
	.sv-high .holder {
		background: repeating-linear-gradient(180deg, var(--acc) 0 10px, color-mix(in srgb, var(--acc) 45%, transparent) 10px 14px);
	}
	.box {
		padding: 14px 16px;
		border-left: 1px solid var(--rule);
		min-width: 0;
		display: grid;
		align-content: start;
		gap: 9px;
	}
	.box.id {
		border-left: 0;
	}
	.ref {
		font-size: 22px;
		font-weight: 500;
		color: var(--ink);
		letter-spacing: -0.01em;
	}
	.title {
		margin: 0;
		font: 800 34px/0.98 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--ink);
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 16px;
		font-size: 13.5px;
		color: var(--dim);
	}
	.meta b {
		color: var(--ink-2);
		font-weight: 600;
	}
	.meta b.mono {
		font-size: 12.5px;
		font-weight: 500;
	}
	.k {
		font: 700 12px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--faint);
	}
	.v {
		font: 600 14px/1.35 var(--ui);
		color: var(--ink);
	}
	.cnt .v {
		font-size: 13px;
		font-weight: 500;
		color: var(--ink-2);
	}
	.stages {
		grid-column: 2 / -1;
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		border-top: 1px solid var(--rule);
	}
	.stg {
		display: flex;
		align-items: baseline;
		gap: 8px;
		padding: 11px 16px;
		border-left: 1px solid var(--rule);
		min-width: 0;
		color: var(--faint);
	}
	.stg:first-child {
		border-left: 0;
	}
	.stg .g {
		font: 500 13px/1 var(--code);
	}
	.nm {
		font: 700 15px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
	}
	.stg small {
		font: 500 12px/1 var(--code);
		color: var(--faint);
		margin-left: auto;
		white-space: nowrap;
	}
	.stg.done {
		color: var(--ink-2);
	}
	.stg.done .g {
		color: var(--ok);
	}
	.stg.now {
		color: var(--ink);
		background: var(--acc-wash);
		box-shadow: inset 0 -2px 0 var(--acc);
	}
	.stg.now .g {
		color: var(--acc-hi);
	}
	.stg.wait {
		background: var(--person-wash);
		box-shadow: inset 0 -2px 0 var(--person);
	}
	.stg.wait .g,
	.stg.wait small {
		color: var(--person);
	}
	.attck {
		grid-column: 2 / -1;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 8px;
		padding: 10px 16px 12px;
		border-top: 1px solid var(--rule);
		font-size: 13px;
		color: var(--ink-2);
	}
	.attck .k {
		margin-right: 6px;
	}
	.tq {
		padding: 3px 7px;
		background: var(--raised);
		border-radius: 1px;
		white-space: nowrap;
	}
	.tq b {
		font-weight: 500;
		font-size: 12px;
		color: var(--acc-hi);
	}
	.arr {
		color: var(--faint);
	}
	.compact .title {
		font-size: 26px;
	}
	@media (max-width: 1180px) {
		.strip {
			grid-template-columns: 10px minmax(140px, auto) minmax(0, 1fr);
		}
		.holder {
			grid-row: 1 / span 5;
		}
		.rep,
		.cnt {
			grid-row: 2;
		}
		.rep {
			grid-column: 2;
			border-left: 0;
			border-top: 1px solid var(--rule);
		}
		.cnt {
			grid-column: 3;
			border-top: 1px solid var(--rule);
		}
	}
	@media (max-width: 760px) {
		.strip {
			grid-template-columns: 8px minmax(0, 1fr);
		}
		.box.what,
		.rep,
		.cnt,
		.stages,
		.attck {
			grid-column: 2;
			grid-row: auto;
			border-left: 0;
			border-top: 1px solid var(--rule);
		}
		.box.id {
			grid-template-columns: auto auto;
			justify-content: space-between;
			align-items: center;
		}
		.title {
			font-size: 26px;
		}
		.stages {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.stg:nth-child(3) {
			border-left: 0;
		}
		.stg:nth-child(n + 3) {
			border-top: 1px solid var(--rule);
		}
		.stg small {
			display: none;
		}
		.tq {
			white-space: normal;
		}
	}
</style>
