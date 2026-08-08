import { store } from '../lib/store'
import { ProgressBar, StatusBadge } from '../components/ui'

const ORDER_STATUS_STYLE: Record<string, string> = {
  QUEUED: 'text-muted',
  RUNNING: 'text-accent',
  DONE: 'text-ok',
}

export function MesLog() {
  const orders = store.ordersNewestFirst(60)
  const machines = store.machineList()

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[2fr_1fr]">
      <section className="rounded-lg border border-edge bg-surface p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">MES/ERP — Auftrags-Log</h2>
        {orders.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted">Noch keine Aufträge.</div>
        ) : (
          <div className="max-h-[32rem] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface text-left text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="py-2 pr-3 font-medium">Auftrag</th>
                  <th className="py-2 pr-3 font-medium">Produkt</th>
                  <th className="py-2 pr-3 font-medium">Maschine</th>
                  <th className="py-2 pr-3 text-right font-medium">Menge</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Fortschritt</th>
                  <th className="py-2 pr-3 text-right font-medium">Sensor-Batches</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.order_id} className="border-t border-edge/60">
                    <td className="num py-1.5 pr-3">{o.order_id}</td>
                    <td className="py-1.5 pr-3">{o.product}</td>
                    <td className="num py-1.5 pr-3">M{o.machine_id}</td>
                    <td className="num py-1.5 pr-3 text-right">{o.qty}</td>
                    <td className={`py-1.5 pr-3 font-medium ${ORDER_STATUS_STYLE[o.status] ?? ''}`}>{o.status}</td>
                    <td className="py-1.5 pr-3">
                      <ProgressBar value={o.progress} />
                    </td>
                    <td className="num py-1.5 pr-3 text-right text-muted">
                      {store.batchCounts.get(o.machine_id) ?? 0}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-edge bg-surface p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">Vorhersage-Metriken</h2>
        <div className="space-y-2">
          {machines.map((m) => (
            <div key={m.id} className="flex items-center justify-between border-b border-edge/60 py-1.5 last:border-0">
              <span className="num w-12">M{m.id}</span>
              <StatusBadge status={m.status} />
              <span className="num text-sm text-muted">
                {m.anomaly_score != null ? m.anomaly_score.toFixed(3) : '—'}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">
          Letzter Anomalie-Score je Maschine (aus den ML-Ereignissen). „—" heißt: seit dem Start keine Auffälligkeit.
        </p>
      </section>
    </div>
  )
}
