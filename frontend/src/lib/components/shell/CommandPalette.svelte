<script lang="ts">
	// ⌘K: the navigation (DESIGN.md §3). Groups do / go to / switch, scoped to the focused incident first; Tab widens
	// it to everything. "?" asks the watch a question and answers in a hunt tile.
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { flip } from 'svelte/animate';
	import { prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import { fade } from 'svelte/transition';
	import { api } from '$lib/api';
	import type { AssetDoc, IdentityDoc, ProposalDoc, RuleInfo, SavedSearch } from '$lib/api/types';
	import { groupRanked, highlight, rankCommands, type Command } from '$lib/commands';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui, TILE_LABEL, type TileKind } from '$lib/stores/ui.svelte';
	import { hm, secondsLeft } from '$lib/time/clock';
	import { clockLongLabel, noticeHref } from '$lib/util/labels';

	let input = $state<HTMLInputElement | null>(null);
	let query = $state('');
	let widened = $state(false);
	let active = $state(0);
	let returnFocus: HTMLElement | null = null;

	let approvals = $state<ProposalDoc[]>([]);
	let saved = $state<SavedSearch[]>([]);
	let assets = $state<AssetDoc[]>([]);
	let people = $state<IdentityDoc[]>([]);
	let rules = $state<RuleInfo[]>([]);
	let loaded = false;

	async function load() {
		const tasks: Promise<unknown>[] = [api.approvals.list({ status: 'pending' }).then((r) => (approvals = r.items))];
		if (!loaded) {
			if (session.can('hunting:read')) tasks.push(api.hunting.saved().then((r) => (saved = r)));
			if (session.can('rules:read')) tasks.push(api.rules.list().then((r) => (rules = r)));
			tasks.push(api.directory.assets().then((r) => (assets = r)));
			tasks.push(api.directory.identities().then((r) => (people = r)));
		}
		await Promise.allSettled(tasks);
		loaded = true;
	}

	$effect(() => {
		if (ui.paletteOpen) {
			returnFocus = document.activeElement as HTMLElement | null;
			query = ui.paletteMode === 'ask' ? '? ' + ui.paletteQuery : ui.paletteQuery;
			widened = false;
			active = 0;
			load();
			tick().then(() => input?.focus());
		}
	});

	function close() {
		ui.closePalette();
		returnFocus?.focus?.();
	}
	function go(href: string) {
		close();
		goto(href);
	}

	const commands = $derived.by((): Command[] => {
		if (ui.paletteMode === 'tile') {
			return (Object.keys(TILE_LABEL) as TileKind[]).map((k) => ({
				id: 'tile-' + k, group: 'switch', icon: '▦', title: `Show ${TILE_LABEL[k]} in this tile`, hint: ui.tiles.includes(k) ? 'open' : '',
				run: () => {
					ui.swap(k);
					close();
				}
			}));
		}
		const list: Command[] = [];
		const can = (p: string) => session.can(p);
		// ---------- do ----------
		for (const p of approvals) {
			const mine = (session.role && p.requiredRoles.includes(session.role)) || false;
			list.push({ id: 'hold-' + p.id, group: 'do', icon: '‖', scope: p.incidentReference, title: `${p.kindLabel} (${p.target}): hold to authorise`, hint: mine ? 'awaiting you' : 'read only', keywords: `approve authorise ${p.kind} ${p.target}`, run: () => go(`/incidents/${p.incidentReference}?hold=${p.id}`) });
			list.push({ id: 'decline-' + p.id, group: 'do', icon: '✕', scope: p.incidentReference, title: `Decline with a reason: ${p.kindLabel.toLowerCase()}`, keywords: `reject ${p.target}`, run: () => go(`/incidents/${p.incidentReference}?decline=${p.id}`) });
		}
		const isolated = assets.filter((a) => a.isolated);
		list.push({ id: 'release', group: 'do', icon: '↺', title: 'Release a computer from isolation', hint: isolated.length ? `${isolated.length} isolated` : 'none isolated', disabled: !isolated.length, keywords: 'isolation unisolate', run: () => go('/assets?isolated=1') });
		for (const d of live.clocks) {
			if (d.state === 'not_started') list.push({ id: 'decide-' + d.id, group: 'do', icon: '◆', scope: d.incidentReference, title: `Record the materiality decision for ${d.incidentReference}`, hint: 'SEC 8-K', keywords: 'material sec 8-k decide', run: () => go(noticeHref(d)) });
			else if (d.noticeId && d.state !== 'submitted') list.push({ id: 'submit-' + d.id, group: 'do', icon: '↗', scope: d.incidentReference, title: `Review and submit: ${clockLongLabel(d)}`, hint: can('notices:submit') ? `${hm(secondsLeft(d.dueAt, live.now))} left` : 'draft', keywords: 'notice submit report regulator', run: () => go(noticeHref(d)) });
		}
		if (can('audit:read')) {
			list.push({ id: 'verify', group: 'do', icon: '⛓', title: 'Verify the log', keywords: 'audit chain', run: () => go('/activity?verify=1') });
			list.push({ id: 'export', group: 'do', icon: '⇩', title: 'Export evidence pack', keywords: 'audit csv download', run: () => go('/activity?export=1') });
		}
		if (can('hunting:read')) list.push({ id: 'newhunt', group: 'do', icon: '⌕', title: 'Start a new hunt', keywords: 'search events', run: () => go('/hunt') });
		// ---------- go to ----------
		list.push({ id: 'watch', group: 'go to', icon: '◉', title: 'The watch', keys: ['g', 'w'], run: () => go('/watch') });
		for (const i of live.incidents) list.push({ id: 'inc-' + i.id, group: 'go to', icon: '▸', scope: i.reference, title: `${i.reference} ${i.title}`, hint: i.status, run: () => go(`/incidents/${i.reference}`) });
		for (const d of live.clocks) if (d.state !== 'not_started') list.push({ id: 'clock-' + d.id, group: 'go to', icon: '◷', scope: d.incidentReference, title: `Clock: ${clockLongLabel(d)} for ${d.incidentReference}`, hint: `${hm(secondsLeft(d.dueAt, live.now))} left`, run: () => go(noticeHref(d)) });
		if (can('hunting:read')) {
			list.push({ id: 'hunt', group: 'go to', icon: '⌕', title: 'Hunt', keys: ['g', 'h'], run: () => go('/hunt') });
			list.push({ id: 'saved', group: 'go to', icon: '▤', title: 'Saved searches', keywords: 'hunts', run: () => go('/hunt/saved') });
			for (const s of saved) list.push({ id: 'sv-' + s.id, group: 'go to', icon: '▸', title: `Hunt: ${s.name.toLowerCase()}`, hint: 'saved search', run: () => go(`/hunt?saved=${s.id}`) });
		}
		if (can('ueba:read')) list.push({ id: 'behaviour', group: 'go to', icon: '◇', title: 'Unusual behaviour', keys: ['g', 'u'], keywords: 'anomalies rhythm', run: () => go('/behaviour') });
		if (can('rules:read')) {
			list.push({ id: 'rules', group: 'go to', icon: '§', title: 'Detection rules', keys: ['g', 'r'], run: () => go('/rules') });
			for (const r of rules) list.push({ id: 'rule-' + r.id, group: 'go to', icon: '§', title: `Rule: ${r.title}`, hint: r.attack[0]?.techniqueId ?? '', run: () => go(`/rules/${r.id}`) });
		}
		list.push({ id: 'assets', group: 'go to', icon: '▣', title: 'Assets', keys: ['g', 'a'], run: () => go('/assets') });
		for (const a of assets) list.push({ id: 'ast-' + a.id, group: 'go to', icon: '▣', title: `Assets: ${a.name}`, hint: `${a.site} · ${a.kind.replace('_', ' ')}`, keywords: a.businessService ?? '', run: () => go(`/assets?name=${a.name}`) });
		list.push({ id: 'identities', group: 'go to', icon: '◎', title: 'People and service accounts', keys: ['g', 'i'], keywords: 'identities users', run: () => go('/identities') });
		for (const p of people) list.push({ id: 'idn-' + p.id, group: 'go to', icon: '◎', title: `People: ${p.displayName}`, hint: `${p.department} · ${p.username}`, keywords: p.username, run: () => go(`/identities?u=${p.username}`) });
		list.push({ id: 'regimes', group: 'go to', icon: '⚙', title: 'Settings: regime contacts', keys: ['g', 's'], keywords: 'regulators nis2 sec soci', run: () => go('/settings/regimes') });
		list.push({ id: 'members', group: 'go to', icon: '⚙', title: 'Settings: members and invitations', keywords: 'people roles invite', run: () => go('/settings/members') });
		if (can('audit:read')) list.push({ id: 'activity', group: 'go to', icon: '≡', title: 'Activity log', keys: ['g', 'l'], keywords: 'audit', run: () => go('/activity') });
		list.push({ id: 'weekly', group: 'go to', icon: '☼', title: 'Weekly report', keywords: 'board', run: () => go('/brief/week') });
		// ---------- switch ----------
		list.push({ id: 'brief', group: 'switch', icon: '☼', title: 'Morning brief (executive view)', keys: ['⌘', 'B'], run: () => go('/brief') });
		list.push({ id: 'theme', group: 'switch', icon: '◐', title: ui.theme === 'dark' ? 'Day watch (light theme)' : 'Night (dark theme)', keywords: 'theme light dark', run: () => (ui.toggleTheme(), close()) });
		for (const r of ['analyst', 'responder', 'auditor'] as const) list.push({ id: 'layout-' + r, group: 'switch', icon: '▦', title: `Layout: ${r}`, keywords: 'tiles preset', run: () => (ui.setPreset(r), go('/watch')) });
		list.push({ id: 'split', group: 'switch', icon: '▦', title: 'Split the focused tile', keys: ['⌘', '\\'], run: () => (ui.split(), go('/watch')) });
		list.push({ id: 'zoom', group: 'switch', icon: '▢', title: 'Zoom the focused tile', keys: ['⌘', '⇧', '↵'], run: () => ((ui.zoomed = !ui.zoomed), close()) });
		list.push({ id: 'typing', group: 'switch', icon: '⌨', title: ui.holdByTyping ? 'Confirm actions by holding' : 'Confirm actions by typing the name instead of holding', keywords: 'accessibility hold', run: () => (ui.setHoldByTyping(!ui.holdByTyping), close()) });
		list.push({ id: 'signout', group: 'switch', icon: '⏻', title: 'Sign out', keywords: 'log out', run: async () => (close(), await session.logout(), goto('/sign-in')) });
		return list;
	});

	const asking = $derived(query.trimStart().startsWith('?'));
	const ranked = $derived(asking ? [] : rankCommands(commands, query, { scope: ui.scope?.reference, widened }));
	const groups = $derived(groupRanked(ranked, query ? 8 : 6));
	const flat = $derived(groups.flatMap((g) => g.items));
	$effect(() => {
		void query;
		active = 0;
	});

	function run(i: number) {
		const c = flat[i]?.command;
		if (c && !c.disabled) c.run();
	}
	function ask() {
		const q = query.trim().replace(/^\?\s*/, '');
		go(`/hunt?ask=${encodeURIComponent(q)}`);
	}
	function onkey(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			close();
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			active = Math.min(flat.length - 1, active + 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			active = Math.max(0, active - 1);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (asking) ask();
			else run(active);
		} else if (e.key === 'Tab') {
			e.preventDefault();
			if (ui.scope) widened = !widened;
		}
	}
	const scale = (_n: Element) => ({
		duration: prefersReducedMotion.current ? 120 : 160,
		easing: cubicOut,
		css: (t: number) => (prefersReducedMotion.current ? `opacity:${t}` : `opacity:${t}; transform: translate(-50%, ${(1 - t) * -6}px) scale(${0.985 + 0.015 * t})`)
	});
	let idx = 0;
	const indexOf = (id: string) => flat.findIndex((r) => r.command.id === id);
	void idx;
