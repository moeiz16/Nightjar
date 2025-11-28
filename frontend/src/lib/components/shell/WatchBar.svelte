<script lang="ts">
	// The watch bar (DESIGN.md §0.1): docked full width at the bottom, tmux style. Numbered windows on Skeleton's
	// Navigation bar (Alt+1 … Alt+8), then intake health in plain words, ⌘K, you (Avatar + Popover) and the theme.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Avatar, Navigation, Popover, Switch } from '@skeletonlabs/skeleton-svelte';
	import { live } from '$lib/stores/live.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { ui, WINDOWS, windowFor } from '$lib/stores/ui.svelte';

	const current = $derived(windowFor(page.url.pathname));
	const windows = $derived(WINDOWS.filter((w) => !w.perm || session.can(w.perm)));
	const healthy = $derived(live.pipeline.status === 'normal');
	let me = $state(false);
</script>

<footer class="watchbar">
	<Navigation.Bar
		value={String(current)}
		base="flex"
		background=""
		width="w-auto min-w-0"
		height="h-full"
		padding="p-0"
		classes="windows"
		tilesJustify="justify-start"
		tilesGap="gap-0"
		tilesClasses="h-full"
	>
		{#each windows as w (w.n)}
			<Navigation.Tile
				id={String(w.n)}
				href={w.href}
				label={w.label}
				title="{w.label} (Alt+{w.n})"
				selected={current === w.n}
				base="win"
				width="w-auto"
				rounded="rounded-none"
				padding="px-3"
				gap="gap-2"
				classes="flex-row items-center"
				hover="hover:bg-[var(--raised)]"
				active="on"
				labelBase="lbl"
			>
				<span class="n">{w.n}</span>
			</Navigation.Tile>
		{/each}
	</Navigation.Bar>

	<span class="c health" class:live={healthy} class:warn={!healthy}>
		{#if healthy}<i class="pulse" aria-hidden="true"></i>{:else}<span aria-hidden="true">◐</span>{/if}
		<span class="hide-l">{live.pipeline.plainStatus}</span>
	</span>
	{#if ui.statusNote}<span class="c note hide-xl" title={ui.statusNote}>{ui.statusNote}</span>{/if}
	<span class="sp"></span>
	<button class="c k" type="button" onclick={() => ui.openPalette()} aria-label="Go to anything (⌘K)"><kbd>⌘</kbd><kbd>K</kbd><span class="hide-s">go anywhere</span></button>
	<span class="c you">
		<Popover
			open={me}
			onOpenChange={(e) => (me = e.open)}
			positioning={{ placement: 'top-end' }}
			triggerBase="youb"
			contentBase="nj-pop youp"
			triggerAriaLabel="You: {session.name}, {session.roleLabel}"
		>
			{#snippet trigger()}
				<Avatar name={session.name || 'You'} size="size-7" font="text-[11px] font-semibold" background="bg-[var(--raised)]" border="border border-[var(--line-strong)]" rounded="rounded-full" />
				<span class="who hide-m">{session.name}<span class="hide-xl"> · {session.roleLabel}</span></span>
			{/snippet}
			{#snippet content()}
				<div class="ph">
					<b>{session.name}</b>
					<span class="mono faint">{session.roleLabel} · {session.org?.name ?? ''}</span>
				</div>
				<div class="opt">
					<Switch name="theme" checked={ui.theme === 'light'} onCheckedChange={(e) => ui.setTheme(e.checked ? 'light' : 'dark')} classes="nj-switch" controlActive="bg-[var(--teal)]" controlInactive="bg-[var(--line-strong)]">Day watch (light)</Switch>
				</div>
				<div class="opt">
					<Switch name="typing" checked={ui.holdByTyping} onCheckedChange={(e) => ui.setHoldByTyping(e.checked)} classes="nj-switch" controlActive="bg-[var(--teal)]" controlInactive="bg-[var(--line-strong)]">Confirm by typing the name instead of holding</Switch>
				</div>
				<a class="opt lnkrow" href="/settings/members" onclick={() => (me = false)}>Members and invitations</a>
				<a class="opt lnkrow" href="/settings/regimes" onclick={() => (me = false)}>Regulator contacts</a>
				<button class="opt lnkrow" type="button" onclick={async () => ((me = false), await session.logout(), goto('/sign-in'))}>Sign out</button>
			{/snippet}
		</Popover>
	</span>
</footer>

<style>
	.watchbar {
		height: 44px;
		display: flex;
		align-items: stretch;
		border-top: 1px solid var(--line-strong);
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--muted);
		background: var(--bg);
		white-space: nowrap;
		min-width: 0;
	}
	.watchbar :global(.windows) {
		border-right: 1px solid var(--line);
		flex: 0 1 auto;
		min-width: 0;
		overflow-x: auto;
		scrollbar-width: none;
	}
	.watchbar :global(.win) {
		display: flex;
		height: 100%;
		color: var(--muted);
		text-decoration: none;
		border-right: 1px solid var(--line);
		position: relative;
	}
	.watchbar :global(.win .lbl) {
		font: 400 12px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
	}
	.watchbar :global(.win .n) {
		font: 600 11px/1 var(--mono);
		font-variation-settings: 'MONO' 1;
		color: var(--faint);
	}
	.watchbar :global(.win.on) {
		background: var(--pane);
		color: var(--ink);
	}
	.watchbar :global(.win.on::before) {
		content: '';
		position: absolute;
		left: 0;
		right: 0;
		top: -1px;
		height: 2px;
		background: var(--teal);
	}
	.watchbar :global(.win.on .n) {
		color: var(--teal);
	}
	.c {
		flex: none;
		padding: 0 14px;
		border-right: 1px solid var(--line);
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}
	.live {
		color: var(--teal);
	}
	.warn {
		color: var(--amber);
	}
	.note {
		flex: 0 1 auto;
		overflow: hidden;
		text-overflow: ellipsis;
		display: block;
		line-height: 43px;
	}
	.sp {
		flex: 1;
	}
	.k {
		border-left: 1px solid var(--line);
		gap: 6px;
		color: var(--muted);
	}
	.k:hover {
		color: var(--ink);
	}
	.you {
		border-right: 0;
		padding: 0 10px;
	}
	.you :global(.youb) {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--ink-2);
		height: 36px;
		padding: 0 4px;
		border-radius: 3px;
	}
	.you :global(.youb:hover) {
		background: var(--raised);
	}
	:global(.youp) {
		width: 320px;
		font: 400 13px/1.4 var(--sans);
		font-variation-settings: 'MONO' 0;
		white-space: normal;
	}
	:global(.youp .ph) {
		display: grid;
		gap: 4px;
		padding: 14px 14px 12px;
		border-bottom: 1px solid var(--line);
	}
	:global(.youp .ph .mono) {
		font-size: 11.5px;
	}
	:global(.youp .opt) {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		padding: 10px 14px;
		border-bottom: 1px solid var(--line);
		text-decoration: none;
		color: var(--ink-2);
		text-align: left;
		width: 100%;
	}
	:global(.youp .lnkrow:hover) {
		background: var(--raised);
		color: var(--ink);
	}
	:global(.nj-switch) {
		display: flex !important;
		flex-direction: row-reverse;
		justify-content: space-between;
		align-items: center;
		gap: 14px;
		width: 100%;
		cursor: pointer;
	}
	:global(.youp .opt:last-child) {
		border-bottom: 0;
	}
	@media (max-width: 1720px) {
		.hide-xl {
			display: none !important;
		}
	}
	@media (max-width: 1420px) {
		.hide-l {
			display: none !important;
		}
	}
	@media (max-width: 1100px) {
		.hide-m {
			display: none !important;
		}
	}
	@media (max-width: 760px) {
		.watchbar {
			height: 56px;
		}
		.hide-s,
		.health {
			display: none !important;
		}
		.watchbar :global(.win) {
			flex-direction: column !important;
			justify-content: center;
			gap: 3px !important;
			padding: 0 9px !important;
		}
		.watchbar :global(.win .lbl) {
			font-size: 10.5px;
		}
		.watchbar :global(.windows) {
			-webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent);
			mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent);
		}
		.k {
			padding: 0 10px;
		}
		.k :global(kbd:first-child) {
			display: none;
		}
	}
</style>
