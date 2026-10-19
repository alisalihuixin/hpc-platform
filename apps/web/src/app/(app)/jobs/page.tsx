'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { JobStatusBadge } from '@/components/JobStatusBadge';
import { ApiError, jobsApi } from '@/lib/api/client';
import type { JobRead, JobStatus } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const STATUS_OPTIONS: { value: '' | JobStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'canceled', label: '已取消' },
];

const PAGE_SIZE = 100;

export default function JobsPage() {
  const [status, setStatus] = useState<'' | JobStatus>('');
  const [jobs, setJobs] = useState<JobRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await jobsApi.list({
        status: status || undefined,
        limit: PAGE_SIZE,
      });
      setJobs(data.items);
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

  async function cancel(job: JobRead) {
    if (!window.confirm(`确认取消作业 #${job.id}「${job.name}」？`)) return;
    setBusyId(job.id);
    setError(null);
    try {
      await jobsApi.cancel(job.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '取消失败');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">作业</h1>
          <p className="mt-1 text-sm text-slate-500">
            普通用户只看自己的作业，admin 可查看全部；取消仅限排队中 / 运行中。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as '' | JobStatus)}
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
          <Link
            href="/jobs/new"
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
          >
            提交作业
          </Link>
        </div>
      </header>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : jobs.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-400">
          暂无作业，先去「提交作业」创建一个。
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">资源</th>
                <th className="px-4 py-3 font-medium">walltime</th>
                <th className="px-4 py-3 font-medium">排队时间</th>
                <th className="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-slate-400">#{job.id}</td>
                  <td className="px-4 py-3">
                    <Link href={`/jobs/${job.id}`} className="font-medium hover:text-brand-700">
                      {job.name}
                    </Link>
                    <div className="text-xs text-slate-400">group #{job.group_id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <JobStatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {job.num_nodes} 节点 / {job.num_gpus} 卡
                  </td>
                  <td className="px-4 py-3 text-slate-600">{job.walltime_min} 分钟</td>
                  <td className="px-4 py-3 text-slate-500">{formatDateTime(job.queued_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <Link href={`/jobs/${job.id}`} className="text-xs text-brand-600 hover:underline">
                        详情
                      </Link>
                      {job.status === 'queued' || job.status === 'running' ? (
                        <button
                          type="button"
                          disabled={busyId === job.id}
                          onClick={() => void cancel(job)}
                          className="text-xs text-red-600 hover:underline disabled:opacity-50"
                        >
                          {busyId === job.id ? '取消中…' : '取消'}
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-400">
            共 {total} 个作业{total > PAGE_SIZE ? `（当前显示最近 ${PAGE_SIZE} 个）` : ''}
          </div>
        </div>
      )}
    </div>
  );
}
