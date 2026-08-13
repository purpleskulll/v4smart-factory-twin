#!/usr/bin/env python3
"""Erzeugt die Demo-Dashboards für zellwerk.

Leitgedanke: Das Interessante an diesem System ist nicht, dass Messwerte
laufen — das kann jede Zeitreihendatenbank. Interessant ist, dass ein Agent
aus diesen Werten eine Ursache ableitet und sie BELEGT. Genau das war bisher
nur auf der Kommandozeile sichtbar und damit für eine Demo wertlos.

Drei Ansichten:
  1 Linienübersicht      — die Anlage in Betrieb (bereits vorhanden)
  2 Agenten & Befunde    — was die KI tut, womit sie es belegt
  3 Zellen & Genealogie  — Rückverfolgung, Aufträge, Ausschuss
"""

import json
import pathlib

DS = {"type": "grafana-postgresql-datasource", "uid": "zellwerk-timescale"}
AUS = pathlib.Path(__file__).resolve().parent / "zellwerk" / "dashboards" / "json"


def sql_panel(title, sql, gid, x, y, w, h, typ="table", beschreibung="", options=None):
    p = {
        "type": typ, "title": title, "id": gid, "description": beschreibung,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": DS,
        "targets": [{"refId": "A", "format": "table", "rawQuery": True, "rawSql": sql}],
        "fieldConfig": {"defaults": {}, "overrides": []},
    }
    if options:
        p["options"] = options
    return p


def text_panel(title, inhalt, gid, x, y, w, h):
    return {
        "type": "text", "title": title, "id": gid,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {"mode": "markdown", "content": inhalt},
    }


def dashboard(uid, titel, panels, beschreibung=""):
    return {
        "uid": uid, "title": titel, "description": beschreibung,
        "tags": ["zellwerk"], "timezone": "browser", "schemaVersion": 39,
        "version": 1, "refresh": "10s",
        "time": {"from": "now-3h", "to": "now"},
        "panels": panels,
    }


# ---------------------------------------------------------------------------
# 2 — Agenten & Befunde
# ---------------------------------------------------------------------------

agenten = [
    text_panel("Was hier zu sehen ist", """
Die Agenten arbeiten im **Shadow Mode**: sie untersuchen und schlagen vor —
ausgeführt wird nichts. Jeder Werkzeugaufruf landet im Audit-Log, damit
nachvollziehbar bleibt, worauf ein Vorschlag beruht.

Einen Lauf starten (ohne Terminal): die **Agenten-Adresse** öffnen
(`zellwerk-agenten…`) und dort ein Playbook anklicken. Der Bericht steht
danach dort und zusätzlich unten in dieser Tabelle.

Ein Vorschlag ohne Beleg ist wertlos — deshalb steht in jeder Begründung,
welche Werte, welche Charge und welcher Zeitraum ihn stützen.
""", 1, 0, 0, 24, 4),

    sql_panel(
        "Vorschläge der Agenten — mit ihrer Begründung",
        """SELECT ts AS "Zeitpunkt",
       replace(tool, 'propose_action:', '') AS "Maßnahme",
       params::text AS "Parameter",
       begruendung AS "Begründung (die Evidenz)"
FROM action_log
WHERE tool LIKE 'propose_action%'
ORDER BY ts DESC LIMIT 20""",
        2, 0, 4, 24, 10,
        beschreibung="Der Abschluss jeder Untersuchung. Nichts davon wurde ausgeführt.",
    ),

    sql_panel(
        "Werkzeugketten — was der Agent selbst gewählt hat",
        """SELECT ts AS "Zeitpunkt", tool AS "Werkzeug",
       left(params::text, 80) AS "Parameter", mode AS "Modus"
FROM action_log
WHERE tool NOT LIKE 'playbook%'
ORDER BY ts DESC LIMIT 40""",
        3, 0, 14, 14, 11,
        beschreibung="Die Reihenfolge ist nicht vorgegeben — sie entsteht aus dem, "
                     "was der Agent unterwegs findet.",
    ),

    sql_panel(
        "Werkzeugnutzung insgesamt",
        """SELECT tool AS "Werkzeug", count(*) AS "Aufrufe"
FROM action_log WHERE tool NOT LIKE 'playbook%'
GROUP BY tool ORDER BY count(*) DESC""",
        4, 14, 14, 10, 11,
    ),

    sql_panel(
        "Playbook-Läufe",
        """SELECT ts AS "Zeitpunkt",
       replace(tool, 'playbook:', '') AS "Playbook",
       ergebnis->>'runden' AS "Runden",
       left(begruendung, 400) AS "Bericht (Anfang)"
FROM action_log WHERE tool LIKE 'playbook%'
ORDER BY ts DESC LIMIT 10""",
        5, 0, 25, 24, 9,
    ),
]

# ---------------------------------------------------------------------------
# 3 — Zellen & Genealogie
# ---------------------------------------------------------------------------

