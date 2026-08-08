import { useEffect, useState, useSyncExternalStore } from 'react'

import { store } from './lib/store'
import { connect } from './lib/ws'
import { ControlCenter } from './views/ControlCenter'
import { MesLog } from './views/MesLog'
import { ScadaLive } from './views/ScadaLive'

const TABS = [
  { key: 'control', label: 'Control Center' },
  { key: 'scada', label: 'SCADA Live' },
  { key: 'mes', label: 'MES/ERP Log' },
] as const

type TabKey = (typeof TABS)[number]['key']

export function App() {
  const [tab, setTab] = useState<TabKey>('control')
  // Re-Render folgt dem gedrosselten Store-Tick (4x/s), nicht jedem Frame.
  useSyncExternalStore(store.subscribe, store.getVersion)

  useEffect(() => {
    connect()
  }, [])

  return (
    <div className="min-h-screen bg-bg">
      <header className="border-b border-edge bg-surface/60">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-4 px-6 py-3">
          <div className="text-base font-semibold tracking-tight">
            V4Smart <span className="text-muted">Factory Digital Twin</span>
          </div>

          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`rounded px-3 py-1.5 text-sm transition ${
                  tab === t.key ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${store.connected ? 'bg-ok' : 'bg-error'}`} />
            <span className={`text-xs font-medium ${store.connected ? 'text-ok' : 'text-error'}`}>
              {store.connected ? 'LIVE' : 'RECONNECT…'}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 py-6">
        {tab === 'control' && <ControlCenter />}
        {tab === 'scada' && <ScadaLive />}
        {tab === 'mes' && <MesLog />}
      </main>
    </div>
  )
}
