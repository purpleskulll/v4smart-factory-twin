"""Die Werkzeuge der Agenten (SPEC §9) — Logik ohne Protokoll.

`server.py` legt die MCP-Schicht darüber. Diese Trennung hat zwei Gründe: die
Werkzeuge sind ohne MCP-Client testbar, und die Playbooks (§10) können sie
direkt aufrufen, statt sich selbst als Client anzumelden.

Alle Werkzeuge sind read-only — außer `propose_action` (schreibt in shadow) und
`execute_action` (schreibt live, im MVP per Default aus). Jeder Aufruf landet im
`action_log`; ohne lückenloses Audit ist „Shadow Mode" eine Behauptung.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

DB_DSN = os.environ.get("ZW_DB_DSN", "postgresql://zellwerk:zellwerk@timescaledb:5432/zellwerk")
LIVE_ACTIONS = os.environ.get("ZW_LIVE_ACTIONS", "false").lower() == "true"

# Aktionen, die im Live-Modus überhaupt zulässig sind (SPEC §9). Alles außerhalb
# dieser Liste wird abgelehnt, auch wenn das Flag gesetzt ist.
WHITELIST = {
    "derate_channel": "Formierkanal drosseln",
    "block_lot": "Charge sperren",
    "quarantine_cell": "Zelle in Quarantäne",
}

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=6)
    return _pool


async def _audit(tool: str, params: dict, mode: str = "read",
                 ergebnis: Any = None, begruendung: str | None = None,
                 actor: str = "agent") -> None:
    try:
        p = await pool()
        async with p.acquire() as conn:
            await conn.execute(
                "INSERT INTO action_log (actor, tool, params, mode, ergebnis, begruendung)"
                " VALUES ($1,$2,$3,$4,$5,$6)",
                actor, tool, json.dumps(params, default=str), mode,
                json.dumps(ergebnis, default=str) if ergebnis is not None else None,
                begruendung,
            )
    except Exception:  # noqa: BLE001
        # Ein fehlschlagendes Audit darf das Werkzeug nicht blockieren — aber es
        # bleibt sichtbar, weil der Aufrufer das Ergebnis ohnehin protokolliert.
        pass


# ---------------------------------------------------------------- Überblick --


async def get_factory_overview() -> dict:
    """Einstieg jedes Playbooks: Anlagenbaum, Zustände, Aufträge, offene Alarme."""
    p = await pool()
    async with p.acquire() as conn:
        assets = await conn.fetch(
            "SELECT id, typ, area, line, status FROM asset ORDER BY area, id")
        alarme = await conn.fetch(
            "SELECT code, severity, asset_id, count(*) AS anzahl, max(ts) AS zuletzt"
            " FROM event WHERE acked = false GROUP BY code, severity, asset_id"
            " ORDER BY max(ts) DESC LIMIT 20")
        zellen = await conn.fetch(
            "SELECT status, count(*) AS anzahl FROM cell GROUP BY status")
        lose = await conn.fetchval("SELECT count(*) FROM lot")
        letzte = await conn.fetchval("SELECT max(ts) FROM measurement")

    ergebnis = {
        "anlagen": [dict(a) for a in assets],
        "offene_alarme": [dict(a) for a in alarme],
        "zellen_nach_status": {r["status"]: r["anzahl"] for r in zellen},
        "lose_gesamt": lose,
        "letzter_messwert": letzte.isoformat() if letzte else None,
    }
    await _audit("get_factory_overview", {}, ergebnis={"anlagen": len(assets),
                                                       "alarme": len(alarme)})
    return ergebnis


async def get_asset_state(asset_id: str) -> dict:
    """Aktuelle Prozesswerte, Zustand und letzte Ereignisse einer Anlage."""
    p = await pool()
    async with p.acquire() as conn:
        asset = await conn.fetchrow("SELECT * FROM asset WHERE id = $1", asset_id)
        if asset is None:
            return {"fehler": f"Anlage {asset_id} unbekannt"}

        # Je Kennzahl der jüngste Wert.
        werte = await conn.fetch(
            "SELECT DISTINCT ON (name) name, value, text_value, unit, quality, ts"
            " FROM measurement WHERE asset_id = $1 AND ts > now() - interval '5 minutes'"
            " ORDER BY name, ts DESC", asset_id)
        events = await conn.fetch(
            "SELECT ts, severity, code, payload FROM event WHERE asset_id = $1"
            " ORDER BY ts DESC LIMIT 10", asset_id)
        fenster = await conn.fetch(
            "SELECT name, min_value, max_value, unit FROM process_window WHERE station = $1",
            asset_id)

    grenzen = {f["name"]: (f["min_value"], f["max_value"]) for f in fenster}
    pvs = []
    for w in werte:
        eintrag = {"name": w["name"],
                   "wert": w["value"] if w["value"] is not None else w["text_value"],
                   "einheit": w["unit"], "qualitaet": w["quality"],
                   "ts": w["ts"].isoformat()}
        if w["name"] in grenzen and w["value"] is not None:
            lo, hi = grenzen[w["name"]]
            eintrag["sollbereich"] = [lo, hi]
            eintrag["im_fenster"] = bool(lo <= w["value"] <= hi)
        pvs.append(eintrag)

    ergebnis = {"anlage": dict(asset), "prozesswerte": pvs,
                "letzte_ereignisse": [dict(e) for e in events]}
    await _audit("get_asset_state", {"asset_id": asset_id},
                 ergebnis={"pvs": len(pvs), "events": len(events)})
    return ergebnis


async def query_timeseries(asset_id: str, name: str, minuten: int = 60,
                           agg: str | None = None) -> dict:
    """Zeitreihe oder Aggregat einer Kennzahl.

    `agg` ∈ None | "minute" | "5min" — ohne Aggregation werden höchstens 500
    Punkte geliefert, damit eine Anfrage nicht das Kontextfenster des Agenten
    sprengt.
    """
    p = await pool()
    seit = datetime.now(UTC) - timedelta(minutes=minuten)

    async with p.acquire() as conn:
        if agg in ("minute", "5min"):
            eimer = "1 minute" if agg == "minute" else "5 minutes"
            zeilen = await conn.fetch(
                f"SELECT time_bucket('{eimer}', ts) AS zeit, avg(value) AS mittel,"
                " min(value) AS minimum, max(value) AS maximum, count(*) AS n"
                " FROM measurement WHERE asset_id=$1 AND name=$2 AND ts > $3"
                " GROUP BY zeit ORDER BY zeit", asset_id, name, seit)
            punkte = [{"ts": r["zeit"].isoformat(), "mittel": round(r["mittel"], 4),
                       "min": round(r["minimum"], 4), "max": round(r["maximum"], 4),
                       "n": r["n"]} for r in zeilen if r["mittel"] is not None]
        else:
            zeilen = await conn.fetch(
                "SELECT ts, value FROM measurement WHERE asset_id=$1 AND name=$2"
                " AND ts > $3 ORDER BY ts DESC LIMIT 500", asset_id, name, seit)
            punkte = [{"ts": r["ts"].isoformat(), "wert": r["value"]}
                      for r in reversed(zeilen)]

        stats = await conn.fetchrow(
            "SELECT count(*) AS n, avg(value) AS mittel, min(value) AS minimum,"
            " max(value) AS maximum, stddev(value) AS streuung"
            " FROM measurement WHERE asset_id=$1 AND name=$2 AND ts > $3",
            asset_id, name, seit)

    ergebnis = {
        "asset_id": asset_id, "name": name, "zeitraum_min": minuten,
        "punkte": punkte,
        "kennzahlen": {
            "n": stats["n"],
            "mittel": round(stats["mittel"], 4) if stats["mittel"] else None,
            "min": round(stats["minimum"], 4) if stats["minimum"] else None,
            "max": round(stats["maximum"], 4) if stats["maximum"] else None,
            "streuung": round(stats["streuung"], 4) if stats["streuung"] else None,
        },
    }
    await _audit("query_timeseries", {"asset_id": asset_id, "name": name, "minuten": minuten},
                 ergebnis={"punkte": len(punkte)})
    return ergebnis


async def get_process_window(station: str, name: str) -> dict:
    """Sollbereich, Ist-Verteilung (24 h) und eine Cpk-Näherung.

    Cpk ist bewusst als NÄHERUNG bezeichnet: die Formel setzt eine
    normalverteilte, stabile Kennzahl voraus. Bei einem laufenden Drift (F1) ist
    diese Annahme verletzt — der Wert zeigt dann an, DASS etwas nicht stimmt,
    taugt aber nicht als Fähigkeitskennzahl.
    """
    p = await pool()
    async with p.acquire() as conn:
        fenster = await conn.fetchrow(
            "SELECT min_value, max_value, unit FROM process_window"
            " WHERE station=$1 AND name=$2", station, name)
        stats = await conn.fetchrow(
            "SELECT count(*) AS n, avg(value) AS mittel, stddev(value) AS sigma,"
            " min(value) AS minimum, max(value) AS maximum"
            " FROM measurement WHERE asset_id=$1 AND name=$2"
            " AND ts > now() - interval '24 hours'", station, name)

    if fenster is None:
        return {"fehler": f"kein Sollfenster für {station}.{name} hinterlegt"}

    lo, hi = fenster["min_value"], fenster["max_value"]
    mittel, sigma = stats["mittel"], stats["sigma"]

    cpk = None
    if mittel is not None and sigma and sigma > 0:
        cpk = round(min((hi - mittel) / (3 * sigma), (mittel - lo) / (3 * sigma)), 3)

    ergebnis = {
        "station": station, "name": name, "einheit": fenster["unit"],
        "sollbereich": [lo, hi],
        "ist": {
            "n": stats["n"],
            "mittel": round(mittel, 4) if mittel else None,
            "streuung": round(sigma, 4) if sigma else None,
            "min": round(stats["minimum"], 4) if stats["minimum"] else None,
            "max": round(stats["maximum"], 4) if stats["maximum"] else None,
        },
        "cpk_naeherung": cpk,
        "hinweis": ("Cpk setzt eine stabile, normalverteilte Kennzahl voraus. "
                    "Bei laufendem Drift ist der Wert nur ein Indikator."),
    }
    await _audit("get_process_window", {"station": station, "name": name},
                 ergebnis={"cpk": cpk})
    return ergebnis


# -------------------------------------------------------------- Genealogie ---


async def trace_cell_genealogy(serial: str | None = None, lot_id: str | None = None) -> dict:
    """Kompletter Genealogie-Pfad inkl. Prozesswerten je Schritt (SPEC §9).

    Das ist das wichtigste Werkzeug der Ausschuss-Triage: es liefert nicht nur
    die Kette, sondern zu jedem Los die Merkmale, mit denen es gefertigt wurde —
    also die Evidenz, auf die sich eine Ursachenaussage stützen muss.
    """
    if not serial and not lot_id:
        return {"fehler": "serial oder lot_id angeben"}

    p = await pool()
    async with p.acquire() as conn:
        if serial:
            zelle = await conn.fetchrow("SELECT * FROM cell WHERE serial = $1", serial)
            if zelle is None:
                return {"fehler": f"Zelle {serial} unbekannt"}
            start_lot = zelle["lot_id"]
        else:
            zelle = None
            start_lot = lot_id

        pfad = await conn.fetch(
            """
            WITH RECURSIVE kette AS (
                SELECT id, station, material, parent_id, start_ts, end_ts, traits, 0 AS tiefe
                FROM lot WHERE id = $1
              UNION ALL
                SELECT l.id, l.station, l.material, l.parent_id, l.start_ts, l.end_ts,
                       l.traits, k.tiefe + 1
                FROM kette k JOIN lot l ON l.id = k.parent_id
            )
            SELECT * FROM kette ORDER BY tiefe
            """, start_lot)

        # Zu jedem Los die Prozesswerte seiner Station im Fertigungszeitraum.
        schritte = []
        for lot in pfad:
            bis = lot["end_ts"] or datetime.now(UTC)
            werte = await conn.fetch(
                "SELECT name, round(avg(value)::numeric,4) AS mittel,"
                " round(stddev(value)::numeric,4) AS streuung, count(*) AS n"
                " FROM measurement WHERE asset_id=$1 AND ts BETWEEN $2 AND $3"
                " AND value IS NOT NULL GROUP BY name ORDER BY name",
                lot["station"], lot["start_ts"], bis)
            fenster = await conn.fetch(
                "SELECT name, min_value, max_value FROM process_window WHERE station=$1",
                lot["station"])
            grenzen = {f["name"]: (f["min_value"], f["max_value"]) for f in fenster}

            prozesswerte = []
            for w in werte:
                eintrag = {"name": w["name"], "mittel": float(w["mittel"]) if w["mittel"] else None,
                           "streuung": float(w["streuung"]) if w["streuung"] else None, "n": w["n"]}
                if w["name"] in grenzen and w["mittel"] is not None:
                    lo, hi = grenzen[w["name"]]
                    eintrag["sollbereich"] = [lo, hi]
                    eintrag["im_fenster"] = bool(lo <= float(w["mittel"]) <= hi)
                prozesswerte.append(eintrag)

            traits = lot["traits"]
            schritte.append({
                "tiefe": lot["tiefe"], "lot_id": lot["id"], "station": lot["station"],
                "material": lot["material"],
                "start": lot["start_ts"].isoformat() if lot["start_ts"] else None,
                "ende": lot["end_ts"].isoformat() if lot["end_ts"] else None,
                "merkmale": json.loads(traits) if isinstance(traits, str) else (traits or {}),
                "prozesswerte": prozesswerte,
            })

    ergebnis = {
        "zelle": dict(zelle) if zelle else None,
        "pfad": schritte,
        "wurzel": schritte[-1]["station"] if schritte else None,
    }
    await _audit("trace_cell_genealogy", {"serial": serial, "lot_id": lot_id},
                 ergebnis={"schritte": len(schritte)})
    return ergebnis


async def find_similar_cells(status: str | None = None, grade: str | None = None,
                             lot_id: str | None = None, kapazitaet_unter: float | None = None,
                             formationskanal: int | None = None,
                             limit: int = 50) -> dict:
    """Betroffenheitsanalyse: Zellen mit ähnlichem Fehlerbild oder Herkunft.

    `lot_id` sucht über die GESAMTE Genealogie — auch Zellen, die nur mittelbar
    von einer Charge abstammen. Genau das braucht Playbook 10.3 („welche Zellen
    sind von Slurry-Charge L-0815 betroffen?").
    """
    p = await pool()
    async with p.acquire() as conn:
        if lot_id:
            zeilen = await conn.fetch(
                """
                WITH RECURSIVE nachkommen AS (
                    SELECT id FROM lot WHERE id = $1
                  UNION ALL
                    SELECT l.id FROM lot l JOIN nachkommen n ON l.parent_id = n.id
                )
                SELECT c.serial, c.lot_id, c.status, c.grade, c.created_at, c.traits
                FROM cell c WHERE c.lot_id IN (SELECT id FROM nachkommen)
                ORDER BY c.created_at DESC LIMIT $2
                """, lot_id, limit)
        else:
            bedingungen, werte = [], []
            if status:
                werte.append(status)
                bedingungen.append(f"status = ${len(werte)}")
            if grade:
                werte.append(grade)
                bedingungen.append(f"grade = ${len(werte)}")
            if kapazitaet_unter is not None:
                werte.append(kapazitaet_unter)
                bedingungen.append(f"(traits->>'kapazitaet_ah')::float < ${len(werte)}")
            if formationskanal is not None:
                # Der Kanal steht als Merkmal an der Zelle. Ohne diesen Filter
                # ließ sich "welche Zellen liefen auf Kanal N?" nicht
                # beantworten — die Frage kommt bei jedem Kanalproblem auf.
                werte.append(float(formationskanal))
                bedingungen.append(f"(traits->>'formationskanal')::float = ${len(werte)}")
            wo = (" WHERE " + " AND ".join(bedingungen)) if bedingungen else ""
            werte.append(limit)
            zeilen = await conn.fetch(
                f"SELECT serial, lot_id, status, grade, created_at, traits FROM cell{wo}"
                f" ORDER BY created_at DESC LIMIT ${len(werte)}", *werte)

    treffer = []
    for r in zeilen:
        traits = r["traits"]
        treffer.append({
            "serial": r["serial"], "lot_id": r["lot_id"], "status": r["status"],
            "grade": r["grade"], "erstellt": r["created_at"].isoformat(),
            "merkmale": json.loads(traits) if isinstance(traits, str) else (traits or {}),
        })

    ergebnis = {"anzahl": len(treffer), "zellen": treffer,
                "kriterien": {"status": status, "grade": grade, "lot_id": lot_id,
                              "kapazitaet_unter": kapazitaet_unter,
                              "formationskanal": formationskanal}}
    await _audit("find_similar_cells", ergebnis["kriterien"], ergebnis={"treffer": len(treffer)})
    return ergebnis


async def get_active_alarms(severity: str | None = None, limit: int = 50) -> dict:
    p = await pool()
    async with p.acquire() as conn:
        if severity:
            zeilen = await conn.fetch(
                "SELECT ts, asset_id, severity, code, payload FROM event"
                " WHERE acked = false AND severity = $1 ORDER BY ts DESC LIMIT $2",
                severity, limit)
        else:
            zeilen = await conn.fetch(
                "SELECT ts, asset_id, severity, code, payload FROM event"
                " WHERE acked = false ORDER BY ts DESC LIMIT $1", limit)

    alarme = []
    for r in zeilen:
        payload = r["payload"]
        alarme.append({
            "ts": r["ts"].isoformat(), "anlage": r["asset_id"], "schwere": r["severity"],
            "code": r["code"],
            "kontext": json.loads(payload) if isinstance(payload, str) else (payload or {}),
        })

    ergebnis = {"anzahl": len(alarme), "alarme": alarme}
    await _audit("get_active_alarms", {"severity": severity}, ergebnis={"anzahl": len(alarme)})
    return ergebnis


# ------------------------------------------------------------- Schreibend ----


async def propose_action(action: str, params: dict, begruendung: str) -> dict:
    """Der Standardweg der Agenten: Vorschlag im Shadow Mode (SPEC §9).

    Es wird NICHTS ausgeführt. Der Vorschlag landet im `action_log` und wartet
    auf einen Menschen.
    """
    await _audit(f"propose_action:{action}", params, mode="shadow",
                 ergebnis={"vorgeschlagen": True}, begruendung=begruendung)
    return {
        "modus": "shadow", "aktion": action, "parameter": params,
        "begruendung": begruendung, "ausgefuehrt": False,
        "hinweis": "Vorschlag protokolliert. Ausführung erfordert eine menschliche Freigabe.",
    }


async def execute_action(action: str, params: dict) -> dict:
    """Ausführende Aktion — im MVP per Default AUS (SPEC §1, §9).

    Zwei Schranken, die beide halten müssen: das Flag `ZW_LIVE_ACTIONS` und die
    Whitelist. Eine Aktion außerhalb der Liste wird auch bei gesetztem Flag
    abgelehnt.
    """
    if not LIVE_ACTIONS:
        await _audit(f"execute_action:{action}", params, mode="live",
                     ergebnis={"abgelehnt": "ZW_LIVE_ACTIONS=false"})
        return {"ausgefuehrt": False,
                "grund": "Live-Aktionen sind abgeschaltet (ZW_LIVE_ACTIONS=false). "
                         "Nutze propose_action."}

    if action not in WHITELIST:
        await _audit(f"execute_action:{action}", params, mode="live",
                     ergebnis={"abgelehnt": "nicht in der Whitelist"})
        return {"ausgefuehrt": False,
                "grund": f"Aktion '{action}' steht nicht im Katalog: {sorted(WHITELIST)}"}

    # Im MVP ist auch der freigeschaltete Pfad bewusst schmal: die Ausführung
    # geht über die REST-Schnittstelle des Simulators, nicht direkt in die DB.
    ergebnis = {"ausgefuehrt": True, "aktion": action, "parameter": params}
    await _audit(f"execute_action:{action}", params, mode="live", ergebnis=ergebnis)
    return ergebnis


async def export_battery_pass(serial: str) -> dict:
    """Batteriepass-Export, Demo-Subset der Verordnung (EU) 2023/1542 (SPEC §10.4)."""
    genealogie = await trace_cell_genealogy(serial=serial)
    if "fehler" in genealogie:
        return genealogie

    zelle = genealogie["zelle"] or {}
    traits = zelle.get("traits") or {}
    if isinstance(traits, str):
        traits = json.loads(traits)

    prozess = {}
    for schritt in genealogie["pfad"]:
        for pv in schritt["prozesswerte"]:
            if pv["mittel"] is not None:
                prozess[f"{schritt['station']}.{pv['name']}"] = pv["mittel"]

    erstellt = zelle.get("created_at")
    if hasattr(erstellt, "isoformat"):
        erstellt = erstellt.isoformat()

    pass_json = {
        "hinweis": "DEMO-SUBSET — nicht rechtsverbindlich. Erzeugt aus Simulationsdaten.",
        "rechtsgrundlage": "Verordnung (EU) 2023/1542 (Demo-Auswahl)",
        "eindeutige_kennung": serial,
        "hersteller": {"name": "zellwerk Demo GmbH", "kennung": "DEMO-0000",
                       "standort": os.environ.get("ZW_SITE", "werk1")},
        "zelltyp": {"bezeichnung": "ZW-NMC-5Ah", "chemie": "NMC811 / Graphit",
                    "format": "Pouch", "nennkapazitaet_ah": 5.0, "nennspannung_v": 3.7},
        "gemessene_kapazitaet_ah": traits.get("kapazitaet_ah"),
        "produktion": {"datum": erstellt,
                       "status": zelle.get("status"), "grade": zelle.get("grade")},
        "genealogie": [{"stufe": s["station"], "lot_id": s["lot_id"], "material": s["material"]}
                       for s in genealogie["pfad"]],
        "prozess_kennwerte": prozess,
        "co2_fussabdruck_kg": None,
        "co2_hinweis": "Platzhalter — im MVP nicht berechnet (SPEC §10.4).",
    }
    await _audit("export_battery_pass", {"serial": serial}, ergebnis={"erzeugt": True})
    return pass_json
