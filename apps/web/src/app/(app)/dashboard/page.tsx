'use client';

import { useAuth } from '@/components/AuthProvider';

const MILESTONES = [
  'JWT access + refresh（登出后旧 token 立即失效）',
  'RBAC：admin / user 两角色，越权返回 403',
  '登录限流：滑动窗口（Redis），超出返回 429',
  'OpenAPI 自动生成前端类型客户端',
];

export default function DashboardPage() {
  const { user, isAdmin } = useAuth();

  if (!user) return null;

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

      <section className="rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-slate-700">M1 已交付能力</h2>
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
            你拥有 admin 角色，可访问「用户管理」页面。
          </p>
        ) : (
          <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
            user 角色访问「用户管理」将返回 403（RBAC 生效）。
          </p>
        )}
      </section>
    </div>
  );
}
