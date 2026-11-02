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
  inputClass,
  tableFooter,
  tableHead,
  tableShell,
  th,
} from '@/components/ui/styles';
import { ApiError, groupsApi, usersApi } from '@/lib/api/client';
import type { GroupRead, UserRead } from '@/lib/api/types';

interface CreateDraft {
  name: string;
  description: string;
  gpu_limit: string;
  max_nodes_per_job: string;
  max_gpus_per_job: string;
  member_ids: number[];
}

interface EditDraft {
  name: string;
  description: string;
  is_active: boolean;
  member_ids: number[];
}

const EMPTY_CREATE: CreateDraft = {
  name: '',
  description: '',
  gpu_limit: '0',
  max_nodes_per_job: '1',
  max_gpus_per_job: '1',
  member_ids: [],
};

function toggleId(ids: number[], id: number): number[] {
  return ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id];
}

function MemberPicker({
  users,
  selected,
  onToggle,
}: {
  users: UserRead[];
  selected: number[];
  onToggle: (id: number) => void;
}) {
  return (
    <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
      {users.map((user) => (
        <label key={user.id} className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={selected.includes(user.id)}
            onChange={() => onToggle(user.id)}
          />
          <span className="font-medium">{user.username}</span>
          <span className="text-xs text-slate-400">{user.email}</span>
        </label>
      ))}
    </div>
  );
}

