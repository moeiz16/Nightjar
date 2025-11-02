// ⌘K command model (DESIGN.md §3): groups do / go to / switch, scoped to the focused incident first; Tab widens
// to everything. Pure functions so ranking and matching are unit-tested.

export type CommandGroup = 'do' | 'go to' | 'switch';
export const GROUP_ORDER: CommandGroup[] = ['do', 'go to', 'switch'];

export interface Command {
	id: string;
	group: CommandGroup;
	title: string;
	icon: string;
	hint?: string;
	keys?: string[];
	keywords?: string;
	/** Incident reference this command belongs to (NJ-1038); undefined = global. */
	scope?: string;
	disabled?: boolean;
	run: () => void | Promise<void>;
}

export interface Match {
	score: number;
	ranges: [number, number][];
}

/**
 * Case-insensitive match. Contiguous substrings score highest (word starts best); otherwise every query
 * character must appear in order (subsequence). Returns null when it does not match.
 */
export function matchText(text: string, query: string): Match | null {
	const q = query.trim().toLowerCase();
	if (!q) return { score: 0, ranges: [] };
	const t = text.toLowerCase();
	const idx = t.indexOf(q);
	if (idx >= 0) {
		const wordStart = idx === 0 || /[\s\-·:/(]/.test(t[idx - 1] ?? '');
		return { score: 100 - Math.min(idx, 40) + (wordStart ? 30 : 0) + (idx === 0 ? 20 : 0), ranges: [[idx, idx + q.length]] };
	}
	// all words present (any order)
	const words = q.split(/\s+/).filter(Boolean);
	if (words.length > 1 && words.every((w) => t.includes(w))) {
		const ranges = words.map((w) => {
			const i = t.indexOf(w);
			return [i, i + w.length] as [number, number];
		});
		return { score: 60 - words.length, ranges: ranges.sort((a, b) => a[0] - b[0]) };
	}
	// subsequence
	const ranges: [number, number][] = [];
	let ti = 0;
	let gaps = 0;
	for (const ch of q.replace(/\s+/g, '')) {
		const found = t.indexOf(ch, ti);
		if (found < 0) return null;
		gaps += found - ti;
		const last = ranges[ranges.length - 1];
		if (last && last[1] === found) last[1] = found + 1;
		else ranges.push([found, found + 1]);
		ti = found + 1;
	}
	if (gaps > q.length * 3) return null;
	return { score: Math.max(1, 40 - gaps - ranges.length * 3), ranges };
}

export interface Ranked {
	command: Command;
	match: Match;
}

/** Filter and rank. When `scope` is set and not widened, other incidents' commands are hidden. */
export function rankCommands(commands: Command[], query: string, opts: { scope?: string | null; widened?: boolean } = {}): Ranked[] {
	const scope = opts.scope ?? null;
	const out: Ranked[] = [];
	for (const c of commands) {
		if (scope && !opts.widened && c.scope && c.scope !== scope) continue;
		const m = matchText(c.title, query) ?? (c.keywords ? matchText(c.keywords, query) : null);
		if (!m) continue;
		const fromKeywords = !matchText(c.title, query);
		const score = (fromKeywords ? { ...m, ranges: [] } : m).score + (scope && c.scope === scope ? 25 : 0) - (c.disabled ? 15 : 0);
		out.push({ command: c, match: { score, ranges: fromKeywords ? [] : m.ranges } });
	}
	return out.sort(
		(a, b) => GROUP_ORDER.indexOf(a.command.group) - GROUP_ORDER.indexOf(b.command.group) || b.match.score - a.match.score
	);
}

/** Group ranked results for display, keeping each group's best-first order. */
export function groupRanked(list: Ranked[], perGroup = 7): { group: CommandGroup; items: Ranked[] }[] {
	return GROUP_ORDER.map((group) => ({ group, items: list.filter((r) => r.command.group === group).slice(0, perGroup) })).filter(
		(g) => g.items.length
	);
}

/** Split text into plain and highlighted parts for <mark>. */
export function highlight(text: string, ranges: [number, number][]): { text: string; hit: boolean }[] {
	if (!ranges.length) return [{ text, hit: false }];
	const parts: { text: string; hit: boolean }[] = [];
	let i = 0;
	for (const [a, b] of ranges) {
		if (a > i) parts.push({ text: text.slice(i, a), hit: false });
		parts.push({ text: text.slice(a, b), hit: true });
		i = b;
	}
	if (i < text.length) parts.push({ text: text.slice(i), hit: false });
	return parts;
}

/** g-chords: `g w` the watch, `g h` hunt, `g b` brief, `g r` rules, `g a` assets, `g l` activity, `g s` settings. */
export const G_CHORDS: Record<string, string> = {
	w: '/watch',
	h: '/hunt',
	b: '/brief',
	r: '/rules',
	a: '/assets',
	l: '/activity',
	s: '/settings/regimes',
	i: '/identities',
	u: '/behaviour'
};

/** True when a key event happened inside something that takes text. */
export function isTyping(target: EventTarget | null): boolean {
	const el = target as HTMLElement | null;
	if (!el || typeof el.tagName !== 'string') return false;
	return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
}
