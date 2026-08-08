// WebSocket-Client: same-origin /ws, Auto-Reconnect mit Backoff 1 -> 10 s,
// Snapshot-Resync nach jedem Verbindungsaufbau (CLAUDE.md §16).

import { store } from './store'

const BACKOFF_START_MS = 1000
const BACKOFF_MAX_MS = 10000

let socket: WebSocket | null = null
let backoff = BACKOFF_START_MS
let timer: number | undefined

function url(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}

function handle(raw: string) {
  let frame: any
  try {
    frame = JSON.parse(raw)
  } catch {
    return
  }
  switch (frame.t) {
    case 'snapshot':
      store.applySnapshot(frame.machines ?? [], frame.orders ?? [], frame.events ?? [], frame.stats ?? null)
      break
    case 'telemetry':
      store.applyTelemetry(frame)
      break
    case 'event':
      store.applyEvent(frame.event)
      break
    case 'order':
      store.applyOrder(frame.order)
      break
    case 'stats':
      store.applyStats(frame.stats)
      break
  }
}

export function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return

  socket = new WebSocket(url())

  socket.onopen = () => {
    backoff = BACKOFF_START_MS
    store.setConnected(true)
  }
  socket.onmessage = (e) => handle(typeof e.data === 'string' ? e.data : '')
  socket.onclose = () => {
    store.setConnected(false)
    socket = null
    scheduleReconnect()
  }
  socket.onerror = () => {
    // onclose folgt und übernimmt den Reconnect.
    socket?.close()
  }
}

function scheduleReconnect() {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    connect()
    backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
  }, backoff)
}
