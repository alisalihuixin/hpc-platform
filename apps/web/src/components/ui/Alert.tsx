/** Inline message strip used by every list/form page (D33). */
type AlertTone = 'error' | 'success' | 'info' | 'warning';

const TONES: Record<AlertTone, string> = {
  error: 'bg-red-50 text-red-600',
  success: 'bg-emerald-50 text-emerald-700',
  info: 'bg-slate-50 text-slate-600',
  warning: 'bg-amber-50 text-amber-700',
};

export function Alert({
  tone = 'error',
  children,
}: {
  tone?: AlertTone;
  children: React.ReactNode;
}) {
  return (
    <p className={`rounded-lg px-4 py-3 text-sm ${TONES[tone]}`} role="status">
      {children}
    </p>
  );
}
