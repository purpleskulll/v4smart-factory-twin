// ============================================================================
// WebSocket-Probe für middleware-core (CLAUDE.md §10).
//
// Aufruf (aus dem Dev-Container):
//   docker run --rm --network v4smart_backend \
//     -v /workspace/scripts/ws_probe.js:/probe.js:ro node:22-alpine node /probe.js
//
// Node 22 bringt WebSocket global mit — bewusst OHNE npm/npx: das Netz
// v4smart_backend ist `internal`, dort kann `npx wscat` gar nicht installieren.
// Bind-Mounts müssen unter /workspace liegen (der innere Daemon löst sie gegen
// SEIN Dateisystem auf).
// ============================================================================
const url = process.env.WS_URL || 'ws://middleware-core:8080/ws';
const seconds = Number(process.env.WS_SECONDS || 8);

const ws = new WebSocket(url);
const seen = {};
let snapshot = null;

ws.onmessage = (e) => {
  const frame = JSON.parse(e.data);
  seen[frame.t] = (seen[frame.t] || 0) + 1;
  if (frame.t === 'snapshot') snapshot = frame;
};
ws.onerror = () => {
  console.log('WS-FEHLER: keine Verbindung zu ' + url);
  process.exit(1);
};

setTimeout(() => {
  console.log('Frame-Typen in ' + seconds + 's: ' + JSON.stringify(seen));
  if (snapshot) {
    console.log('snapshot: machines=' + snapshot.machines.length +
      ' orders=' + snapshot.orders.length +
      ' events=' + snapshot.events.length +
      ' in_rate=' + (snapshot.stats && snapshot.stats.in_rate));
  }
  // Ohne telemetry-Frames ist der Live-Stream tot -> harter Fehler.
  const ok = seen.snapshot >= 1 && seen.telemetry > 0 && seen.stats > 0;
  console.log(ok ? 'WS OK' : 'WS UNVOLLSTAENDIG');
  process.exit(ok ? 0 : 1);
}, seconds * 1000);
