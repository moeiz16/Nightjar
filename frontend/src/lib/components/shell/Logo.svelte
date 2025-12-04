<script lang="ts">
	// The nightjar's eye inside a dial ring: an upper lid arc, a dot, and one tick at midnight.
	let { size = 24 }: { size?: number } = $props();
</script>

<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width={size} height={size}>
	<circle cx="12" cy="12" r="10.4" stroke="currentColor" stroke-width="1.4" opacity=".55" />
	<path d="M12 1.2v3.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="square" />
	<path d="M5.2 13.4C7.1 10 9.4 8.6 12 8.6s4.9 1.4 6.8 4.8" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
	<circle cx="12" cy="13.6" r="2.4" fill="currentColor" />
</svg>
