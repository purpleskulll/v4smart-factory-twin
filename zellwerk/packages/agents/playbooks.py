"""Agenten-Playbooks (SPEC §10).

Jedes Playbook hat einen festen Ablauf, einen klaren Auslöser und ein klares
Endartefakt. Ausgabe immer: Befund, Evidenz, Empfehlung, Konfidenz.

Zwei Leitplanken, die in jedem System-Prompt stehen:

* **Keine Behauptung ohne Beleg.** Ein Agent, der eine Ursache nennt, ohne den
  Messwert dazu zu zeigen, ist in der Fertigung wertlos — niemand sperrt eine
  Charge auf ein Bauchgefühl hin.
* **Shadow Mode.** Der Abschluss ist ein Vorschlag über `propose_action`, nie
  eine Ausführung. Das ist keine Formalie: der Agent sieht einen Ausschnitt,
  der Mensch entscheidet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from mcpserver import tools

from .llm import LLM, Lauf, tool_schema

# ---------------------------------------------------------------------------
# Werkzeugkatalog für die Agenten (Teilmenge von SPEC §9)
# ---------------------------------------------------------------------------

TOOLS = [
    tool_schema(
        "get_factory_overview",
        "Überblick über die Fabrik: Anlagen, Zustände, offene Alarme, Zellen nach "
        "Status. Startpunkt jeder Untersuchung.",
        {},
    ),
    tool_schema(
        "get_asset_state",
        "Detailblick auf EINE Anlage: aktuelle Prozesswerte mit Sollbereich und "
        "Kennzeichnung, ob sie im Fenster liegen, plus letzte Ereignisse.",
        {"asset_id": {"type": "string",
                      "description": "mixer01 | coater01 | calender01 | assembly01 | "
                                     "filling01 | formation01"}},
        ["asset_id"],
    ),
    tool_schema(
        "query_timeseries",
        "Zeitlicher Verlauf einer Kennzahl mit Kennwerten (Mittel, Streuung, Min, Max). "
        "Für einen Trend agg='minute' oder agg='5min' verwenden. Ein Drift zeigt sich "
        "im Verlauf, nicht im Momentanwert.",
        {"asset_id": {"type": "string"}, "name": {"type": "string"},
         "minuten": {"type": "integer", "description": "Rückblick in Minuten, Standard 60"},
         "agg": {"type": "string", "description": "minute | 5min (optional)"}},
        ["asset_id", "name"],
    ),
    tool_schema(
        "get_process_window",
        "Läuft eine Kennzahl im Sollfenster? Liefert Sollbereich, Ist-Verteilung "
        "der letzten 24 h und eine Cpk-Näherung.",
        {"station": {"type": "string"}, "name": {"type": "string"}},
        ["station", "name"],
    ),
    tool_schema(
        "trace_cell_genealogy",
        "Verfolgt eine Zelle rückwärts durch die gesamte Fertigung bis zur "
        "Slurry-Charge. Liefert je Stufe das Los, seine Merkmale UND die "
        "Prozesswerte der Station im Fertigungszeitraum. Entscheidend, wenn "
        "mehrere Ursachen dasselbe Endsymptom erzeugen können.",
        {"serial": {"type": "string", "description": "Seriennummer, z. B. ZW-2026-000123"},
         "lot_id": {"type": "string", "description": "alternativ eine Los-Nummer"}},
    ),
    tool_schema(
        "find_similar_cells",
        "Findet Zellen mit ähnlichem Fehlerbild oder gemeinsamer Herkunft. Mit "
        "lot_id werden ALLE Nachkommen einer Charge gefunden, auch mittelbare; mit "
        "formationskanal alle Zellen, die auf einem bestimmten Kanal formiert wurden.",
        {"status": {"type": "string", "description": "in_prozess | ok | ausschuss | quarantaene"},
         "grade": {"type": "string"}, "lot_id": {"type": "string"},
         "kapazitaet_unter": {"type": "number"},
         "formationskanal": {"type": "integer",
                             "description": "Zellen, die auf diesem Formierkanal liefen"},
         "limit": {"type": "integer"}},
    ),
    tool_schema(
        "get_active_alarms",
        "Offene, nicht quittierte Ereignisse mit Kontext.",
        {"severity": {"type": "string", "description": "info | warn | alarm"},
         "limit": {"type": "integer"}},
    ),
    tool_schema(
        "propose_action",
        "Schlägt eine Maßnahme VOR, ohne sie auszuführen. Die Begründung MUSS die "
        "Evidenz nennen (welche Werte, welche Charge, welcher Zeitraum).",
        {"action": {"type": "string",
                    "description": "block_lot | quarantine_cell | derate_channel | "
                                   "check_recipe | calibrate_pump | maintenance_request"},
         "params": {"type": "object"},
         "begruendung": {"type": "string"}},
        ["action", "params", "begruendung"],
    ),
]

DISPATCH = {
    "get_factory_overview": lambda a: tools.get_factory_overview(),
    "get_asset_state": lambda a: tools.get_asset_state(a["asset_id"]),
    "query_timeseries": lambda a: tools.query_timeseries(
        a["asset_id"], a["name"], a.get("minuten", 60), a.get("agg")),
    "get_process_window": lambda a: tools.get_process_window(a["station"], a["name"]),
    "trace_cell_genealogy": lambda a: tools.trace_cell_genealogy(a.get("serial"), a.get("lot_id")),
    "find_similar_cells": lambda a: tools.find_similar_cells(
        a.get("status"), a.get("grade"), a.get("lot_id"),
        a.get("kapazitaet_unter"), a.get("formationskanal"), a.get("limit", 50)),
    "get_active_alarms": lambda a: tools.get_active_alarms(a.get("severity"), a.get("limit", 50)),
    "propose_action": lambda a: tools.propose_action(
        a["action"], a.get("params", {}), a["begruendung"]),
    "export_battery_pass": lambda a: tools.export_battery_pass(a["serial"]),
}


async def dispatch(name: str, eingabe: dict):
    fn = DISPATCH.get(name)
    if fn is None:
        return {"fehler": f"unbekanntes Werkzeug: {name}"}
    return await fn(eingabe)


BASIS_SYSTEM = """Du bist ein Diagnose-Agent in einer Lithium-Ionen-Zellfertigung.

