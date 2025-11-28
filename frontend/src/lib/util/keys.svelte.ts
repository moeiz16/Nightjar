// Global keyboard: ⌘K / Ctrl K, ⌘B, g-chords and Alt+1…8 for the watch bar's windows. Keys never fire while
// typing in a field.
import { goto } from '$app/navigation';
import { G_CHORDS, isTyping } from '$lib/commands';
import { ui, WINDOWS } from '$lib/stores/ui.svelte';
import { session } from '$lib/stores/session.svelte';

export function handleShellKey(e: KeyboardEvent, state: { g: number }): boolean {
	const mod = e.metaKey || e.ctrlKey;
	const k = e.key.toLowerCase();
	if (mod && k === 'k') {
		e.preventDefault();
		if (ui.paletteOpen) ui.closePalette();
		else ui.openPalette();
		return true;
	}
	if (ui.paletteOpen) return false;
	if (mod && k === 'b') {
		e.preventDefault();
		goto('/brief');
		return true;
	}
	// Alt+1 … Alt+8: the numbered windows of the watch bar (e.code, so Option on a Mac works too)
	if (e.altKey && !mod && /^Digit[1-8]$/.test(e.code)) {
		const w = WINDOWS.find((x) => x.n === Number(e.code.slice(5)));
		if (w && (!w.perm || session.can(w.perm))) {
			e.preventDefault();
			goto(w.href);
			return true;
		}
		return false;
	}
	if (isTyping(e.target) || mod || e.altKey) return false;
	if (k === 'g') {
		state.g = Date.now();
		return true;
	}
	if (state.g && Date.now() - state.g < 1200 && G_CHORDS[k]) {
		state.g = 0;
		e.preventDefault();
		goto(G_CHORDS[k]!);
		return true;
	}
	state.g = 0;
	return false;
}

export function useShellKeys() {
	const state = { g: 0 };
	$effect(() => {
		const fn = (e: KeyboardEvent) => {
			if (!session.me) return;
			handleShellKey(e, state);
		};
		window.addEventListener('keydown', fn);
		return () => window.removeEventListener('keydown', fn);
	});
}
