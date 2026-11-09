'use client';

import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import {
  buttonPrimary,
  buttonSecondary,
  buttonToolbar,
  emptyState,
  inputInlineClass,
  tableFooter,
  tableHead,
  tableShell,
  th,
} from '@/components/ui/styles';
import { ApiError, groupsApi, reportsApi } from '@/lib/api/client';
import type { GroupRead, MonthlyBillingRow } from '@/lib/api/types';
import { formatHours } from '@/lib/format';

interface Draft {
  since: string;
  until: string;
  groupId: string;
}

const EMPTY_DRAFT: Draft = { since: '', until: '', groupId: '' };

/** ``2026-01`` -> ``2026-01-01T00:00:00Z`` */
function monthStart(value: string): string | undefined {
  return value ? `${value}-01T00:00:00Z` : undefined;
}

/** ``2026-02`` -> last day of February 23:59:59Z */
function monthEnd(value: string): string | undefined {
  if (!value) return undefined;
  const [year, month] = value.split('-').map(Number);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${value}-${String(lastDay).padStart(2, '0')}T23:59:59Z`;
}

export default function ReportsPage() {
  const { isAdmin } = useAuth();
  const [items, setItems] = useState<MonthlyBillingRow[]>([]);
  const [totalHours, setTotalHours] = useState('0');
  const [groups, setGroups] = useState<GroupRead[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [filters, setFilters] = useState<Draft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await reportsApi.monthly({
        since: monthStart(filters.since),
        until: monthEnd(filters.until),
        group_id: filters.groupId ? Number(filters.groupId) : undefined,
      });
      setItems(data.items);
      setTotalHours(String(data.total_gpu_hours));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!isAdmin) return;
    void groupsApi
      .list()
      .then((data) => setGroups(data.items))
      .catch(() => setGroups([]));
  }, [isAdmin]);

  async function refreshView() {
    setRefreshing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await reportsApi.refresh();
      setNotice(result.refreshed ? '物化视图已刷新。' : `未刷新：${result.detail}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '刷新失败');
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">用量报表 · 月结</h1>
          <p className="mt-1 text-sm text-slate-500">
            按月 × 用户 × 资源组汇总净卡时（冲正已折抵）；普通成员只看自己的数据。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isAdmin ? (
            <button
              type="button"
              disabled={refreshing}
              onClick={() => void refreshView()}
              className={buttonSecondary}
            >
              {refreshing ? '刷新中…' : '刷新物化视图'}
            </button>
          ) : null}
          <button type="button" onClick={() => void load()} className={buttonToolbar}>
            重新加载
          </button>
        </div>
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          setFilters(draft);
        }}
        className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4"
      >
        <label className="text-sm">
          <span className="text-slate-600">起始月</span>
          <input
            type="month"
            value={draft.since}
            onChange={(event) => setDraft({ ...draft, since: event.target.value })}
            className={inputInlineClass}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">结束月</span>
          <input
            type="month"
            value={draft.until}
            onChange={(event) => setDraft({ ...draft, until: event.target.value })}
            className={inputInlineClass}
          />
        </label>
        {isAdmin ? (
          <label className="text-sm">
            <span className="text-slate-600">资源组</span>
            <select
              value={draft.groupId}
              onChange={(event) => setDraft({ ...draft, groupId: event.target.value })}
              className={inputInlineClass}
            >
              <option value="">全部</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                  {group.is_active ? '' : '（已停用）'}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <button type="submit" className={buttonPrimary}>
          查询
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(EMPTY_DRAFT);
            setFilters(EMPTY_DRAFT);
          }}
          className={buttonSecondary}
        >
          重置
        </button>
      </form>

      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      <section className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">总净卡时</div>
          <div className="mt-2 text-2xl font-semibold">{formatHours(totalHours)}</div>
          <div className="mt-1 text-xs text-slate-400">GPU·小时</div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">月结条目</div>
          <div className="mt-2 text-2xl font-semibold">{items.length}</div>
          <div className="mt-1 text-xs text-slate-400">月 × 用户 × 组</div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">数据来源</div>
          <div className="mt-2 text-sm font-medium text-slate-700">
            {isAdmin ? '物化视图（无则实时聚合）' : '与个人用量同一口径'}
          </div>
          <div className="mt-1 text-xs text-slate-400">冲正行按负数计入</div>
        </div>
      </section>

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : items.length === 0 ? (
        <div className={emptyState}>所选区间还没有月结数据。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>月份</th>
                <th className={th}>用户</th>
                <th className={th}>资源组</th>
                <th className={th}>净卡时</th>
                <th className={th}>作业数</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((row) => (
                <tr
                  key={`${row.month}-${row.user_id}-${row.group_id}`}
                  className="hover:bg-slate-50/60"
                >
                  <td className="px-4 py-3 font-medium">{row.month}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {row.username ?? `user #${row.user_id}`}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {row.group_name ?? `group #${row.group_id}`}
                  </td>
                  <td className="px-4 py-3 font-medium">{formatHours(row.gpu_hours)}</td>
                  <td className="px-4 py-3 text-slate-600">{row.job_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={tableFooter}>共 {items.length} 条月结记录</div>
        </div>
      )}

      {!isAdmin ? (
        <p className="text-xs text-slate-400">
          提示：手动刷新物化视图仅 admin 可用；非管理员调用会收到 403。
        </p>
      ) : null}
    </div>
  );
}
