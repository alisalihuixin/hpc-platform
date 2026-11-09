'use client';

import { useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import { ApiError, reportsApi } from '@/lib/api/client';
import type { DashboardReport } from '@/lib/api/types';
import { formatHours } from '@/lib/format';

const MILESTONES = [
  'M1 认证与 RBAC：JWT 双令牌、越权 403、登录限流',
  'M2 资源 / 配额 / 作业 / 计量：原子预扣、调度与计费全链路',
  'M3 SSE + 管理页：秒级状态刷新、节点/配额/用户/资源组管理',
  'M4 月结报表 + 审计：物化视图、触发器留痕',
];

function TrendChart({ points }: { points: DashboardReport['trend'] }) {
  const max = Math.max(...points.map((point) => Number(point.gpu_hours)), 1);

  return (
    <div className="flex h-44 items-end gap-3">
      {points.map((point) => {
        const value = Number(point.gpu_hours);
        return (
          <div key={point.month} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[10px] text-slate-400">{formatHours(value)}</span>
            <div className="flex w-full flex-1 items-end">
              <div
                className="w-full rounded-t bg-brand-500"
                style={{ height: `${Math.max((value / max) * 100, 2)}%` }}
                title={`${point.month}: ${formatHours(value)} GPU·h`}
              />
            </div>
            <span className="text-[10px] text-slate-400">{point.month.slice(5)} 月</span>
          </div>
        );
      })}
    </div>
  );
}

function TopList({ title, rows }: { title: string; rows: DashboardReport['top_users'] }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-400">{title}</div>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">本月暂无用量</p>
      ) : (
        <ol className="mt-2 space-y-2">
          {rows.map((row, index) => (
            <li key={row.id} className="flex items-center justify-between text-sm">
              <span className="text-slate-700">
                <span className="mr-2 text-slate-400">{index + 1}.</span>
                {row.name ?? `#${row.id}`}
              </span>
              <span className="font-medium">{formatHours(row.gpu_hours)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const { user, isAdmin } = useAuth();
  const [snapshot, setSnapshot] = useState<DashboardReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    setLoading(true);
    setError(null);
    reportsApi
      .dashboard()
      .then(setSnapshot)
      .catch((err) => setError(err instanceof ApiError ? err.message : '看板加载失败'))
      .finally(() => setLoading(false));
  }, [isAdmin]);

  if (!user) return null;

  const totals = snapshot?.totals;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">概览</h1>
        <p className="mt-1 text-sm text-slate-500">
          当前登录：{user.full_name ?? user.username}（{user.email}）
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">用户 ID</div>
          <div className="mt-2 text-2xl font-semibold">{user.id}</div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">角色</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {user.roles.map((role) => (
              <span
                key={role.id}
                className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700"
              >
                {role.name}
              </span>
            ))}
          </div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">状态</div>
          <div className="mt-2 text-2xl font-semibold text-emerald-600">
            {user.is_active ? '正常' : '已禁用'}
          </div>
        </div>
      </section>

      {isAdmin ? (
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">平台看板</h2>
            <span className="text-xs text-slate-400">趋势取近 6 个自然月</span>
          </div>

          {error ? <Alert>{error}</Alert> : null}

          {loading ? (
            <p className="text-sm text-slate-400">加载中…</p>
          ) : snapshot && totals ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <div className="text-xs uppercase tracking-wide text-slate-400">运行中作业</div>
                  <div className="mt-2 text-2xl font-semibold">{totals.running_jobs}</div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <div className="text-xs uppercase tracking-wide text-slate-400">排队作业</div>
                  <div className="mt-2 text-2xl font-semibold">{totals.queued_jobs}</div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <div className="text-xs uppercase tracking-wide text-slate-400">在线节点</div>
                  <div className="mt-2 text-2xl font-semibold">
                    {totals.online_nodes}
                    <span className="ml-1 text-sm text-slate-400">/ {totals.total_nodes}</span>
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <div className="text-xs uppercase tracking-wide text-slate-400">GPU 占用</div>
                  <div className="mt-2 text-2xl font-semibold">
                    {totals.gpu_used}
                    <span className="ml-1 text-sm text-slate-400">/ {totals.gpu_total}</span>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="text-xs uppercase tracking-wide text-slate-400">
                  月度净卡时趋势
                </div>
                <div className="mt-4">
                  <TrendChart points={snapshot.trend} />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <TopList title="本月 TOP 用户" rows={snapshot.top_users} />
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  <TopList title="本月 TOP 资源组" rows={snapshot.top_groups} />
                </div>
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      <section className="rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-slate-700">已交付能力</h2>
        <ul className="mt-3 space-y-2 text-sm text-slate-600">
          {MILESTONES.map((item) => (
            <li key={item} className="flex gap-2">
              <span className="text-emerald-500">✓</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
        {isAdmin ? (
          <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            你拥有 admin 角色，可访问管理页与审计日志。
          </p>
        ) : (
          <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
            user 角色访问管理接口返回 403（RBAC 生效）。
          </p>
        )}
      </section>
    </div>
  );
}
