// jsdom gaps used by the components under test.
if (!window.matchMedia) {
	window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
}

import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';
afterEach(() => cleanup());
