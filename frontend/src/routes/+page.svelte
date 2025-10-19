<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$api/client';

	let status = $state('checking…');

	onMount(async () => {
		try {
			const health = await api.get<{ status: string; today: string }>('/health/ready');
			status = `API ${health.status} · today ${health.today}`;
		} catch {
			status = 'API not reachable on :8030';
		}
	});
</script>

<svelte:head>
	<title>Nightjar SOC</title>
</svelte:head>

<main>
	<h1>Nightjar SOC</h1>
	<p>Scaffold only. The console is designed in frontend/DESIGN.md.</p>
	<p>{status}</p>
</main>
