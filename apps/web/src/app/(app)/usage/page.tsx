'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { ApiError, usageApi } from '@/lib/api/client';
import type { UsageEventRead } from '@/lib/api/types';
import { formatDateTime, formatHours } from '@/lib/format';

interface TimeRange {
  since: string;
  until: string;
}

export default function UsagePage() {
  const [draft, setDraft] = useState<TimeRange>({ since: '', until: '' });
  const [range, setRange] = useState<TimeRange>({ since: '', until: '' });
  const [items, setItems] = useState<UsageEventRead[]>([]);
  const [total, setTotal] = useState(0);
  const [totalHours, setTotalHours] = useState('0');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await usageApi.mine({
        since: range.since || undefined,
        until: range.until || undefined,
      });
      setItems(data.items);
      setTotal(data.total);
      setTotalHours(data.total_gpu_hours);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">我的用量</h1>
        <p className="mt-1 text-sm text-slate-500">
          按作业开始时间筛选；冲正行以负数计入，明细合计与汇总值一致。
        </p>
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          setRange(draft);
        }}
        className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4"
      >
        <label className="text-sm">
          <span className="text-slate-600">开始时间</span>
          <input
            type="datetime-local"
            value={draft.since}
            onChange={(event) => setDraft({ ...draft, since: event.target.value })}
            className="mt-1 block rounded-lg border border-slate-200 px-3 py-1.5"
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">结束时间</span>
          <input
            type="datetime-local"
            value={draft.until}
            onChange={(event) => setDraft({ ...draft, until: event.target.value })}
            className="mt-1 block rounded-lg border border-slate-200 px-3 py-1.5"
          />
        </label>
        <button
          type="submit"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          查询
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft({ since: '', until: '' });
            setRange({ since: '', until: '' });
          }}
          className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          重置
        </button>
      </form>

      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}

      <section className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">总卡时</div>
          <div className="mt-2 text-2xl font-semibold">{formatHours(totalHours)}</div>
          <div className="mt-1 text-xs text-slate-400">GPU·小时（净额）</div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">明细条数</div>
          <div className="mt-2 text-2xl font-semibold">{total}</div>
          <div className="mt-1 text-xs text-slate-400">含冲正补偿行</div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400">对账口径</div>
          <div className="mt-2 text-sm font-medium text-slate-700">
            明细 signed_gpu_hours 求和 = 总卡时
          </div>
          <Link href="/jobs" className="mt-2 inline-block text-xs text-brand-600 hover:underline">
            查看作业 →
          </Link>
        </div>
      </section>

      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-400">
          该时间段内还没有用量记录。
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">开始时间</th>
                <th className="px-4 py-3 font-medium">作业</th>
                <th className="px-4 py-3 font-medium">GPU</th>
                <th className="px-4 py-3 font-medium">时长</th>
                <th className="px-4 py-3 font-medium">卡时</th>
                <th className="px-4 py-3 font-medium">单价 / 卡时</th>
                <th className="px-4 py-3 font-medium">备注</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => {
                const rate =
                  typeof item.rate_snapshot.gpu_hour === 'string'
                    ? item.rate_snapshot.gpu_hour
                    : '—';
                return (
                  <tr key={item.id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-3 text-slate-600">{formatDateTime(item.started_at)}</td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/jobs/${item.job_id}`}
                        className="text-brand-600 hover:underline"
                      >
                        #{item.job_id}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{item.gpus}</td>
                    <td className="px-4 py-3 text-slate-600">{item.duration_min} 分钟</td>
                    <td
                      className={`px-4 py-3 font-medium ${
                        item.reversed ? 'text-red-600' : 'text-slate-800'
                      }`}
                    >
                      {formatHours(item.signed_gpu_hours)}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{rate}</td>
                    <td className="px-4 py-3">
                      {item.reversed ? (
                        <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
                          冲正
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">正常</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
