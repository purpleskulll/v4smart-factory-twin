#!/usr/bin/env bash
# ============================================================================
# V4Smart — Smoke-Test Factory-Simulator (Schritt 02)
# Prüft: sensor_raw fließt, Messrate ~ TOTAL_RATE, Payload ist FlatBuffers
# (file_identifier "SNR1"), mes_orders liefert JSON-Aufträge.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

C="docker compose"
TOTAL_RATE="$(grep -E '^TOTAL_RATE=' .env 2>/dev/null | cut -d= -f2 || true)"
TOTAL_RATE="${TOTAL_RATE:-2000}"
WINDOW=6

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  OK\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m  %s\n' "$*"; exit 1; }

say "1/3 Messrate auf sensor_raw (${WINDOW}s Fenster, Ziel ~${TOTAL_RATE}/s)"
COUNT="$( (timeout "$WINDOW" $C exec -T redpanda \
            rpk topic consume sensor_raw -o end -f 'x\n' 2>/dev/null || true) | wc -l)"
RATE=$(( COUNT / WINDOW ))
MIN=$(( TOTAL_RATE * 60 / 100 ))   # -40 % Toleranz (Consumer-Overhead über exec)
echo "  gemessen: $COUNT Messages in ${WINDOW}s ≈ ${RATE}/s"
[ "$RATE" -ge "$MIN" ] && ok "Rate ok (>= ${MIN}/s)" \
  || fail "Rate zu niedrig (${RATE}/s < ${MIN}/s) → Simulator-Logs prüfen"

say "2/3 Payload ist FlatBuffers (file_identifier SNR1)"
IDENT="$( (timeout 10 $C exec -T redpanda \
            rpk topic consume sensor_raw -n 1 -o end -f '%v' 2>/dev/null || true) \
          | head -c 8 | tail -c 4)"
[ "$IDENT" = "SNR1" ] && ok "SNR1-Identifier gefunden (Bytes 4..8)" \
  || fail "Kein FlatBuffers-Identifier (gefunden: '$IDENT') — JSON auf dem Hot Path?"

say "3/3 MES-Aufträge auf mes_orders (JSON)"
ORDER="$( (timeout 30 $C exec -T redpanda \
            rpk topic consume mes_orders -n 1 -o end -f '%v' 2>/dev/null || true) )"
echo "$ORDER" | jq -e '.order_id and .machine_id and .status' >/dev/null 2>&1 \
  && ok "Auftrag: $(echo "$ORDER" | jq -c '{order_id, machine_id, status}')" \
  || fail "Kein valider MES-Auftrag in 30s (gefunden: '$ORDER')"

printf '\n\033[1;32m✔ SIMULATOR OK\033[0m — %s msg/s FlatBuffers + MES-Aufträge fließen.\n' "$RATE"
