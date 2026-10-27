/**
 * Job status stream (F4.7).
 *
 * Uses `fetch` + `ReadableStream` instead of `EventSource` because the native
 * API cannot set headers: the Bearer token stays in `Authorization` rather than
 * leaking into a URL query string.  Reconnects with a new token after a 401.
 */
import { apiBase, endpoints, getAccessToken, tryRefresh } from './client';
import type { JobStatus } from './types';

export interface JobEvent {
  type: string;
  job_id: number;
  user_id: number;
  status: JobStatus;
  at: string;
}

const RECONNECT_MS = 3000;

/** Subscribe to `job.updated`; returns an unsubscribe function. */
export function subscribeJobEvents(onEvent: (event: JobEvent) => void): () => void {
  const controller = new AbortController();
  let closed = false;
  let retryTimer: number | undefined;
  let refreshedOnce = false;

  const scheduleReconnect = (delay = RECONNECT_MS) => {
    if (closed) return;
    if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(() => void connect(), delay);
  };

  const connect = async () => {
    const token = getAccessToken();
    if (!token) {
      scheduleReconnect();
      return;
    }

    try {
      const response = await fetch(`${apiBase}${endpoints.jobsStream}`, {
        headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
        signal: controller.signal,
      });

      if (response.status === 401 && !refreshedOnce) {
        refreshedOnce = await tryRefresh();
        scheduleReconnect(refreshedOnce ? 0 : RECONNECT_MS);
        return;
      }
      refreshedOnce = false;
      if (!response.ok || !response.body) {
        throw new Error(`job stream failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer = (buffer + decoder.decode(value, { stream: true })).replace(
          /\r\n/g,
          '\n',
        );
        let boundary = buffer.indexOf('\n\n');
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          const data = frame
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trim())
            .join('');
          if (data) {
            try {
              onEvent(JSON.parse(data) as JobEvent);
            } catch {
              // Ignore malformed frames; the stream stays alive.
            }
          }
          boundary = buffer.indexOf('\n\n');
        }
      }
    } catch {
      if (controller.signal.aborted) return;
    }

    scheduleReconnect();
  };

  void connect();

  return () => {
    closed = true;
    controller.abort();
    if (retryTimer !== undefined) window.clearTimeout(retryTimer);
  };
}
