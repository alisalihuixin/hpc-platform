/**
 * Job status stream (F4.7).
 *
 * The transport lives in `sse.ts` so the diagnosis stream (D54) shares the same
 * auth, reconnect and frame-parsing behaviour.
 */
import { endpoints } from './client';
import { subscribeSse } from './sse';
import type { JobStatus } from './types';

export interface JobEvent {
  type: string;
  job_id: number;
  user_id: number;
  status: JobStatus;
  at: string;
}

/** Subscribe to `job.updated`; returns an unsubscribe function. */
export function subscribeJobEvents(onEvent: (event: JobEvent) => void): () => void {
  return subscribeSse(
    endpoints.jobsStream,
    (data) => onEvent(JSON.parse(data) as JobEvent),
  );
}
