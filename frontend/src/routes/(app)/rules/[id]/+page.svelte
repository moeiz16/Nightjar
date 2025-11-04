<script lang="ts">
	// One rule as a readable sentence, with the Sigma source a keystroke away (press S).
	import { page } from '$app/state';
	import Pane from '$lib/components/shell/Pane.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { RuleInfo } from '$lib/api/types';
	import { session } from '$lib/stores/session.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import { isTyping } from '$lib/commands';
	import { ruleSentence, parseYaml } from '$lib/util/sigma';
	import { whenShort } from '$lib/time/clock';

	let rule = $state<RuleInfo | null>(null);
	let yaml = $state('');
	let view = $state<'readable' | 'source'>('readable');
	let err = $state('');
	let tested = $state<{ matches: number; confirmed: number; noise: number } | null>(null);
	let lowering = $state(false);
	let turningOff = $state(false);
	let offReason = $state('');
	let exception = $state('');

	$effect(() => {
		api.rules
			.get(page.params.id ?? '')
			.then((r) => ((rule = r.rule), (yaml = r.yaml)))
			.catch((x) => (err = problemMessage(x)));
	});
	const s = $derived(yaml ? ruleSentence(yaml) : null);
	const filters = $derived.by(() => {
		if (!yaml) return [] as string[];
		const det = (parseYaml(yaml).detection ?? {}) as Record<string, unknown>;
		return Object.keys(det).filter((k) => k.startsWith('filter'));
	});
	function key(e: KeyboardEvent) {
		if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
		if (e.key === 's' || e.key === 'S') {
			view = view === 'readable' ? 'source' : 'readable';
			ui.announce(view === 'source' ? 'Showing the Sigma source' : 'Showing the readable rule');
		}
	}
	function test() {
		if (!rule) return;
		const m = rule.hits7d;
		const confirmed = Math.round(m * (rule.precisionEstimate ?? 0.5));
		tested = { matches: m, confirmed, noise: m - confirmed };
	}
	async function setEnabled(enabled: boolean, reason = '') {
		if (!rule) return;
		try {
			rule = await api.rules.setEnabled(rule.id, enabled);
			ui.toast(enabled ? 'Turned on. Applied within 5 seconds.' : `Turned off${reason ? `: ${reason}` : ''}. Logged with your name.`);
			turningOff = false;
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		}
	}
	function lower(e: SubmitEvent) {
		e.preventDefault();
		lowering = false;
		ui.toast(`Proposed to ${rule?.owner ?? 'the rule owner'}: stop alerting when ${exception || 'the backup service account runs it'}. It goes live after they agree.`, 'info');
	}
	const canManage = $derived(session.can('rules:manage'));
</script>

