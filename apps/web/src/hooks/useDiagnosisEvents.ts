'use client';

import { useEffect, useRef } from 'react';

import {
  subscribeDiagnosisEvents,
  type DiagnosisEvent,
} from '@/lib/api/diagnosis-events';

/** Keep a live SSE subscription for one diagnosis run. */
export function useDiagnosisEvents(
  runId: number,
  onEvent: (event: DiagnosisEvent) => void,
): void {
  const handler = useRef(onEvent);

  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  useEffect(
    () => subscribeDiagnosisEvents(runId, (event) => handler.current(event)),
    [runId],
  );
}
