'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { DiagnosisStatusBadge } from '@/components/DiagnosisStatusBadge';
import { Alert } from '@/components/ui/Alert';
import { emptyState, tableFooter, tableHead, tableShell, th } from '@/components/ui/styles';
import { ApiError, diagnosisApi } from '@/lib/api/client';
import type { DiagnosisListItem, RunStatus } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const STATUS_OPTIONS: { value: '' | RunStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待诊断' },
  { value: 'running', label: '诊断中' },
  { value: 'succeeded', label: '已完成' },
  { value: 'degraded', label: '降级' },
  { value: 'failed', label: '失败' },
];

const PAGE_SIZE = 50;

function formatConfidence(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function formatDuration(value: number | null): string {
  if (value === null) return '—';
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

export default function DiagnosisPage() {
  const [status, setStatus] = useState<'' | RunStatus>('');
  const [items, setItems] = useState<DiagnosisListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await diagnosisApi.list({
        status: status || undefined,
        limit: PAGE_SIZE,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  // Runs are executed by their own SSE stream; the list only needs a cheap poll
  // while something is still in flight (no global diagnosis channel).
  const hasActive = items.some((item) => item.status === 'pending' || item.status === 'running');
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">故障诊断</h1>
          <p className="mt-1 text-sm text-slate-500">
            每次诊断都记录完整证据链；打开详情即可看到 plan / 工具 / 自检 / 报告的时间线。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as '' | RunStatus)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value || 'all'} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            刷新
          </button>
        </div>
      </header>

      {error ? <Alert>{error}</Alert> : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : items.length === 0 ? (
        <div className={emptyState}>暂无诊断记录。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>ID</th>
                <th className={th}>告警</th>
                <th className={th}>状态</th>
                <th className={th}>根因</th>
                <th className={th}>置信度</th>
                <th className={th}>耗时</th>
                <th className={th}>创建时间</th>
                <th className={th}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-slate-400">#{item.id}</td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-800">{item.alert_summary}</div>
                    <div className="text-xs text-slate-400">
                      告警 #{item.alert_id} · {item.alert_severity}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <DiagnosisStatusBadge status={item.status} />
                  </td>
                  <td className="max-w-xs px-4 py-3 text-slate-600">
                    {item.root_cause ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{formatConfidence(item.confidence)}</td>
                  <td className="px-4 py-3 text-slate-600">{formatDuration(item.duration_ms)}</td>
                  <td className="px-4 py-3 text-slate-500">{formatDateTime(item.created_at)}</td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/diagnosis/${item.id}`}
                      className="text-xs text-brand-600 hover:underline"
                    >
                      查看
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={tableFooter}>共 {total} 次诊断</div>
        </div>
      )}
    </div>
  );
}
