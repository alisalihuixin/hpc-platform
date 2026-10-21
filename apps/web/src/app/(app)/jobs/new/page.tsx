'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { ApiError, jobsApi, quotasApi } from '@/lib/api/client';
import type { JobCreate, QuotaRead } from '@/lib/api/types';

export default function NewJobPage() {
  const router = useRouter();
  const [quotas, setQuotas] = useState<QuotaRead[]>([]);
  const [groupId, setGroupId] = useState<number | null>(null);
  const [name, setName] = useState('train-job');
  const [numNodes, setNumNodes] = useState('1');
  const [numGpus, setNumGpus] = useState('1');
  const [numCpus, setNumCpus] = useState('8');
  const [memGb, setMemGb] = useState('32');
  const [priority, setPriority] = useState('0');
  const [walltime, setWalltime] = useState('60');
  const [paramsText, setParamsText] = useState('{}');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await quotasApi.mine();
        if (cancelled) return;
        setQuotas(data.items);
        setGroupId(data.items[0]?.group_id ?? null);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : '加载资源组失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const quota = useMemo(
    () => quotas.find((item) => item.group_id === groupId) ?? null,
    [quotas, groupId],
  );

  const nodes = Number(numNodes);
  const gpus = Number(numGpus);
  const cpus = Number(numCpus);
  const memory = Number(memGb);
  const jobPriority = Number(priority);
  const walltimeMin = Number(walltime);

  const validation: string[] = [];
  if (!name.trim()) validation.push('作业名称不能为空');
  if (!quota) validation.push('请选择资源组');
  if (!Number.isInteger(nodes) || nodes < 1) validation.push('节点数必须是 ≥ 1 的整数');
  if (!Number.isInteger(gpus) || gpus < 0) validation.push('GPU 数必须是 ≥ 0 的整数');
  if (!Number.isInteger(cpus) || cpus < 0) validation.push('CPU 核数必须是 ≥ 0 的整数');
  if (!Number.isInteger(memory) || memory < 0) validation.push('内存必须是 ≥ 0 的整数（GB）');
  if (!Number.isInteger(jobPriority) || jobPriority < 0 || jobPriority > 1000) {
    validation.push('优先级必须在 0–1000 之间');
  }
  if (!Number.isInteger(walltimeMin) || walltimeMin < 1) validation.push('walltime 必须 ≥ 1 分钟');
  if (quota && nodes > quota.max_nodes_per_job) {
    validation.push(`节点数超过本组单作业上限（${quota.max_nodes_per_job}）`);
  }
  if (quota && gpus > quota.max_gpus_per_job) {
    validation.push(`GPU 数超过本组单作业上限（${quota.max_gpus_per_job}）`);
  }
  if (quota && gpus > quota.gpu_available) {
    validation.push(`GPU 数超过本组当前余量（${quota.gpu_available}）`);
  }

  let params: Record<string, unknown> = {};
  let paramsError: string | null = null;
  try {
    const parsed: unknown = JSON.parse(paramsText || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      params = parsed as Record<string, unknown>;
    } else {
      paramsError = 'params 必须是 JSON 对象';
    }
  } catch {
    paramsError = 'params 不是合法 JSON';
  }
  if (paramsError) validation.push(paramsError);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!quota || validation.length > 0) return;

    const payload: JobCreate = {
      group_id: quota.group_id,
      name: name.trim(),
      num_nodes: nodes,
      num_gpus: gpus,
      num_cpus: cpus,
      mem_gb: memory,
      priority: jobPriority,
      walltime_min: walltimeMin,
      params,
    };

    setSubmitting(true);
    setError(null);
    try {
      const job = await jobsApi.create(payload);
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '提交失败');
      setSubmitting(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-400">加载中…</p>;

  if (quotas.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">提交作业</h1>
        <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-700">
          你还没有加入任何资源组，无法提交作业。请联系管理员将你加入某个组并分配配额。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">提交作业</h1>
          <p className="mt-1 text-sm text-slate-500">
            提交即预扣配额，随后由调度器按 FIFO 分配节点。
          </p>
        </div>
        <Link href="/jobs" className="text-sm text-brand-600 hover:underline">
          返回作业列表
        </Link>
      </header>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <form
          onSubmit={(event) => void submit(event)}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6"
        >
          <label className="block text-sm">
            <span className="text-slate-600">资源组</span>
            <select
              value={groupId ?? ''}
              onChange={(event) => setGroupId(Number(event.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            >
              {quotas.map((item) => (
                <option key={item.group_id} value={item.group_id}>
                  {item.group_name ?? `group #${item.group_id}`}
                </option>
              ))}
            </select>
          </label>

          <label className="block text-sm">
            <span className="text-slate-600">作业名称</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={255}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block text-sm">
              <span className="text-slate-600">节点数</span>
              <input
                type="number"
                min={1}
                value={numNodes}
                onChange={(event) => setNumNodes(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">GPU 数</span>
              <input
                type="number"
                min={0}
                value={numGpus}
                onChange={(event) => setNumGpus(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">walltime（分钟）</span>
              <input
                type="number"
                min={1}
                value={walltime}
                onChange={(event) => setWalltime(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block text-sm">
              <span className="text-slate-600">CPU 核数</span>
              <input
                type="number"
                min={0}
                value={numCpus}
                onChange={(event) => setNumCpus(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">内存（GB）</span>
              <input
                type="number"
                min={0}
                value={memGb}
                onChange={(event) => setMemGb(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">优先级（越高越先）</span>
              <input
                type="number"
                min={0}
                max={1000}
                value={priority}
                onChange={(event) => setPriority(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              />
            </label>
          </div>

          <label className="block text-sm">
            <span className="text-slate-600">params（JSON 对象）</span>
            <textarea
              value={paramsText}
              onChange={(event) => setParamsText(event.target.value)}
              rows={5}
              spellCheck={false}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-xs"
            />
          </label>

          {validation.length > 0 ? (
            <ul className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
              {validation.map((message) => (
                <li key={message}>· {message}</li>
              ))}
            </ul>
          ) : null}

          <button
            type="submit"
            disabled={submitting || validation.length > 0}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {submitting ? '提交中…' : '提交作业'}
          </button>
        </form>

        <aside className="space-y-4">
          <section className="rounded-2xl border border-slate-200 bg-white p-5">
            <h2 className="text-sm font-semibold text-slate-700">本组配额</h2>
            {quota ? (
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-500">可用 GPU</dt>
                  <dd className="font-medium">
                    {quota.gpu_available} / {quota.gpu_limit}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">单作业节点上限</dt>
                  <dd className="font-medium">{quota.max_nodes_per_job}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">单作业 GPU 上限</dt>
                  <dd className="font-medium">{quota.max_gpus_per_job}</dd>
                </div>
              </dl>
            ) : (
              <p className="mt-2 text-sm text-slate-400">未选择资源组</p>
            )}
          </section>

          <p className="rounded-xl bg-slate-100 px-4 py-3 text-xs text-slate-500">
            提交时服务端会再次校验并原子预扣；配额不足返回 409，超单作业上限返回 422。
          </p>
        </aside>
      </div>
    </div>
  );
}
