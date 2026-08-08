import { store, type Machine } from '../lib/store'
import { Sparkline } from './Sparkline'
import { STATUS_BG, STATUS_BORDER, StatusBadge } from './ui'

/** Puls-Dot: leuchtet kurz auf, sobald ein Telemetry-Frame eintraf (§16). */
function BlinkDot({ id, status }: { id: number; status: Machine['status'] }) {
  const last = store.lastFrameAt.get(id) ?? 0
  const fresh = Date.now() - last < 200
  return <span className={`h-2 w-2 rounded-full ${STATUS_BG[status]} ${fresh ? 'dot-pulse' : 'opacity-30'}`} />
}

function Value({ label, value, unit }: { label: string; value: number; unit: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="num text-lg font-semibold">
        {value.toFixed(2)}
        <span className="ml-1 text-xs font-normal text-muted">{unit}</span>
      </div>
    </div>
  )
}

export function MachineCard({ machine }: { machine: Machine }) {
  const trace = store.trace(machine.id)
  return (
    <div className={`rounded-lg border bg-surface p-4 ${STATUS_BORDER[machine.status]}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BlinkDot id={machine.id} status={machine.status} />
          <span className="font-semibold">Maschine {machine.id}</span>
        </div>
        <StatusBadge status={machine.status} />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <Value label="Temperatur" value={machine.temp} unit="°C" />
        <Value label="Druck" value={machine.press} unit="bar" />
        <Value label="Vibration" value={machine.vib} unit="mm/s" />
        <Value label="Speed" value={machine.speed} unit="×" />
      </div>

      <div className="mt-3 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wide text-muted">Vibration</span>
          <Sparkline values={trace.vib} color="#38BDF8" />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wide text-muted">Temperatur</span>
          <Sparkline values={trace.temp} color="#F59E0B" />
        </div>
      </div>

      {machine.anomaly_score != null && (
        <div className="num mt-2 text-xs text-muted">anomaly_score {machine.anomaly_score.toFixed(3)}</div>
      )}
    </div>
  )
}
