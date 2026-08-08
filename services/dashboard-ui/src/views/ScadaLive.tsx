import { MachineCard } from '../components/MachineCard'
import { store } from '../lib/store'

export function ScadaLive() {
  const machines = store.machineList()

  if (machines.length === 0) {
    return (
      <div className="rounded-lg border border-edge bg-surface p-8 text-center">
        <div className="text-lg font-medium">Keine Maschinen bekannt</div>
        <p className="mt-1 text-sm text-muted">Sobald Telemetrie eintrifft, erscheinen hier die Maschinenkarten.</p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {machines.map((m) => (
        <MachineCard key={m.id} machine={m} />
      ))}
    </div>
  )
}
