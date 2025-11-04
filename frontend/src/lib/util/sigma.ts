// Detection rules as readable sentences (DESIGN.md §6.10), with the Sigma source a keystroke away. A small,
// dependency-free reader for the Sigma subset the rule bundle uses (maps, lists, scalars) and a translator from
// selections, modifiers and the condition into plain words. Pure, unit-tested.

export type Yaml = string | number | boolean | null | Yaml[] | { [k: string]: Yaml };

function scalar(raw: string): Yaml {
	const v = raw.trim();
	if (v === '') return null;
	if ((v.startsWith("'") && v.endsWith("'")) || (v.startsWith('"') && v.endsWith('"'))) return v.slice(1, -1).replace(/''/g, "'").replace(/\\\\/g, '\\');
	if (v === 'true' || v === 'false') return v === 'true';
	if (v === 'null' || v === '~') return null;
	if (/^-?\d+(\.\d+)?$/.test(v)) return Number(v);
	return v;
}

/** Parse the Sigma subset: indentation-nested maps and lists of scalars or maps. */
export function parseYaml(src: string): { [k: string]: Yaml } {
	const lines = src
		.split(/\r?\n/)
		.map((l) => l.replace(/\s+#.*$/, ''))
		.filter((l) => l.trim() && !l.trim().startsWith('#'));
	let i = 0;
	const indentOf = (l: string) => l.length - l.trimStart().length;
	function block(indent: number): Yaml {
		const first = lines[i];
		if (first === undefined) return null;
		if (first.trimStart().startsWith('- ')) {
			const arr: Yaml[] = [];
			while (i < lines.length && indentOf(lines[i]!) === indent && lines[i]!.trimStart().startsWith('- ')) {
				const item = lines[i]!.trimStart().slice(2);
				i++;
				const m = /^([^:]+):\s*(.*)$/.exec(item);
				if (m && !/^['"]/.test(item)) {
					const obj: { [k: string]: Yaml } = { [m[1]!.trim()]: scalar(m[2]!) };
					if (i < lines.length && indentOf(lines[i]!) > indent) Object.assign(obj, block(indentOf(lines[i]!)) as object);
					arr.push(obj);
				} else arr.push(scalar(item));
			}
			return arr;
		}
		const obj: { [k: string]: Yaml } = {};
		while (i < lines.length && indentOf(lines[i]!) === indent) {
			const line = lines[i]!.trim();
			const m = /^([^:]+?):(?:\s+(.*))?$/.exec(line);
			i++;
			if (!m) continue;
			const key = m[1]!.trim();
			const rest = m[2];
			if (rest !== undefined && rest.trim() !== '') obj[key] = scalar(rest);
			else if (i < lines.length && indentOf(lines[i]!) > indent) obj[key] = block(indentOf(lines[i]!));
			else if (i < lines.length && indentOf(lines[i]!) === indent && lines[i]!.trimStart().startsWith('- ')) obj[key] = block(indent);
			else obj[key] = null;
		}
		return obj;
	}
	const out = block(0);
	return out && typeof out === 'object' && !Array.isArray(out) ? out : {};
}

const FIELD: Record<string, string> = {
	Operation: 'the mailbox action',
	ForwardTo: 'the forwarding address',
	eventType: 'the sign-in event',
	outcome: 'the result',
	country_seen_before: 'the country was seen before for this person',
	network_seen_before: 'the network was seen before for this key',
	Image: 'the program',
	CommandLine: 'the command line',
	User: 'the account',
	'userIdentity.type': 'the identity type',
	eventName: 'the cloud action',
	action: 'the action',
	status: 'the result',
	'cs-host': 'the website',
	'cs-bytes': 'the upload size',
	EventID: 'the Windows event',
	SubjectUserName: 'the account'
};
const SOURCE: Record<string, string> = {
	'm365/audit': 'a Microsoft 365 mailbox change',
	'okta/system': 'a sign-in',
	'windows/process_creation': 'a program starts on a Windows computer',
	'windows/security': 'a Windows security event',
	'aws/cloudtrail': 'a cloud account action',
	'fortigate/vpn': 'a remote-access sign-in',
	'/proxy': 'a web upload'
};
const VALUE: Record<string, string> = {
	'New-InboxRule': 'creating an inbox rule',
	'user.session.start': 'a successful sign-in',
	'mfa.push.denied': 'a refused sign-in prompt',
	SUCCESS: 'success',
	IAMUser: 'a cloud user',
	'4698': 'a scheduled task was created'
};

const label = (f: string) => FIELD[f] ?? f.replace(/[._-]/g, ' ');
const val = (v: Yaml) => {
	const s = String(v);
	return VALUE[s] ?? `“${s.replace(/^\\/, '')}”`;
};

function clause(key: string, value: Yaml): string {
	const [field, ...mods] = key.split('|');
	const f = label(field!);
	if (typeof value === 'boolean') return value ? f : f.replace(' was seen before', ' was never seen before');
	const list = Array.isArray(value) ? value : [value];
	const all = mods.includes('all');
	const op = mods.includes('contains') ? 'contains' : mods.includes('endswith') ? 'ends with' : mods.includes('startswith') ? 'starts with' : mods.includes('gt') ? 'is more than' : 'is';
	const vs = list.map((x) => (mods.includes('gt') && typeof x === 'number' ? (x >= 1e6 ? `${Math.round(x / 1e6)} MB` : String(x)) : val(x)));
	if (vs.length === 1) return `${f} ${op} ${vs[0]}`;
	return `${f} ${op} ${all ? 'all of' : 'one of'} ${vs.join(', ')}`;
}

function selectionWords(sel: Yaml): string[] {
	if (!sel || typeof sel !== 'object') return [];
	if (Array.isArray(sel)) return sel.flatMap((s) => selectionWords(s));
	return Object.entries(sel).map(([k, v]) => clause(k, v));
}

export interface Sentence {
	when: string;
	clauses: string[];
	unless: string[];
	count: string | null;
}

export function ruleSentence(yaml: string): Sentence {
	const doc = parseYaml(yaml);
	const ls = (doc.logsource ?? {}) as Record<string, Yaml>;
	const key = `${ls.product ?? ''}/${ls.service ?? ls.category ?? ''}`;
	const when = SOURCE[key] ?? SOURCE[`/${ls.category ?? ''}`] ?? 'an event arrives';
	const det = (doc.detection ?? {}) as Record<string, Yaml>;
	const cond = String(det.condition ?? 'selection');
	const [main = '', agg] = cond.split('|').map((s) => s.trim());
	const positives: string[] = [];
	const negatives: string[] = [];
	for (const part of main.split(/\s+and\s+/)) {
		const neg = part.startsWith('not ');
		const name = part.replace(/^not\s+/, '').trim();
		const words = selectionWords(det[name] ?? null);
		(neg ? negatives : positives).push(...words);
	}
	let count: string | null = null;
	if (agg) {
		const m = /count\((\w*)\)\s*by\s*(\w+)\s*(>=|>)\s*(\d+)/.exec(agg);
		if (m) {
			const n = Number(m[4]) + (m[3] === '>' ? 1 : 0);
			const by = m[2] === 'user' ? 'the same person' : m[2] === 'src_ip' ? 'the same address' : `the same ${m[2]}`;
			const what = m[1] === 'user' ? 'different accounts' : 'times';
			const tf = String(det.timeframe ?? '');
			const win = tf ? ` within ${tf.replace(/m$/, ' minutes').replace(/h$/, ' hours')}` : '';
			count = `${n} or more ${what} for ${by}${win}`;
		}
	}
	return { when, clauses: positives, unless: negatives, count };
}

/** One line for ledgers. */
export function ruleOneLine(yaml: string): string {
	const s = ruleSentence(yaml);
	const parts = [`When ${s.when}`, s.clauses.length ? `and ${s.clauses.join(' and ')}` : '', s.unless.length ? `unless ${s.unless.join(' or ')}` : '', s.count ? `, ${s.count}` : ''];
	return parts.filter(Boolean).join(' ').replace(' ,', ',');
}
