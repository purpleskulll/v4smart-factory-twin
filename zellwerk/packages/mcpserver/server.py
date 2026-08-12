"""MCP-Server: macht die Werkzeuge aus `tools.py` für KI-Clients nutzbar (SPEC §9).

Diese Datei enthält bewusst KEINE Logik — nur Protokoll und Beschreibungen. Die
Beschreibungen sind allerdings nicht Beiwerk: sie sind das, woran ein Agent
entscheidet, welches Werkzeug er greift. Eine unklare Beschreibung führt
zuverlässig zu falschen Aufrufen.

Start (stdio, für Claude Desktop/Code):
    python -m mcpserver.server
Start (HTTP/SSE, für Dienste im Compose-Netz):
    python -m mcpserver.server --http
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import tools

mcp = FastMCP("zellwerk")


@mcp.tool()
async def get_factory_overview() -> dict:
    """Überblick über die gesamte Fabrik: Anlagen und ihre Zustände, laufende
    Aufträge, offene Alarme, Zellen nach Status.

    Startpunkt für jede Untersuchung. Von hier aus entscheidet sich, welche
    Anlage oder welche Charge man genauer ansieht.
    """
    return await tools.get_factory_overview()


@mcp.tool()
async def get_asset_state(asset_id: str) -> dict:
    """Detailblick auf EINE Anlage: aktuelle Prozesswerte mit Sollbereich und
    Angabe, ob sie im Fenster liegen, plus die letzten Ereignisse.

    asset_id ist eine der Stationen: mixer01, coater01, calender01, assembly01,
    filling01, formation01.
    """
    return await tools.get_asset_state(asset_id)


@mcp.tool()
async def query_timeseries(asset_id: str, name: str, minuten: int = 60,
                           agg: str | None = None) -> dict:
    """Zeitlicher Verlauf einer einzelnen Kennzahl, mit Kennwerten (Mittel,
    Streuung, Min, Max).

    Für einen Trend über längere Zeiträume `agg="minute"` oder `agg="5min"`
    verwenden — sonst werden höchstens 500 Einzelpunkte geliefert.
    Nützlich, um einen Drift zu belegen: nicht der aktuelle Wert zählt, sondern
    seine Entwicklung.
    """
    return await tools.query_timeseries(asset_id, name, minuten, agg)


@mcp.tool()
async def get_process_window(station: str, name: str) -> dict:
    """Beantwortet: Läuft diese Kennzahl im Sollfenster?

    Liefert den hinterlegten Sollbereich, die Ist-Verteilung der letzten 24 h
    und eine Cpk-Näherung. Achtung: Cpk setzt eine stabile Kennzahl voraus; bei
    laufendem Drift ist der Wert nur ein Indikator, keine Fähigkeitskennzahl.
    """
    return await tools.get_process_window(station, name)


@mcp.tool()
async def trace_cell_genealogy(serial: str | None = None, lot_id: str | None = None) -> dict:
    """Verfolgt eine Zelle rückwärts durch die gesamte Fertigung — bis zur
    Slurry-Charge im Mischer.

    Liefert je Fertigungsstufe: das Los, den Zeitraum, die Merkmale, mit denen
    es gefertigt wurde, UND die Prozesswerte der Station in genau diesem
    Zeitraum, jeweils mit Sollbereich und Fenster-Kennung.

    Das ist das entscheidende Werkzeug für Ursachensuche: Zwei verschiedene
    Ursachen können dasselbe Endsymptom erzeugen (zu wenig Kapazität entsteht
    sowohl durch zu niedrige Porosität als auch durch Elektrolyt-Unterdosierung).
    Unterscheiden lassen sie sich nur über diesen Pfad.
    """
    return await tools.trace_cell_genealogy(serial, lot_id)


@mcp.tool()
async def find_similar_cells(status: str | None = None, grade: str | None = None,
                             lot_id: str | None = None,
                             kapazitaet_unter: float | None = None,
                             limit: int = 50) -> dict:
    """Findet Zellen mit ähnlichem Fehlerbild oder gemeinsamer Herkunft.

    Mit `lot_id` werden ALLE Zellen gefunden, die von dieser Charge abstammen —
    auch mittelbar über mehrere Fertigungsstufen. Das ist der Weg zur
    Betroffenheitsanalyse ("welche Zellen sind von Charge X betroffen und wo
    sind sie jetzt?").

    status: in_prozess | ok | ausschuss | quarantaene
    """
    return await tools.find_similar_cells(status, grade, lot_id, kapazitaet_unter, limit)


@mcp.tool()
async def get_active_alarms(severity: str | None = None, limit: int = 50) -> dict:
    """Offene, noch nicht quittierte Ereignisse mit ihrem Kontext.

    severity: info | warn | alarm
    """
    return await tools.get_active_alarms(severity, limit)


@mcp.tool()
async def propose_action(action: str, params: dict, begruendung: str) -> dict:
    """Schlägt eine Maßnahme VOR, ohne sie auszuführen (Shadow Mode).

    Das ist der vorgesehene Abschluss einer Untersuchung. Der Vorschlag wird
    protokolliert und wartet auf eine menschliche Freigabe — es passiert nichts
    an der Anlage.

    Die Begründung muss die Evidenz nennen, auf der der Vorschlag beruht
    (welche Werte, welche Charge, welcher Zeitraum). Ein Vorschlag ohne Beleg
    ist für den Empfänger wertlos.

    Übliche Aktionen: block_lot, quarantine_cell, derate_channel,
    check_recipe, calibrate_pump
    """
    return await tools.propose_action(action, params, begruendung)


@mcp.tool()
async def execute_action(action: str, params: dict) -> dict:
    """Führt eine Maßnahme TATSÄCHLICH aus. Im MVP standardmäßig abgeschaltet.

    Nur zulässig, wenn ZW_LIVE_ACTIONS=true gesetzt ist UND die Aktion im
    Whitelist-Katalog steht. Im Normalfall ist propose_action das richtige
    Werkzeug.
    """
    return await tools.execute_action(action, params)


@mcp.tool()
async def export_battery_pass(serial: str) -> dict:
    """Erzeugt den Batteriepass einer Zelle als JSON (Demo-Subset der
    Verordnung (EU) 2023/1542).

    Enthält Kennung, Hersteller, Zelltyp, gemessene Kapazität, Produktionsdaten,
    die vollständige Genealogie und die Prozess-Kennwerte je Fertigungsstufe.
    Ausdrücklich als Demo gekennzeichnet und nicht rechtsverbindlich.
    """
    return await tools.export_battery_pass(serial)


def main() -> None:
    if "--http" in sys.argv:
        # Für Dienste im Compose-Netz (z. B. die Playbooks).
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = 8765
        mcp.run(transport="sse")
    else:
        # Für Claude Desktop / Claude Code.
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
