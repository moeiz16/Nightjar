// Global keyboard: ⌘K / Ctrl K, ⌘B, g-chords, and the tile keys (⌘\ split, ⌘[ ⌘] focus, ⌥W / ⌘W close,
// ⌘⇧↵ zoom). Keys never fire while typing in a field.
import { goto } from '$app/navigation';
import { page } from '$app/state';
import { G_CHORDS, isTyping } from '$lib/commands';
import { ui } from '$lib/stores/ui.svelte';
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
	const onWatch = page.url.pathname === '/watch';
	if (mod && e.key === '\\') {
		e.preventDefault();
		if (!onWatch) goto('/watch');
		ui.split();
		return true;
	}
	if (mod && (e.key === '[' || e.key === ']')) {
		e.preventDefault();
		ui.move(e.key === '[' ? -1 : 1);
		return true;
	}
	if ((mod && k === 'w') || (e.altKey && (k === 'w' || e.code === 'KeyW'))) {
		if (onWatch && ui.tiles.length > 1) {
			e.preventDefault();
			ui.close();
			return true;
		}
		return false;
	}
	if (mod && e.shiftKey && e.key === 'Enter') {
		e.preventDefault();
		ui.zoomed = !ui.zoomed;
		return true;
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
	$effect(() => {
		if (session.me) ui.saveLayout(session.me.user.id);
		void ui.tiles.length;
		void ui.tiles.join();
	});
}
