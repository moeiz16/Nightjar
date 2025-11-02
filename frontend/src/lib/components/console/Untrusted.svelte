<script lang="ts">
	// Quoted evidence from a log: plain text in mono, a dashed "as received" box, never instructions.
	import type { UntrustedText } from '$lib/api/types';
	import { asUntrusted, FLAG_NOTICE, flagWords, visibleText } from '$lib/util/untrusted';

	let { value, label = 'from the event, as received', inline = false, max = 2000 }: { value: UntrustedText | string | null | undefined; label?: string | null; inline?: boolean; max?: number } = $props();
	const u = $derived(asUntrusted(value));
	const text = $derived(visibleText(u.text, max));
</script>

{#if inline}
	<span class="inl" class:flagged={u.flags.length > 0} title={u.flags.length ? FLAG_NOTICE : 'Untrusted text from a log, shown as received'}
		><span class="q" aria-hidden="true">“</span>{text}<span class="q" aria-hidden="true">”</span>{#if u.flags.length}<span class="flag" role="note"> ! {FLAG_NOTICE.toLowerCase()}</span>{/if}</span
	>
{:else}
	<figure class="ut" class:flagged={u.flags.length > 0}>
		{#if label}<figcaption>{label}{#if u.field} · {u.field}{/if}{#if u.truncated} · shortened{/if}</figcaption>{/if}
		<blockquote class="untrusted">{text}</blockquote>
		{#if u.flags.length}
			<p class="flag" role="note"><span class="g" aria-hidden="true">!</span>{FLAG_NOTICE}<span class="why"> · {flagWords(u.flags)}</span></p>
		{/if}
	</figure>
{/if}

<style>
	.ut {
		margin: 0;
		display: grid;
		gap: 5px;
		min-width: 0;
	}
	figcaption {
		font: 400 11px/1 var(--mono);
		color: var(--faint);
	}
	blockquote {
		margin: 0;
	}
	.flagged blockquote {
		border-color: color-mix(in srgb, var(--amber) 55%, transparent);
	}
	.flag {
		margin: 0;
		font: 400 11.5px/1.45 var(--mono);
		color: var(--amber);
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.flag .g {
		font-weight: 600;
	}
	.flag .why {
		color: var(--muted);
	}
	.inl {
		font: 400 12px/1.5 var(--mono);
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}
	.inl .q {
		color: var(--faint);
	}
	.inl.flagged {
		text-decoration: underline dashed color-mix(in srgb, var(--amber) 60%, transparent);
		text-underline-offset: 3px;
	}
	.inl .flag {
		display: inline;
		font-size: 11px;
	}
</style>
