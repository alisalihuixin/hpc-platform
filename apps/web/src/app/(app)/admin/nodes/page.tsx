'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { ApiError, nodesApi } from '@/lib/api/client';
import type { NodeCreate, NodeRead, NodeStatus } from '@/lib/api/types';

const STATUS_OPTIONS: { value: NodeStatus; label: string }[] = [
  { value: 'online', label: '在线' },
  { value: 'offline', label: '离线' },
  { value: 'draining', label: '排水' },
];

const STATUS_STYLES: Record<NodeStatus, string> = {
  online: 'bg-emerald-50 text-emerald-700',
  offline: 'bg-slate-100 text-slate-500',
  draining: 'bg-amber-50 text-amber-700',
};

interface Draft {
  hostname: string;
  cpu_cores: string;
  mem_gb: string;
  gpu_model: string;
  gpu_total: string;
}

const EMPTY_DRAFT: Draft = {
  hostname: '',
  cpu_cores: '32',
  mem_gb: '128',
  gpu_model: '',
  gpu_total: '8',
};

export default function AdminNodesPage() {
  const { isAdmin } = useAuth();
  const [nodes, setNodes] = useState<NodeRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await nodesApi.list();
      setNodes(data.items);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError('当前账号没有管理员权限（403）。');
      } else {
        setError(err instanceof ApiError ? err.message : '加载失败');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const editing = nodes.find((node) => node.id === editingId) ?? null;

  function resetForm() {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError(null);
  }

  function startEdit(node: NodeRead) {
    setEditingId(node.id);
    setDraft({
      hostname: node.hostname,
      cpu_cores: String(node.cpu_cores),
      mem_gb: String(node.mem_gb),
      gpu_model: node.gpu_model ?? '',
      gpu_total: String(node.gpu_total),
    });
    setFormError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const cpu = Number(draft.cpu_cores);
    const mem = Number(draft.mem_gb);
    const gpus = Number(draft.gpu_total);
    const problems: string[] = [];
    if (!draft.hostname.trim()) problems.push('主机名不能为空');
    if (!Number.isInteger(cpu) || cpu < 1) problems.push('CPU 核数必须 ≥ 1');
    if (!Number.isInteger(mem) || mem < 1) problems.push('内存必须 ≥ 1 GB');
    if (!Number.isInteger(gpus) || gpus < 0) problems.push('GPU 总数必须 ≥ 0');
    if (editing && gpus < editing.gpu_used) {
      problems.push(`GPU 总数不能低于已用（${editing.gpu_used}）`);
    }
    if (problems.length > 0) {
      setFormError(problems.join('；'));
      return;
    }

    const payload: NodeCreate = {
      hostname: draft.hostname.trim(),
      cpu_cores: cpu,
      mem_gb: mem,
      gpu_model: draft.gpu_model.trim() || null,
      gpu_total: gpus,
    };

    setBusy(true);
    setFormError(null);
    try {
      if (editingId !== null) {
        await nodesApi.update(editingId, payload);
      } else {
        await nodesApi.create(payload);
      }
      resetForm();
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(node: NodeRead, status: NodeStatus) {
    if (status === node.status) return;
    setBusy(true);
    setError(null);
    try {
      await nodesApi.update(node.id, { status });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '状态更新失败');
    } finally {
      setBusy(false);
    }
  }

  async function remove(node: NodeRead) {
    if (!window.confirm(`确认删除节点 ${node.hostname}？`)) return;
    setBusy(true);
    setError(null);
    try {
      await nodesApi.remove(node.id);
      if (editingId === node.id) resetForm();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '删除失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">管理 · 节点</h1>
          <p className="mt-1 text-sm text-slate-500">
            admin 可注册 / 编辑 / 删除节点并切换在线状态；offline 与 draining 不再接收新作业。
          </p>
        </div>
        <Link href="/nodes" className="text-sm text-brand-600 hover:underline">
          查看资源视图 →
        </Link>
      </header>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      <form
        onSubmit={(event) => void submit(event)}
        className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">
            {editing ? `编辑节点 #${editing.id}` : '注册新节点'}
          </h2>
          {editing ? (
            <button
              type="button"
              onClick={resetForm}
              className="text-xs text-slate-500 hover:underline"
            >
              取消编辑
            </button>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <label className="text-sm">
            <span className="text-slate-600">主机名</span>
            <input
              value={draft.hostname}
              onChange={(event) => setDraft({ ...draft, hostname: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">CPU 核数</span>
            <input
              type="number"
              min={1}
              value={draft.cpu_cores}
              onChange={(event) => setDraft({ ...draft, cpu_cores: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">内存（GB）</span>
            <input
              type="number"
              min={1}
              value={draft.mem_gb}
              onChange={(event) => setDraft({ ...draft, mem_gb: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">GPU 型号</span>
            <input
              value={draft.gpu_model}
              onChange={(event) => setDraft({ ...draft, gpu_model: event.target.value })}
              placeholder="A100-80G"
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">GPU 总数</span>
            <input
              type="number"
              min={0}
              value={draft.gpu_total}
              onChange={(event) => setDraft({ ...draft, gpu_total: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            />
          </label>
        </div>

        {formError ? (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{formError}</p>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {busy ? '保存中…' : editing ? '保存修改' : '注册节点'}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : nodes.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-400">
          还没有节点，先在上方注册一台。
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">主机名</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">GPU</th>
                <th className="px-4 py-3 font-medium">CPU</th>
                <th className="px-4 py-3 font-medium">内存</th>
                <th className="px-4 py-3 font-medium">型号</th>
                <th className="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium">{node.hostname}</td>
                  <td className="px-4 py-3">
                    <select
                      value={node.status}
                      disabled={busy}
                      onChange={(event) =>
                        void changeStatus(node, event.target.value as NodeStatus)
                      }
                      className={`rounded-full px-2 py-1 text-xs font-medium ${STATUS_STYLES[node.status]}`}
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {node.gpu_used}/{node.gpu_total}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {node.cpu_used}/{node.cpu_cores}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {node.mem_used}/{node.mem_gb} GB
                  </td>
                  <td className="px-4 py-3 text-slate-600">{node.gpu_model ?? '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => startEdit(node)}
                        className="text-xs text-brand-600 hover:underline"
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void remove(node)}
                        className="text-xs text-red-600 hover:underline disabled:opacity-50"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-400">
            共 {nodes.length} 个节点
          </div>
        </div>
      )}

      {!isAdmin && !error ? (
        <p className="text-xs text-slate-400">提示：非管理员账号调用节点管理接口会收到 403。</p>
      ) : null}
    </div>
  );
}
