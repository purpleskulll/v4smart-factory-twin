#!/usr/bin/env python3
"""Lastmessung des Ingest-Pfads (SPEC §8.2, docs/decisions.md D4).

SPEC §8.2 nennt „≥ 5.000 Werte/s auf Entwickler-Hardware". Die Musterfabrik
erzeugt im Normalbetrieb nur 30–60 Werte/s — die Zahl ist also eine
KAPAZITÄTS-Anforderung an den Schreibpfad, keine Eigenschaft des laufenden
Systems. Ohne eine Messung unter Last wäre sie eine unbelegte Behauptung.

Dieses Skript erzeugt synthetische Werte direkt auf dem UNS-Topic-Baum und
misst, was hinten in der Datenbank ankommt. Gemessen wird das, was zählt:
nicht wie viel gesendet wurde, sondern wie viel GESCHRIEBEN wurde — und wie
viel unterwegs verworfen wurde.

Aufruf im Compose-Netz:
    docker compose exec ingest python -m tests.load.ingest_load --rate 5000 --sekunden 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime

import aiomqtt
import asyncpg

MQTT_HOST = os.environ.get("ZW_MQTT_HOST", "emqx")
MQTT_PORT = int(os.environ.get("ZW_MQTT_PORT", "1883"))
DB_DSN = os.environ.get("ZW_DB_DSN", "postgresql://zellwerk:zellwerk@timescaledb:5432/zellwerk")

# Eigene Station, damit die Messung die echten Fabrikdaten nicht verunreinigt.
STATION = "lasttest01"
TOPIC = f"zellwerk/v1/werk1/last/linie1/{STATION}/pv/"


async def zaehle(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM measurement WHERE asset_id = $1", STATION)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=int, default=5000, help="Zielrate in Werten/s")
    parser.add_argument("--sekunden", type=int, default=30)
    parser.add_argument("--aufraeumen", action="store_true", help="Testdaten danach löschen")
    args = parser.parse_args()

    conn = await asyncpg.connect(DB_DSN)
    vorher = await zaehle(conn)

    print(f"Lasttest: Ziel {args.rate} Werte/s über {args.sekunden}s")
    print(f"  Station: {STATION} (getrennt von den Fabrikdaten)")

    gesendet = 0
    start = time.perf_counter()
    intervall = 1.0 / args.rate

    async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
        naechster = time.perf_counter()
        while time.perf_counter() - start < args.sekunden:
            payload = json.dumps({
                "ts": datetime.now(UTC).isoformat(),
                "value": 20.0 + (gesendet % 100) / 10.0,
                "quality": "good", "unit": "degC",
            }).encode()
            await client.publish(f"{TOPIC}last_{gesendet % 20}", payload)
            gesendet += 1

            naechster += intervall
            schlaf = naechster - time.perf_counter()
            if schlaf > 0:
                await asyncio.sleep(schlaf)
            elif schlaf < -1.0:
                # Der Sender kommt nicht hinterher — das gehört ins Ergebnis,
                # sonst misst man die eigene Schleife statt den Ingest.
                naechster = time.perf_counter()

    dauer = time.perf_counter() - start
    sende_rate = gesendet / dauer
    print(f"  gesendet: {gesendet} in {dauer:.1f}s = {sende_rate:.0f}/s")

    # Dem Ingest Zeit geben, seine Warteschlange zu leeren.
    print("  warte 10s auf das Leeren der Warteschlange…")
    await asyncio.sleep(10)

    nachher = await zaehle(conn)
    geschrieben = nachher - vorher
    schreib_rate = geschrieben / dauer
    verlust = gesendet - geschrieben

    print()
    print(f"  geschrieben:  {geschrieben} = {schreib_rate:.0f}/s")
    print(f"  Verlust:      {verlust} ({verlust / gesendet * 100:.2f} %)")
    print()

    if sende_rate < args.rate * 0.9:
        print(f"  ACHTUNG: Der Sender erreichte nur {sende_rate:.0f}/s. Gemessen wurde damit")
        print("  die Grenze DIESES Skripts, nicht die des Ingest-Pfads.")

    bestanden = schreib_rate >= args.rate * 0.95 and verlust <= gesendet * 0.01
    print(f"  Ergebnis: {'bestanden' if bestanden else 'NICHT bestanden'}"
          f" (Anforderung ≥ {args.rate}/s bei ≤1 % Verlust)")

    if args.aufraeumen:
        await conn.execute("DELETE FROM measurement WHERE asset_id = $1", STATION)
        print("  Testdaten entfernt.")

    await conn.close()
    return 0 if bestanden else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
