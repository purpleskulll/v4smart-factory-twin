"""Musterfabrik — Hauptprozess (SPEC §7).

Startet die sechs OPC-UA-Server, treibt den Takt der Fabrik und bietet die
REST-Schnittstelle für die Fault-Injection (§7.3).

Aufgabenteilung mit dem Konnektor:
  * Prozesswerte gehen über OPC UA hinaus; der Konnektor liest sie und
    publiziert sie in den UNS. Das ist der Weg, den später auch echte Maschinen
    nehmen — das Kernversprechen des Produkts (§8.1).
  * `trace`-Meldungen (Genealogie) publiziert die Fabrik direkt in den UNS.
    Begründung: Genealogie kommt in einer echten Anlage aus dem MES, nicht aus
    der SPS — sie über OPC UA zu schicken wäre gerade nicht realistisch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiomqtt
import uvicorn
from fastapi import FastAPI, HTTPException

from .opcua_layer import StationServer
from .stations import Factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("simfactory")

MQTT_HOST = os.environ.get("ZW_MQTT_HOST", "emqx")
MQTT_PORT = int(os.environ.get("ZW_MQTT_PORT", "1883"))
SITE = os.environ.get("ZW_SITE", "werk1")
TICK_S = float(os.environ.get("ZW_TICK_S", "1.0"))
# Zeitraffer: 1.0 = Echtzeit. Für Demos lässt sich die Fabrik beschleunigen,
# ohne die Prozesslogik anzufassen.
SPEED = float(os.environ.get("ZW_SPEED", "1.0"))


class Simulation:
    def __init__(self) -> None:
        self.factory = Factory(started_at=datetime.now(UTC).replace(tzinfo=None))
        self.servers: list[StationServer] = []
        self.running = False
        self._published_lots: set[str] = set()
        self._published_cells: dict[str, str] = {}
        self.ticks = 0

    # ------------------------------------------------------------ Lebenszyklus
    async def start_opcua(self) -> None:
        for station in self.factory.stations:
            werte = station.tick(self.factory.now)
            server = StationServer(station)
            await server.start(werte)
            self.servers.append(server)

    async def stop_opcua(self) -> None:
        for server in self.servers:
            await server.stop()

    # ------------------------------------------------------------------- Takt
    async def run(self) -> None:
        self.running = True
        while self.running:
            try:
                await self._tick_once()
            except Exception:  # noqa: BLE001
                # Ein Fehler im Takt darf die Fabrik nicht stillschweigend
                # anhalten — er wird protokolliert, dann läuft es weiter.
                log.exception("Fehler im Fabriktakt")
            await asyncio.sleep(TICK_S / SPEED)

    async def _tick_once(self) -> None:
        werte = self.factory.step(TICK_S)
        self.ticks += 1
        for server in self.servers:
            if (pvs := werte.get(server.station.station_id)) is not None:
                await server.publish(pvs)
        await self._publish_traces()

    async def _publish_traces(self) -> None:
        """Neue Lose und Zellen als `trace` in den UNS (SPEC §6.1).

        Zeitstempel sind ECHTZEIT, nicht Simulationszeit. Das ist wichtig: der
        Konnektor stempelt jeden Messwert mit der Wanduhr. Trüge ein Los
        stattdessen seine Simulationszeit, liefen beide Achsen im Zeitraffer
        auseinander — die Abfrage „Prozesswerte im Fertigungszeitraum dieses
        Loses" fände dann nichts, und der Batteriepass bliebe leer. Genau das
        ist passiert, bevor diese Zeile so aussah wie jetzt.
        """
        jetzt = datetime.now(UTC).isoformat()
        neue: list[tuple[str, dict]] = []

        for lot_id, lot in self.factory.genealogy.lots.items():
            if lot_id in self._published_lots:
                continue
            self._published_lots.add(lot_id)
            neue.append((
                f"zellwerk/v1/{SITE}/{lot.station}/trace/lot",
                {
                    "ts": jetzt,
                    "value": {
                        "lot_id": lot.id, "station": lot.station, "material": lot.material,
                        "parent_id": lot.parent_id,
                        "traits": {k: round(v, 4) for k, v in lot.traits.items()
                                   if isinstance(v, (int, float))},
                    },
                    "quality": "good", "unit": "",
                },
            ))

        # Eine Zelle wird bei JEDER Statusänderung neu gemeldet, nicht nur beim
        # Anlegen. Vorher merkte sich der Simulator nur, DASS eine Zelle schon
        # publiziert war — die Datenbank blieb deshalb für immer bei
        # "in_prozess" stehen, obwohl die Fabrik längst OK, Ausschuss oder
        # Quarantäne kannte. Damit war jede Auswertung über Zellstatus wertlos.
        for serial, cell in self.factory.genealogy.cells.items():
            if self._published_cells.get(serial) == cell.status:
                continue
            self._published_cells[serial] = cell.status
            neue.append((
                f"zellwerk/v1/{SITE}/zelle/trace/cell",
                {
                    "ts": jetzt,
                    "value": {"serial": cell.serial, "lot_id": cell.lot_id,
                              "status": cell.status, "grade": cell.grade,
                              # Die Merkmale tragen die gemessene Kapazität und
                              # die Herkunft — ohne sie kann weder der
                              # Batteriepass noch eine Betroffenheitsanalyse
                              # etwas belegen.
                              "traits": {k: round(v, 4) for k, v in cell.traits.items()
                                         if isinstance(v, (int, float))}},
                    "quality": "good", "unit": "",
                },
            ))

        if not neue:
            return
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                for topic, payload in neue:
                    await client.publish(topic, json.dumps(payload).encode())
        except Exception as exc:  # noqa: BLE001
            # Nicht verschlucken: ohne Traces gibt es keine Genealogie in der DB.
            log.warning("trace-Publish fehlgeschlagen (%s) — %d Meldungen verloren",
                        exc, len(neue))


SIM = Simulation()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await SIM.start_opcua()
    task = asyncio.create_task(SIM.run())
    log.info("Musterfabrik läuft — %d Stationen, Takt %.1fs (Speed %.1fx)",
             len(SIM.servers), TICK_S, SPEED)
    yield
    SIM.running = False
    task.cancel()
    await SIM.stop_opcua()


app = FastAPI(title="zellwerk Musterfabrik", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": SIM.running,
        "ticks": SIM.ticks,
        "sim_zeit": SIM.factory.now.isoformat(),
        "stationen": [s.station.station_id for s in SIM.servers],
        "aktive_fehler": sorted({f for s in SIM.factory.stations for f in s.faults}),
    }


@app.get("/state")
async def state() -> dict:
    """Zustandsüberblick — nützlich für Demo und Fehlersuche."""
    f = SIM.factory
    return {
        "sim_zeit": f.now.isoformat(),
        "lose": len(f.genealogy.lots),
        "zellen": len(f.genealogy.cells),
        "formiert": len(f.formation.fertig),
        "zellen_nach_status": {
            status: sum(1 for c in f.genealogy.cells.values() if c.status == status)
            for status in ("in_prozess", "ok", "ausschuss", "quarantaene")
        },
        "warteschlangen": {
            "slurry": len(f._slurry_queue), "elektrode": len(f._elektrode_queue),
            "kalandriert": len(f._kalander_queue), "formierung": len(f._formier_queue),
        },
    }


@app.post("/faults/{fault_id}")
async def inject_fault(fault_id: str) -> dict:
    """Fehlerszenario einspielen (SPEC §7.3): F1–F5."""
    try:
        station = SIM.factory.inject(fault_id.upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log.warning("Fehlerszenario %s eingespielt auf %s", fault_id.upper(), station)
    return {"fault": fault_id.upper(), "station": station,
            "eingespielt_um": SIM.factory.now.isoformat()}


@app.delete("/faults/{fault_id}")
async def clear_fault(fault_id: str) -> dict:
    SIM.factory.clear(fault_id.upper())
    log.info("Fehlerszenario %s zurückgenommen", fault_id.upper())
    return {"fault": fault_id.upper(), "aktiv": False}


@app.get("/faults")
async def list_faults() -> dict:
    beschreibung = {
        "F1": "Viskositätsdrift im Mischer → Schichtdicke streut → Porosität zu niedrig",
        "F2": "Trocknertemperatur zu hoch → Haftung sinkt → Delamination in der Assemblierung",
        "F3": "Übertemperatur in einem Formierkanal → Quarantäne (Edge-Rule drosselt)",
        "F4": "Elektrolyt-Unterdosierung → niedrige Kapazität, nur über Genealogie zuordenbar",
        "F5": "Zykler-Kanalausfall → quality=bad, Durchsatzverlust OHNE Qualitätsproblem",
    }
    aktiv = {f for s in SIM.factory.stations for f in s.faults}
    return {"verfuegbar": beschreibung, "aktiv": sorted(aktiv)}


@app.post("/formation/derate/{channel}")
async def derate(channel: int, factor: float = 0.5) -> dict:
    """Drosselung eines Formierkanals — der Zielpunkt der Edge-Rule (§8.3)."""
    if not SIM.factory.formation.derate_channel(channel, factor):
        raise HTTPException(status_code=404, detail=f"Kanal {channel} unbekannt")
    return {"kanal": channel, "faktor": factor}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