</script>

{#if ui.paletteOpen}
	<div class="scrim" transition:fade={{ duration: 120 }} onclick={(e) => e.target === e.currentTarget && close()} role="presentation">
		<div class="palette" role="dialog" aria-modal="true" aria-label="Go to anything" transition:scale>
			<div class="in">
				{#if ui.scope && !widened && ui.paletteMode !== 'tile'}<span class="scope">{ui.scope.reference}</span>{/if}
				{#if ui.paletteMode === 'tile'}<span class="scope">tile</span>{/if}
				<input
					bind:this={input}
					bind:value={query}
					onkeydown={onkey}
					role="combobox"
					aria-expanded="true"
					aria-controls="pal-list"
					aria-activedescendant={flat[active] ? 'pal-' + flat[active]!.command.id : undefined}
					aria-label="Search commands, or type ? to ask the watch"
					placeholder={ui.paletteMode === 'tile' ? 'Show in this tile…' : 'Go to anything, do something, or type ? to ask'}
					autocomplete="off"
					spellcheck="false"
				/>
				<kbd>esc</kbd>
			</div>
			<div class="list" id="pal-list" role="listbox" aria-label="Results">
				{#if asking}
					<div class="grp">
						<h6>ask the watch</h6>
						<button class="it on" type="button" onclick={ask}>
							<span class="ic">?</span><span>{query.trim().replace(/^\?\s*/, '') || 'which laptops opened the link?'}</span><span class="hint">answer in a hunt tile <kbd>⏎</kbd></span>
						</button>
					</div>
				{:else if !flat.length}
					<p class="none">Nothing matches “{query}”. Type <span class="mono">?</span> to ask the watch instead.</p>
				{/if}
				{#each groups as g (g.group)}
					<div class="grp" role="group" aria-label={g.group}>
						<h6>{g.group}</h6>
						{#each g.items as r (r.command.id)}
							{@const i = indexOf(r.command.id)}
							<button
								id={'pal-' + r.command.id}
								class="it"
								class:on={i === active}
								class:off={r.command.disabled}
								type="button"
								role="option"
								aria-selected={i === active}
								aria-disabled={r.command.disabled}
								tabindex="-1"
								onmouseenter={() => (active = i)}
								onclick={() => run(i)}
								animate:flip={{ duration: prefersReducedMotion.current ? 0 : 180 }}
							>
								<span class="ic">{r.command.icon}</span>
								<span class="tt">{#each highlight(r.command.title, r.match.ranges) as part, pi (pi)}{#if part.hit}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}</span>
								<span class="hint">
									{#if r.command.hint}{r.command.hint}{/if}
									{#if r.command.keys}{#each r.command.keys as k (k)}<kbd>{k}</kbd>{/each}{/if}
									{#if i === active}<kbd>⏎</kbd>{/if}
								</span>
							</button>
						{/each}
					</div>
				{/each}
			</div>
			<div class="ft">
				<span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>⏎</kbd> open</span>
				{#if ui.scope}<span><kbd>⇥</kbd> {widened ? `narrow to ${ui.scope.reference}` : 'widen to everything'}</span>{/if}
				<span class="ask">type “?” to ask the watch</span>
			</div>
		</div>
	</div>
{/if}

<style>
	.scrim {
		position: fixed;
		inset: 0;
		background: rgba(3, 6, 10, 0.55);
		backdrop-filter: blur(2px);
		z-index: 50;
	}
	:global(:root[data-theme='light']) .scrim {
		background: rgba(20, 35, 45, 0.22);
	}
	.palette {
		position: absolute;
		left: 50%;
		top: 96px;
		transform: translateX(-50%);
		width: min(640px, calc(100vw - 24px));
		border-radius: 10px;
		background: var(--pane);
		border: 1px solid var(--line-strong);
		box-shadow: var(--shadow);
		overflow: hidden;
		display: flex;
		flex-direction: column;
		max-height: calc(100vh - 140px);
	}
	.in {
		display: flex;
		align-items: center;
		gap: 12px;
		height: 54px;
		padding: 0 18px;
		border-bottom: 1px solid var(--line);
		flex: none;
	}
	.scope {
		font: 500 11.5px/1 var(--mono);
		color: var(--teal);
		border: 1px solid var(--teal-line);
		border-radius: 4px;
		padding: 4px 6px;
		white-space: nowrap;
	}
	input {
		flex: 1;
		min-width: 0;
		background: none;
		border: 0;
		outline: 0;
		color: var(--ink);
		font: 400 17px/1 var(--sans);
	}
	input::placeholder {
		color: var(--faint);
	}
	.list {
		overflow-y: auto;
		min-height: 0;
	}
	.grp {
		padding: 8px 8px 4px;
	}
	h6 {
		margin: 4px 10px 6px;
		font: 400 11px/1 var(--mono);
		color: var(--faint);
	}
	.it {
		display: flex;
		align-items: center;
		gap: 12px;
		width: 100%;
		min-height: 40px;
		padding: 0 10px;
		border-radius: 6px;
		color: var(--ink-2);
		text-align: left;
	}
	.it .ic {
		width: 20px;
		font: 500 13px/1 var(--mono);
		color: var(--muted);
		text-align: center;
		flex: none;
	}
	.it .tt {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.it .hint {
		margin-left: auto;
		display: flex;
		gap: 6px;
		align-items: center;
		font: 400 12px/1 var(--mono);
		color: var(--faint);
		white-space: nowrap;
		flex: none;
	}
	.it.on {
		background: var(--sel);
		color: var(--ink);
	}
	.it.on .ic {
		color: var(--teal);
	}
	.it.off {
		opacity: 0.6;
	}
	mark {
		background: none;
		color: var(--teal);
	}
	.none {
		padding: 22px 18px;
		margin: 0;
		color: var(--muted);
		font-size: 13.5px;
	}
	.ft {
		display: flex;
		gap: 18px;
		padding: 10px 18px;
		border-top: 1px solid var(--line);
		font: 400 11.5px/1 var(--mono);
		color: var(--faint);
		flex: none;
		flex-wrap: wrap;
	}
	.ft span {
		display: inline-flex;
		gap: 6px;
		align-items: center;
	}
	.ft .ask {
		margin-left: auto;
	}
	@media (max-width: 760px) {
		.palette {
			top: 12px;
			max-height: calc(100vh - 90px);
		}
		.ft .ask {
			display: none;
		}
		.it .hint {
			display: none;
		}
	}
</style>
