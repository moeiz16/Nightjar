// The app's API: the real Nightjar SOC API through the /api proxy by default, or the in-browser mock layer
// when PUBLIC_API_MOCK=1 (npm run dev:mock / preview:mock). Both drive the same typed client.
import { env } from '$env/dynamic/public';
import { browser } from '$app/environment';
import { createClient } from './client';
import { endpoints } from './endpoints';
import { createMockServer, type MockServer } from './mock/server';

export const MOCK = env.PUBLIC_API_MOCK === '1' || env.PUBLIC_API_MOCK === 'true';

let mockServer: MockServer | null = null;
export function mock(): MockServer | null {
	if (!MOCK) return null;
	if (!mockServer) {
		mockServer = createMockServer({
			live: browser,
			storage: browser ? window.localStorage : null,
			setCookie: browser ? (n, v) => (document.cookie = `${n}=${encodeURIComponent(v)}; path=/; SameSite=Lax`) : undefined
		});
	}
	return mockServer;
}

let sessionEndedHandler: ((code: string) => void) | null = null;
export function onSessionEnded(fn: (code: string) => void) {
	sessionEndedHandler = fn;
}

export const client = createClient({
	fetch: (input, init) => (MOCK ? mock()!.fetch(input, init) : fetch(input, init)),
	onSessionEnded: (code) => sessionEndedHandler?.(code)
});
export const api = endpoints(client);
export { ApiError, problemMessage } from './client';
