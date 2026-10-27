'use client';

import { useEffect, useRef } from 'react';

import { subscribeJobEvents, type JobEvent } from '@/lib/api/job-events';

/** Keep a live SSE subscription for the component's lifetime. */
export function useJobEvents(onEvent: (event: JobEvent) => void): void {
  const handler = useRef(onEvent);

  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  useEffect(() => subscribeJobEvents((event) => handler.current(event)), []);
}
