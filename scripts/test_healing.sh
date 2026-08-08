#!/usr/bin/env bash
# ============================================================================
# V4Smart — E2E-Test der Self-Healing-Kette (Schritt 04/07) — DER Kern-Test.
#
# Ablauf:  Fehler injizieren → predictive-ml erkennt Anomalie → Throttle-
#          Command → Simulator drosselt → Werte normalisieren → Maschine OK.
#
# PASS-Kriterien (CLAUDE.md §12/§13):
#   1. Drosselung (status=THROTTLED) innerhalb von 60 s nach Injection
#   2. Temperatur erreicht NIE >= 85 °C (ERROR wird verhindert)
#   3. Maschine ist innerhalb von 240 s wieder status=OK
# Das Script druckt eine Timeline und endet mit PASS/FAIL (Exit 0/1).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

NET="v4smart_backend"
CURL_IMG="curlimages/curl:8.10.1"
API="http://middleware-core:8080"

api()  { docker run --rm --network "$NET" "$CURL_IMG" -sf --max-time 10 "$@"; }
say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  OK\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m  %s\n' "$*"; exit 1; }

say "Vorbedingungen"
api "$API/api/health" | grep -q '"ok":true' || fail "middleware-core nicht erreichbar"
STATE="$(api "$API/api/machines")"
echo "$STATE" | jq -e 'length >= 1' >/dev/null || fail "Keine Maschinen bekannt"
if echo "$STATE" | jq -e 'all(.[]; .status == "OFFLINE")' >/dev/null; then
  echo "  Fabrik steht — starte sie…"
  api -X POST -H 'Content-Type: application/json' \
      -d '{"type":"factory","action":"start"}' "$API/api/control" >/dev/null
  sleep 8
fi
ok "Fabrik läuft ($(echo "$STATE" | jq 'length') Maschinen)"

say "Injiziere Hardware-Fehler (Vibrations-Rampe)"
RESP="$(api -X POST -H 'Content-Type: application/json' \
        -d '{"type":"inject_error"}' "$API/api/control")"
M="$(echo "$RESP" | jq -r '.machine_id')"
[ -n "$M" ] && [ "$M" != "null" ] || fail "Keine machine_id in Antwort: $RESP"
ok "Fehler injiziert auf Maschine $M"

say "Beobachte Self-Healing (Timeline, alle 3 s)"
T0="$(date +%s)"; THROTTLED_AT=""; HEALED_AT=""; MAXTEMP=0
while :; do
  NOW=$(( $(date +%s) - T0 ))
  ROW="$(api "$API/api/machines" | jq -r --argjson m "$M" \
        '.[] | select(.id == $m) | "\(.status) \(.vib) \(.temp)"')"
  [ -n "$ROW" ] || fail "Maschine $M nicht mehr in /api/machines"
  read -r ST VIB TEMP <<< "$ROW"
  printf '  t+%3ds  M%-2s  status=%-9s  vib=%-6s mm/s  temp=%-6s °C\n' \
         "$NOW" "$M" "$ST" "$VIB" "$TEMP"
  MAXTEMP="$(awk -v a="$MAXTEMP" -v b="$TEMP" 'BEGIN{print (b>a)?b:a}')"

  if [ -z "$THROTTLED_AT" ] && [ "$ST" = "THROTTLED" ]; then
    THROTTLED_AT="$NOW"; ok "Self-Healing hat gedrosselt (t+${NOW}s)"
  fi
  [ "$ST" = "ERROR" ] && fail "Maschine $M in ERROR gelaufen — Heilung zu spät (max Temp: $MAXTEMP)"
  if [ -n "$THROTTLED_AT" ] && [ "$ST" = "OK" ]; then
    HEALED_AT="$NOW"; ok "Maschine wieder OK (t+${NOW}s)"; break
  fi
  [ "$NOW" -ge 240 ] && fail "Timeout: kein vollständiger Healing-Zyklus in 240 s"
  sleep 3
done

say "Auswertung"
awk -v t="$MAXTEMP" 'BEGIN{exit !(t < 85)}' \
  || fail "Temperatur hat 85 °C erreicht (max: $MAXTEMP °C)"
[ "$THROTTLED_AT" -le 60 ] \
  || fail "Drosselung kam zu spät (t+${THROTTLED_AT}s > 60 s)"

printf '\n\033[1;32m✔ PASS\033[0m — Anomalie erkannt → gedrosselt (t+%ss) → geheilt (t+%ss), max Temp %s °C (< 85)\n' \
       "$THROTTLED_AT" "$HEALED_AT" "$MAXTEMP"
