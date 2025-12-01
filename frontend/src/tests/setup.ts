// jsdom gaps used by the components under test.
if (!window.matchMedia) {
	window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
}

if (!('ResizeObserver' in window)) {
	// bind:clientWidth (the shift chart) observes size; jsdom has no layout, so nothing ever resizes
	(window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
		observe() {}
		unobserve() {}
		disconnect() {}
	};
}

import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';
afterEach(() => cleanup());