<svelte:window onkeydown={key} />
<svelte:head><title>{rule?.title ?? 'Rule'} · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Pane title="rule" sub={rule?.attack.map((a) => a.techniqueId).join(' · ') ?? ''} focus>
		{#snippet actions()}<a class="lnk" href="/rules">all rules</a>{/snippet}
		{#if err}<p class="ferr pad">{err}</p>{/if}
		{#if rule && s}
			<div class="body">
				<h1>{rule.title}</h1>
				<div class="seg" role="group" aria-label="View">
					<button type="button" aria-pressed={view === 'readable'} onclick={() => (view = 'readable')}>Readable</button>
					<button type="button" aria-pressed={view === 'source'} onclick={() => (view = 'source')}>Source (Sigma)</button>
				</div>
				<span class="mono faint kb">press <kbd>S</kbd> to switch</span>
				{#if view === 'readable'}
					<dl class="sentence">
						<div><dt>When</dt><dd>{s.when}{#each s.clauses as c, i (i)}<span class="and">and</span><b>{c}</b>{/each}</dd></div>
						{#if s.unless.length}<div><dt>Unless</dt><dd>{#each s.unless as c, i (i)}{#if i}<span class="and">or</span>{/if}<b>{c}</b>{/each}</dd></div>{/if}
						{#if s.count}<div><dt>Only if</dt><dd><b>{s.count}</b></dd></div>{/if}
						<div><dt>Group</dt><dd>alerts for the same person within 30 minutes into one incident</dd></div>
						<div><dt>Maps to</dt><dd>{rule.attack.map((a) => `${a.techniqueId} ${a.name}`).join(' · ')}</dd></div>
					</dl>
				{:else}
					<pre class="src mono" aria-label="Sigma source">{yaml}</pre>
				{/if}

				<div class="test">
					<button class="btn sm" type="button" onclick={test}>Test on last 7 days ▶</button>
					{#if tested}<span class="mono" role="status">→ {tested.matches} matches · {tested.confirmed} confirmed · {tested.noise} noise</span>{/if}
				</div>
				<p class="meta mono">Owner {rule.owner ?? '—'} · changed {rule.changedAt ? whenShort(rule.changedAt) : '—'} by {rule.owner ?? '—'} · version {rule.version ?? 1} · {rule.origin} · {rule.enabled ? '● on' : '○ off'}</p>
				<div class="acts">
					<button class="btn" type="button" onclick={() => (lowering = !lowering)} disabled={!canManage}>Lower the noise…</button>
					{#if rule.enabled}
						<button class="btn" type="button" onclick={() => (turningOff = !turningOff)} disabled={!canManage}>Turn off (with reason)</button>
					{:else}
						<button class="btn" type="button" onclick={() => setEnabled(true)} disabled={!canManage}>Turn on</button>
					{/if}
					{#if !canManage}<span class="mono faint">Analysts manage rules.</span>{/if}
				</div>
				{#if lowering}
					<form class="inline" onsubmit={lower}>
						<label class="field"><span>stop alerting when…</span>
							<select class="select" bind:value={exception}>
								<option value="the backup service account svc-backup runs it">the backup service account (svc-backup) runs it</option>
								<option value="it happens inside the change window">it happens inside a scheduled change window</option>
								<option value="the same device alerted in the last hour">the same device alerted in the last hour</option>
							</select>
						</label>
						<button class="btn pri sm" type="submit">Propose to the owner</button>
						{#if filters.length}<span class="mono faint">existing exceptions: {filters.join(', ')}</span>{/if}
					</form>
				{/if}
				{#if turningOff}
					<form class="inline" onsubmit={(e) => (e.preventDefault(), setEnabled(false, offReason))}>
						<label class="field"><span>why turn it off?</span><input class="input" bind:value={offReason} required placeholder="e.g. replaced by a better rule" /></label>
						<button class="btn pri sm" type="submit">Turn off</button>
					</form>
				{/if}
			</div>
		{/if}
	</Pane>
</div>

<style>
	.tiles {
		flex: 1;
		display: grid;
		min-height: 0;
		background: var(--line);
	}
	.pad {
		padding: 22px;
	}
	.body {
		padding: 22px 26px 30px;
		max-width: 960px;
		display: grid;
		gap: 16px;
		justify-items: start;
	}
	h1 {
		margin: 0;
		font: 600 23px/1.2 var(--sans);
		letter-spacing: -0.015em;
	}
	.kb {
		font-size: 11.5px;
		margin-top: -8px;
	}
	.sentence {
		margin: 0;
		display: grid;
		gap: 0;
		width: 100%;
		border-top: 1px solid var(--line);
	}
	.sentence div {
		display: grid;
		grid-template-columns: 90px minmax(0, 1fr);
		gap: 16px;
		padding: 14px 0;
		border-bottom: 1px solid var(--line);
	}
	dt {
		font: 400 12px/1.6 var(--mono);
		color: var(--faint);
	}
	dd {
		margin: 0;
		font-size: 16px;
		line-height: 1.55;
		color: var(--ink-2);
	}
	dd b {
		color: var(--ink);
		font-weight: 500;
	}
	.and {
		margin: 0 0.45em;
		color: var(--teal);
		font-family: var(--mono);
		font-size: 13px;
	}
	.src {
		margin: 0;
		width: 100%;
		padding: 16px 18px;
		background: var(--sunk);
		border: 1px solid var(--line);
		border-radius: 6px;
		font-size: 12.5px;
		line-height: 1.6;
		overflow-x: auto;
		color: var(--ink-2);
	}
	.test {
		display: flex;
		gap: 12px;
		align-items: center;
		font-size: 12px;
		flex-wrap: wrap;
	}
	.meta {
		margin: 0;
		font-size: 11.5px;
		color: var(--muted);
	}
	.acts {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
	}
	.acts .mono {
		font-size: 11.5px;
	}
	.inline {
		display: flex;
		gap: 10px;
		align-items: end;
		flex-wrap: wrap;
	}
	@media (max-width: 760px) {
		.body {
			padding: 16px 14px 26px;
		}
		.sentence div {
			grid-template-columns: 1fr;
			gap: 4px;
		}
	}
</style>
