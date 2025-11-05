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

<p class="mono" style="padding:40px 20px;max-width:1160px;margin:0 auto;color:var(--muted)">Opening this week’s report…</p>
