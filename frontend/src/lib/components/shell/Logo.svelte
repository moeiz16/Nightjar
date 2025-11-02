<script lang="ts">
	// The nightjar's eye: an upper arc, a fainter lower arc and a dot.
	let { size = 22 }: { size?: number } = $props();
</script>

<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width={size} height={size}>
	<path d="M2.5 13.2C5.6 7.6 9 5.6 12 5.6s6.4 2 9.5 7.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
	<path d="M5.6 13.6c2 2.9 4.2 4.2 6.4 4.2s4.4-1.3 6.4-4.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" opacity=".55" />
	<circle cx="12" cy="12" r="2.3" fill="currentColor" />
</svg>
