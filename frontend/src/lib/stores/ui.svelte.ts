// UI state shared by the shell, the bands and ⌘K: theme, palette, the turnover sections per role, preferences,
// and the Skeleton toaster every confirmation goes through.
import { createToaster } from '@skeletonlabs/skeleton-svelte';
import type { Role } from '$lib/api/types';

export type Theme = 'dark' | 'light';
/** The numbered sections of the turnover (the watch's handover band). */
export type SectionKind = 'awaiting' | 'clocks' | 'intake' | 'log' | 'hunt' | 'activity' | 'evidence';
export const SECTION_LABEL: Record<SectionKind, string> = {
	awaiting: 'waiting on you',
	clocks: 'clocks to watch',
	intake: 'intake, live',
	log: 'since you last looked',
	hunt: 'worth a hunt',
	activity: 'the activity chain',
	evidence: 'evidence'
};
/** What each role needs from the handover first (DESIGN.md §0.3). Responders and admins decide; analysts hunt;
 *  auditors check the chain. Every role sees the clocks. */
export const SECTIONS: Record<Exclude<Role, 'executive'>, SectionKind[]> = {
	responder: ['awaiting', 'clocks', 'intake', 'log'],
	admin: ['awaiting', 'clocks', 'intake', 'log'],
	analyst: ['hunt', 'clocks', 'intake', 'log'],
	auditor: ['activity', 'clocks', 'evidence', 'log']
};
export function sectionsFor(role: Role | null): SectionKind[] {
	if (!role || role === 'executive') return [...SECTIONS.responder];
	return [...SECTIONS[role]];
}

/** The numbered windows of the watch bar (Alt+1 … Alt+8), tmux style. */
export const WINDOWS = [
	{ n: 1, href: '/watch', label: 'watch', perm: null },
	{ n: 2, href: '/hunt', label: 'hunt', perm: 'hunting:read' },
	{ n: 3, href: '/behaviour', label: 'behaviour', perm: 'ueba:read' },
	{ n: 4, href: '/rules', label: 'rules', perm: 'rules:read' },
	{ n: 5, href: '/assets', label: 'assets', perm: null },
	{ n: 6, href: '/identities', label: 'people', perm: null },
	{ n: 7, href: '/activity', label: 'log', perm: 'audit:read' },
	{ n: 8, href: '/brief', label: 'brief', perm: null }
] as const;
/** The window a path belongs to (an incident and its notice live under the watch). */
export function windowFor(path: string): number {
	if (path.startsWith('/incidents') || path.startsWith('/watch')) return 1;
	if (path.startsWith('/settings')) return 0;
	const hit = WINDOWS.find((w) => w.n > 1 && path.startsWith(w.href));
	return hit?.n ?? 0;
}

function read(key: string): string | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage.getItem(key);
	} catch {
		return null;
	}
}
function write(key: string, v: string) {
	try {
		localStorage.setItem(key, v);
	} catch {
		/* storage unavailable: preference lasts for this visit only */
	}
}

export type ToastTone = 'ok' | 'info' | 'bad';
export const toaster = createToaster({ placement: 'bottom-start', overlap: false, gap: 8, max: 4 });

class Ui {
	theme = $state<Theme>('dark');
	briefTheme = $state<Theme>('light');
	paletteOpen = $state(false);
	paletteMode = $state<'all' | 'ask'>('all');
	paletteQuery = $state('');
	/** The incident ⌘K is scoped to (its reference chip). */
	scope = $state<{ id: string; reference: string } | null>(null);
	sections = $state<SectionKind[]>(sectionsFor(null));
	holdByTyping = $state(false);
	announcement = $state('');
	/** A short "since you last looked" line for the watch bar. */
	statusNote = $state('');

	init() {
		const t = read('nj_theme');
		if (t === 'light' || t === 'dark') this.theme = t;
		const b = read('nj_brief_theme');
		if (b === 'light' || b === 'dark') this.briefTheme = b;
		this.holdByTyping = read('nj_hold_typing') === '1';
	}
	setTheme(t: Theme) {
		this.theme = t;
		write('nj_theme', t);
		document.documentElement.dataset.theme = t;
	}
	toggleTheme() {
		this.setTheme(this.theme === 'dark' ? 'light' : 'dark');
	}
	setBriefTheme(t: Theme) {
		this.briefTheme = t;
		write('nj_brief_theme', t);
		document.documentElement.dataset.theme = t;
	}
	setHoldByTyping(v: boolean) {
		this.holdByTyping = v;
		write('nj_hold_typing', v ? '1' : '0');
	}
	openPalette(mode: 'all' | 'ask' = 'all', query = '') {
		this.paletteMode = mode;
		this.paletteQuery = query;
		this.paletteOpen = true;
	}
	closePalette() {
		this.paletteOpen = false;
	}
	// ---------- the turnover ----------
	loadSections(role: Role | null) {
		this.sections = sectionsFor(role);
	}
	setPreset(role: Role) {
		this.sections = sectionsFor(role);
	}
	// ---------- feedback ----------
	announce(text: string) {
		this.announcement = '';
		queueMicrotask(() => (this.announcement = text));
	}
	toast(text: string, tone: ToastTone = 'ok') {
		toaster.create({ description: text, type: tone === 'ok' ? 'success' : tone === 'bad' ? 'error' : 'info', duration: 4200 });
		this.announce(text);
	}
}

export const ui = new Ui();
