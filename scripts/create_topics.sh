#!/usr/bin/env bash
# ============================================================================
# V4Smart — Topic-Anlage (läuft als Einmal-Job "redpanda-init", idempotent)
# Übersicht & Formate: CLAUDE.md §6
# ============================================================================
set -uo pipefail

B="-X brokers=redpanda:9092"

create() {
  local name="$1" parts="$2"; shift 2
  if rpk topic create "$name" -p "$parts" -r 1 "$@" $B >/dev/null 2>&1; then
    echo "topic angelegt:   $name (p=$parts)"
  else
    echo "topic vorhanden:  $name"
  fi
}

# Hot Path (FlatBuffers) — kurze Retention, viele Partitionen (Skalierbarkeit)
create sensor_raw      16 -c retention.ms=3600000
create sensor_clean    16 -c retention.ms=3600000

# Control/Event Plane (JSON) — 24 h Retention
create mes_orders       3 -c retention.ms=86400000
create machine_control  3 -c retention.ms=86400000
create system_events    3 -c retention.ms=86400000

echo "--- Topics ---"
rpk topic list $B

# Verifikation: nur mit Exit 0 beenden, wenn wirklich alle Topics existieren
# (depends_on: service_completed_successfully verlässt sich darauf)
MISSING=0
LIST="$(rpk topic list $B 2>/dev/null)"
for t in sensor_raw sensor_clean mes_orders machine_control system_events; do
  echo "$LIST" | grep -q "^$t" || { echo "FEHLER: Topic $t fehlt!"; MISSING=1; }
done
[ "$MISSING" -eq 0 ] && echo "redpanda-init: fertig."
exit $MISSING
