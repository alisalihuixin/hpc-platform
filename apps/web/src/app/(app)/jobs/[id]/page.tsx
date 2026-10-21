'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { JobStatusBadge } from '@/components/JobStatusBadge';
import { ApiError, jobsApi } from '@/lib/api/client';
import type { JobDetail } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-800">{value}</div>
    </div>
  );
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = Number(params.id);

  const [job, setJob] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!Number.isFinite(jobId)) return;
    try {
      const data = await jobsApi.get(jobId);
      setJob(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  // No SSE yet (D26): poll while the job can still change state.
  const active = job?.status === 'queued' || job?.status === 'running';
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [active, load]);

  async function cancel() {
    if (!job || !window.confirm(`确认取消作业 #${job.id}「${job.name}」？`)) return;
    setBusy(true);
    setError(null);
    try {
      await jobsApi.cancel(job.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '取消失败');
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-400">加载中…</p>;
  if (!job) {
    return (
      <div className="space-y-4">
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">
          {error ?? '作业不存在'}
        </p>
        <Link href="/jobs" className="text-sm text-brand-600 hover:underline">
          返回作业列表
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">{job.name}</h1>
            <JobStatusBadge status={job.status} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            作业 #{job.id} · 资源组 #{job.group_id} · 用户 #{job.user_id}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {active ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void cancel()}
              className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              {busy ? '取消中…' : '取消作业'}
            </button>
          ) : null}
          <Link href="/jobs" className="text-sm text-brand-600 hover:underline">
            返回作业列表
          </Link>
        </div>
      </header>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetaItem label="节点数" value={`${job.num_nodes} 节点`} />
        <MetaItem label="GPU 数" value={`${job.num_gpus} 卡`} />
        <MetaItem label="CPU / 内存" value={`${job.num_cpus} 核 / ${job.mem_gb} GB`} />
        <MetaItem label="优先级" value={job.priority} />
        <MetaItem label="walltime" value={`${job.walltime_min} 分钟`} />
        <MetaItem label="消耗卡时" value={`${job.gpu_minutes} GPU·分钟`} />
        <MetaItem label="分配节点" value={job.node_id ? `node #${job.node_id}` : '未分配'} />
        <MetaItem label="排队时间" value={formatDateTime(job.queued_at)} />
        <MetaItem label="开始时间" value={formatDateTime(job.started_at)} />
        <MetaItem label="结束时间" value={formatDateTime(job.ended_at)} />
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-slate-700">运行参数</h2>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-900 p-4 text-xs text-slate-100">
          {JSON.stringify(job.params, null, 2)}
        </pre>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">日志（mock）</h2>
          {active ? <span className="text-xs text-slate-400">每 3 秒自动刷新</span> : null}
        </div>
        <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-slate-900 p-4 text-xs leading-relaxed text-slate-100">
          {job.log}
        </pre>
      </section>
    </div>
  );
}
