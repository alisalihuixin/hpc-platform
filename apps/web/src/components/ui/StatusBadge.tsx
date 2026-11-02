import type { JobStatus, NodeStatus } from '@/lib/api/types';

/** Single source of truth for every pill in the app (D33). */
export type BadgeTone = 'brand' | 'success' | 'warning' | 'danger' | 'neutral';

const TONES: Record<BadgeTone, string> = {
  brand: 'bg-brand-50 text-brand-700',
  success: 'bg-emerald-50 text-emerald-700',
  warning: 'bg-amber-50 text-amber-700',
  danger: 'bg-red-50 text-red-600',
  neutral: 'bg-slate-100 text-slate-500',
};

export function badgeToneClass(tone: BadgeTone): string {
  return TONES[tone];
}

export function StatusBadge({
  tone,
  children,
}: {
  tone: BadgeTone;
  children: React.ReactNode;
}) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export const JOB_STATUS_BADGE: Record<JobStatus, { tone: BadgeTone; label: string }> = {
  queued: { tone: 'warning', label: '排队中' },
  running: { tone: 'brand', label: '运行中' },
  completed: { tone: 'success', label: '已完成' },
  failed: { tone: 'danger', label: '失败' },
  canceled: { tone: 'neutral', label: '已取消' },
};

export const NODE_STATUS_BADGE: Record<NodeStatus, { tone: BadgeTone; label: string }> = {
  online: { tone: 'success', label: '在线' },
  offline: { tone: 'neutral', label: '离线' },
  draining: { tone: 'warning', label: '排水' },
};
