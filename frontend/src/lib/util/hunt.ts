// Plain-words hunting (DESIGN.md §6.7): "devices that uploaded to upload.files-sync.example in the last 7 days"
// becomes structured filters (contract HuntQueryDoc). Nothing is ever sent as a query language. Pure.
import type { HuntFilter, HuntQueryDoc } from '$lib/api/types';

export interface Chip {
	label: string;
	filter: HuntFilter;
}

// Plain words → the normaliser's action names (backend/nightjar/ingest/formats: the same names the hunting store,
// the saved searches and the suggestions use). First match wins, so the specific phrases come first.
const ACTIONS: [RegExp, string | string[], string][] = [
	[/\binbox rules?\b|\bforward(ing)? rules?\b/i, 'inbox-rule-created', 'inbox rule created'],
	[/\b(refused|denied|declined|rejected)\b.*\bprompts?\b|\bprompts?\b.*\b(refused|denied|declined)\b|\bmfa fatigue\b/i, 'mfa-push-denied', 'sign-in prompt refused'],
	[/\b(approved|accepted)\b.*\bprompts?\b|\bprompts?\b.*\b(approved|accepted)\b/i, 'mfa-push-approved', 'sign-in prompt approved'],
	[/\bprompts?\b|\bmfa\b|\bpush(es)?\b/i, ['mfa-push-sent', 'mfa-push-denied', 'mfa-push-approved'], 'sign-in prompt'],
	[/\bpublic code\b|\bleaked keys?\b|\bsecrets?\b/i, 'secret-exposed', 'secret in public code'],
	[/\bmail from\b|\bgot mail\b|\breceived mail\b|\bemails?\b|\bphish/i, 'email-delivered', 'mail delivered'],
	[/\bmailbox\b|\bread (the )?mail\b|\bmail (items )?read\b/i, 'mailbox-items-accessed', 'mailbox read'],
	[/\bfailed sign-?ins?\b|\bfailed logins?\b|\bsign-?ins? (that )?failed\b|\bspray\b|\bwrong password/i, 'logon-failed', 'sign-in failed'],
	[/\bsession reuse\b|\breused\b|\baddress changed\b/i, 'session-ip-changed', 'session moved address'],
	[/\bscheduled tasks?\b/i, 'scheduled-task-created', 'scheduled task created'],
	[/\bservices? (installed|created)\b|\bnew services?\b|\bpsexec\b/i, 'service-installed', 'service installed'],
	[/\bfile shares?\b|\bshares? (opened|accessed)\b/i, 'share-accessed', 'file share opened'],
	[/\bbucket\b|\bobjects? (read|downloaded)\b|\binvoices? (read|downloaded)\b/i, 's3-get-object', 'cloud file read'],
	[/\bnew (cloud )?users?\b|\bcreated? (a )?users?\b/i, 'iam-create-user', 'cloud user created'],
	[/\b(opened|clicked)\b.*\blinks?\b|\blinks? (opened|clicked)\b|\bvisited\b|\bconnect(ed|ions?)\b/i, 'network-connection', 'web connection'],
	[/\bupload(ed|s)?\b|\bsent out\b|\btransfers?\b/i, ['network-connection', 'network-flow'], 'upload'],
	[/\b(programs?|processes|process) (ran|started|run)\b|\bran\b|\bstarted\b/i, 'process-started', 'program started'],
	[/\bsign-?ins?\b|\bsigned in\b|\blogins?\b|\blogged on\b/i, 'logon', 'sign-in']
];
const DOMAIN = /\b((?:[a-z0-9-]+\.)+(?:example|com|net|org|io|be|nl|de|au|dev))\b/i;
const IPV4 = /\b(\d{1,3}(?:\.\d{1,3}){3})\b/;
const HOST = /\b((?:mem|ant|mel)-[a-z]+-?\d*|aws-prod|m365-tenant|okta)\b/i;
const USER = /\b([a-z]\.[a-z]{2,}|svc-[a-z]+|ci-deploy|adm-[a-z]+)\b/;

export function parseRange(text: string, now: Date): { from: Date; label: string } {
	const t = text.toLowerCase();
	const n = /\blast (\d{1,3}) (day|days|hour|hours|week|weeks)\b/.exec(t);
	if (n) {
		const k = Number(n[1]);
		const unit = n[2]!.startsWith('hour') ? 3600e3 : n[2]!.startsWith('week') ? 7 * 864e5 : 864e5;
		return { from: new Date(+now - k * unit), label: `last ${k} ${n[2]}` };
	}
	if (/\btoday\b|\blast night\b|\btonight\b/.test(t)) return { from: new Date(+now - 18 * 3600e3), label: 'since last night' };
	if (/\bthis week\b/.test(t)) return { from: new Date(+now - 7 * 864e5), label: 'this week' };
	if (/\bthis month\b|\blast 30\b/.test(t)) return { from: new Date(+now - 30 * 864e5), label: 'last 30 days' };
	return { from: new Date(+now - 7 * 864e5), label: 'last 7 days' };
}

export function parseHunt(text: string, now: Date): { query: HuntQueryDoc; chips: Chip[]; range: string } {
	const chips: Chip[] = [];
	const d = DOMAIN.exec(text);
	if (d) chips.push({ label: `domain = ${d[1]!.toLowerCase()}`, filter: { field: 'url', op: 'contains', value: d[1]!.toLowerCase() } });
	const ip = IPV4.exec(text);
	if (ip) chips.push({ label: `address = ${ip[1]}`, filter: { field: 'source.ip', op: 'eq', value: ip[1]! } });
	const h = HOST.exec(text);
	if (h) chips.push({ label: `device = ${h[1]!.toLowerCase()}`, filter: { field: 'host.name', op: 'eq', value: h[1]!.toLowerCase() } });
	const stripped = text.replace(DOMAIN, ' ');
	const u = USER.exec(stripped);
	if (u) chips.push({ label: `person = ${u[1]}`, filter: { field: 'user.name', op: 'eq', value: u[1]! } });
	for (const [rx, action, label] of ACTIONS) {
		if (rx.test(text)) {
			chips.push({ label: `event = ${label}`, filter: Array.isArray(action) ? { field: 'action', op: 'in', value: action } : { field: 'action', op: 'eq', value: action } });
			break;
		}
	}
	const r = parseRange(text, now);
	return {
		chips,
		range: r.label,
		query: { from: r.from.toISOString(), to: now.toISOString(), relativeHours: Math.round((+now - +r.from) / 3600e3), filters: chips.map((c) => c.filter), groupBy: [], metric: 'count', bucket: null, limit: 200, text: text.trim() }
	};
}

/** Rebuild chips from a stored query (saved searches). */
export function chipsFromQuery(q: HuntQueryDoc): Chip[] {
	const words: Record<string, string> = { domain: 'domain', url: 'link', 'host.name': 'device', 'user.name': 'person', 'source.ip': 'address', host: 'device', user: 'person', action: 'event', department: 'team', 'email.sender': 'sender', bytes_out: 'sent' };
	return q.filters.map((f) => ({ label: `${words[f.field] ?? f.field} ${f.op === 'contains' ? '∋' : f.op === 'gt' ? '>' : '='} ${Array.isArray(f.value) ? f.value.join(', ') : f.value}`, filter: f }));
}
