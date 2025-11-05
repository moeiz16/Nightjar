<script lang="ts">
	// Seven nights, 18:00 to 08:00: ◆ an incident (lavender for last night), ● closed as noise or worth a look.
	import type { IncidentSummary } from '$lib/api/types';
	import { demoNight, weekdayName, now } from '$lib/time/clock';
	import { nightIndex } from '$lib/util/brief';

	let { incidents, at = now() }: { incidents: IncidentSummary[]; at?: Date } = $props();
	const X0 = 96;
	const X1 = 694;
	const HRS = 14;
	const ROW = 30;
	const TICKS = [{ l: '18:00', h: 0 }, { l: '21:00', h: 3 }, { l: '00:00', h: 6 }, { l: '03:00', h: 9 }, { l: '06:00', h: 12 }, { l: '08:00', h: 14 }];
	const night = $derived(demoNight(at));
	const x = (h: number) => X0 + ((X1 - X0) * h) / HRS;
	const marks = $derived(
		incidents
			.map((i) => ({ i, pos: nightIndex(new Date(i.firstSeen), night) }))
			.filter((m): m is { i: IncidentSummary; pos: { row: number; hour: number } } => !!m.pos)
	);
	const labels = $derived(
		Array.from({ length: 7 }, (_, d) => (d === 6 ? 'last night' : `${weekdayName(new Date(+night - (6 - d) * 864e5 - 3600e3), undefined, 'short')} night`))
	);
	const lastNight = $derived(marks.filter((m) => m.pos.row === 6 && m.i.verdict !== 'benign').slice(0, 2));
	const short = (t: string) => (t.length > 26 ? t.slice(0, 24) + '…' : t);
</script>

<svg viewBox="0 0 720 236" role="img" aria-label="Seven nights from 18:00 to 08:00. {marks.length} incidents; {marks.filter((m) => m.pos.row === 6).length} last night.">
	{#each TICKS as { l, h } (l)}
		<line x1={x(h)} y1="6" x2={x(h)} y2={7 * ROW + 4} stroke="var(--rule)" stroke-dasharray={h === 6 ? undefined : '2 4'} />
		<text x={x(h)} y={7 * ROW + 22} text-anchor="middle" font-family="var(--mono)" font-size="11" fill="var(--faint)">{l}</text>
	{/each}
	{#each labels as l, d (d)}
		{@const y = 20 + d * ROW}
		<text x="0" y={y + 4} font-family="var(--sans)" font-size="12.5" fill={d === 6 ? 'var(--ink)' : 'var(--muted)'}>{l}</text>
		<line x1={X0} y1={y} x2={X1} y2={y} stroke="var(--rule-2)" />
	{/each}
	{#each marks as m (m.i.id)}
		{@const y = 20 + m.pos.row * ROW}
		{@const cx = x(m.pos.hour)}
		{#if m.i.verdict === 'benign' || m.i.likelyBenign}
			<circle {cx} cy={y} r="3.2" fill="var(--faint)" opacity=".75"><title>{m.i.reference} {m.i.title} · closed as noise</title></circle>
		{:else}
			<rect x={cx - 6} y={y - 6} width="12" height="12" transform="rotate(45 {cx} {y})" fill={m.pos.row === 6 ? 'var(--lav)' : 'var(--teal)'}><title>{m.i.reference} {m.i.title}</title></rect>
		{/if}
	{/each}
	{#each lastNight as m, k (m.i.id)}
		<text x={x(m.pos.hour) + (k % 2 ? -10 : 12)} y={20 + 6 * ROW - 10} text-anchor={k % 2 ? 'end' : 'start'} font-family="var(--sans)" font-size="12" fill="var(--ink-2)">{short(m.i.title)}</text>
	{/each}
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
		margin-top: 18px;
		overflow: visible;
	}
</style>
