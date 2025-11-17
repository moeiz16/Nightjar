// Typed endpoints for every contract route the web app uses (docs/API_CONTRACT.md Part 1).
import type { Client } from './client';
import type * as T from './types';

const enc = encodeURIComponent;

/** The API forbids unknown fields: send only the contract's HuntQueryDoc keys. */
export function wireQuery(q: T.HuntQueryDoc): Omit<T.HuntQueryDoc, 'text'> {
	const { text: _text, ...rest } = q;
	void _text;
	return rest;
}

const dirCache = new Map<string, Promise<unknown[]>>();
/** The inventory changes only through approved actions, so one fetch per page load is enough. */
function directoryList<X>(c: Client, path: string): Promise<X[]> {
	const hit = dirCache.get(path);
	if (hit) return hit as Promise<X[]>;
	const p = c.get<X[]>(path);
	dirCache.set(path, p as Promise<unknown[]>);
	p.catch(() => dirCache.delete(path));
	return p;
}

export function endpoints(c: Client) {
	return {
		auth: {
			csrf: () => c.get<void>('/auth/csrf'),
			login: (email: string, password: string) => c.post<T.Me>('/auth/login', { email, password }),
			logout: () => c.post<void>('/auth/logout'),
			me: () => c.get<T.Me>('/auth/me'),
			/** 200 with `me: null` when signed out: public pages check without an error in the console */
			session: () => c.get<{ me: T.Me | null }>('/auth/session'),
			refresh: () => c.post<{ expiresAt: string; absoluteExpiresAt: string }>('/auth/refresh'),
			switchOrg: (orgId: string) => c.post<T.Me>('/auth/switch-org', { orgId })
		},
		org: {
			get: () => c.get<T.Org>('/org'),
			members: () => c.get<{ items: T.Member[] }>('/org/members'),
			setRole: (id: string, role: T.Role) => c.patch<T.Member>(`/org/members/${enc(id)}`, { role }),
			removeMember: (id: string) => c.delete<void>(`/org/members/${enc(id)}`),
			invitations: () => c.get<{ items: T.Invitation[] } | T.Invitation[]>('/org/invitations'),
			invite: (email: string, role: T.Role) => c.post<T.Invitation>('/org/invitations', { email, role }),
			revokeInvitation: (id: string) => c.delete<void>(`/org/invitations/${enc(id)}`)
		},
		invitations: {
			preview: (token: string) => c.get<T.InvitationPreview>(`/invitations/${enc(token)}`),
			accept: (token: string, body: { fullName?: string; password?: string }) =>
				c.post<T.Me>(`/invitations/${enc(token)}/accept`, body)
		},
		events: {
			river: (after?: string, limit = 50) =>
				c.get<{ lines: T.RiverLine[]; eventsPerMinute: number; minutes?: T.MinuteTotal[] }>('/events/river', { query: { after, limit } }),
			list: (q: Record<string, string | number | undefined>) => c.get<T.Paged<T.NormalizedEventDoc>>('/events', { query: q }),
			get: (eventId: string) => c.get<T.NormalizedEventDoc>(`/events/${enc(eventId)}`)
		},
		rules: {
			list: (q: { q?: string; enabled?: boolean } = {}) => c.get<T.RuleInfo[]>('/rules', { query: q }),
			get: (id: string) => c.get<{ rule: T.RuleInfo; yaml: string; exceptions?: T.RuleException[] }>(`/rules/${enc(id)}`),
			setEnabled: (id: string, enabled: boolean) => c.patch<T.RuleInfo>(`/rules/${enc(id)}`, { enabled })
		},
		exceptions: {
			list: (ruleId?: string) => c.get<T.RuleException[]>('/suppressions', { query: { ruleId } }),
			add: (body: { ruleId: string; field: 'user' | 'host' | 'ip'; value: string; reason: string; expiresInDays?: number }) =>
				c.post<T.RuleException>('/suppressions', body),
			remove: (id: string) => c.delete<void>(`/suppressions/${enc(id)}`)
		},
		incidents: {
			list: (q: Record<string, string | number | boolean | undefined> = {}) =>
				c.get<T.Paged<T.IncidentSummary>>('/incidents', { query: q }),
			get: (id: string) => c.get<T.Incident>(`/incidents/${enc(id)}`),
			timeline: (id: string, audience?: 'executive') =>
				c.get<T.TimelineEntry[]>(`/incidents/${enc(id)}/timeline`, { query: { audience } }),
			patch: (id: string, body: Record<string, unknown> & { version: number }) => c.patch<T.Incident>(`/incidents/${enc(id)}`, body),
			note: (id: string, body: string) => c.post<T.TimelineEntry>(`/incidents/${enc(id)}/notes`, { body }),
			investigations: (id: string) => c.get<T.RunSummary[]>(`/incidents/${enc(id)}/investigations`),
			actions: (id: string) => c.get<T.ProposalDoc[]>(`/incidents/${enc(id)}/actions`),
			reporting: (id: string) => c.get<T.IncidentReporting>(`/incidents/${enc(id)}/reporting`),
			detections: (id: string) => c.get<T.DetectionDoc[]>(`/incidents/${enc(id)}/detections`),
			/** Summarised evidence every reader may see (the CISO included): no matched fields or log text. */
			evidence: (id: string) => c.get<T.EvidenceSummary>(`/incidents/${enc(id)}/evidence`),
			addHuntMatch: (id: string, eventId: string, note?: string) =>
				c.post<T.TimelineEntry>(`/incidents/${enc(id)}/evidence`, { eventId, note: note || undefined }),
			defer: (id: string, regime: T.Regime, body: { entityId: string; decideBy: string; note?: string }) =>
				c.post<T.RegimeAssessment>(`/incidents/${enc(id)}/reporting/${regime}/defer`, body),
			assess: (id: string, regime: T.Regime, d: T.RegimeDetermination) =>
				c.post<T.RegimeAssessment>(`/incidents/${enc(id)}/reporting/${regime}/assess`, d),
			draftNotice: (id: string, regime: T.Regime, type: string) => c.post<T.Notice>(`/incidents/${enc(id)}/notices`, { regime, type })
		},
		investigations: {
			get: (runId: string) => c.get<T.Run>(`/investigations/${enc(runId)}`),
			step: (runId: string, seq: number) => c.get<T.AgentStepDoc>(`/investigations/${enc(runId)}/steps/${seq}`),
			disagree: (runId: string, seq: number, verdict: T.Verdict, note: string) =>
				c.post<T.StepFeedback>(`/investigations/${enc(runId)}/steps/${seq}/feedback`, { verdict, note }),
			rerun: (incidentId: string, reason?: string) => c.post<T.RunSummary>(`/incidents/${enc(incidentId)}/investigations`, { reason })
		},
		approvals: {
			list: (q: { status?: 'pending' | 'all'; incidentId?: string; mine?: boolean; pageSize?: number } = {}) =>
				c.get<T.Paged<T.ProposalDoc>>('/approvals', { query: q }),
			/** Only ever called after a completed 1.2 s hold (contract §1.10). */
			approve: (id: string, note?: string) => c.post<T.ProposalDoc>(`/approvals/${enc(id)}/approve`, { note }),
			reject: (id: string, reason: string) => c.post<T.ProposalDoc>(`/approvals/${enc(id)}/reject`, { reason })
		},
		reporting: {
			clocks: () => c.get<T.Deadline[]>('/reporting/clocks')
		},
		notices: {
			get: (id: string) => c.get<T.Notice>(`/notices/${enc(id)}`),
			patch: (id: string, sections: { key: string; body: string }[], version: number) =>
				c.patch<T.Notice>(`/notices/${enc(id)}`, { sections, version }),
			approve: (id: string) => c.post<T.Notice>(`/notices/${enc(id)}/approve`),
			submit: (id: string, channel: 'portal' | 'email' | 'oral', note?: string) =>
				c.post<T.Notice>(`/notices/${enc(id)}/submit`, { channel, note })
		},
		hunting: {
			fields: () => c.get<T.HuntField[]>('/hunting/fields'),
			search: (q: T.HuntQueryDoc) => c.post<T.HuntResultDoc>('/hunting/search', wireQuery(q)),
			saved: () => c.get<T.SavedSearch[]>('/hunting/saved'),
			save: (name: string, description: string, query: T.HuntQueryDoc) =>
				c.post<T.SavedSearch>('/hunting/saved', { name, description, query: wireQuery(query) }),
			updateSaved: (id: string, body: Partial<T.SavedSearch>) => c.patch<T.SavedSearch>(`/hunting/saved/${enc(id)}`, body),
			deleteSaved: (id: string) => c.delete<void>(`/hunting/saved/${enc(id)}`),
			runSaved: (id: string) => c.post<T.HuntResultDoc>(`/hunting/saved/${enc(id)}/run`, {}),
			suggestions: () => c.get<T.Suggestion[]>('/hunting/suggestions')
		},
		watch: {
			get: () => c.get<T.WatchDoc>('/watch'),
			shiftLog: (since?: string) => c.get<T.ShiftLogEntry[]>('/watch/shift-log', { query: { since } }),
			seen: () => c.post<void>('/watch/seen')
		},
		executive: {
			posture: () => c.get<T.Posture>('/executive/posture'),
			morning: () => c.get<T.MorningBrief>('/executive/morning'),
			briefs: () => c.get<T.BriefListItem[]>('/executive/briefs'),
			brief: (week: string) => c.get<T.WeeklyBrief>(`/executive/briefs/${enc(week)}`)
		},
		ueba: {
			anomalies: (q: { entityType?: 'user' | 'host'; minScore?: number } = {}) =>
				c.get<T.Paged<T.AnomalyDoc>>('/ueba/anomalies', { query: q })
		},
		directory: {
			assets: () => directoryList<T.AssetDoc>(c, '/assets'),
			identities: () => directoryList<T.IdentityDoc>(c, '/identities')
		},
		settings: {
			regulators: () => c.get<T.RegimeContacts>('/settings/regulators'),
			saveRegulators: (body: T.RegimeContacts) => c.put<T.RegimeContacts>('/settings/regulators', body),
			profile: () => c.get<T.OrgProfile>('/settings/profile'),
			preferences: (body: Partial<T.OrgSettings>) => c.patch<T.OrgSettings>('/settings/preferences', body)
		},
		audit: {
			list: (q: Record<string, string | number | undefined> = {}) => c.get<T.Paged<T.AuditEntry>>('/audit', { query: q }),
			verify: () => c.get<T.AuditVerify>('/audit/verify')
		}
	};
}

export type Endpoints = ReturnType<typeof endpoints>;
