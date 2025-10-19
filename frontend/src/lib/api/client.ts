// Minimal API client following docs/API_CONTRACT.md §1.1–1.2: same-origin /api/v1, cookie session,
// X-CSRF-Token echoed from the nj_csrf cookie on unsafe methods, RFC 7807 problem details as errors.
// Builder C extends this (typed endpoints, session refresh, SSE).

export const API_BASE = '/api/v1';
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

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
	}
	get code(): string | undefined {
		return this.problem.code;
	}
}

function readCookie(name: string): string | undefined {
	if (typeof document === 'undefined') return undefined;
	const hit = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
	return hit ? decodeURIComponent(hit.slice(name.length + 1)) : undefined;
}

async function ensureCsrf(): Promise<string> {
	let token = readCookie('nj_csrf');
	if (!token) {
		await fetch(`${API_BASE}/auth/csrf`, { credentials: 'same-origin' });
		token = readCookie('nj_csrf');
	}
	return token ?? '';
}

async function request<T>(method: string, path: string, body?: unknown, fetcher: typeof fetch = fetch): Promise<T> {
	const headers: Record<string, string> = { Accept: 'application/json' };
	if (body !== undefined) headers['Content-Type'] = 'application/json';
	if (UNSAFE.has(method)) headers['X-CSRF-Token'] = await ensureCsrf();
	const res = await fetcher(`${API_BASE}${path}`, {
		method,
		headers,
		credentials: 'same-origin',
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (res.status === 204) return undefined as T;
	const data = await res.json().catch(() => ({}));
	if (!res.ok) throw new ApiError({ title: res.statusText, status: res.status, type: 'about:blank', ...data });
	return data as T;
}

export const api = {
	get: <T>(path: string, fetcher?: typeof fetch) => request<T>('GET', path, undefined, fetcher),
	post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
	patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
	put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
	delete: <T>(path: string) => request<T>('DELETE', path)
};
