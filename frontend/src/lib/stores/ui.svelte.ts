// UI state shared by the shell, the tiles and ⌘K: theme, palette, tile layout, personal preferences.
import type { Role } from '$lib/api/types';

export type Theme = 'dark' | 'light';
export type TileKind = 'events' | 'incident' | 'awaiting' | 'hunt' | 'activity' | 'evidence' | 'behaviour';
export const TILE_LABEL: Record<TileKind, string> = {
	events: 'events',
	incident: 'incident',
	awaiting: 'awaiting you',
	hunt: 'hunt',
	activity: 'activity',
	evidence: 'evidence',
	behaviour: 'unusual behaviour'
};
export const LAYOUTS: Record<Exclude<Role, 'executive' | 'admin'> | 'admin', TileKind[]> = {
	analyst: ['events', 'incident', 'hunt'],
	responder: ['events', 'incident', 'awaiting'],
	auditor: ['activity', 'incident', 'evidence'],
	admin: ['events', 'incident', 'awaiting']
};
export function presetFor(role: Role | null): TileKind[] {
	if (!role || role === 'executive') return [...LAYOUTS.responder];
	return [...LAYOUTS[role]];
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

export interface Toast {
	id: number;
	text: string;
	tone: 'ok' | 'info' | 'bad';
}

class Ui {
	theme = $state<Theme>('dark');
	briefTheme = $state<Theme>('light');
	paletteOpen = $state(false);
	paletteMode = $state<'all' | 'tile' | 'ask'>('all');
	paletteQuery = $state('');
	/** The incident ⌘K is scoped to (its reference chip). */
	scope = $state<{ id: string; reference: string } | null>(null);
	tiles = $state<TileKind[]>(['events', 'incident', 'awaiting']);
	focus = $state(1);
	zoomed = $state(false);
	mobileTile = $state(1);
	holdByTyping = $state(false);
	announcement = $state('');
	/** A short "since you last looked" line for the status line. */
	statusNote = $state('');
	toasts = $state<Toast[]>([]);
	#tid = 0;

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
	openPalette(mode: 'all' | 'tile' | 'ask' = 'all', query = '') {
		this.paletteMode = mode;
		this.paletteQuery = query;
		this.paletteOpen = true;
	}
	closePalette() {
		this.paletteOpen = false;
	}
	// ---------- tiles ----------
	loadLayout(userId: string, role: Role | null) {
		const saved = read(`nj_layout_${userId}`);
		const preset = presetFor(role);
		this.tiles = saved ? (saved.split(',').filter(Boolean) as TileKind[]) : preset;
		if (!this.tiles.length) this.tiles = preset;
		this.focus = Math.min(this.tiles.indexOf('incident') >= 0 ? this.tiles.indexOf('incident') : 0, this.tiles.length - 1);
		this.mobileTile = this.focus;
	}
	saveLayout(userId: string) {
		write(`nj_layout_${userId}`, this.tiles.join(','));
	}
	split(kind?: TileKind) {
		if (this.tiles.length >= 4) return false;
		const order: TileKind[] = ['events', 'incident', 'awaiting', 'hunt', 'activity', 'behaviour', 'evidence'];
		const next = kind ?? order.find((k) => !this.tiles.includes(k)) ?? 'hunt';
		this.tiles.splice(this.focus + 1, 0, next);
		this.focus = this.focus + 1;
		this.zoomed = false;
		return true;
	}
	close() {
		if (this.tiles.length <= 1) return false;
		this.tiles.splice(this.focus, 1);
		this.focus = Math.max(0, Math.min(this.focus, this.tiles.length - 1));
		this.zoomed = false;
		return true;
	}
	move(delta: number) {
		const n = this.tiles.length;
		this.focus = (this.focus + delta + n) % n;
		this.mobileTile = this.focus;
	}
	swap(kind: TileKind) {
		const existing = this.tiles.indexOf(kind);
		if (existing >= 0 && existing !== this.focus) {
			this.focus = existing;
			return;
		}
		this.tiles[this.focus] = kind;
	}
	setPreset(role: Role) {
		this.tiles = presetFor(role);
		this.focus = 1;
		this.zoomed = false;
	}
	// ---------- feedback ----------
	announce(text: string) {
		this.announcement = '';
		queueMicrotask(() => (this.announcement = text));
	}
	toast(text: string, tone: Toast['tone'] = 'ok') {
		const id = ++this.#tid;
		this.toasts.push({ id, text, tone });
		this.announce(text);
		setTimeout(() => (this.toasts = this.toasts.filter((t) => t.id !== id)), 4200);
	}
}

export const ui = new Ui();
