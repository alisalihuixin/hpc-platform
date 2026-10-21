'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, nodesApi } from '@/lib/api/client';
import type { NodeRead, NodeStatus } from '@/lib/api/types';

const STATUS_STYLES: Record<NodeStatus, { label: string; className: string }> = {
  online: { label: '在线', className: 'bg-emerald-50 text-emerald-700' },
  offline: { label: '离线', className: 'bg-slate-100 text-slate-500' },
  draining: { label: '排水', className: 'bg-amber-50 text-amber-700' },
};

export default function NodesPage() {
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
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          刷新
        </button>
      </header>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : nodes.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-400">
          暂无节点，等待管理员注册。
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">主机名</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">GPU 占用</th>
                <th className="px-4 py-3 font-medium">型号</th>
                <th className="px-4 py-3 font-medium">CPU</th>
                <th className="px-4 py-3 font-medium">内存</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {nodes.map((node) => {
                const percent =
                  node.gpu_total > 0 ? Math.round((node.gpu_used / node.gpu_total) * 100) : 0;
                const status = STATUS_STYLES[node.status];
                return (
                  <tr key={node.id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-3 font-medium">{node.hostname}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${status.className}`}
                      >
                        {status.label}
                      </span>
                    </td>
                    <td className="px-4 py-3">
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
                    <td className="px-4 py-3 text-slate-600">{node.gpu_model ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-600">
                      {node.cpu_used}/{node.cpu_cores} 核
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {node.mem_used}/{node.mem_gb} GB
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-400">
            共 {total} 个节点
          </div>
        </div>
      )}
    </div>
  );
}
