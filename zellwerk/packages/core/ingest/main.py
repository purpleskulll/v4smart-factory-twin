"""Ingest: Unified Namespace → TimescaleDB (SPEC §8.2).

Abonniert `zellwerk/v1/#` und schreibt gebündelt. Bündeln ist hier nicht
Optimierung, sondern Voraussetzung: einzelne INSERTs schaffen die geforderte
Kapazität (§8.2, docs/decisions.md D4) nicht annähernd.

Zwei Dinge, die aus Erfahrung hier drinstehen müssen:
  * Der Schreibpfad darf den Lesepfad nicht blockieren. Läuft die Warteschlange
    voll, werden Werte VERWORFEN und GEZÄHLT — nicht gepuffert, bis der Speicher
    ausgeht.
  * Verworfene Werte tauchen in `/metrics` auf. Ein Datenverlust, den die
    Kennzahlen nicht zeigen, ist schlimmer als der Verlust selbst.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import UTC, datetime

import aiomqtt
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

MQTT_HOST = os.environ.get("ZW_MQTT_HOST", "emqx")
MQTT_PORT = int(os.environ.get("ZW_MQTT_PORT", "1883"))
DB_DSN = os.environ.get("ZW_DB_DSN", "postgresql://zellwerk:zellwerk@timescaledb:5432/zellwerk")

BATCH_MAX = int(os.environ.get("ZW_INGEST_BATCH", "500"))
FLUSH_S = float(os.environ.get("ZW_INGEST_FLUSH_S", "1.0"))
QUEUE_MAX = int(os.environ.get("ZW_INGEST_QUEUE", "50000"))


class Stats:
    def __init__(self) -> None:
        self.empfangen = 0
        self.geschrieben = 0
        self.verworfen = 0
        self.fehler = 0
        self.traces = 0

    def as_dict(self) -> dict:
        return {
            "empfangen": self.empfangen, "geschrieben": self.geschrieben,
            "verworfen": self.verworfen, "fehler": self.fehler, "traces": self.traces,
        }


STATS = Stats()


def parse_topic(topic: str) -> tuple[str, str, str] | None:
    """`zellwerk/v1/{site}/{area}/{line}/{station}/{kind}/{name}` → (station, kind, name).

    Trace-Topics sind kürzer: `zellwerk/v1/{site}/{station}/trace/lot`.
    """
    parts = topic.split("/")
    if len(parts) == 8:
        return parts[5], parts[6], parts[7]
    if len(parts) == 6 and parts[4] == "trace":
        return parts[3], "trace", parts[5]
    return None


async def writer(pool: asyncpg.Pool, queue: asyncio.Queue) -> None:
    """Sammelt aus der Queue und schreibt gebündelt."""
    batch: list[tuple] = []
    last_flush = asyncio.get_event_loop().time()

    while True:
        timeout = max(0.05, FLUSH_S - (asyncio.get_event_loop().time() - last_flush))
        try:
            item = await asyncio.wait_for(queue.get(), timeout=timeout)
            batch.append(item)
        except TimeoutError:
            pass

        genug = len(batch) >= BATCH_MAX
        faellig = (asyncio.get_event_loop().time() - last_flush) >= FLUSH_S
        if not batch or not (genug or faellig):
            continue

        try:
            async with pool.acquire() as conn:
                await conn.executemany(
                    "INSERT INTO measurement (ts, asset_id, name, value, text_value, unit, quality)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    batch,
                )
            STATS.geschrieben += len(batch)
        except Exception as exc:  # noqa: BLE001
            STATS.fehler += len(batch)
            log.warning("Schreibfehler für %d Werte: %s", len(batch), exc)
        finally:
            batch.clear()
            last_flush = asyncio.get_event_loop().time()


async def handle_trace(pool: asyncpg.Pool, name: str, payload: dict) -> None:
    """Genealogie-Meldungen ins semantische Modell (SPEC §6.2)."""
    value = payload.get("value") or {}
    async with pool.acquire() as conn:
        if name == "lot":
            await conn.execute(
                "INSERT INTO lot (id, station, material, parent_id, start_ts, traits)"
                " VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (id) DO UPDATE"
                " SET traits = EXCLUDED.traits",
                value.get("lot_id"), value.get("station"), value.get("material"),
                value.get("parent_id"),
                datetime.fromisoformat(payload["ts"]) if payload.get("ts") else datetime.now(UTC),
                json.dumps(value.get("traits", {})),
            )
            if value.get("parent_id"):
                await conn.execute(
                    "INSERT INTO genealogy (parent_kind, parent_id, child_kind, child_id)"
                    " VALUES ('lot',$1,'lot',$2) ON CONFLICT DO NOTHING",
                    value["parent_id"], value["lot_id"],
                )
        elif name == "cell":
            await conn.execute(
                "INSERT INTO cell (serial, lot_id, status, grade, created_at, traits)"
                " VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (serial) DO UPDATE"
                " SET status = EXCLUDED.status, grade = EXCLUDED.grade,"
                "     traits = EXCLUDED.traits",
                value.get("serial"), value.get("lot_id"),
                value.get("status", "in_prozess"), value.get("grade"),
                datetime.fromisoformat(payload["ts"]) if payload.get("ts") else datetime.now(UTC),
                json.dumps(value.get("traits", {})),
            )
            await conn.execute(
                "INSERT INTO genealogy (parent_kind, parent_id, child_kind, child_id)"
                " VALUES ('lot',$1,'cell',$2) ON CONFLICT DO NOTHING",
                value.get("lot_id"), value.get("serial"),
            )
    STATS.traces += 1


async def consume(pool: asyncpg.Pool, queue: asyncio.Queue) -> None:
    async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
        await client.subscribe("zellwerk/v1/#")
        log.info("abonniert: zellwerk/v1/# auf %s:%d", MQTT_HOST, MQTT_PORT)

        async for message in client.messages:
            STATS.empfangen += 1
            zerlegt = parse_topic(str(message.topic))
            if zerlegt is None:
                continue
            station, kind, name = zerlegt

            try:
                payload = json.loads(message.payload)
            except (ValueError, TypeError):
                STATS.fehler += 1
                continue

            if kind == "trace":
                with contextlib.suppress(Exception):
                    await handle_trace(pool, name, payload)
                continue
            if kind != "pv":
                continue

            value = payload.get("value")
            ist_zahl = isinstance(value, (int, float)) and not isinstance(value, bool)
            numerisch = value if ist_zahl else None
            text = None if numerisch is not None else (str(value) if value is not None else None)

            ts = datetime.fromisoformat(payload["ts"]) if payload.get("ts") else datetime.now(UTC)
            eintrag = (ts, station, name, numerisch, text,
                       payload.get("unit", ""), payload.get("quality", "good"))

            try:
                queue.put_nowait(eintrag)
            except asyncio.QueueFull:
                # Bewusst verwerfen statt puffern — und zählen, damit der
                # Verlust in /metrics sichtbar ist.
                STATS.verworfen += 1


async def report() -> None:
    """Regelmäßiger Statusbericht — auch das ist Sichtbarkeit."""
    while True:
        await asyncio.sleep(30)
        log.info("Ingest: %s", STATS.as_dict())


async def main() -> None:
    pool = None
    for versuch in range(30):
        try:
            pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=8)
            break
        except Exception as exc:  # noqa: BLE001
            log.info("warte auf Datenbank (%d/30): %s", versuch + 1, exc)
            await asyncio.sleep(2)
    if pool is None:
        raise SystemExit("Datenbank nach 60s nicht erreichbar — Abbruch")

    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    tasks = [asyncio.create_task(writer(pool, queue)), asyncio.create_task(report())]

    while True:
        try:
            await consume(pool, queue)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT-Verbindung verloren (%s) — neuer Versuch in 5s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
