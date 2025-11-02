// Untrusted text (contract §1.1): anything that came from a log is shown as plain text only — no HTML, no
// markdown, no autolinks. Invisible and direction-changing characters are made visible so a log line cannot
// disguise itself, and a non-empty flags list gets the "treated as data only" notice.
import type { UntrustedText } from '$lib/api/types';

// eslint-disable-next-line no-control-regex -- matching control characters is the point: they are made visible
const BIDI_OR_INVISIBLE = new RegExp('[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F\\u200B-\\u200F\\u202A-\\u202E\\u2060-\\u2064\\u2066-\\u2069\\uFEFF]', 'g');

/** Make hidden characters visible as ⟨U+202E⟩ and cap the length. Never returns markup. */
export function visibleText(text: string, max = 2000): string {
	const cleaned = String(text ?? '').replace(BIDI_OR_INVISIBLE, (c) => `⟨U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}⟩`);
	return cleaned.length > max ? cleaned.slice(0, max) + '…' : cleaned;
}

export const FLAG_NOTICE = 'This text contained instructions and was treated as data only';

export function flagWords(flags: string[]): string {
	const words: Record<string, string> = {
		instruction_like: 'reads like an instruction',
		role_play: 'addresses the watch directly',
		hidden_text: 'had hidden characters',
		markup: 'contained markup',
		url_obfuscation: 'had a disguised link'
	};
	return flags.map((f) => words[f] ?? f.replace(/_/g, ' ')).join(' · ');
}

export function isFlagged(u: UntrustedText | null | undefined): boolean {
	return !!u && Array.isArray(u.flags) && u.flags.length > 0;
}

/** Accept either the contract shape or a bare string (defensive: older payloads). */
export function asUntrusted(v: UntrustedText | string | null | undefined): UntrustedText {
	if (v == null) return { text: '', flags: [] };
	if (typeof v === 'string') return { text: v, flags: [] };
	return { text: String(v.text ?? ''), flags: Array.isArray(v.flags) ? v.flags : [], field: v.field, truncated: v.truncated };
}
