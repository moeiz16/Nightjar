import { describe, expect, it, vi } from 'vitest';
import { ApiError, buildQuery, createClient, problemMessage, REFRESH_EVERY_MS } from '$lib/api/client';
import { endpoints } from '$lib/api/endpoints';
import { serverOffset, setServerOffset } from '$lib/time/clock';

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
}

describe('api client', () => {
	it('sends the CSRF cookie as X-CSRF-Token on unsafe methods', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json({ ok: true }));
		const c = createClient({ fetch, readCookie: () => 'tok123' });
		await c.post('/approvals/a/approve', {});
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('tok123');
	});
	it('does not send a CSRF token on GET', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json([]));
		const c = createClient({ fetch, readCookie: () => 'tok' });
		await c.get('/rules');
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
	});
	it('fetches /auth/csrf first when the cookie is missing', async () => {
		let cookie: string | undefined;
		const fetch = vi.fn(async (url: string) => {
			if (url.endsWith('/auth/csrf')) {
				cookie = 'fresh';
				return new Response(null, { status: 204 });
			}
			return json({});
		});
		const c = createClient({ fetch: fetch as unknown as typeof globalThis.fetch, readCookie: () => cookie });
		await c.post('/auth/login', { email: 'a', password: 'b' });
		expect(fetch.mock.calls[0]![0]).toBe('/api/v1/auth/csrf');
		expect(((fetch.mock.calls[1] as unknown as [string, RequestInit])[1].headers as Record<string, string>)['X-CSRF-Token']).toBe('fresh');
	});
	it('prefixes /api/v1 and builds the query string, skipping empty values', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json({ items: [] }));
		const c = createClient({ fetch });
		await c.get('/incidents', { query: { status: 'all', q: '', page: 2, assignee: undefined } });
		expect(fetch.mock.calls[0]![0]).toBe('/api/v1/incidents?status=all&page=2');
		expect(buildQuery({})).toBe('');
	});
	it('turns RFC 7807 problems into ApiError with code and field errors', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json({ type: 'x', title: 'Validation failed', status: 422, code: 'validation_error', errors: [{ field: 'email', message: 'Enter a valid email address.' }] }, 422));
		const c = createClient({ fetch, readCookie: () => 't' });
		const err = await c.post('/org/invitations', {}).catch((e: ApiError) => e) as ApiError;
		expect(err).toBeInstanceOf(ApiError);
		expect(err.code).toBe('validation_error');
		expect(err.status).toBe(422);
		expect(err.fieldErrors()).toEqual({ email: 'Enter a valid email address.' });
	});
	it('returns undefined for 204', async () => {
		const c = createClient({ fetch: async () => new Response(null, { status: 204 }), readCookie: () => 't' });
		expect(await c.post('/auth/logout')).toBeUndefined();
	});
	it('calls onSessionEnded on 401 session_expired', async () => {
		const ended = vi.fn();
		const c = createClient({ fetch: async () => json({ status: 401, code: 'session_expired', title: 'x' }, 401), onSessionEnded: ended });
		await c.get('/watch').catch(() => undefined);
		expect(ended).toHaveBeenCalledWith('session_expired');
	});
	it('does not end the session on 401 invalid_credentials', async () => {
		const ended = vi.fn();
		const c = createClient({ fetch: async () => json({ status: 401, code: 'invalid_credentials', title: 'x' }, 401), onSessionEnded: ended, readCookie: () => 't' });
		await c.post('/auth/login', {}).catch(() => undefined);
		expect(ended).not.toHaveBeenCalled();
	});
	it('maps a network failure to a plain "unavailable" problem', async () => {
		const c = createClient({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
		const err = await c.get('/watch').catch((e: ApiError) => e) as ApiError;
		expect(err.code).toBe('unavailable');
		expect(err.message).not.toMatch(/fetch/i);
	});
	it('slides the session at most every 5 minutes', async () => {
		let t = 0;
		const fetch = vi.fn(async (..._a: unknown[]) => json({ expiresAt: 'x', absoluteExpiresAt: 'y' }));
		const c = createClient({ fetch, readCookie: () => 't', nowMs: () => t });
		expect(await c.touch()).toBe(false);
		t = REFRESH_EVERY_MS + 1;
		expect(await c.touch()).toBe(true);
		expect(await c.touch()).toBe(false);
		expect(fetch).toHaveBeenCalledTimes(1);
		expect(fetch.mock.calls[0]![0]).toBe('/api/v1/auth/refresh');
	});
	it('takes the server clock offset from the Date header', async () => {
		setServerOffset(0);
		const ahead = new Date(Date.now() + 3 * 3600e3).toUTCString();
		const c = createClient({ fetch: async () => json({}, 200, { date: ahead }) });
		await c.get('/watch');
		expect(Math.round(serverOffset() / 3600e3)).toBe(3);
		setServerOffset(0);
	});
	it('words problems for people, never engineering terms', () => {
		const mk = (code: string) => new ApiError({ type: 'x', title: 't', status: 409, code });
		expect(problemMessage(mk('self_approval'))).toMatch(/someone else has to authorise/);
		expect(problemMessage(mk('stale_version'))).toMatch(/Reload/);
		expect(problemMessage(mk('agents_unavailable'))).toMatch(/teammates/);
		expect(problemMessage(new Error('x'))).toMatch(/Something went wrong/);
	});
	it('approve posts only to the contract path', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json({ id: 'a', status: 'approved' }));
		const api = endpoints(createClient({ fetch, readCookie: () => 't' }));
		await api.approvals.approve('a-1');
		expect(fetch.mock.calls[0]![0]).toBe('/api/v1/approvals/a-1/approve');
		expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('POST');
	});
	it('encodes path ids', async () => {
		const fetch = vi.fn(async (..._a: unknown[]) => json({}));
		const api = endpoints(createClient({ fetch }));
		await api.incidents.get('NJ 1038/x');
		expect(fetch.mock.calls[0]![0]).toBe('/api/v1/incidents/NJ%201038%2Fx');
	});
});
