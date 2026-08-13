"""Edge-Rule-Runner: bindet die Engine an den MQTT-Strom (SPEC §8.3).

Messbare Anforderung der SPEC: Symptom → `cmd` unter 500 ms. Deshalb misst
dieser Dienst seine eigene Latenz und legt sie offen — eine Anforderung, die man
nicht sehen kann, ist keine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import aiomqtt
import asyncpg

from ..events.store import EventStore
from .engine import RuleEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("rules")

MQTT_HOST = os.environ.get("ZW_MQTT_HOST", "emqx")
MQTT_PORT = int(os.environ.get("ZW_MQTT_PORT", "1883"))
DB_DSN = os.environ.get("ZW_DB_DSN", "postgresql://zellwerk:zellwerk@timescaledb:5432/zellwerk")
RULES_PATH = os.environ.get("ZW_RULES_PATH", "/app/core/rules/rules.yaml")


class Latenz:
    """Sammelt die Zeit von eingehendem Wert bis abgesetztem Kommando."""

    def __init__(self) -> None:
        self.werte: list[float] = []

    def add(self, ms: float) -> None:
        self.werte.append(ms)
        # Nur die jüngsten behalten — das ist Betriebsmessung, kein Archiv.
        if len(self.werte) > 500:
            self.werte = self.werte[-500:]

    def report(self) -> dict:
        if not self.werte:
            return {"n": 0}
        sortiert = sorted(self.werte)
        return {
            "n": len(sortiert),
            "min_ms": round(sortiert[0], 2),
            "median_ms": round(sortiert[len(sortiert) // 2], 2),
            "p95_ms": round(sortiert[int(len(sortiert) * 0.95)], 2),
            "max_ms": round(sortiert[-1], 2),
        }


LATENZ = Latenz()


async def log_event(store: EventStore, asset: str, severity: str,
                    code: str, payload: dict) -> None:
    """Meldet ein Ereignis über die Ereignisschicht.

    Der Umweg über den EventStore statt eines direkten INSERT ist der Grund,
    warum ein schwankender Messwert nicht hunderte Einträge erzeugt: dort wird
    entprellt und am bestehenden Eintrag hochgezählt.
    """
    try:
        await store.emit(code, severity, asset, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ereignis %s konnte nicht gespeichert werden: %s", code, exc)


async def run(engine: RuleEngine, store: EventStore) -> None:
    async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
        await client.subscribe("zellwerk/v1/+/+/+/+/pv/#")
        log.info("Regelwerk aktiv: %d Regeln", len(engine.rules))

        async for message in client.messages:
            eingang = time.perf_counter()
            topic = str(message.topic)
            try:
                payload = json.loads(message.payload)
            except (ValueError, TypeError):
                continue

            value = payload.get("value")
            quality = payload.get("quality", "good")
            treffer = engine.evaluate(topic, value, time.time(), quality)
            if not treffer:
                continue

            for trigger in treffer:
                station = topic.split("/")[5] if len(topic.split("/")) > 5 else None
                for action in engine.resolve_actions(trigger):
                    if action.kind == "publish" and action.topic:
                        await client.publish(action.topic,
                                             json.dumps(action.payload or {}).encode())
                        ms = (time.perf_counter() - eingang) * 1000.0
                        LATENZ.add(ms)
                        log.warning(
                            "Regel %s ausgelöst (%s=%.2f, gehalten %.1fs) → %s in %.1f ms",
                            trigger.rule_id, topic.rsplit("/", 1)[-1], trigger.value,
                            trigger.held_for_s, action.topic, ms,
                        )
                    elif action.kind == "event":
                        await log_event(
                            store, station, action.severity or "warn", action.code or "UNBEKANNT",
                            {"regel": trigger.rule_id, "topic": topic, "wert": trigger.value,
                             "gehalten_s": round(trigger.held_for_s, 1)},
                        )
                        log.warning("Ereignis %s (%s = %.2f)", action.code, topic, trigger.value)


async def report() -> None:
    while True:
        await asyncio.sleep(60)
        bericht = LATENZ.report()
        if bericht["n"]:
            log.info("Edge-Latenz (Symptom→cmd): %s", bericht)


async def main() -> None:
    engine = RuleEngine.from_yaml(RULES_PATH)

    pool = None
    for _ in range(30):
        try:
            pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=4)
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(2)
    if pool is None:
        raise SystemExit("Datenbank nicht erreichbar — Abbruch")

    store = EventStore(pool)
    asyncio.create_task(report())
    while True:
        try:
            await run(engine, store)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT-Verbindung verloren (%s) — neuer Versuch in 5s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
