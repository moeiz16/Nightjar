import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import type { ProxyOptions } from 'vite';

// The browser talks to one origin (:3030); /api is proxied to the Nightjar SOC API on :8030 so the session
// and CSRF cookies just work. SSE (/api/v1/events/stream) streams through the same proxy.
const API_TARGET = process.env.NIGHTJAR_API_URL ?? 'http://127.0.0.1:8030';

const apiProxy: Record<string, ProxyOptions> = {
	'/api': {
		target: API_TARGET,
		changeOrigin: false,
		ws: false,
		configure: (proxy) => {
			proxy.on('proxyRes', (res) => {
				if (res.headers['content-type']?.includes('text/event-stream')) {
					res.headers['cache-control'] = 'no-cache, no-transform';
				}
			});
		}
	}
};

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: { port: 3030, strictPort: true, proxy: apiProxy },
	preview: { port: 3030, strictPort: true, proxy: apiProxy },
	// Component tests mount Svelte in jsdom, so resolve the browser build.
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['src/tests/setup.ts']
	}
});
