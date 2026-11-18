/**
 * Diagnosis progress stream (D54).
 *
 * Frames arrive in the order the graph executed them; each `diagnosis.step`
 * mirrors one persisted `diagnosis_steps` row so the timeline can be rendered
 * without a second request.  The stream ends after `run.finished`.
 */
import { subscribeSse } from './sse';

export type DiagnosisRunStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'degraded'
  | 'failed';

/** Same vocabulary as the backend's `StepPhase`. */
export type DiagnosisStepNode =
  | 'plan'
  | 'tool'
  | 'observe'
  | 'reflect'
  | 'report'
  | 'fallback';

export interface DiagnosisStepEvent {
  id: number;
  seq: number;
  node: DiagnosisStepNode;
  tool_name: string | null;
  args: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  latency_ms: number;
  created_at: string;
}

interface BaseEvent {
  run_id: number;
  at: string;
}

export type DiagnosisEvent =
  | (BaseEvent & { type: 'diagnosis.step'; step: DiagnosisStepEvent })
  | (BaseEvent & { type: 'run.status'; status: DiagnosisRunStatus })
  | (BaseEvent & {
      type: 'run.finished';
      status: DiagnosisRunStatus;
      root_cause: string | null;
      confidence: number | null;
      iterations: number;
      tokens: number;
      duration_ms: number | null;
      error: string | null;
    });

/** Path is dynamic, so it cannot be checked against the generated paths table. */
export function diagnosisStreamPath(runId: number): string {
  return `/api/v1/diagnosis/${runId}/stream`;
}

/** Subscribe to one run's progress; returns an unsubscribe function. */
export function subscribeDiagnosisEvents(
  runId: number,
  onEvent: (event: DiagnosisEvent) => void,
): () => void {
  return subscribeSse(diagnosisStreamPath(runId), (data) =>
    onEvent(JSON.parse(data) as DiagnosisEvent),
  );
}
