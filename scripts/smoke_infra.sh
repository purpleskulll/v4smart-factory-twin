#!/usr/bin/env bash
# ============================================================================
# V4Smart — Smoke-Test Infrastruktur (Schritt 00)
# Läuft im Dev-Container (cwd egal, Script wechselt selbst nach /workspace).
# Prüft: Redpanda healthy, alle Topics, Kafka-Roundtrip, QuestDB HTTP + ILP.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

C="docker compose"
NET="v4smart_backend"
CURL_IMG="curlimages/curl:8.10.1"

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  OK\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m  %s\n' "$*"; exit 1; }

say "1/5 Warte auf Redpanda (healthy)"
for i in $(seq 1 45); do
  if $C exec -T redpanda rpk cluster health 2>/dev/null | grep -qE 'Healthy:.*true'; then
    ok "Redpanda ist healthy"; break
  fi
  [ "$i" -eq 45 ] && fail "Redpanda wird nicht healthy → docker compose logs redpanda"
  sleep 2
done

say "2/5 Topics vorhanden?"
$C exec -T redpanda rpk topic list > /tmp/v4_topics.txt
for t in sensor_raw sensor_clean mes_orders machine_control system_events; do
  grep -q "^$t" /tmp/v4_topics.txt && ok "Topic $t" \
    || fail "Topic $t fehlt → docker compose logs redpanda-init"
done

say "3/5 Kafka Produce/Consume-Roundtrip"
MSG="smoke-$$-$RANDOM"
$C exec -T redpanda rpk topic create smoke_tmp -p 1 >/dev/null 2>&1 || true
$C exec -T redpanda bash -c "echo '$MSG' | rpk topic produce smoke_tmp" >/dev/null
GOT="$($C exec -T redpanda rpk topic consume smoke_tmp -n 1 --offset start -f '%v')"
$C exec -T redpanda rpk topic delete smoke_tmp >/dev/null 2>&1 || true
case "$GOT" in *"$MSG"*) ok "Roundtrip ($MSG)";; *) fail "Roundtrip kaputt: '$GOT'";; esac

say "4/5 QuestDB HTTP-API (SELECT 1)"
RES="$(docker run --rm --network "$NET" "$CURL_IMG" -sf 'http://questdb:9000/exec?query=SELECT%201' || true)"
echo "$RES" | grep -q '"dataset":\[\[1\]\]' && ok "QuestDB antwortet" \
  || fail "QuestDB HTTP kaputt: $RES → docker compose logs questdb"

say "5/5 QuestDB ILP-Schreibtest (Port 9009)"
docker run --rm --network "$NET" alpine:3.20 \
  sh -c "printf 'smoke_ilp,src=smoke value=1i\n' | nc -w 2 questdb 9009" || true
for i in $(seq 1 12); do
  N="$(docker run --rm --network "$NET" "$CURL_IMG" -sf \
        'http://questdb:9000/exec?query=SELECT%20count()%20FROM%20smoke_ilp' 2>/dev/null \
        | jq -r '.dataset[0][0]' 2>/dev/null || echo 0)"
  if [ "${N:-0}" -ge 1 ] 2>/dev/null; then ok "ILP → Tabelle smoke_ilp ($N Zeile/n)"; break; fi
  [ "$i" -eq 12 ] && fail "ILP-Zeile nicht in QuestDB angekommen"
  sleep 1
done

printf '\n\033[1;32m✔ INFRASTRUKTUR OK\033[0m — Redpanda, Topics, QuestDB (HTTP+ILP) laufen.\n'
