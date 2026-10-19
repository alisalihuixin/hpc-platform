'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { useAuth } from '@/components/AuthProvider';

const NAV_ITEMS = [
  { href: '/dashboard', label: '概览', adminOnly: false },
  { href: '/jobs', label: '作业', adminOnly: false },
  { href: '/jobs/new', label: '提交作业', adminOnly: false },
  { href: '/nodes', label: '节点', adminOnly: false },
  { href: '/usage', label: '我的用量', adminOnly: false },
  { href: '/admin/users', label: '用户管理', adminOnly: true },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, status, isAdmin, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (status === 'anonymous') router.replace('/login');
  }, [status, router]);

  if (status !== 'authenticated' || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        正在校验登录状态…
      </div>
    );
  }

  const items = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);

  // Longest matching prefix wins so /jobs/new does not also light up /jobs.
  const activeHref = items
    .filter((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href;

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            H
          </span>
          <span className="font-semibold">HPC Platform</span>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {items.map((item) => {
            const active = item.href === activeHref;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm transition ${
                  active
                    ? 'bg-brand-50 font-medium text-brand-700'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-slate-200 p-4 text-xs text-slate-400">
          M2 · 资源 / 配额 / 作业 / 计量
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-6">
          <div className="text-sm text-slate-500">HPC 作业调度与计费管理平台</div>
          <div className="flex items-center gap-4">
            <div className="text-right text-sm leading-tight">
              <div className="font-medium">{user.full_name ?? user.username}</div>
              <div className="text-xs text-slate-400">
                {user.roles.map((role) => role.name).join(' / ')}
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                void logout().then(() => router.replace('/login'));
              }}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              退出登录
            </button>
          </div>
        </header>

        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
