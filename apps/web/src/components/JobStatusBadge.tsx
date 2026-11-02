import type { JobStatus } from '@/lib/api/types';

import { JOB_STATUS_BADGE, StatusBadge } from './ui/StatusBadge';

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { tone, label } = JOB_STATUS_BADGE[status];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}
