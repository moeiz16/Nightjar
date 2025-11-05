// The morning brief's words (DESIGN.md §6.11), composed at runtime from the API so every date and count is
// computed from today. Plain language only: business systems first, no host names. Pure, unit-tested.
import type { IncidentSummary, RegimeAssessment } from '$lib/api/types';

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
export function numberWord(n: number, capital = true): string {
	const w = WORDS[n] ?? String(n);
	return capital ? w : w.toLowerCase();
}

export interface HeadlineParts {
	lead: string;
	tail: string;
	em: string;
	end: string;
}

/**
 * "One incident from the last two nights is reportable. It is contained, and | two decisions are yours | today."
 */
export function briefHeadline(reportable: number, contained: number, decisions: number, window: 'overnight' | 'the last two nights'): HeadlineParts {
	const lead =
		reportable === 0
			? `Nothing from ${window} is reportable.`
			: `${numberWord(reportable)} ${reportable === 1 ? 'incident' : 'incidents'} from ${window} ${reportable === 1 ? 'is' : 'are'} reportable.`;
	const allContained = reportable > 0 && contained >= reportable;
	const tail = reportable === 0 ? ' The watch handled the night' : allContained ? (reportable === 1 ? ' It is contained' : ' All are contained') : ` ${numberWord(reportable - contained)} still needs containing`;
	if (decisions === 0) return { lead, tail: tail + '.', em: '', end: ' Nothing needs your decision today.' };
	return { lead, tail: tail + ', and ', em: `${numberWord(decisions, false)} ${decisions === 1 ? 'decision is' : 'decisions are'} yours`, end: ' today.' };
}

/** A short business name for an incident, for questions like "Is … material?" */
export function incidentShortName(i: Pick<IncidentSummary, 'kind' | 'title'>): string {
	switch (i.kind) {
		case 'cloud_credential_exposure':
			return 'the cloud key exposure at the invoices store';
		case 'credential_misuse':
			return 'the Antwerp mailbox takeover';
		case 'ransomware_precursor':
			return 'the dispatch platform intrusion';
		case 'data_exfiltration':
			return 'the contractor data transfer';
		case 'brute_force':
			return 'the password spray';
		default:
			return i.title.charAt(0).toLowerCase() + i.title.slice(1);
	}
}

export function decisionQuestion(a: RegimeAssessment, i: Pick<IncidentSummary, 'kind' | 'title'>): string {
	const name = incidentShortName(i);
	if (a.regime === 'sec_8k') return `Is ${name} material to Halden Logistics?`;
	if (a.regime === 'gdpr_art33') return `Does ${name} need a personal-data breach notification?`;
	return `Is ${name} reportable under ${a.regimeLabel}?`;
}

/** First sentence of a plain-language summary. */
export function firstSentence(text: string): string {
	const m = /^(.+?[.!?])(\s|$)/.exec(text.trim());
	return (m?.[1] ?? text).trim();
}

/** Which of 7 nights (0 = oldest, 6 = last night) an instant falls in, or -1 when outside 18:00–08:00. */
export function nightIndex(at: Date, lastNightMidnight: Date): { row: number; hour: number } | null {
	const hFromMidnight = (+at - +lastNightMidnight) / 3600e3; // last night spans -6 .. +8
	for (let row = 6; row >= 0; row--) {
		const offset = (6 - row) * 24;
		const h = hFromMidnight + offset; // relative to that night's midnight
		if (h >= -6 && h <= 8) return { row, hour: h + 6 }; // 0 = 18:00, 14 = 08:00
	}
	return null;
}