export default function AdminGroupsPage() {
  const { isAdmin } = useAuth();
  const [groups, setGroups] = useState<GroupRead[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<CreateDraft>(EMPTY_CREATE);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({
    name: '',
    description: '',
    is_active: true,
    member_ids: [],
  });
  const [editError, setEditError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [groupData, userData] = await Promise.all([groupsApi.list(), usersApi.list(0, 200)]);
      setGroups(groupData.items);
      setUsers(userData.items);
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

  const editing = groups.find((group) => group.id === editingId) ?? null;

  function closePanels() {
    setCreateOpen(false);
    setCreateDraft(EMPTY_CREATE);
    setCreateError(null);
    setEditingId(null);
    setEditError(null);
  }

  function startEdit(group: GroupRead) {
    setCreateOpen(false);
    setCreateError(null);
    setNotice(null);
    setEditingId(group.id);
    setEditDraft({
      name: group.name,
      description: group.description ?? '',
      is_active: group.is_active,
      member_ids: group.members.map((member) => member.id),
    });
    setEditError(null);
  }

  async function submitCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const gpuLimit = Number(createDraft.gpu_limit);
    const maxNodes = Number(createDraft.max_nodes_per_job);
    const maxGpus = Number(createDraft.max_gpus_per_job);
    const problems: string[] = [];
    if (createDraft.name.trim().length === 0) problems.push('组名不能为空');
    if (!Number.isInteger(gpuLimit) || gpuLimit < 0) problems.push('GPU 上限必须 ≥ 0');
    if (!Number.isInteger(maxNodes) || maxNodes < 0) problems.push('单作业节点上限必须 ≥ 0');
    if (!Number.isInteger(maxGpus) || maxGpus < 0) problems.push('单作业 GPU 上限必须 ≥ 0');
    if (problems.length > 0) {
      setCreateError(problems.join('；'));
      return;
    }

    setBusy(true);
    setCreateError(null);
    try {
      const created = await groupsApi.create({
        name: createDraft.name.trim(),
        description: createDraft.description.trim() || null,
        gpu_limit: gpuLimit,
        max_nodes_per_job: maxNodes,
        max_gpus_per_job: maxGpus,
        member_ids: createDraft.member_ids,
      });
      closePanels();
      setNotice(`已创建资源组 ${created.name}（配额 ${created.quota?.gpu_limit ?? 0} 卡）`);
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

    if (editDraft.name.trim().length === 0) {
      setEditError('组名不能为空');
      return;
    }

    setBusy(true);
    setEditError(null);
    try {
      await groupsApi.update(editing.id, {
        name: editDraft.name.trim(),
        description: editDraft.description.trim() || null,
        is_active: editDraft.is_active,
      });
      await groupsApi.setMembers(editing.id, editDraft.member_ids);
      closePanels();
      setNotice(`已更新资源组 ${editDraft.name.trim()}`);
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(group: GroupRead) {
    if (!window.confirm(`停用资源组「${group.name}」？成员将无法再用该组配额提交作业。`)) return;

    setBusy(true);
    setNotice(null);
    try {
      await groupsApi.remove(group.id);
      setNotice(`已停用资源组 ${group.name}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '停用失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">管理 · 资源组</h1>
          <p className="mt-1 text-sm text-slate-500">
            每个资源组对应一条配额；停用为软删除（作业与流水历史保留），仍有 GPU 占用时不允许停用。
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
            {createOpen ? '收起表单' : '新建资源组'}
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
          <h2 className="text-sm font-semibold text-slate-700">新建资源组</h2>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              <span className="text-slate-600">组名</span>
              <input
                type="text"
                required
                value={createDraft.name}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, name: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">描述（可选）</span>
              <input
                type="text"
                value={createDraft.description}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, description: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">GPU 上限（整组）</span>
              <input
                type="number"
                min={0}
                value={createDraft.gpu_limit}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, gpu_limit: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">单作业节点上限</span>
              <input
                type="number"
                min={0}
                value={createDraft.max_nodes_per_job}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, max_nodes_per_job: event.target.value })
                }
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">单作业 GPU 上限</span>
              <input
                type="number"
                min={0}
                value={createDraft.max_gpus_per_job}
                onChange={(event) =>
                  setCreateDraft({ ...createDraft, max_gpus_per_job: event.target.value })
                }
                className={inputClass}
              />
            </label>
          </div>

          <div className="text-sm">
            <div className="mb-1 text-slate-600">成员</div>
            <MemberPicker
              users={users}
              selected={createDraft.member_ids}
              onToggle={(id) =>
                setCreateDraft({
                  ...createDraft,
                  member_ids: toggleId(createDraft.member_ids, id),
                })
              }
            />
          </div>

          {createError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{createError}</p>
          ) : null}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className={buttonPrimary}
            >
              {busy ? '创建中…' : '创建'}
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
              编辑资源组 · {editing.name}（group #{editing.id}）
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
              <span className="text-slate-600">组名</span>
              <input
                type="text"
                required
                value={editDraft.name}
                onChange={(event) => setEditDraft({ ...editDraft, name: event.target.value })}
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              <span className="text-slate-600">描述</span>
              <input
                type="text"
                value={editDraft.description}
                onChange={(event) =>
                  setEditDraft({ ...editDraft, description: event.target.value })
                }
                className={inputClass}
              />
            </label>
          </div>

          <div className="text-sm">
            <div className="mb-1 text-slate-600">
              成员（当前 {editing.members.length} 人，保存时整体覆盖）
            </div>
            <MemberPicker
              users={users}
              selected={editDraft.member_ids}
              onToggle={(id) =>
                setEditDraft({ ...editDraft, member_ids: toggleId(editDraft.member_ids, id) })
              }
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={editDraft.is_active}
              onChange={(event) =>
                setEditDraft({ ...editDraft, is_active: event.target.checked })
              }
            />
            启用（取消勾选即停用该组）
          </label>

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
      ) : groups.length === 0 ? (
        <div className={emptyState}>还没有资源组。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>资源组</th>
                <th className={th}>GPU 配额</th>
                <th className={th}>单作业上限</th>
                <th className={th}>成员</th>
                <th className={th}>状态</th>
                <th className={th}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {groups.map((group) => (
                <tr key={group.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3">
                    <div className="font-medium">{group.name}</div>
                    <div className="text-xs text-slate-400">
                      {group.description ?? `group #${group.id}`}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {group.quota
                      ? `${group.quota.gpu_used}/${group.quota.gpu_limit}（余 ${group.quota.gpu_available}）`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {group.quota
                      ? `${group.quota.max_nodes_per_job} 节点 / ${group.quota.max_gpus_per_job} 卡`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {group.members.length === 0
                      ? '—'
                      : group.members
                          .slice(0, 3)
                          .map((member) => member.username)
                          .join('、') + (group.members.length > 3 ? ` 等 ${group.members.length} 人` : '')}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge tone={group.is_active ? 'success' : 'neutral'}>
                      {group.is_active ? '启用' : '已停用'}
                    </StatusBadge>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => startEdit(group)}
                      className="text-xs text-brand-600 hover:underline"
                    >
                      编辑
                    </button>
                    {group.is_active ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void deactivate(group)}
                        className="ml-3 text-xs text-red-500 hover:underline disabled:opacity-50"
                      >
                        停用
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={tableFooter}>共 {groups.length} 个资源组</div>
        </div>
      )}

      {!isAdmin && !error ? (
        <p className="text-xs text-slate-400">提示：非管理员账号调用资源组接口会收到 403。</p>
      ) : null}
    </div>
  );
}
