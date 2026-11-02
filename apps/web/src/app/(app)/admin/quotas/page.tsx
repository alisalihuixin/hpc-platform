'use client';

import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import {
  buttonPrimary,
  emptyState,
  inputClass,
  tableFooter,
  tableHead,
  tableShell,
  th,
} from '@/components/ui/styles';
import { ApiError, quotasApi } from '@/lib/api/client';
import type { QuotaRead } from '@/lib/api/types';

interface Draft {
  gpu_limit: string;
  max_nodes_per_job: string;
  max_gpus_per_job: string;
}

const EMPTY_DRAFT: Draft = {
  gpu_limit: '0',
  max_nodes_per_job: '1',
  max_gpus_per_job: '1',
};

export default function AdminQuotasPage() {
  const { isAdmin } = useAuth();
  const [quotas, setQuotas] = useState<QuotaRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await quotasApi.list();
      setQuotas(data.items);
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

  const editing = quotas.find((quota) => quota.group_id === editingId) ?? null;

  function startEdit(quota: QuotaRead) {
    setEditingId(quota.group_id);
    setDraft({
      gpu_limit: String(quota.gpu_limit),
      max_nodes_per_job: String(quota.max_nodes_per_job),
      max_gpus_per_job: String(quota.max_gpus_per_job),
    });
    setFormError(null);
  }

  function resetForm() {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;

    const gpuLimit = Number(draft.gpu_limit);
    const maxNodes = Number(draft.max_nodes_per_job);
    const maxGpus = Number(draft.max_gpus_per_job);
    const problems: string[] = [];
    if (!Number.isInteger(gpuLimit) || gpuLimit < 0) problems.push('GPU 上限必须 ≥ 0');
    if (!Number.isInteger(maxNodes) || maxNodes < 0) problems.push('单作业节点上限必须 ≥ 0');
    if (!Number.isInteger(maxGpus) || maxGpus < 0) problems.push('单作业 GPU 上限必须 ≥ 0');
    if (gpuLimit < editing.gpu_used) {
      problems.push(`GPU 上限不能低于已用（${editing.gpu_used}）`);
    }
    if (problems.length > 0) {
      setFormError(problems.join('；'));
      return;
    }

    setBusy(true);
    setFormError(null);
    try {
      await quotasApi.update(editing.group_id, {
        gpu_limit: gpuLimit,
        max_nodes_per_job: maxNodes,
        max_gpus_per_job: maxGpus,
      });
      resetForm();
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">管理 · 配额</h1>
        <p className="mt-1 text-sm text-slate-500">
          调低上限只影响后续提交，不会终止正在运行的作业；GPU 上限不得低于已用量。
        </p>
      </header>

      {error ? <Alert>{error}</Alert> : null}

      {editing ? (
        <form
          onSubmit={(event) => void submit(event)}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">
              编辑配额 · {editing.group_name ?? `group #${editing.group_id}`}
            </h2>
            <button
              type="button"
              onClick={resetForm}
              className="text-xs text-slate-500 hover:underline"
            >
              取消
            </button>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <label className="text-sm">
              <span className="text-slate-600">GPU 上限（已用 {editing.gpu_used}）</span>
              <input
                type="number"
                min={editing.gpu_used}
                value={draft.gpu_limit}
                onChange={(event) => setDraft({ ...draft, gpu_limit: event.target.value })}
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">单作业节点上限</span>
              <input
                type="number"
                min={0}
                value={draft.max_nodes_per_job}
                onChange={(event) =>
                  setDraft({ ...draft, max_nodes_per_job: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">单作业 GPU 上限</span>
              <input
                type="number"
                min={0}
                value={draft.max_gpus_per_job}
                onChange={(event) =>
                  setDraft({ ...draft, max_gpus_per_job: event.target.value })
                }
                className={inputClass}
              />
            </label>
          </div>

          {formError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{formError}</p>
          ) : null}

          <button type="submit" disabled={busy} className={buttonPrimary}>
            {busy ? '保存中…' : '保存修改'}
          </button>
        </form>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : quotas.length === 0 ? (
        <div className={emptyState}>还没有资源组配额。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>资源组</th>
                <th className={th}>GPU 余量</th>
                <th className={th}>单作业节点上限</th>
                <th className={th}>单作业 GPU 上限</th>
                <th className={th}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {quotas.map((quota) => {
                const percent =
                  quota.gpu_limit > 0
                    ? Math.round((quota.gpu_used / quota.gpu_limit) * 100)
                    : 0;
                return (
                  <tr key={quota.group_id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-3">
                      <div className="font-medium">
                        {quota.group_name ?? `group #${quota.group_id}`}
                      </div>
                      <div className="text-xs text-slate-400">group #{quota.group_id}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-28 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-2 rounded-full bg-brand-500"
                            style={{ width: `${Math.min(percent, 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-slate-500">
                          {quota.gpu_used}/{quota.gpu_limit}（余 {quota.gpu_available}）
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{quota.max_nodes_per_job}</td>
                    <td className="px-4 py-3 text-slate-600">{quota.max_gpus_per_job}</td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => startEdit(quota)}
                        className="text-xs text-brand-600 hover:underline"
                      >
                        编辑
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className={tableFooter}>共 {quotas.length} 个资源组</div>
        </div>
      )}

      {!isAdmin && !error ? (
        <p className="text-xs text-slate-400">提示：非管理员账号调用配额管理接口会收到 403。</p>
      ) : null}
    </div>
  );
}
