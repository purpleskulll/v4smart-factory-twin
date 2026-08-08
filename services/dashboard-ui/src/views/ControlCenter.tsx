import { useState } from 'react'

import { sendControl, store } from '../lib/store'
import { EventRow, StatTile } from '../components/ui'

export function ControlCenter() {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const stats = store.stats
  const running = stats?.factory_running ?? false
  const counts = store.countByStatus()
  const events = store.eventsNewestFirst(60)

  async function act(body: Record<string, unknown>, label: string) {
    setBusy(true)
    setNotice(null)
    const res = await sendControl(body)
    // Optimistischer Zustand + Bestätigung durch das nächste Event-Frame (§16).
    setNotice(
      res.ok
        ? res.machine_id != null
          ? `${label}: Maschine ${res.machine_id}`
          : `${label} gesendet`
        : `${label} fehlgeschlagen: ${res.error}`,
    )
    setBusy(false)
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-edge bg-surface p-4">
        <div className="flex flex-wrap items-center gap-3">
          <button
            disabled={busy}
            onClick={() => act({ type: 'factory', action: running ? 'stop' : 'start' }, running ? 'Fabrik stoppen' : 'Fabrik starten')}
            className={`rounded px-4 py-2 text-sm font-medium transition disabled:opacity-50 ${
              running ? 'bg-error/20 text-error hover:bg-error/30' : 'bg-ok/20 text-ok hover:bg-ok/30'
            }`}
          >
            {running ? 'Fabrik stoppen' : 'Fabrik starten'}
          </button>

          <button
            disabled={busy || !running}
            onClick={() => act({ type: 'inject_error' }, 'Fehler injiziert')}
            className="rounded bg-throttled/20 px-4 py-2 text-sm font-medium text-throttled transition hover:bg-throttled/30 disabled:opacity-40"
          >
            Inject Random Hardware Error
          </button>

          <span className="text-sm text-muted">
            Fabrik: <span className={running ? 'text-ok' : 'text-offline'}>{running ? 'läuft' : 'gestoppt'}</span>
          </span>
        </div>
        {notice && <div className="mt-3 text-sm text-muted">{notice}</div>}
      </section>

      {!running && (
        <section className="rounded-lg border border-edge bg-surface p-6 text-center">
          <div className="text-lg font-medium">Die Fabrik steht</div>
          <p className="mt-1 text-sm text-muted">
            Alle Maschinen sind OFFLINE und senden nur einen Heartbeat. Starte die Fabrik, um Live-Telemetrie zu sehen.
          </p>
        </section>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="msg/s raw" value={stats?.in_rate ?? 0} hint={`verworfen ${stats?.dropped_rate ?? 0}/s`} />
        <StatTile label="msg/s clean" value={stats?.clean_rate ?? 0} />
        <StatTile label="DB rows/s" value={stats?.db_rows_s ?? 0} />
        <StatTile label="WS-Clients" value={stats?.ws_clients ?? 0} />
        <StatTile label="Maschinen OK" value={counts.OK} hint={`${counts.THROTTLED} gedrosselt`} />
        <StatTile label="Störungen" value={counts.ERROR} hint={`${counts.OFFLINE} offline`} />
      </section>

      <section className="rounded-lg border border-edge bg-surface p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted">Self-Healing-Engine — Live-Feed</h2>
        {events.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted">Noch keine Ereignisse.</div>
        ) : (
          <div className="max-h-[26rem] overflow-y-auto pr-2">
            {events.map((e, i) => (
              <EventRow key={`${e.ts}-${e.kind}-${i}`} kind={e.kind} machineId={e.machine_id} ts={e.ts} detail={e.detail} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
