// Leichter Store ohne Bibliothek. Telemetrie kommt mit 8 Maschinen x 5 Hz — die
// UI wird deshalb nur 4x pro Sekunde benachrichtigt (CLAUDE.md §16, Punkt 5),
// die Frames selbst laufen ungedrosselt in die Ringpuffer.

export type Status = 'OK' | 'THROTTLED' | 'ERROR' | 'OFFLINE'

export interface Machine {
  id: number
  status: Status
  temp: number
  press: number
  vib: number
  speed: number
  last_seen_ms: number
  anomaly_score: number | null
}

export interface Order {
  order_id: string
  product: string
  qty: number
  machine_id: number
  status: 'QUEUED' | 'RUNNING' | 'DONE'
  progress: number
  ts: string
}

export interface FactoryEvent {
  kind: string
  machine_id?: number
  ts: string
  detail?: Record<string, unknown>
}

export interface Stats {
  in_rate: number
  valid_rate: number
  dropped_rate: number
  clean_rate: number
  db_rows_s: number
  ws_clients: number
  factory_running: boolean
}

export const TRACE_POINTS = 60
export const EVENTS_CAP = 200
export const ORDERS_CAP = 200

interface Trace {
  vib: number[]
  temp: number[]
}

/** Behält je Auftrag nur den zuletzt gemeldeten Stand (Reihenfolge bleibt). */
function dedupeOrders(orders: Order[]): Order[] {
  const latest = new Map<string, Order>()
  for (const order of orders) latest.set(order.order_id, order)
  return [...latest.values()]
}

class Store {
  machines = new Map<number, Machine>()
  traces = new Map<number, Trace>()
  orders: Order[] = []
  events: FactoryEvent[] = []
  stats: Stats | null = null
  connected = false
  /** Zeitpunkt des letzten Telemetry-Frames je Maschine — treibt den Blink-Dot. */
  lastFrameAt = new Map<number, number>()
  /** Telemetry-Frames je Maschine seit Beginn des aktuellen Auftrags (§16, View 3). */
  batchCounts = new Map<number, number>()

  private version = 0
  private dirty = false
  private listeners = new Set<() => void>()

  constructor() {
    // UI-Tick: 4x/s reicht fürs Auge und hält die Seite bei 40 Frames/s ruhig.
    setInterval(() => {
      if (!this.dirty) return
      this.dirty = false
      this.version++
      this.listeners.forEach((l) => l())
    }, 250)
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getVersion = () => this.version

  private touch() {
    this.dirty = true
  }

  /** Sofortige Benachrichtigung für Zustandswechsel, die nicht warten dürfen. */
  private flush() {
    this.dirty = false
    this.version++
    this.listeners.forEach((l) => l())
  }

  setConnected(value: boolean) {
    if (this.connected === value) return
    this.connected = value
    this.flush()
  }

  applySnapshot(machines: Machine[], orders: Order[], events: FactoryEvent[], stats: Stats | null) {
    this.machines = new Map(machines.map((m) => [m.id, m]))
    // Snapshot-Resync: neueste zuerst geliefert, intern chronologisch halten.
    // WICHTIG: Der Snapshot enthält JEDE Auftragsnachricht (QUEUED, mehrere
    // RUNNING-Updates, DONE). Die Tabelle ist aber eine Auftragsliste, kein
    // Nachrichten-Log — ohne diese Zusammenfassung stand ein Auftrag vier Mal
    // untereinander, gleichzeitig mit 0 %, 52 % und 100 % (im Screenshot gesehen).
    this.orders = dedupeOrders([...orders].reverse()).slice(-ORDERS_CAP)
    this.events = [...events].reverse().slice(-EVENTS_CAP)
    this.stats = stats
    this.flush()
  }

  applyTelemetry(t: { m: number; temp: number; press: number; vib: number; speed: number; status: Status; ts_ms: number }) {
    const existing = this.machines.get(t.m)
    this.machines.set(t.m, {
      id: t.m,
      status: t.status,
      temp: t.temp,
      press: t.press,
      vib: t.vib,
      speed: t.speed,
      last_seen_ms: t.ts_ms,
      anomaly_score: existing?.anomaly_score ?? null,
    })

    let trace = this.traces.get(t.m)
    if (!trace) {
      trace = { vib: [], temp: [] }
      this.traces.set(t.m, trace)
    }
    trace.vib.push(t.vib)
    trace.temp.push(t.temp)
    if (trace.vib.length > TRACE_POINTS) trace.vib.shift()
    if (trace.temp.length > TRACE_POINTS) trace.temp.shift()

    this.lastFrameAt.set(t.m, Date.now())
    this.batchCounts.set(t.m, (this.batchCounts.get(t.m) ?? 0) + 1)
    this.touch()
  }

  applyEvent(event: FactoryEvent) {
    this.events.push(event)
    if (this.events.length > EVENTS_CAP) this.events.shift()

    if (event.kind === 'anomaly_detected' && typeof event.machine_id === 'number') {
      const score = (event.detail as { score?: number } | undefined)?.score
      const machine = this.machines.get(event.machine_id)
      if (machine && typeof score === 'number') {
        this.machines.set(event.machine_id, { ...machine, anomaly_score: score })
      }
    }
    // Ereignisse sind selten und wichtig — sofort zeigen.
    this.flush()
  }

  applyOrder(order: Order) {
    const idx = this.orders.findIndex((o) => o.order_id === order.order_id)
    if (idx >= 0) this.orders[idx] = order
    else {
      this.orders.push(order)
      if (this.orders.length > ORDERS_CAP) this.orders.shift()
      // Neuer Auftrag: Sensor-Batch-Zähler der Maschine beginnt neu (§16).
      this.batchCounts.set(order.machine_id, 0)
    }
    this.touch()
  }

  applyStats(stats: Stats) {
    this.stats = stats
    this.touch()
  }

  machineList(): Machine[] {
    return [...this.machines.values()].sort((a, b) => a.id - b.id)
  }

  eventsNewestFirst(limit = EVENTS_CAP): FactoryEvent[] {
    return this.events.slice(-limit).reverse()
  }

  ordersNewestFirst(limit = ORDERS_CAP): Order[] {
    return this.orders.slice(-limit).reverse()
  }

  trace(id: number): Trace {
    return this.traces.get(id) ?? { vib: [], temp: [] }
  }

  countByStatus(): Record<Status, number> {
    const counts: Record<Status, number> = { OK: 0, THROTTLED: 0, ERROR: 0, OFFLINE: 0 }
    this.machines.forEach((m) => {
      counts[m.status] = (counts[m.status] ?? 0) + 1
    })
    return counts
  }
}

export const store = new Store()

/** Steuerkommandos (§10). Fehler werden sichtbar gemacht, nie verschluckt. */
export async function sendControl(body: Record<string, unknown>): Promise<{ ok: boolean; machine_id?: number; error?: string }> {
  try {
    const res = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error ?? `HTTP ${res.status}` }
    return { ok: true, machine_id: data.machine_id }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}
