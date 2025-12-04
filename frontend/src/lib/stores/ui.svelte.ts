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

/** The lamp panel's eight windows (Alt+1 … Alt+8): the navigation, engraved like an annunciator. */
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
/** The station name the head shows for a path, before a page names itself (DESIGN.md §3). */
const STATIONS: [string, string][] = [
	['/incidents/', 'Incident'],
	['/watch', 'The watch'],
	['/hunt/saved', 'Saved searches'],
	['/hunt', 'Hunt'],
	['/behaviour', 'Unusual behaviour'],
	['/rules/', 'Rule'],
	['/rules', 'Detection rules'],
	['/assets', 'Assets'],
	['/identities', 'People and accounts'],
	['/activity', 'Activity log'],
	['/settings/members', 'Members'],
	['/settings/regimes', 'Regulator contacts'],
	['/brief/week', 'Weekly report'],
	['/brief', 'Morning brief']
];
export function stationFor(path: string): string {
	if (/^\/incidents\/[^/]+\/notice\//.test(path)) return 'Notice draft';
	return STATIONS.find(([p]) => path.startsWith(p))?.[1] ?? 'Nightjar SOC';
}

/** The line under the station name for pages that do not write their own. */
const LINES: [string, string][] = [
	['/hunt/saved', 'Searches worth repeating, with an owner, a note and an alert'],
	['/hunt', 'Ask the events a question in plain words'],
	['/behaviour', 'Each person and device against their own normal, last 14 days'],
	['/rules/', 'What it matches, in a sentence, with its noise and its exceptions'],
	['/rules', 'Every detection rule and how often it has been right'],
	['/assets', 'Every server, laptop and cloud account, and what it does for the business'],
	['/identities', 'How each account normally signs in, and what is out of character'],
	['/activity', 'Every sensitive action, by a person or a teammate, sealed in order'],
	['/settings/members', 'Who is on the watch bill, and what each role may authorise'],
	['/settings/regimes', 'Which regulators apply, and who is asked and who submits']
];
export function stationLine(path: string): string {
	return LINES.find(([p]) => path.startsWith(p))?.[1] ?? '';
}

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
	/** A short "since you last looked" line under the station name. */
	statusNote = $state('');
	/** A page's own station name and summary line, kept only while that page is open. */
	station = $state<{ path: string; title: string; sub?: string } | null>(null);

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
	/** Name the open page in the head (pass its path, so a stale name never shows on the next page). */
	setStation(path: string, title: string, sub?: string) {
		this.station = { path, title, sub };
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