Die Linie hat sechs Stationen in dieser Reihenfolge:
  mixer01 (Mischen) → coater01 (Beschichten) → calender01 (Kalandrieren)
  → assembly01 (Wickeln/Stapeln) → filling01 (Elektrolyt) → formation01 (Formierung)

Material fließt in dieser Richtung. Ein Fehler an einer frühen Station wird oft
erst an einer späten sichtbar — deshalb ist die Genealogie dein wichtigstes
Werkzeug.

ARBEITSWEISE — nicht verhandelbar:
1. Keine Aussage ohne Beleg. Nenne zu jeder Behauptung den Messwert, die Charge
   und den Zeitraum, aus dem du sie ableitest. Hast du etwas nicht geprüft, sage
   das, statt es zu vermuten.
2. Unterscheide, was du GEMESSEN hast, von dem, was du DARAUS SCHLIESST.
3. Prüfe die naheliegende Erklärung UND mindestens eine Alternative. Mehrere
   Ursachen können dasselbe Symptom erzeugen; welche es war, entscheidet die
   Genealogie, nicht die Plausibilität.
4. Du führst nichts aus. Dein Abschluss ist ein Vorschlag über propose_action.
5. Nach dem propose_action-Aufruf folgt IMMER noch dein vollständiger Bericht im
   unten stehenden Format. Ein Werkzeugaufruf ist kein Bericht — der Empfänger
   sieht nur deinen Text.

ANTWORTFORMAT (immer diese vier Abschnitte, auf Deutsch):
BEFUND — was ist der Fall, in ein bis zwei Sätzen
EVIDENZ — die konkreten Werte mit Station, Charge und Zeitbezug
EMPFEHLUNG — was zu tun ist, und warum genau das
KONFIDENZ — hoch/mittel/niedrig, mit Begründung; nenne, was deine Aussage
            widerlegen oder erhärten würde"""


@dataclass
class PlaybookErgebnis:
    playbook: str
    bericht: str
    evidenz: list[dict]
    runden: int
    abgebrochen: bool

    def as_dict(self) -> dict:
        return {"playbook": self.playbook, "bericht": self.bericht,
                "evidenz": self.evidenz, "runden": self.runden,
                "abgebrochen": self.abgebrochen}


class Playbook:
    name = "basis"
    aufgabe = ""
    system = BASIS_SYSTEM

    def __init__(self, llm: LLM | None = None) -> None:
        self.llm = llm or LLM()

    def frage(self, **kwargs) -> str:
        return self.aufgabe.format(**kwargs)

    async def run(self, **kwargs) -> PlaybookErgebnis:
        lauf: Lauf = await self.llm.run_with_tools(
            system=self.system, aufgabe=self.frage(**kwargs),
            tools=TOOLS, dispatch=dispatch,
        )
        await tools._audit(
            f"playbook:{self.name}", kwargs, mode="shadow",
            ergebnis={"runden": lauf.runden, "werkzeuge": [a.name for a in lauf.aufrufe]},
            begruendung=lauf.antwort[:2000],
        )
        return PlaybookErgebnis(self.name, lauf.antwort, lauf.evidenz(),
                                lauf.runden, lauf.abgebrochen)


# ---------------------------------------------------------------------------
# 10.1 Ausschuss-Triage
# ---------------------------------------------------------------------------


class AusschussTriage(Playbook):
    """Akzeptanz (SPEC §10.1): findet bei F1 und F4 die richtige Wurzelstation."""

    name = "ausschuss_triage"
    aufgabe = """In der Formierung fallen Zellen durch den Kapazitätstest.

Finde die VERURSACHENDE Station und belege sie.

Vorgehen:
1. Verschaffe dir einen Überblick und sieh dir die offenen Alarme an.
2. Finde betroffene Zellen (Status ausschuss oder auffällig niedrige Kapazität).
3. Verfolge mindestens eine davon über die Genealogie zurück und vergleiche die
   Prozesswerte JEDER vorgelagerten Stufe mit ihrem Sollfenster.
