// Live updates over SSE (contract §1.17). EventSource reconnects on its own (server sends retry: 3000) and
// resumes with Last-Event-ID. In mock mode the mock server emits the same typed events.
import { STREAM_EVENT_TYPES, type StreamEvent } from './types';
import { API_BASE } from './client';

export type StreamHandler = (e: StreamEvent) => void;
export interface StreamSource {
	subscribe(fn: StreamHandler): () => void;
}

/** Parse one SSE frame payload into a typed event; unknown or malformed frames are dropped. */
export function parseFrame(type: string, data: string): StreamEvent | null {
	if (!(STREAM_EVENT_TYPES as string[]).includes(type)) return null;
	try {
		return { type, data: JSON.parse(data) } as StreamEvent;
	} catch {
		return null;
	}
}

export function eventSourceStream(url = `${API_BASE}/events/stream`, ES: typeof EventSource = EventSource): StreamSource {
	return {
		subscribe(fn) {
			const es = new ES(url, { withCredentials: true });
			const listeners: [string, (m: MessageEvent) => void][] = STREAM_EVENT_TYPES.map((t) => [
				t,
				(m: MessageEvent) => {
					const e = parseFrame(t, String(m.data));
					if (e) fn(e);
				}
			]);
			for (const [t, l] of listeners) es.addEventListener(t, l as EventListener);
			return () => {
				for (const [t, l] of listeners) es.removeEventListener(t, l as EventListener);
				es.close();
			};
		}
	};
}
