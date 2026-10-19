import type { JobStatus } from '@/lib/api/types';

const STYLES: Record<JobStatus, { label: string; className: string }> = {
  queued: { label: '排队中', className: 'bg-amber-50 text-amber-700' },
  running: { label: '运行中', className: 'bg-brand-50 text-brand-700' },
  completed: { label: '已完成', className: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '失败', className: 'bg-red-50 text-red-600' },
  canceled: { label: '已取消', className: 'bg-slate-100 text-slate-500' },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const style = STYLES[status];
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}>
      {style.label}
    </span>
  );
}