genealogie = [
    text_panel("Rückverfolgung", """
Jede Zelle lässt sich bis zur Slurry-Charge im Mischer zurückverfolgen. Das ist
die Grundlage für Ursachensuche **und** für den Batteriepass — dieselbe Kette,
einmal rückwärts und einmal vorwärts gelesen.

Die Merkmale (`traits`) tragen die Werte, mit denen ein Los gefertigt wurde.
Dort steht die Evidenz, auf die sich eine Ursachenaussage stützt.
""", 1, 0, 0, 24, 4),

    sql_panel("Zellen nach Status", """
SELECT status AS "Status", count(*) AS "Anzahl"
FROM cell GROUP BY status ORDER BY count(*) DESC""",
              2, 0, 4, 6, 7, typ="bargauge",
              options={"orientation": "horizontal", "displayMode": "gradient",
                       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "",
                                         "values": True},
                       "showUnfilled": True}),

    sql_panel("Fertigungsaufträge", """
SELECT po.id AS "Auftrag", po.produkt AS "Produkt", po.sollmenge AS "Soll",
       count(c.serial) AS "gefertigt",
       count(*) FILTER (WHERE c.status='ok') AS "in Ordnung",
       count(*) FILTER (WHERE c.status='ausschuss') AS "Ausschuss"
FROM production_order po LEFT JOIN cell c ON c.order_id = po.id
GROUP BY po.id, po.produkt, po.sollmenge ORDER BY po.id""",
              3, 6, 4, 18, 7),

    sql_panel(
        "Auffällige Zellen — mit ihrer Herkunft",
        """SELECT c.serial AS "Seriennummer", c.status AS "Status", c.grade AS "Befund",
       c.order_id AS "Auftrag",
       round((c.traits->>'kapazitaet_ah')::numeric, 3) AS "Kapazität (Ah)",
       round((c.traits->>'dosiermenge_g')::numeric, 3) AS "Elektrolyt (g)",
       round((c.traits->>'vorstufe_porositaet_pct')::numeric, 2) AS "Porosität (%)",
       round((c.traits->>'vorstufe_vorstufe_vorstufe_viskositaet_pas')::numeric, 2) AS "Viskosität (Pa·s)"
FROM cell c
WHERE c.status IN ('ausschuss','quarantaene')
ORDER BY c.created_at DESC LIMIT 25""",
        4, 0, 11, 24, 10,
        beschreibung="Zwei Ursachen erzeugen dasselbe Symptom: zu wenig Kapazität entsteht "
                     "durch zu niedrige Porosität ODER durch zu wenig Elektrolyt. "
                     "Die beiden rechten Spalten trennen die Fälle.",
    ),

    sql_panel(
        "Genealogie — vollständiger Pfad der zuletzt gefertigten Zelle",
        """WITH RECURSIVE juengste AS (
    SELECT serial, lot_id FROM cell ORDER BY created_at DESC LIMIT 1
), pfad AS (
    SELECT j.serial, l.id AS lot_id, l.station, l.material, l.parent_id,
           l.traits, 0 AS tiefe
    FROM juengste j JOIN lot l ON l.id = j.lot_id
  UNION ALL
    SELECT p.serial, l.id, l.station, l.material, l.parent_id, l.traits, p.tiefe+1
    FROM pfad p JOIN lot l ON l.id = p.parent_id
)
SELECT tiefe AS "Stufe", station AS "Station", lot_id AS "Charge",
       material AS "Material", traits::text AS "Merkmale"
FROM pfad ORDER BY tiefe""",
        5, 0, 21, 24, 9,
        beschreibung="Von der Zelle rückwärts bis zur Slurry-Charge.",
    ),

    sql_panel("Chargen je Station", """
SELECT station AS "Station", count(*) AS "Chargen"
FROM lot GROUP BY station ORDER BY station""",
              6, 0, 30, 8, 7),

    sql_panel("Offene Ereignisse", """
SELECT ts AS "Zeitpunkt", asset_id AS "Anlage", severity AS "Schwere",
       code AS "Code", payload->>'wiederholungen' AS "Wiederholungen",
       payload->>'wert' AS "Auslösender Wert"
FROM event WHERE acked = false ORDER BY ts DESC LIMIT 20""",
              7, 8, 30, 16, 7,
              beschreibung="Wiederholungen werden am Eintrag hochgezählt, statt neue Zeilen zu erzeugen."),
]


def main():
    AUS.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "agenten.json": dashboard(
            "zellwerk-agenten", "zellwerk — Agenten & Befunde", agenten,
            "Was die KI-Agenten untersuchen, was sie vorschlagen und womit sie es belegen."),
        "genealogie.json": dashboard(
            "zellwerk-genealogie", "zellwerk — Zellen & Genealogie", genealogie,
            "Rückverfolgung jeder Zelle bis zur Slurry-Charge, Aufträge und Ausschuss."),
    }
    for name, d in dashboards.items():
        pfad = AUS / name
        pfad.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {name:20} {len(d['panels'])} Panels")


if __name__ == "__main__":
    main()
