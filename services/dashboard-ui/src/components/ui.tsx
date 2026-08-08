import type { Status } from '../lib/store'

export const STATUS_COLOR: Record<Status, string> = {
  OK: 'text-ok',
  THROTTLED: 'text-throttled',
  ERROR: 'text-error',
  OFFLINE: 'text-offline',
}

export const STATUS_BORDER: Record<Status, string> = {
  OK: 'border-ok/60',
  THROTTLED: 'border-throttled/70',
  ERROR: 'border-error/80',
  OFFLINE: 'border-offline/40',
}

export const STATUS_BG: Record<Status, string> = {
  OK: 'bg-ok',
  THROTTLED: 'bg-throttled',
  ERROR: 'bg-error',
  OFFLINE: 'bg-offline',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_BG[status]}`} />
      {status}
    </span>
  )
}

export function StatTile({ label, value, unit, hint }: { label: string; value: string | number; unit?: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-edge bg-surface px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="num mt-1 text-2xl font-semibold text-text">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  )
}

export function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-edge">
        <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
      <span className="num w-10 text-right text-xs text-muted">{pct.toFixed(0)}%</span>
    </div>
  )
}

const EVENT_STYLE: Record<string, string> = {
  anomaly_detected: 'text-throttled',
  healing_applied: 'text-accent',
  healed: 'text-ok',
  machine_error: 'text-error',
  error_injected: 'text-error',
  factory_state: 'text-muted',
  info: 'text-muted',
}

export function relativeTime(ts: string): string {
  const then = Date.parse(ts)
  if (Number.isNaN(then)) return ''
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return `vor ${secs} s`
  if (secs < 3600) return `vor ${Math.round(secs / 60)} min`
  return `vor ${Math.round(secs / 3600)} h`
}

export function EventRow({ kind, machineId, ts, detail }: { kind: string; machineId?: number; ts: string; detail?: Record<string, unknown> }) {
  const parts = Object.entries(detail ?? {})
    .filter(([, v]) => typeof v !== 'object')
    .map(([k, v]) => `${k}=${typeof v === 'number' ? Number(v.toFixed(2)) : v}`)
    .join('  ')
  return (
    <div className="flex items-baseline gap-3 border-b border-edge/60 py-1.5 text-sm last:border-0">
      <span className="num w-16 shrink-0 text-xs text-muted">{relativeTime(ts)}</span>
      <span className={`w-36 shrink-0 font-medium ${EVENT_STYLE[kind] ?? 'text-text'}`}>{kind}</span>
      <span className="num w-10 shrink-0 text-muted">{machineId != null ? `M${machineId}` : '—'}</span>
      <span className="num truncate text-xs text-muted">{parts}</span>
    </div>
  )
}
