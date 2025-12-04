<script lang="ts">
	import { page } from '$app/state';
	const items = [
		['/settings/regimes', 'Regulator contacts'],
		['/settings/members', 'Members']
	];
</script>

<nav class="snav" aria-label="Settings sections">
	<span class="faint eng">Settings</span>
	{#each items as [href, label] (href)}<a {href} aria-current={page.url.pathname === href ? 'page' : undefined}>{label}</a>{/each}
</nav>

<style>
	.snav {
		display: flex;
		gap: 6px;
		align-items: center;
		margin: 0 0 14px;
		font: 700 14px/1 var(--display);
		text-transform: uppercase;
		letter-spacing: 0.14em;
	}
	.snav .eng {
		margin-right: 10px;
		font-size: 13px;
	}
	a {
		text-decoration: none;
		color: var(--dim);
		padding: 9px 14px;
		box-shadow: inset 0 0 0 1px var(--rule);
		background: var(--plate);
	}
	a:hover {
		color: var(--ink);
	}
	a[aria-current='page'] {
		color: var(--acc-ink);
		background: var(--acc);
		box-shadow: none;
	}
</style>
