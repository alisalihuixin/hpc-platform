'use client';

import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import { StatusBadge } from '@/components/ui/StatusBadge';
import {
  buttonPrimary,
  buttonSecondary,
  buttonToolbar,
  inputClass,
  tableFooter,
  tableHead,
  tableShell,
  th,
} from '@/components/ui/styles';
import { ApiError, usersApi } from '@/lib/api/client';
import type { UserRead } from '@/lib/api/types';

// 角色种子来自迁移（admin / user）；后续新增角色时在这里追加即可。
const ROLE_OPTIONS = ['user', 'admin'] as const;

interface CreateDraft {
  email: string;
  username: string;
  full_name: string;
  password: string;
  roles: string[];
}

interface EditDraft {
  full_name: string;
  roles: string[];
  is_active: boolean;
  password: string;
}

const EMPTY_CREATE: CreateDraft = {
  email: '',
  username: '',
  full_name: '',
  password: '',
  roles: ['user'],
};

function toggleRole(roles: string[], role: string): string[] {
  return roles.includes(role) ? roles.filter((item) => item !== role) : [...roles, role];
}

export default function AdminUsersPage() {
  const { isAdmin, user: currentUser } = useAuth();
  const [users, setUsers] = useState<UserRead[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<CreateDraft>(EMPTY_CREATE);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({
    full_name: '',
    roles: [],
    is_active: true,
    password: '',
  });
  const [editError, setEditError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await usersApi.list(0, 200);
      setUsers(data.items);
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
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const editing = users.find((item) => item.id === editingId) ?? null;

  function closePanels() {
    setCreateOpen(false);
    setCreateDraft(EMPTY_CREATE);
    setCreateError(null);
    setEditingId(null);
    setEditError(null);
  }

  function startEdit(target: UserRead) {
    setCreateOpen(false);
    setCreateError(null);
    setNotice(null);
    setEditingId(target.id);
    setEditDraft({
      full_name: target.full_name ?? '',
      roles: target.roles.map((role) => role.name),
      is_active: target.is_active,
      password: '',
    });
    setEditError(null);
  }

  async function submitCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const problems: string[] = [];
    if (!createDraft.email.includes('@')) problems.push('邮箱格式不正确');
    if (createDraft.username.trim().length < 3) problems.push('用户名至少 3 个字符');
    if (createDraft.password.length < 8) problems.push('初始密码至少 8 个字符');
    if (createDraft.roles.length === 0) problems.push('至少分配一个角色');
    if (problems.length > 0) {
      setCreateError(problems.join('；'));
      return;
    }

    setBusy(true);
    setCreateError(null);
    try {
      const created = await usersApi.create({
        email: createDraft.email.trim(),
        username: createDraft.username.trim(),
        full_name: createDraft.full_name.trim() || null,
        password: createDraft.password,
        roles: createDraft.roles,
      });
      setCreateDraft(EMPTY_CREATE);
      setCreateOpen(false);
      setNotice(`已创建用户 ${created.username}（${created.email}）`);
      await load();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : '创建失败');
    } finally {
      setBusy(false);
    }
  }

  async function submitEdit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;

    const problems: string[] = [];
    if (editDraft.roles.length === 0) problems.push('至少分配一个角色');
    if (editDraft.password.length > 0 && editDraft.password.length < 8) {
      problems.push('新密码至少 8 个字符');
    }
    if (problems.length > 0) {
      setEditError(problems.join('；'));
      return;
    }

    setBusy(true);
    setEditError(null);
    try {
      await usersApi.update(editing.id, {
        full_name: editDraft.full_name.trim() || null,
        roles: editDraft.roles,
        is_active: editDraft.is_active,
        ...(editDraft.password ? { password: editDraft.password } : {}),
      });
      const changedPassword = editDraft.password.length > 0;
      closePanels();
      setNotice(
        `已更新用户 ${editing.username}${changedPassword ? '（密码已重置）' : ''}`,
      );
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">用户管理</h1>
          <p className="mt-1 text-sm text-slate-500">
            admin 可创建用户、分配角色、重置密码；密码重置后旧密码立即失效。
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void load()} className={buttonToolbar}>
            刷新
          </button>
          <button
            type="button"
            onClick={() => {
              closePanels();
              setCreateOpen((open) => !open);
            }}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
          >
            {createOpen ? '收起表单' : '新建用户'}
          </button>
        </div>
      </header>

      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      {createOpen ? (
        <form
          onSubmit={(event) => void submitCreate(event)}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5"
        >
          <h2 className="text-sm font-semibold text-slate-700">新建用户</h2>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              <span className="text-slate-600">邮箱</span>
              <input
                type="email"
                required
                value={createDraft.email}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, email: event.target.value })
                }
                placeholder="alice@hpc.local"
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">用户名</span>
              <input
                type="text"
                required
                minLength={3}
                value={createDraft.username}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, username: event.target.value })
                }
                placeholder="alice"
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">姓名（可选）</span>
              <input
                type="text"
                value={createDraft.full_name}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, full_name: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">初始密码（≥ 8 位）</span>
              <input
                type="password"
                required
                minLength={8}
                value={createDraft.password}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, password: event.target.value })
                }
                className={inputClass}
              />
            </label>
          </div>

          <fieldset className="text-sm">
            <legend className="text-slate-600">角色</legend>
            <div className="mt-2 flex gap-4">
              {ROLE_OPTIONS.map((role) => (
                <label key={role} className="flex items-center gap-2 text-slate-700">
                  <input
                    type="checkbox"
                    checked={createDraft.roles.includes(role)}
                    onChange={() =>
                      setCreateDraft({
                        ...createDraft,
                        roles: toggleRole(createDraft.roles, role),
                      })
                    }
                  />
                  {role}
                </label>
              ))}
            </div>
          </fieldset>

          {createError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{createError}</p>
          ) : null}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className={buttonPrimary}
            >
              {busy ? '创建中…' : '创建用户'}
            </button>
            <button
              type="button"
              onClick={closePanels}
              className={buttonSecondary}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}

      {editing ? (
        <form
          onSubmit={(event) => void submitEdit(event)}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">
              编辑用户 · {editing.username}（{editing.email}）
            </h2>
            <button
              type="button"
              onClick={closePanels}
              className="text-xs text-slate-500 hover:underline"
            >
              取消
            </button>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              <span className="text-slate-600">姓名</span>
              <input
                type="text"
                value={editDraft.full_name}
                onChange={(event) =>
                  setEditDraft({ ...editDraft, full_name: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">重置密码（留空则不修改）</span>
              <input
                type="password"
                minLength={8}
                value={editDraft.password}
                onChange={(event) =>
                  setEditDraft({ ...editDraft, password: event.target.value })
                }
                placeholder="至少 8 位"
                className={inputClass}
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-sm">
            <fieldset>
              <legend className="text-slate-600">角色</legend>
              <div className="mt-2 flex gap-4">
                {ROLE_OPTIONS.map((role) => (
                  <label key={role} className="flex items-center gap-2 text-slate-700">
                    <input
                      type="checkbox"
                      checked={editDraft.roles.includes(role)}
                      onChange={() =>
                        setEditDraft({
                          ...editDraft,
                          roles: toggleRole(editDraft.roles, role),
                        })
                      }
                    />
                    {role}
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="flex items-center gap-2 text-slate-700">
              <input
                type="checkbox"
                checked={editDraft.is_active}
                onChange={(event) =>
                  setEditDraft({ ...editDraft, is_active: event.target.checked })
                }
              />
              账号启用
            </label>
          </div>

          {editError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{editError}</p>
          ) : null}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className={buttonPrimary}
            >
              {busy ? '保存中…' : '保存修改'}
            </button>
            <button
              type="button"
              onClick={closePanels}
              className={buttonSecondary}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>ID</th>
                <th className={th}>用户名</th>
                <th className={th}>邮箱</th>
                <th className={th}>角色</th>
                <th className={th}>状态</th>
                <th className={th}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-slate-400">{user.id}</td>
                  <td className="px-4 py-3">
                    <div className="font-medium">{user.username}</div>
                    {user.full_name ? (
                      <div className="text-xs text-slate-400">{user.full_name}</div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{user.email}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {user.roles.map((role) => (
                        <span
                          key={role.id}
                          className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                        >
                          {role.name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge tone={user.is_active ? 'success' : 'danger'}>
                      {user.is_active ? '启用' : '禁用'}
                    </StatusBadge>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => startEdit(user)}
                      className="text-xs text-brand-600 hover:underline"
                    >
                      编辑
                    </button>
                    {currentUser?.id === user.id ? (
                      <span className="ml-2 text-xs text-slate-400">（当前账号）</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={tableFooter}>共 {total} 个用户</div>
        </div>
      )}

      {!isAdmin && !error ? (
        <p className="text-xs text-slate-400">提示：非管理员账号访问该接口会收到 403。</p>
      ) : null}
    </div>
  );
}