4. Achtung, das ist der Kern der Aufgabe: Ein Kapazitätsdefizit kann mindestens
   zwei verschiedene Ursachen haben —
     * zu niedrige Porosität nach dem Kalandrieren (Ursprung meist eine
       Viskositätsdrift im Mischer), oder
     * zu wenig Elektrolyt bei der Befüllung.
   Beide sehen in der Formierung gleich aus. Prüfe BEIDE Pfade und entscheide
   anhand der Messwerte, welcher zutrifft. Schließe den anderen ausdrücklich aus.
5. Schlage über propose_action eine Maßnahme vor.

Nutze die Werkzeuge, rate nicht."""


# ---------------------------------------------------------------------------
# 10.2 Formierungs-Anomalie
# ---------------------------------------------------------------------------


class FormierungsAnomalie(Playbook):
    """Akzeptanz (SPEC §10.2): F3 und F5 werden korrekt unterschiedlich klassifiziert."""

    name = "formierungs_anomalie"
    aufgabe = """In der Formierung (formation01) gibt es eine Auffälligkeit.

Kläre, ob ein ANLAGENPROBLEM oder ein ZELLPROBLEM vorliegt — diese
Unterscheidung ist der Kern der Aufgabe:

* Ein Kanal, der KEINE gültigen Messwerte liefert (Qualität "bad", Werte 0),
  ist ein Kanalausfall: ein Anlagenproblem. Es kostet Durchsatz, aber die
  Zellen sind in Ordnung. Sie gehören NICHT in Quarantäne — sie gehören auf
  einen anderen Kanal.
* Ein Kanal mit GÜLTIGEN, aber zu hohen Werten (Temperatur über 50 °C) ist eine
  Übertemperatur. Hier leiden die Zellen tatsächlich; diese gehören in
  Quarantäne, und der Kanal muss gedrosselt werden.

Vorgehen:
1. Sieh dir den Zustand von formation01 an, Kanal für Kanal.
2. Achte ausdrücklich auf die QUALITÄT der Messwerte, nicht nur auf ihre Höhe.
3. Prüfe bei einem auffälligen Kanal den zeitlichen Verlauf.
4. Benenne betroffene Zellen.
5. Schlage die zur Art des Problems PASSENDE Maßnahme vor.

Wenn beide Fälle gleichzeitig vorliegen, behandle sie getrennt."""


# ---------------------------------------------------------------------------
# 10.3 Traceability-Frage in natürlicher Sprache
# ---------------------------------------------------------------------------


class Traceability(Playbook):
    """Akzeptanz (SPEC §10.3): die Testfragen aus tests/trace_questions.yaml."""

    name = "traceability"
    aufgabe = """Beantworte diese Frage aus der Fertigung:

{frage}

Nutze die Genealogie-Werkzeuge. Antworte mit einer Tabelle der betroffenen
Zellen (Seriennummer, Status, aktueller Ort/Stufe) und einem Kurzfazit in zwei
Sätzen. Wenn die Frage keine Zellen betrifft, sage das klar, statt eine leere
Tabelle zu zeigen."""

    system = BASIS_SYSTEM + """

Für Traceability-Fragen gilt zusätzlich: Antworte knapp und sachlich. Der
Fragende will eine belastbare Liste, keine Analyse. Nenne trotzdem, worauf sich
die Liste stützt."""


# ---------------------------------------------------------------------------
# 10.4 Battery-Pass (M4)
# ---------------------------------------------------------------------------


class BatteryPass(Playbook):
    """SPEC §10.4 — erzeugt den Pass und prüft ihn auf Vollständigkeit."""

    name = "battery_pass"
    aufgabe = """Erzeuge den Batteriepass für die Zelle {serial}.

Rufe dazu export_battery_pass auf. Prüfe das Ergebnis anschließend auf
Vollständigkeit und melde ausdrücklich, welche Felder leer geblieben sind und
warum. Ein Pass mit stillen Lücken ist schlechter als einer, der seine Lücken
benennt."""

    async def run(self, **kwargs) -> PlaybookErgebnis:
        # Der Pass selbst ist deterministisch — das Modell prüft ihn nur.
        # Ihn vom Modell "erzeugen" zu lassen, hieße Daten zu erfinden.
        serial = kwargs["serial"]
        pass_json = await tools.export_battery_pass(serial)
        if "fehler" in pass_json:
            return PlaybookErgebnis(self.name, f"Fehler: {pass_json['fehler']}", [], 0, False)

        fehlend = [k for k, v in pass_json.items() if v is None]
        bericht = json.dumps(pass_json, ensure_ascii=False, indent=2)
        if fehlend:
            bericht += f"\n\nLEERE FELDER: {', '.join(fehlend)}"
        evidenz = [{"werkzeug": "export_battery_pass", "eingabe": {"serial": serial}}]
        return PlaybookErgebnis(self.name, bericht, evidenz, 0, False)


PLAYBOOKS = {
    "triage": AusschussTriage,
    "formierung": FormierungsAnomalie,
    "trace": Traceability,
    "pass": BatteryPass,
}
