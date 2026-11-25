'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { DiagnosisStatusBadge } from '@/components/DiagnosisStatusBadge';
import { Alert } from '@/components/ui/Alert';
import { panel, tableShell } from '@/components/ui/styles';
import { useDiagnosisEvents } from '@/hooks/useDiagnosisEvents';
import { ApiError, diagnosisApi } from '@/lib/api/client';
import type { DiagnosisRead, DiagnosisStepRead, StepPhase } from '@/lib/api/types';
import { formatDateTime } from '@/lib/format';

const NODE_LABEL: Record<StepPhase, string> = {
  plan: '规划',
  tool: '工具调用',
  observe: '证据',
  reflect: '自检',
  report: '报告',
  fallback: '降级报告',
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function pickString(record: Record<string, unknown> | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === 'string' && value ? value : null;
}

function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function StepCard({ step }: { step: DiagnosisStepRead }) {
  const result = asRecord(step.result);
  const evidenceId = pickString(result, 'evidence_id');
  const sufficient = result?.sufficient;

  return (
    <li className="relative pl-6">
      <span className="absolute left-0 top-2 h-2.5 w-2.5 rounded-full bg-brand-500 ring-4 ring-brand-50" />
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <span className="font-mono">#{step.seq}</span>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600">
            {NODE_LABEL[step.node]}
          </span>
          {step.tool_name ? (
            <span className="font-mono text-slate-600">{step.tool_name}</span>
          ) : null}
          {step.latency_ms > 0 ? <span>{step.latency_ms}ms</span> : null}
          <span className="ml-auto">{formatDateTime(step.created_at)}</span>
        </div>

        {step.node === 'plan' ? (
          <p className="mt-2 text-sm text-slate-600">
            {result?.done ? '模型判断证据已足够' : '模型请求新的证据'}
            {pickString(result, 'reason') ? `：${pickString(result, 'reason')}` : ''}
          </p>
        ) : null}

        {step.node === 'observe' && evidenceId ? (
          <p className="mt-2 text-sm text-slate-600">
            证据 <span className="font-mono">{evidenceId}</span> ·{' '}
            {result?.ok ? '请求成功' : '请求失败'}
          </p>
        ) : null}

        {step.node === 'reflect' && typeof sufficient === 'boolean' ? (
          <p className="mt-2 text-sm text-slate-600">
            自检结论：{sufficient ? '证据充分' : '证据不足'}
            {pickString(result, 'reason') ? ` —— ${pickString(result, 'reason')}` : ''}
          </p>
        ) : null}

        {(step.node === 'report' || step.node === 'fallback') && pickString(result, 'root_cause') ? (
          <p className="mt-2 text-sm font-medium text-slate-800">
            {pickString(result, 'root_cause')}
          </p>
        ) : null}

        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer select-none hover:text-slate-700">入参与返回</summary>
          <div className="mt-1 space-y-1">
            <div className="text-slate-400">args</div>
            <JsonBlock value={step.args} />
            <div className="text-slate-400">result</div>
            <JsonBlock value={step.result} />
          </div>
        </details>
      </div>
    </li>
  );
}

export default function DiagnosisDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = Number(params.id);

  const [run, setRun] = useState<DiagnosisRead | null>(null);
  const [steps, setSteps] = useState<DiagnosisStepRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!Number.isFinite(runId)) return;
    try {
      const data = await diagnosisApi.get(runId);
      setRun(data);
      setSteps(data.steps ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Each frame mirrors a persisted step, so the timeline can grow in place.
  useDiagnosisEvents(runId, (event) => {
    if (event.type === 'diagnosis.step') {
      setSteps((prev) =>
        prev.some((step) => step.id === event.step.id)
          ? prev
          : [...prev, event.step].sort((a, b) => a.seq - b.seq),
      );
      return;
    }
    if (event.type === 'run.status') {
      setRun((prev) => (prev ? { ...prev, status: event.status } : prev));
      return;
    }
    setRun((prev) =>
      prev
        ? {
            ...prev,
            status: event.status,
            root_cause: event.root_cause,
            confidence: event.confidence,
            iterations: event.iterations,
            tokens: event.tokens,
            duration_ms: event.duration_ms,
            error: event.error,
          }
        : prev,
    );
  });

  // Polling fallback in case the SSE stream is unavailable.
  const active = run?.status === 'pending' || run?.status === 'running';
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const reportStep = useMemo(
    () => [...steps].reverse().find((step) => step.node === 'report' || step.node === 'fallback'),
    [steps],
  );
  const report = asRecord(reportStep?.result);

  if (loading) return <p className="text-sm text-slate-400">加载中…</p>;
  if (!run) {
    return (
      <div className="space-y-4">
        <Alert>{error ?? '诊断记录不存在'}</Alert>
        <Link href="/diagnosis" className="text-sm text-brand-600 hover:underline">
          返回诊断列表
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">诊断 #{run.id}</h1>
            <DiagnosisStatusBadge status={run.status} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            告警 #{run.alert_id} · 创建于 {formatDateTime(run.created_at)}
            {run.trace_id ? ` · trace ${run.trace_id}` : ''}
          </p>
        </div>
        <Link href="/diagnosis" className="text-sm text-brand-600 hover:underline">
          返回诊断列表
        </Link>
      </header>

      {error ? <Alert>{error}</Alert> : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-slate-400">置信度</div>
          <div className="mt-1 text-sm font-medium text-slate-800">
            {run.confidence === null ? '—' : `${Math.round(run.confidence * 100)}%`}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-slate-400">工具调用</div>
          <div className="mt-1 text-sm font-medium text-slate-800">{run.iterations} 次</div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-slate-400">Token</div>
          <div className="mt-1 text-sm font-medium text-slate-800">{run.tokens}</div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-slate-400">耗时</div>
          <div className="mt-1 text-sm font-medium text-slate-800">
            {run.duration_ms === null ? '—' : `${(run.duration_ms / 1000).toFixed(1)}s`}
          </div>
        </div>
      </section>

      <section className={panel}>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">诊断报告</h2>
          {active ? <span className="text-xs text-slate-400">诊断进行中，实时更新</span> : null}
        </div>

        {run.root_cause ? (
          <div className="mt-3 space-y-3">
            <div>
              <div className="text-xs uppercase tracking-wide text-slate-400">根因</div>
              <p className="mt-1 text-base font-medium text-slate-900">{run.root_cause}</p>
            </div>
            {pickString(report, 'fix_proposal') ? (
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-400">建议处置</div>
                <p className="mt-1 text-sm text-slate-700">{pickString(report, 'fix_proposal')}</p>
              </div>
            ) : null}
            {pickString(report, 'reasoning') ? (
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-400">推理过程</div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                  {pickString(report, 'reasoning')}
                </p>
              </div>
            ) : null}
            {Array.isArray(report?.evidence_refs) && report.evidence_refs.length > 0 ? (
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-400">引用证据</div>
                <div className="mt-1 flex flex-wrap gap-2">
                  {(report.evidence_refs as unknown[]).map((ref) => (
                    <span
                      key={String(ref)}
                      className="rounded-full bg-brand-50 px-2 py-0.5 font-mono text-xs text-brand-700"
                    >
                      {String(ref)}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            {run.status === 'degraded' ? (
              <p className="text-xs text-amber-600">
                本结论由规则降级生成（步数 / 预算护栏触发），置信度为 0。
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-400">
            {active ? '等待 agent 生成结论…' : '本次诊断没有产出报告。'}
          </p>
        )}

        {run.error ? <div className="mt-3"><Alert>{run.error}</Alert></div> : null}
      </section>

      <section>
        <h2 className="text-sm font-semibold text-slate-700">证据链时间线（{steps.length} 步）</h2>
        {steps.length === 0 ? (
          <div className={`${tableShell} mt-3 p-8 text-center text-sm text-slate-400`}>
            {active ? '诊断进行中，等待第一步…' : '没有记录到任何步骤。'}
          </div>
        ) : (
          <ol className="mt-3 space-y-3 border-l border-slate-200 pl-4">
            {steps.map((step) => (
              <StepCard key={step.id} step={step} />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
