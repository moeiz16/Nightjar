<script lang="ts">
	import { page } from '$app/state';
	const items = [
		['/settings/regimes', 'regime contacts'],
		['/settings/members', 'members']
	];
</script>

<nav class="snav mono" aria-label="Settings sections">
	<span class="faint">settings ›</span>
	{#each items as [href, label] (href)}<a {href} aria-current={page.url.pathname === href ? 'page' : undefined}>{label}</a>{/each}
</nav>

<style>
	.snav {
		display: flex;
		gap: 16px;
		align-items: center;
		padding: 12px 22px;
		border-bottom: 1px solid var(--line);
		font-size: 12px;
	}
	a {
		text-decoration: none;
		color: var(--muted);
		padding-bottom: 2px;
		border-bottom: 1px solid transparent;
	}
	a[aria-current='page'] {
		color: var(--ink);
		border-color: var(--teal);
	}
</style>
