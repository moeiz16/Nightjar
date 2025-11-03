// Hold to authorise (DESIGN.md §6.5, §11): pointer, Space or Enter held for 1.2 s. Releasing early cancels.
// The 1.2 s applies even with reduced motion: it is the safety, not decoration. Pure controller, unit-tested.

export const HOLD_MS = 1200;

export interface HoldOptions {
	ms?: number;
	onProgress: (p: number) => void;
	onComplete: () => void;
	onCancel?: (p: number) => void;
	now?: () => number;
	raf?: (cb: (t: number) => void) => number;
	caf?: (id: number) => void;
}

export function createHold(o: HoldOptions) {
	const ms = o.ms ?? HOLD_MS;
	const now = o.now ?? (() => performance.now());
	const raf = o.raf ?? ((cb) => requestAnimationFrame(cb));
	const caf = o.caf ?? ((id) => cancelAnimationFrame(id));
	let t0 = 0;
	let frame = 0;
	let holding = false;
	let done = false;
	let progress = 0;

	function step() {
		if (!holding) return;
		progress = Math.min(1, (now() - t0) / ms);
		o.onProgress(progress);
		if (progress >= 1) {
			holding = false;
			done = true;
			o.onComplete();
			return;
		}
		frame = raf(step);
	}
	return {
		start() {
			if (done || holding) return false;
			holding = true;
			t0 = now();
			progress = 0;
			o.onProgress(0);
			frame = raf(step);
			return true;
		},
		/** Release before the end cancels; returns true if it cancelled a hold in progress. */
		release() {
			if (!holding) return false;
			holding = false;
			caf(frame);
			const p = progress;
			progress = 0;
			o.onCancel?.(p);
			o.onProgress(0);
			return true;
		},
		/** Allow another hold (after a refused approval). */
		reset() {
			holding = false;
			done = false;
			progress = 0;
			caf(frame);
			o.onProgress(0);
		},
		get holding() {
			return holding;
		},
		get done() {
			return done;
		},
		get progress() {
			return progress;
		}
	};
}

export type Hold = ReturnType<typeof createHold>;

/** Typed confirmation (accessibility alternative): the typed name must match, ignoring case and spaces. */
export function typedMatches(typed: string, expected: string): boolean {
	const n = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');
	return n(typed).length > 0 && n(typed) === n(expected);
}
