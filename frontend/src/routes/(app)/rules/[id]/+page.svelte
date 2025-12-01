<script lang="ts">
	// One rule as a readable sentence, with the Sigma source a keystroke away (press S).
	import { page } from '$app/state';
	import Band from '$lib/components/ui/Band.svelte';
	import Choice from '$lib/components/ui/Choice.svelte';
	import { api, problemMessage } from '$lib/api';
	import type { RuleException, RuleInfo } from '$lib/api/types';
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
	let exceptions = $state<RuleException[]>([]);
	let exField = $state<'user' | 'host' | 'ip'>('user');
	let exValue = $state('');
	let exReason = $state('');
	let exDays = $state('90');
	let exBusy = $state(false);
	const FIELD_WORDS = { user: 'person', host: 'device', ip: 'address' } as const;

	$effect(() => {
		api.rules
			.get(page.params.id ?? '')
			.then((r) => ((rule = r.rule), (yaml = r.yaml), (exceptions = r.exceptions ?? [])))
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
	async function lower(e: SubmitEvent) {
		e.preventDefault();
		if (!rule) return;
		exBusy = true;
		try {
			const added = await api.exceptions.add({ ruleId: rule.id, field: exField, value: exValue.trim(), reason: exReason.trim(), expiresInDays: exDays ? Number(exDays) : undefined });
			exceptions = [...exceptions, added];
			rule = { ...rule, exceptions: (rule.exceptions ?? 0) + 1 };
			lowering = false;
			exValue = exReason = '';
			ui.toast(`Exception added: ${FIELD_WORDS[added.field]} ${added.value}. Applied within 30 seconds and logged with your name.`);
		} catch (x) {
			ui.toast(problemMessage(x), 'bad');
		} finally {
			exBusy = false;
		}
	}
	async function removeException(x: RuleException) {
		try {
			await api.exceptions.remove(x.id);
			exceptions = exceptions.filter((e) => e.id !== x.id);
			if (rule) rule = { ...rule, exceptions: Math.max(0, (rule.exceptions ?? 1) - 1) };
			ui.toast(`Exception removed: ${FIELD_WORDS[x.field]} ${x.value}.`);
		} catch (e) {
			ui.toast(problemMessage(e), 'bad');
		}
	}
	const canManage = $derived(session.can('rules:manage'));
</script>

<svelte:window onkeydown={key} />
<svelte:head><title>{rule?.title ?? 'Rule'} · Nightjar SOC</title></svelte:head>
<div class="tiles">
	<Band title="rule" sub={rule?.attack.map((a) => a.techniqueId).join(' · ') ?? ''} fill>
		{#snippet actions()}<a class="lnk" href="/rules">all rules</a>{/snippet}
		{#if err}<p class="ferr pad">{err}</p>{/if}
		{#if rule && s}
			<div class="body">
				<h1>{rule.title}</h1>
				<Choice label="View" size="md" bind:value={view} options={[{ value: 'readable', label: 'Readable' }, { value: 'source', label: 'Source (Sigma)' }]} />
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
					<button class="btn preset-outlined-surface-300-700 btn-sm" type="button" onclick={test}>Test on last 7 days ▶</button>
					{#if tested}<span class="mono" role="status">→ {tested.matches} matches · {tested.confirmed} confirmed · {tested.noise} noise</span>{/if}
				</div>
				<p class="meta mono">{rule.hits7d.toLocaleString('en-GB')} {rule.hits7d === 1 ? 'alert' : 'alerts'} in 7 days · {rule.origin} · {rule.enabled ? '● on' : '○ off'} · {exceptions.length === 1 ? '1 exception' : `${exceptions.length} exceptions`}</p>
				{#if exceptions.length}
					<ul class="excs" aria-label="Exceptions">
						{#each exceptions as x (x.id)}
							<li>
								<span><b>{FIELD_WORDS[x.field]} {x.value}</b> · {x.reason}</span>
								<span class="mono faint">{x.createdBy} · {whenShort(x.createdAt)}{x.expiresAt ? ` · until ${whenShort(x.expiresAt)}` : ''}</span>
								{#if canManage}<button class="lnk" type="button" onclick={() => removeException(x)}>remove</button>{/if}
							</li>
						{/each}
					</ul>
				{/if}
				<div class="acts">
					<button class="btn preset-outlined-surface-300-700" type="button" onclick={() => (lowering = !lowering)} disabled={!canManage}>Lower the noise…</button>
					{#if rule.enabled}
						<button class="btn preset-outlined-surface-300-700" type="button" onclick={() => (turningOff = !turningOff)} disabled={!canManage}>Turn off (with reason)</button>
					{:else}
						<button class="btn preset-outlined-surface-300-700" type="button" onclick={() => setEnabled(true)} disabled={!canManage}>Turn on</button>
					{/if}
					{#if !canManage}<span class="mono faint">Analysts manage rules.</span>{/if}
				</div>
				{#if lowering}
					<form class="inline" onsubmit={lower}>
						<label class="label"><span>stop alerting for this</span>
							<select class="select" bind:value={exField}>
								<option value="user">person or service account</option>
								<option value="host">device</option>
								<option value="ip">address</option>
							</select>
						</label>
						<label class="label"><span>{FIELD_WORDS[exField]}</span><input class="input" bind:value={exValue} required placeholder={exField === 'user' ? 'e.g. svc-backup' : exField === 'host' ? 'e.g. ant-backup-01' : 'e.g. 192.0.2.10'} /></label>
						<label class="label wide"><span>why (kept with the exception)</span><input class="input" bind:value={exReason} required minlength="10" placeholder="e.g. nightly backup agent rollout" /></label>
						<label class="label"><span>for</span>
							<select class="select" bind:value={exDays}><option value="30">30 days</option><option value="90">90 days</option><option value="">until removed</option></select>
						</label>
						<button class="btn preset-filled-surface-950-50 btn-sm" type="submit" disabled={exBusy}>Add exception</button>
						{#if filters.length}<span class="mono faint">the rule already ignores: {filters.join(', ')}</span>{/if}
					</form>
				{/if}
				{#if turningOff}
					<form class="inline" onsubmit={(e) => (e.preventDefault(), setEnabled(false, offReason))}>
						<label class="label"><span>why turn it off?</span><input class="input" bind:value={offReason} required placeholder="e.g. replaced by a better rule" /></label>
						<button class="btn preset-filled-surface-950-50 btn-sm" type="submit">Turn off</button>
					</form>
				{/if}
			</div>
		{/if}
	</Band>
</div>

<style>
	.tiles {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
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
		font-variation-settings: 'MONO' 0;
		letter-spacing: -0.015em;
	}
	.kb {
		font-size: 11.5px;
		margin-top: -8px;
	}
	.excs {
		list-style: none;
		margin: -6px 0 0;
		padding: 0;
		display: grid;
		gap: 6px;
		width: 100%;
	}
	.excs li {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 12px;
		align-items: baseline;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.excs b {
		color: var(--ink);
		font-weight: 500;
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
		font-variation-settings: 'MONO' 1;
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
		font-variation-settings: 'MONO' 1;
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
	.inline .wide {
		flex: 1 1 260px;
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
