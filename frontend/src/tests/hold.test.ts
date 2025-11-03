import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/svelte';
import { createHold, HOLD_MS, typedMatches } from '$lib/util/hold';
import HoldButton from '$lib/components/console/HoldButton.svelte';

function harness() {
	let t = 0;
	const frames: ((t: number) => void)[] = [];
	const events = { progress: [] as number[], done: 0, cancel: 0 };
	const h = createHold({
		now: () => t,
		raf: (cb) => (frames.push(cb), frames.length),
		caf: () => (frames.length = 0),
		onProgress: (p) => events.progress.push(p),
		onComplete: () => events.done++,
		onCancel: () => events.cancel++
	});
	const advance = (ms: number) => {
		t += ms;
		const f = frames.splice(0);
		f.forEach((cb) => cb(t));
	};
	return { h, events, advance };
}

describe('hold to authorise (controller)', () => {
	it('completes only after the full 1.2 s', () => {
		const { h, events, advance } = harness();
		h.start();
		advance(600);
		expect(events.done).toBe(0);
		expect(h.progress).toBeCloseTo(0.5);
		advance(600);
		expect(events.done).toBe(1);
		expect(h.done).toBe(true);
		expect(HOLD_MS).toBe(1200);
	});
	it('releasing early cancels and resets to 0', () => {
		const { h, events, advance } = harness();
		h.start();
		advance(1100);
		expect(h.release()).toBe(true);
		advance(500);
		expect(events.done).toBe(0);
		expect(events.cancel).toBe(1);
		expect(events.progress.at(-1)).toBe(0);
	});
	it('ignores a second start while holding (key repeat)', () => {
		const { h } = harness();
		expect(h.start()).toBe(true);
		expect(h.start()).toBe(false);
	});
	it('cannot be held again once done, until reset', () => {
		const { h, advance } = harness();
		h.start();
		advance(1300);
		expect(h.start()).toBe(false);
		h.reset();
		expect(h.start()).toBe(true);
	});
	it('release without a hold is a no-op', () => {
		const { h, events } = harness();
		expect(h.release()).toBe(false);
		expect(events.cancel).toBe(0);
	});
	it('typed confirmation must match the name exactly (case and spacing aside)', () => {
		expect(typedMatches(' akiaexamplehalden01 ', 'AKIAEXAMPLEHALDEN01')).toBe(true);
		expect(typedMatches('AKIA', 'AKIAEXAMPLEHALDEN01')).toBe(false);
		expect(typedMatches('', '')).toBe(false);
	});
});

describe('HoldButton', () => {
	let now = 0;
	beforeEach(() => {
		now = 0;
		vi.spyOn(performance, 'now').mockImplementation(() => now);
		vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(now), 16) as unknown as number);
		vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));
		vi.useFakeTimers();
	});
	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
	});
	const tick = async (ms: number) => {
		now += ms;
		await vi.advanceTimersByTimeAsync(ms);
	};
	it('is a real button that announces how to hold', () => {
		render(HoldButton, { onconfirm: async () => ({ label: 'ok', sub: '' }) });
		const b = screen.getByRole('button', { name: /hold to authorise/i });
		expect(b.tagName).toBe('BUTTON');
		const hint = document.getElementById(b.getAttribute('aria-describedby')!);
		expect(hint?.textContent).toMatch(/Press and hold for 1.2 seconds, or hold Space or Enter/);
	});
	it('a click alone authorises nothing', async () => {
		const onconfirm = vi.fn(async () => ({ label: 'ok', sub: '' }));
		render(HoldButton, { onconfirm });
		await fireEvent.click(screen.getByRole('button'));
		await tick(2000);
		expect(onconfirm).not.toHaveBeenCalled();
	});
	it('Space held for 1.2 s authorises; the stamp shows the result', async () => {
		const onconfirm = vi.fn(async () => ({ label: 'Authorised · 09:21:07', sub: 'by Marcus Bell' }));
		render(HoldButton, { onconfirm });
		const b = screen.getByRole('button');
		await fireEvent.keyDown(b, { key: ' ' });
		await tick(1300);
		expect(onconfirm).toHaveBeenCalledTimes(1);
		expect(await screen.findByText('Authorised · 09:21:07')).toBeTruthy();
	});
	it('releasing Space early cancels', async () => {
		const onconfirm = vi.fn(async () => ({ label: 'ok', sub: '' }));
		render(HoldButton, { onconfirm });
		const b = screen.getByRole('button');
		await fireEvent.keyDown(b, { key: ' ' });
		await tick(700);
		await fireEvent.keyUp(b, { key: ' ' });
		await tick(1000);
		expect(onconfirm).not.toHaveBeenCalled();
	});
	it('a disabled hold (read only) never confirms', async () => {
		const onconfirm = vi.fn(async () => ({ label: 'ok', sub: '' }));
		render(HoldButton, { onconfirm, disabled: true, disabledReason: 'Read only' });
		const b = screen.getByRole('button');
		expect(b.getAttribute('aria-disabled')).toBe('true');
		await fireEvent.keyDown(b, { key: 'Enter' });
		await tick(1500);
		expect(onconfirm).not.toHaveBeenCalled();
	});
	it('a refused approval shows the reason and allows another hold', async () => {
		const onconfirm = vi.fn(async () => {
			throw new Error('You asked for this action, so someone else has to authorise it.');
		});
		render(HoldButton, { onconfirm });
		const b = screen.getByRole('button');
		await fireEvent.keyDown(b, { key: 'Enter' });
		await tick(1300);
		expect(await screen.findByRole('alert')).toBeTruthy();
		expect(screen.getByRole('alert').textContent).toMatch(/someone else/);
	});
});
