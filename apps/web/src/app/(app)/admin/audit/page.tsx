'use client';

import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import { StatusBadge } from '@/components/ui/StatusBadge';
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
import { ApiError, auditApi } from '@/lib/api/client';
import type { AuditLogRead } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const TABLE_OPTIONS = ['users', 'quotas', 'nodes', 'jobs'];
const PAGE_SIZE = 50;

interface Draft {
  tableName: string;
  action: string;
  changedBy: string;
  since: string;
  until: string;
}

const EMPTY_DRAFT: Draft = { tableName: '', action: '', changedBy: '', since: '', until: '' };

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** Show up to three changed columns; ``delete`` has no "after" payload. */
function diffSummary(row: AuditLogRead): string {
  if (row.action === 'delete') return `删除 ${row.table_name} #${row.record_id}`;
  const before = row.before ?? {};
  const after = row.after ?? {};
  const changed = Object.keys(after).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
  );
  if (changed.length === 0) return '—';
  return changed
    .slice(0, 3)
    .map((key) => `${key}: ${String(before[key] ?? '—')} → ${String(after[key] ?? '—')}`)
    .join('；');
}

export default function AdminAuditPage() {
  const { isAdmin } = useAuth();
  const [rows, setRows] = useState<AuditLogRead[]>([]);
  const [total, setTotal] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [filters, setFilters] = useState<Draft>(EMPTY_DRAFT);
  const [skip, setSkip] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await auditApi.list({
        table_name: filters.tableName || undefined,
        action: (filters.action || undefined) as 'update' | 'delete' | undefined,
        changed_by: filters.changedBy || undefined,
        since: toIso(filters.since),
        until: toIso(filters.until),
        skip,
        limit: PAGE_SIZE,
      });
      setRows(data.items);
      setTotal(data.total);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError('当前账号没有管理员权限（403）。');
      } else {
        setError(err instanceof ApiError ? err.message : '加载失败');
      }
    } finally {
      setLoading(false);
    }
  }, [filters, skip]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasPrev = skip > 0;
  const hasNext = skip + PAGE_SIZE < total;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">审计日志</h1>
          <p className="mt-1 text-sm text-slate-500">
            由数据库触发器写入：改字段即留痕，密码等敏感列不会出现在变更内容里。
          </p>
        </div>
        <button type="button" onClick={() => void load()} className={buttonToolbar}>
          刷新
        </button>
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          setSkip(0);
          setFilters(draft);
        }}
        className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4"
      >
        <label className="text-sm">
          <span className="text-slate-600">表</span>
          <select
            value={draft.tableName}
            onChange={(event) => setDraft({ ...draft, tableName: event.target.value })}
            className={inputInlineClass}
          >
            <option value="">全部</option>
            {TABLE_OPTIONS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="text-slate-600">动作</span>
          <select
            value={draft.action}
            onChange={(event) => setDraft({ ...draft, action: event.target.value })}
            className={inputInlineClass}
          >
            <option value="">全部</option>
            <option value="update">update</option>
            <option value="delete">delete</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="text-slate-600">操作人</span>
          <input
            type="text"
            value={draft.changedBy}
            onChange={(event) => setDraft({ ...draft, changedBy: event.target.value })}
            placeholder="user id / system"
            className={inputInlineClass}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">起</span>
          <input
            type="datetime-local"
            value={draft.since}
            onChange={(event) => setDraft({ ...draft, since: event.target.value })}
            className={inputInlineClass}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">止</span>
          <input
            type="datetime-local"
            value={draft.until}
            onChange={(event) => setDraft({ ...draft, until: event.target.value })}
            className={inputInlineClass}
          />
        </label>
        <button type="submit" className={buttonPrimary}>
          筛选
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(EMPTY_DRAFT);
            setFilters(EMPTY_DRAFT);
            setSkip(0);
          }}
          className={buttonSecondary}
        >
          重置
        </button>
      </form>

      {error ? <Alert>{error}</Alert> : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : rows.length === 0 ? (
        <div className={emptyState}>没有符合条件的审计记录。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>时间</th>
                <th className={th}>表 / 记录</th>
                <th className={th}>动作</th>
                <th className={th}>操作人</th>
                <th className={th}>变更</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <tr key={row.id} className="hover:bg-slate-50/60 align-top">
                  <td className="px-4 py-3 text-slate-500">{formatDateTime(row.changed_at)}</td>
                  <td className="px-4 py-3">
                    <div className="font-medium">{row.table_name}</div>
                    <div className="text-xs text-slate-400">#{row.record_id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge tone={row.action === 'delete' ? 'danger' : 'brand'}>
                      {row.action}
                    </StatusBadge>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{row.changed_by ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{diffSummary(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={`${tableFooter} flex items-center justify-between`}>
            <span>
              共 {total} 条 · 第 {Math.floor(skip / PAGE_SIZE) + 1} 页
            </span>
            <span className="flex gap-2">
              <button
                type="button"
                disabled={!hasPrev}
                onClick={() => setSkip((value) => Math.max(value - PAGE_SIZE, 0))}
                className="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-40"
              >
                上一页
              </button>
              <button
                type="button"
                disabled={!hasNext}
                onClick={() => setSkip((value) => value + PAGE_SIZE)}
                className="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-40"
              >
                下一页
              </button>
            </span>
          </div>
        </div>
      )}

      {!isAdmin && !error ? (
        <p className="text-xs text-slate-400">提示：非管理员访问审计接口会收到 403。</p>
      ) : null}
    </div>
  );
}
