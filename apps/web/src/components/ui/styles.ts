/**
 * Shared Tailwind class strings (D33).
 *
 * Keeping the repeated table / button / input recipes in one module means a
 * spacing or border change is applied everywhere at once.
 */

// ---- cards & tables ------------------------------------------------------
export const tableShell = 'overflow-hidden rounded-2xl border border-slate-200 bg-white';
export const tableHead = 'bg-slate-50 text-xs uppercase tracking-wide text-slate-500';
export const th = 'px-4 py-3 font-medium';
export const td = 'px-4 py-3';
export const tableFooter = 'border-t border-slate-100 px-4 py-3 text-xs text-slate-400';
export const emptyState =
  'rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-400';
export const card = 'rounded-2xl border border-slate-200 bg-white';
export const panel = `${card} p-5`;

// ---- buttons -------------------------------------------------------------
export const buttonPrimary =
  'rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50';
export const buttonSecondary =
  'rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50';
export const buttonToolbar =
  'rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50';

// ---- forms ---------------------------------------------------------------
export const inputClass = 'mt-1 w-full rounded-lg border border-slate-200 px-3 py-2';
export const inputInlineClass = 'mt-1 block rounded-lg border border-slate-200 px-3 py-1.5';
