"""Konnektor: OPC UA → Unified Namespace (SPEC §8.1).

Bewusst generisch: WAS abonniert und WOHIN publiziert wird, steht vollständig in
`config.yaml`. Im echten Einsatz zeigt dieselbe Datei auf echte Maschinen statt
auf den Simulator — genau das ist das Kernversprechen des Produkts. Deshalb gibt
es hier keine einzige Zeile, die etwas über Batteriefertigung weiß.

Verbindungsabbrüche werden mit Backoff neu aufgebaut. Ein Konnektor, der nach
einem Maschinen-Neustart still stehenbleibt, wäre im Feld wertlos.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import aiomqtt
import yaml
from asyncua import Client, ua

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("connector")

CONFIG_PATH = os.environ.get("ZW_CONNECTOR_CONFIG", "/app/connector-config.yaml")
MQTT_HOST = os.environ.get("ZW_MQTT_HOST", "emqx")
MQTT_PORT = int(os.environ.get("ZW_MQTT_PORT", "1883"))
SITE = os.environ.get("ZW_SITE", "werk1")


@dataclass
class StationConfig:
    station: str
    endpoint: str
    area: str
    line: str
    poll_s: float
    nodes: list[str]


def load_config(path: str) -> list[StationConfig]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return [
        StationConfig(
            station=entry["station"], endpoint=entry["endpoint"],
            area=entry.get("area", "unbekannt"), line=entry.get("line", "linie1"),
            poll_s=float(entry.get("poll_s", 1.0)), nodes=list(entry.get("nodes", [])),
        )
        for entry in raw.get("stations", [])
    ]


def topic_for(cfg: StationConfig, name: str) -> str:
    """Topic-Baum aus SPEC §6.1."""
    return f"zellwerk/v1/{SITE}/{cfg.area}/{cfg.line}/{cfg.station}/pv/{name}"


def quality_of(status) -> str:
    """OPC-UA-StatusCode → UNS-Qualität (SPEC §6.1).

    Der Simulator bildet einen Kanalausfall (F5) als StatusCode `Bad` ab; diese
    Information MUSS erhalten bleiben, sonst kann Playbook 10.2 F5 nicht von F3
    unterscheiden.
    """
    if status is None:
        return "uncertain"
    try:
        if status.is_good():
            return "good"
    except AttributeError:
        return "uncertain"
    # `name` ist z. B. "Bad", "BadOutOfService", "Uncertain..."
    bezeichnung = getattr(status, "name", "") or str(status)
    return "uncertain" if "Uncertain" in bezeichnung else "bad"


class StationBridge:
    def __init__(self, cfg: StationConfig) -> None:
        self.cfg = cfg
        self.nodes: dict[str, ua.Node] = {}
        self._backoff = 1.0
        self.werte_gesamt = 0

    async def run(self, mqtt: aiomqtt.Client) -> None:
        """Hält die Verbindung zu einer Station und publiziert ihre Werte.

        Der Backoff wird erst zurückgesetzt, wenn tatsächlich DATEN geflossen
        sind — nicht schon, wenn die Verbindung steht. Der Unterschied ist
        entscheidend: Steht die Verbindung, scheitert aber jeder Lesevorgang
        sofort, galt das vorher als Erfolg. Der Abstand blieb bei einer Sekunde,
        und der Konnektor reihte über Stunden tausende Wiederverbindungen
        aneinander (gemessen: 7161), während die Datenbank keinen einzigen
        neuen Wert bekam. Nach außen sah alles gesund aus — der Container lief,
        die Logs meldeten im Sekundentakt "verbunden".
        """
        backoff = 1.0
        while True:
            try:
                async with Client(url=self.cfg.endpoint) as client:
                    await self._discover(client)
                    log.info("%s verbunden: %d Knoten", self.cfg.station, len(self.nodes))
                    # KEIN Zurücksetzen hier — erst nach dem ersten Zyklus,
                    # der wirklich Werte geliefert hat (siehe _poll_loop).
                    await self._poll_loop(client, mqtt, on_erfolg=self._backoff_zuruecksetzen)
                    backoff = self._backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                backoff = self._backoff
                log.warning("%s getrennt (%s) — neuer Versuch in %.0fs",
                            self.cfg.station, exc, backoff)
                await asyncio.sleep(backoff)
                self._backoff = min(backoff * 2, 30.0)
                backoff = self._backoff

    def _backoff_zuruecksetzen(self) -> None:
        """Wird gerufen, sobald ein Poll-Zyklus echte Werte geliefert hat."""
        if self._backoff != 1.0:
            log.info("%s liefert wieder Werte", self.cfg.station)
        self._backoff = 1.0

    async def _discover(self, client: Client) -> None:
        """Findet die Variablen der Station im Objektbaum."""
        self.nodes.clear()
        objects = client.nodes.objects
        for child in await objects.get_children():
            name = (await child.read_browse_name()).Name
            if name != self.cfg.station:
                continue
            for var in await child.get_children():
                var_name = (await var.read_browse_name()).Name
                # Leere Knotenliste = alles abonnieren.
                if not self.cfg.nodes or var_name in self.cfg.nodes:
                    self.nodes[var_name] = var

    async def _poll_loop(self, client: Client, mqtt: aiomqtt.Client,
                         on_erfolg=None) -> None:
        """Liest die Knoten im Takt und publiziert sie.

        Zwei Dinge, die hier aus einem konkreten Ausfall gelernt sind:

        * Ein nicht lesbarer Knoten wird ÜBERSPRUNGEN, nicht als `null`
          publiziert. Vorher schrieb ein toter Konnektor stundenlang leere
          Werte in die Datenbank — der Ingest war grün, die Regeln feuerten
          nie, und niemand sah einen Fehler.
        * Antwortet die ganze Station nicht mehr, fliegt eine Exception nach
          oben, damit `run()` die Verbindung neu aufbaut. Fängt man hier alles
          ab, wird der Reconnect nie erreicht.
        """
        while True:
            fehler = 0
            for name, node in self.nodes.items():
                try:
                    # read_data_value statt read_value: nur so kommt der
                    # StatusCode mit, aus dem sich die Qualität ergibt.
                    datenwert = await node.read_data_value()
                    wert = datenwert.Value.Value
                    qualitaet = quality_of(datenwert.StatusCode)
                except ua.uaerrors.UaStatusCodeError as exc:
                    # WICHTIG: asyncua wirft bei einem Bad-StatusCode eine
                    # Exception. Das ist aber KEIN Lesefehler — es ist die
                    # Aussage der Anlage "dieser Kanal liefert nichts". Genau
                    # diese Information macht einen Kanalausfall überhaupt
                    # erkennbar; würde sie hier verschluckt, verstummte der
                    # Kanal nur stillschweigend und wäre von einer echten
                    # Störung nicht mehr zu unterscheiden.
                    wert = None
                    qualitaet = quality_of(ua.StatusCode(exc.code))
                except Exception as exc:  # noqa: BLE001
                    # Alles andere ist ein echter Lesefehler: überspringen und
                    # zählen, damit unten der Reconnect greifen kann.
                    fehler += 1
                    log.warning("Lesefehler %s.%s: %s", self.cfg.station, name, exc)
                    continue

                payload = {
                    "ts": datetime.now(UTC).isoformat(),
                    "value": wert,
                    "quality": qualitaet,
                    "unit": "",
                }
                await mqtt.publish(topic_for(self.cfg, name), json.dumps(payload).encode())

            if self.nodes and fehler == len(self.nodes):
                raise ConnectionError(
                    f"{self.cfg.station}: kein einziger Knoten lesbar — Verbindung erneuern"
                )
            # Erst jetzt gilt die Verbindung als wirklich brauchbar: es sind
            # Werte geflossen, nicht nur ein Handschlag zustande gekommen.
            gelesen = len(self.nodes) - fehler
            if gelesen > 0:
                self.werte_gesamt += gelesen
                if on_erfolg is not None:
                    on_erfolg()
                    on_erfolg = None  # nur beim ersten erfolgreichen Zyklus melden
            await asyncio.sleep(self.cfg.poll_s)


async def bericht(bridges: list[StationBridge]) -> None:
    """Meldet regelmäßig, welche Station liefert und welche nicht.

    Ein Konnektor, der stillschweigend nichts mehr liefert, ist der schlimmste
    Fall: der Container läuft, die Logs sind ruhig, die Dashboards zeigen
    einfach keine neuen Werte mehr. Genau das ist hier acht Stunden lang
    unentdeckt geblieben. Diese Meldung macht es sichtbar.
    """
    letzte = {b.cfg.station: 0 for b in bridges}
    while True:
        await asyncio.sleep(60)
        stumm = []
        zeilen = []
        for b in bridges:
            neu = b.werte_gesamt - letzte[b.cfg.station]
            letzte[b.cfg.station] = b.werte_gesamt
            zeilen.append(f"{b.cfg.station}={neu}")
            if neu == 0:
                stumm.append(b.cfg.station)
        if stumm:
            log.warning("KEINE Werte in der letzten Minute von: %s", ", ".join(stumm))
        else:
            log.info("Werte je Station (letzte Minute): %s", " ".join(zeilen))


async def main() -> None:
    stations = load_config(CONFIG_PATH)
    log.info("Konnektor startet für %d Stationen", len(stations))

    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as mqtt:
                log.info("MQTT verbunden: %s:%d", MQTT_HOST, MQTT_PORT)
                bridges = [StationBridge(cfg) for cfg in stations]
                aufgaben = [asyncio.create_task(b.run(mqtt)) for b in bridges]
                aufgaben.append(asyncio.create_task(bericht(bridges)))
                try:
                    await asyncio.gather(*aufgaben)
                finally:
                    for a in aufgaben:
                        a.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT-Verbindung verloren (%s) — neuer Versuch in 5s", exc)
            await asyncio.sleep(5.0)


if __name__ == "__main__":
    asyncio.run(main())
