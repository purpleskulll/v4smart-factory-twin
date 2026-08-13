"""Alarm- und Ereignislogik (SPEC §5, §6.2).

Warum das eine eigene Schicht ist und nicht drei Zeilen INSERT im Regel-Runner:

Ein Alarmsystem, das jede Grenzwertverletzung einzeln meldet, erzeugt bei einem
schwankenden Messwert hunderte Einträge — und wird deshalb ignoriert. Genau
dann ist es wertlos, wenn es gebraucht wird. Diese Schicht sorgt für drei
Dinge, die ein roher INSERT nicht leistet:

* **Entprellen.** Derselbe Code an derselben Anlage wird innerhalb eines
  Zeitfensters nicht erneut geschrieben, sondern am bestehenden Eintrag
  hochgezählt.
* **Quittieren.** Ereignisse haben einen Lebenszyklus; `get_active_alarms`
  zeigt nur, was noch offen ist.
* **Kontext mitschreiben.** Ein Alarm ohne den Messwert, der ihn ausgelöst hat,
  zwingt jeden Leser zur Nachrecherche.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import asyncpg

log = logging.getLogger("core.events")

SEVERITIES = ("info", "warn", "alarm")

# Innerhalb dieses Fensters gilt derselbe Code an derselben Anlage als
# Wiederholung. Bewusst großzügig: ein Prozessfehler, der zehn Minuten später
# erneut auftritt, ist meist derselbe Vorgang, kein neuer.
DEDUPE_FENSTER = timedelta(minutes=10)


class EventStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def emit(self, code: str, severity: str, asset_id: str | None,
                   kontext: dict | None = None) -> bool:
        """Schreibt ein Ereignis. Gibt True zurück, wenn es NEU war.

        Eine Wiederholung innerhalb des Entprell-Fensters erhöht nur den Zähler
        am bestehenden Eintrag — sie erzeugt keine zweite Zeile.
        """
        if severity not in SEVERITIES:
            log.warning("unbekannte Schwere '%s' für %s — als 'warn' behandelt", severity, code)
            severity = "warn"

        kontext = dict(kontext or {})
        jetzt = datetime.now(UTC)

        async with self.pool.acquire() as conn:
            bestehend = await conn.fetchrow(
                "SELECT ts, payload FROM event"
                " WHERE code = $1 AND asset_id IS NOT DISTINCT FROM $2"
                "   AND acked = false AND ts > $3"
                " ORDER BY ts DESC LIMIT 1",
                code, asset_id, jetzt - DEDUPE_FENSTER,
            )

            if bestehend is not None:
                payload = bestehend["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                payload = payload or {}
                payload["wiederholungen"] = int(payload.get("wiederholungen", 1)) + 1
                payload["zuletzt"] = jetzt.isoformat()
                payload.update(kontext)
                await conn.execute(
                    "UPDATE event SET payload = $1 WHERE code = $2"
                    " AND asset_id IS NOT DISTINCT FROM $3 AND ts = $4",
                    json.dumps(payload, default=str), code, asset_id, bestehend["ts"],
                )
                return False

            kontext.setdefault("wiederholungen", 1)
            await conn.execute(
                "INSERT INTO event (ts, asset_id, severity, code, payload)"
                " VALUES ($1,$2,$3,$4,$5)",
                jetzt, asset_id, severity, code, json.dumps(kontext, default=str),
            )
            log.info("Ereignis %s (%s) an %s", code, severity, asset_id or "-")
            return True

    async def acknowledge(self, code: str, asset_id: str | None = None) -> int:
        """Quittiert offene Ereignisse. Gibt die Anzahl zurück."""
        async with self.pool.acquire() as conn:
            if asset_id is None:
                ergebnis = await conn.execute(
                    "UPDATE event SET acked = true WHERE code = $1 AND acked = false", code)
            else:
                ergebnis = await conn.execute(
                    "UPDATE event SET acked = true WHERE code = $1"
                    " AND asset_id = $2 AND acked = false", code, asset_id)
        # asyncpg liefert "UPDATE n"
        try:
            return int(ergebnis.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def active(self, severity: str | None = None, limit: int = 50) -> list[dict]:
        async with self.pool.acquire() as conn:
            if severity:
                zeilen = await conn.fetch(
                    "SELECT ts, asset_id, severity, code, payload FROM event"
                    " WHERE acked = false AND severity = $1 ORDER BY ts DESC LIMIT $2",
                    severity, limit)
            else:
                zeilen = await conn.fetch(
                    "SELECT ts, asset_id, severity, code, payload FROM event"
                    " WHERE acked = false ORDER BY ts DESC LIMIT $1", limit)

        ergebnis = []
        for r in zeilen:
            payload = r["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            ergebnis.append({
                "ts": r["ts"].isoformat(), "anlage": r["asset_id"],
                "schwere": r["severity"], "code": r["code"], "kontext": payload or {},
            })
        return ergebnis
