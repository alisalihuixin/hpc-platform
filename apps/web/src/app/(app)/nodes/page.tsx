'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { Alert } from '@/components/ui/Alert';
import { NODE_STATUS_BADGE, StatusBadge } from '@/components/ui/StatusBadge';
import {
  buttonToolbar,
  emptyState,
  tableFooter,
  tableHead,
  tableShell,
  td,
  th,
} from '@/components/ui/styles';
import { ApiError, nodesApi } from '@/lib/api/client';
import type { NodeRead } from '@/lib/api/types';

export default function NodesPage() {
  const { isAdmin } = useAuth();
  const [nodes, setNodes] = useState<NodeRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await nodesApi.list();
      setNodes(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">节点</h1>
          <p className="mt-1 text-sm text-slate-500">
            只读资源视图（F2.3）：GPU 占用实时反映调度器分配结果。
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isAdmin ? (
            <Link href="/admin/nodes" className="text-sm text-brand-600 hover:underline">
              节点管理 →
            </Link>
          ) : null}
          <button type="button" onClick={() => void load()} className={buttonToolbar}>
            刷新
          </button>
        </div>
      </header>

      {error ? <Alert>{error}</Alert> : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : nodes.length === 0 ? (
        <div className={emptyState}>暂无节点，等待管理员注册。</div>
      ) : (
        <div className={tableShell}>
          <table className="w-full text-left text-sm">
            <thead className={tableHead}>
              <tr>
                <th className={th}>主机名</th>
                <th className={th}>状态</th>
                <th className={th}>GPU 占用</th>
                <th className={th}>型号</th>
                <th className={th}>CPU</th>
                <th className={th}>内存</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {nodes.map((node) => {
                const percent =
                  node.gpu_total > 0 ? Math.round((node.gpu_used / node.gpu_total) * 100) : 0;
                const badge = NODE_STATUS_BADGE[node.status];
                return (
                  <tr key={node.id} className="hover:bg-slate-50/60">
                    <td className={`${td} font-medium`}>{node.hostname}</td>
                    <td className={td}>
                      <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
                    </td>
                    <td className={td}>
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-28 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-2 rounded-full bg-brand-500"
                            style={{ width: `${percent}%` }}
                          />
                        </div>
                        <span className="text-xs text-slate-500">
                          {node.gpu_used}/{node.gpu_total}
                        </span>
                      </div>
                    </td>
                    <td className={`${td} text-slate-600`}>{node.gpu_model ?? '—'}</td>
                    <td className={`${td} text-slate-600`}>
                      {node.cpu_used}/{node.cpu_cores} 核
                    </td>
                    <td className={`${td} text-slate-600`}>
                      {node.mem_used}/{node.mem_gb} GB
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className={tableFooter}>共 {total} 个节点</div>
        </div>
      )}
    </div>
  );
}
