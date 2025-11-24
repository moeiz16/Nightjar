// Plain-language labels shared by the console and the brief.
import type { Deadline, Severity, Regime } from '$lib/api/types';

const COUNTRY_SHORT: Record<string, string> = { Belgium: 'BE', 'United States': 'US', Australia: 'AU', US: 'US', BE: 'BE', AU: 'AU' };
export function regimeShort(r: Regime | string): string {
	return r === 'sec_8k' ? 'SEC 8-K' : r === 'gdpr_art33' ? 'GDPR' : r === 'nis2' ? 'NIS2' : r === 'soci' ? 'SOCI' : String(r).toUpperCase();
}
/** "NIS2 early warning" → "early warning": the regime is already named beside it. */
const noticeOnly = (label: string) => label.replace(/^(NIS2|SOCI|GDPR|SEC)\s+/i, '');
export function clockLabel(d: Deadline, withRef = false): string {
	const parts = [regimeShort(d.regime), COUNTRY_SHORT[d.jurisdiction] ?? d.jurisdiction];
	if (d.state === 'not_started') parts.push(d.incidentReference);
	else parts.push(noticeOnly(d.noticeLabel));
	if (withRef && d.state !== 'not_started') parts.push(d.incidentReference);
	return parts.join(' · ');
}
export function clockLongLabel(d: Deadline): string {
	const country = d.jurisdiction;
	if (d.regime === 'sec_8k') return `SEC ${d.noticeLabel.replace(/^SEC\s+/, '')} · ${country}`;
	return `${regimeShort(d.regime)} ${noticeOnly(d.noticeLabel)} · ${country}`;
}
export function noticeHref(d: Deadline): string {
	return d.state === 'not_started' || !d.noticeId ? `/incidents/${d.incidentReference}?decide=${d.regime}` : `/incidents/${d.incidentReference}/notice/${d.regime}?n=${d.noticeId}`;
}
export const SEV_BARS: Record<Severity, string> = {
	critical: '▮▮▮▮',
	high: '▮▮▮▯',
	medium: '▮▮▯▯',
	low: '▮▯▯▯',
	info: '▯▯▯▯'
};
export const SEV_WORD: Record<Severity, string> = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' };
export const LANE: Record<string, { n: number; label: string; letter: string }> = {
	triage: { n: 1, label: 'Triage', letter: 'T' },
	orchestrator: { n: 1, label: 'Triage', letter: 'T' },
	enrichment: { n: 2, label: 'Enrichment', letter: 'E' },
	response: { n: 3, label: 'Response', letter: 'R' },
	people: { n: 4, label: 'People', letter: 'P' }
};
export const STAGES = ['triage', 'investigate', 'contain', 'report'] as const;
export const STAGE_WORD: Record<string, string> = { triage: 'Triage', investigate: 'Investigate', contain: 'Contain', report: 'Report' };
export const STATUS_WORD: Record<string, string> = {
	new: 'new',
	triage: 'triage',
	investigating: 'investigating',
	containing: 'containing',
	monitoring: 'contained · monitoring',
	resolved: 'resolved',
	closed: 'closed'
};
export function plural(n: number, one: string, many = one + 's'): string {
	return `${n.toLocaleString('en-GB')} ${n === 1 ? one : many}`;
}
export function initials(name: string): string {
	return name.split(/\s+/).map((p) => p[0] ?? '').slice(0, 2).join('').toUpperCase();
}
