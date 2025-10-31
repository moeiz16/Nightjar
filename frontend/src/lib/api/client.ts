// API client following docs/API_CONTRACT.md §1.1–1.2: same-origin /api/v1, cookie session, X-CSRF-Token echoed
// from the nj_csrf cookie on unsafe methods, RFC 7807 problem details as errors, session refresh at most every
// 5 minutes while the person is active, and the server clock offset taken from the Date header.
// The transport (fetch) is injected so the mock layer and the tests drive the same code.

import { offsetFromDateHeader, setServerOffset } from '$lib/time/clock';

export const API_BASE = '/api/v1';
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
export const REFRESH_EVERY_MS = 5 * 60 * 1000;

export interface Problem {
	type: string;
	title: string;
	status: number;
	detail?: string;
	code?: string;
	errors?: { field: string | null; message: string }[];
	requestId?: string;
}

export class ApiError extends Error {
	constructor(public readonly problem: Problem) {
		super(problem.detail ?? problem.title);
		this.name = 'ApiError';
	}
	get code(): string | undefined {
		return this.problem.code;
	}
	get status(): number {
		return this.problem.status;
	}
	/** Field errors as a map, for forms. */
	fieldErrors(): Record<string, string> {
		const out: Record<string, string> = {};
		for (const e of this.problem.errors ?? []) out[e.field ?? '_'] = e.message;
		return out;
	}
}

/** Plain-language message for a problem, never engineering vocabulary. */
export function problemMessage(err: unknown): string {
	if (!(err instanceof ApiError)) return 'Something went wrong. Try again in a moment.';
	switch (err.code) {
		case 'invalid_credentials':
			return 'That email and password do not match.';
		case 'session_expired':
		case 'not_authenticated':
			return 'Your session ended. Sign in again to continue.';
		case 'self_approval':
			return 'You asked for this action, so someone else has to authorise it.';
		case 'already_decided':
			return 'Someone has already decided on this.';
		case 'stale_version':
			return 'Someone else changed this while you were editing. Reload to see their version.';
		case 'forbidden':
			return 'Your role cannot do this.';
		case 'expired':
			return 'This link or request has expired.';
		case 'rate_limited':
			return 'Too many attempts. Wait a minute and try again.';
		case 'agents_unavailable':
			return 'The teammates are not on shift right now. Try again shortly.';
		case 'already_submitted':
			return 'This notice has already been recorded as submitted.';
		case 'clock_not_started':
			return 'This clock has not started yet.';
		default:
			return err.problem.detail ?? err.problem.title ?? 'Something went wrong.';
	}
}

export type Fetcher = typeof fetch;

export interface ClientOptions {
	fetch: Fetcher;
	/** Read a cookie (injectable for tests). */
	readCookie?: (name: string) => string | undefined;
	/** Called once when the server says the session is gone. */
	onSessionEnded?: (code: string) => void;
	/** Clock for refresh throttling (tests). */
	nowMs?: () => number;
}

function documentCookie(name: string): string | undefined {
	if (typeof document === 'undefined') return undefined;
	const hit = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
	return hit ? decodeURIComponent(hit.slice(name.length + 1)) : undefined;
}

export interface RequestOptions {
	query?: Record<string, string | number | boolean | undefined | null>;
	signal?: AbortSignal;
}

export function buildQuery(q?: RequestOptions['query']): string {
	if (!q) return '';
	const p = new URLSearchParams();
	for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
	const s = p.toString();
	return s ? `?${s}` : '';
}

export function createClient(opts: ClientOptions) {
	const readCookie = opts.readCookie ?? documentCookie;
	const nowMs = opts.nowMs ?? (() => Date.now());
	let lastRefresh = nowMs();
	let refreshing: Promise<void> | null = null;

	async function ensureCsrf(): Promise<string> {
		let token = readCookie('nj_csrf');
		if (!token) {
			await opts.fetch(`${API_BASE}/auth/csrf`, { credentials: 'same-origin' });
			token = readCookie('nj_csrf');
		}
		return token ?? '';
	}

	async function request<T>(method: string, path: string, body?: unknown, ro: RequestOptions = {}): Promise<T> {
		const headers: Record<string, string> = { Accept: 'application/json' };
		if (body !== undefined) headers['Content-Type'] = 'application/json';
		if (UNSAFE.has(method)) headers['X-CSRF-Token'] = await ensureCsrf();
		let res: Response;
		try {
			res = await opts.fetch(`${API_BASE}${path}${buildQuery(ro.query)}`, {
				method,
				headers,
				credentials: 'same-origin',
				signal: ro.signal,
				body: body === undefined ? undefined : JSON.stringify(body)
			});
		} catch (e) {
			if (e instanceof DOMException && e.name === 'AbortError') throw e;
			throw new ApiError({ type: 'about:blank', title: 'Unavailable', status: 503, code: 'unavailable', detail: 'Nightjar SOC cannot be reached right now.' });
		}
		const off = offsetFromDateHeader(res.headers.get('date'));
		if (off) setServerOffset(off);
		if (res.status === 204) return undefined as T;
		const type = res.headers.get('content-type') ?? '';
		const data = type.includes('json') ? await res.json().catch(() => ({})) : await res.text();
		if (!res.ok) {
			const problem: Problem = {
				type: 'about:blank',
				title: res.statusText || 'Error',
				status: res.status,
				...(typeof data === 'object' ? (data as Partial<Problem>) : { detail: String(data) })
			};
			if (res.status === 401 && (problem.code === 'session_expired' || problem.code === 'not_authenticated')) {
				opts.onSessionEnded?.(problem.code);
			}
			throw new ApiError(problem);
		}
		return data as T;
	}

	/** Slide the session while the person is active; at most once per REFRESH_EVERY_MS. */
	async function touch(): Promise<boolean> {
		if (refreshing) return false;
		if (nowMs() - lastRefresh < REFRESH_EVERY_MS) return false;
		lastRefresh = nowMs();
		refreshing = request<void>('POST', '/auth/refresh', {})
			.then(() => undefined)
			.catch(() => undefined)
			.finally(() => (refreshing = null));
		await refreshing;
		return true;
	}

	return {
		request,
		touch,
		get: <T>(path: string, ro?: RequestOptions) => request<T>('GET', path, undefined, ro),
		post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
		patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
		put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
		delete: <T>(path: string) => request<T>('DELETE', path)
	};
}

export type Client = ReturnType<typeof createClient>;
