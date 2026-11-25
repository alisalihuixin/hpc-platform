import type { RunStatus } from '@/lib/api/types';

import { DIAGNOSIS_STATUS_BADGE, StatusBadge } from './ui/StatusBadge';

export function DiagnosisStatusBadge({ status }: { status: RunStatus }) {
  const { tone, label } = DIAGNOSIS_STATUS_BADGE[status];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}
