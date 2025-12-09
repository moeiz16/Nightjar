<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { isoWeek, now } from '$lib/time/clock';
	onMount(async () => {
		const list = await api.executive.briefs().catch(() => []);
		goto(`/brief/week/${list[0]?.week ?? isoWeek(now())}`, { replaceState: true });
	});
</script>

<p style="padding:40px var(--gut);color:var(--dim)">Opening this week’s report…</p>
