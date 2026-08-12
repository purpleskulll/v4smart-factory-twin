# zellwerk — Musterfabrik + KI-native Middleware für die Batterieproduktion

Eine simulierte Lithium-Ionen-Zellfertigung mit sechs Stationen, die realistische
Prozessdaten über OPC UA ausgibt — und eine Middleware, die daraus ein
semantisches Fabrikmodell baut und es KI-Agenten als MCP-Werkzeuge anbietet.

Der Kern ist nicht die Simulation, sondern was sie ermöglicht: **Fehler pflanzen
sich über das Material fort, nicht über die Uhrzeit.** Eine Slurry-Charge, die
mit erhöhter Viskosität gemischt wurde, trägt diese Eigenschaft weiter — der
Coater streut, der Kalander verfehlt die Zielporosität, und die daraus gebauten
Zellen verlieren in der Formierung Kapazität. Erst diese Kette macht die
Genealogie zu mehr als einer Datenbanktabelle: sie ist der einzige Weg, eine
Ursache zu belegen statt zu vermuten.

## Gemessen im laufenden Stack

Keine Planzahlen — alles aus echten Läufen, jeweils mit dem Weg, auf dem es
gemessen wurde:

| | |
|---|---|
| Kausalkette F1 | Viskosität 6,99 Pa·s (Soll 2–6) → Streuung 10,95 µm → Porosität 20,62 % (Soll 28–38) → Kapazitätsausfall |
| Genealogie | von der Ausschusszelle per SQL bis zur Slurry-Charge auflösbar, vier Stufen |
| Edge-Regel-Latenz | Symptom → Kommando: min 0,30 ms · Median 0,40 ms · max 0,50 ms (n=8) gegen eine Anforderung von 500 ms |
| Geschlossener Regelkreis | Regel feuert bei 50,51 °C → Kommando in 0,5 ms → Simulator drosselt auf Faktor 0,50 → Temperatur fällt auf 44,4 °C |
| Ingest unter Last | 99.990 Werte in 20 s geschrieben = 4999/s, **0 % Verlust**. Der Lastgenerator selbst erreichte 4999/s — die Obergrenze des Schreibpfads liegt also höher und wurde nicht ermittelt |
| Fließgleichgewicht | vier Stunden Normalbetrieb, alle Warteschlangen bei 0 |
| Ausschuss-Triage | Wurzelstation korrekt benannt, Alternativursache aktiv ausgeschlossen, 11 Werkzeugaufrufe in 8 Runden |
| F3 gegen F5 | korrekt gegensätzlich klassifiziert: Quarantäne vs. Umlagern |
| Batteriepass | 16 Prozess-Kennwerte über vier Fertigungsstufen |

## Die sechs Stationen

| Station | Prozessvariablen (Auswahl) | Sollbereich |
|---|---|---|
| `mixer01` Mischen | Viskosität, Feststoffanteil, Temperatur | 2–6 Pa·s · 45–55 % · 20–30 °C |
| `coater01` Beschichten | Nassschichtdicke, Bahngeschwindigkeit, Trocknertemperatur | 120–200 µm · 20–60 m/min · 80–130 °C |
| `calender01` Kalandrieren | Liniendruck, Spaltmaß, Porosität | 300–1500 N/mm · 28–38 % |
| `assembly01` Wickeln/Stapeln | Zugspannung, Ausrichtungsfehler, Takt | < 300 µm |
| `filling01` Elektrolyt | Dosiermenge, Vakuumdruck, Dichtheit | 5 g ±1,5 % |
| `formation01` Formierung | je Kanal: Strom, Spannung, Temperatur, Kapazität | C/10 · 3,0–4,2 V · 25–45 °C |

Die Parameter sind plausible Lehrbuch-Defaults, ausdrücklich keine realen
Firmendaten.

Die Linie läuft im Fließgleichgewicht: Mischer, Coater und Kalander brauchen je
zehn Minuten pro Los, die Assemblierung baut zwanzig Zellen à 30 s, die
Formierung schafft mit acht Kanälen à 240 s genau eine Zelle je 30 s. Ohne diese
Abstimmung staut sich das Material vor der Formierung, und auffällige Chargen
erreichen sie nie — die Kausalkette wäre dann nicht nachweisbar.

## Fehlerszenarien

Über `POST /faults/{id}` auslösbar. Jedes Szenario hat eine definierte
Symptomatik quer über die Stationen — genau das, was die Agenten diagnostizieren:

| ID | Szenario | Wo es sichtbar wird |
|---|---|---|
| F1 | Viskositätsdrift im Mischer | Streuung am Coater → Porosität unter Soll → Kapazitätsverlust in der Formierung |
| F2 | Trocknertemperatur zu hoch | Haftung sinkt, Delamination erst in der Assemblierung |
| F3 | Übertemperatur in einem Formierkanal | Ein Kanal > 50 °C, Zelle in Quarantäne, Edge-Regel drosselt |
| F4 | Elektrolyt-Unterdosierung | Unauffällig bis zur Formierung, dort niedrige Kapazität |
| F5 | Zykler-Kanalausfall | `quality=bad`, Durchsatzverlust **ohne** Qualitätsproblem |

**F1 und F4 erzeugen dasselbe Endsymptom** (zu wenig Kapazität) über
verschiedene Wege. Sie sind in der Formierung nicht unterscheidbar — nur über
die Genealogie. **F3 und F5** sehen beide nach „Kanal auffällig" aus, verlangen
aber gegensätzliche Reaktionen: der eine braucht Quarantäne, der andere nur
einen anderen Kanal. Diese beiden Paare sind die eigentlichen Prüfsteine.

## Architektur

```
  AGENTEN (Playbooks: Triage · Formierung · Traceability · Battery-Pass)
        │  Shadow Mode: sie schlagen vor, sie führen nicht aus
        ▼  MCP (auditierte Werkzeuge)
  SEMANTISCHE SCHICHT — Anlagen · Aufträge · Lose · Zellen · Genealogie
        │                + Edge-Rule-Engine (deterministisch, <1 s)
        ▼  SQL
  TimescaleDB — Zeitreihen (Hypertables) und Modell in EINER Datenbank
        ▲  MQTT subscribe
  UNIFIED NAMESPACE — EMQX, Topic-Baum zellwerk/v1/{site}/{area}/{line}/{station}/{kind}/{name}
        ▲  OPC UA → MQTT
  MUSTERFABRIK — 6 Stationen als OPC-UA-Server + Fault-Injection + Mock-ERP
```

Zwei Entwurfsentscheidungen, die den Unterschied machen:

**Der Konnektor weiß nichts über Batterien.** Was er abonniert und wohin er
publiziert, steht vollständig in `packages/connector/config.yaml`. Im echten
Einsatz zeigen dieselben Einträge auf reale Steuerungen — nichts am Code ändert
sich. Das ist das eigentliche Produktversprechen.

**Der <1-s-Pfad kennt kein Sprachmodell.** Die Edge-Regeln werden deterministisch
direkt im MQTT-Strom ausgewertet, ohne Datenbank-Roundtrip. Ein Modell im
Sicherheitspfad wäre weder schnell noch reproduzierbar. Die KI darf Regeln
*vorschlagen* — einführen darf sie ein Mensch.

## Schnellstart

```bash
cp .env.example .env
make up          # Fabrik + Kern + Dashboards
make up-ai       # zusätzlich die KI-Schicht
make ps
```

Nach etwa 30 Minuten ist die erste Zelle vollständig durchgelaufen. Für Demos
lässt sich die Zeit raffen, ohne die Prozesslogik anzufassen:

```bash
ZW_SPEED=30 docker compose --profile sim up -d --force-recreate simfactory
```

Fehler einspielen und beobachten:

```bash
make fault id=F1
make state
```

## Die Werkzeuge der Agenten

Alle read-only außer den zwei markierten. Jeder Aufruf landet im `action_log` —
ohne lückenloses Audit wäre „Shadow Mode" eine Behauptung statt einer
Eigenschaft.

| Werkzeug | Zweck |
|---|---|
| `get_factory_overview` | Einstieg: Anlagen, Zustände, offene Alarme |
| `get_asset_state` | Detailblick auf eine Station, inkl. Fenster-Kennung |
| `query_timeseries` | Verlauf einer Kennzahl mit Kennwerten |
| `get_process_window` | Sollbereich, Ist-Verteilung, Cpk-Näherung |
| `trace_cell_genealogy` | Zelle rückwärts bis zur Slurry-Charge, mit Prozesswerten je Stufe |
| `find_similar_cells` | Betroffenheitsanalyse über die gesamte Genealogie |
| `get_active_alarms` | Offene Ereignisse mit Kontext |
| `propose_action` | **Schreibend (Shadow)** — protokolliert einen Vorschlag |
| `execute_action` | **Schreibend (Live)** — nur mit Flag *und* Whitelist, per Default aus |
| `export_battery_pass` | Demo-Subset der Verordnung (EU) 2023/1542 |

## Playbooks

```bash
docker compose exec agents python -m agents.runner triage
docker compose exec agents python -m agents.runner formierung
docker compose exec agents python -m agents.runner trace --frage "Welche Zellen stammen aus SLURRY-0003?"
docker compose exec agents python -m agents.runner pass --serial ZW-2026-000042
docker compose exec agents python -m agents.runner testfragen
```

Jeder Bericht hat vier Abschnitte: Befund, Evidenz, Empfehlung, Konfidenz. Die
Evidenz nennt Station, Charge und Zeitraum — eine Ursachenaussage ohne Messwert
ist in der Fertigung wertlos, weil niemand darauf eine Charge sperrt.

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest pytest-asyncio pyyaml
.venv/bin/python -m pytest tests/ -q
```

Die Tests laufen offline in Simulationszeit, ohne Broker und ohne Datenbank. Der
wichtigste ist `test_f1_pflanzt_sich_ueber_das_material_bis_zur_kapazitaet_fort`:
er prüft jedes Glied der Kette einzeln. Fällt er durch, hat der Diagnose-Agent
später nichts zu finden — dann sind die sechs Stationen nur unabhängige
Zufallsgeneratoren.

Ebenso bewusst gesetzt: `test_f4_senkt_kapazitaet_ohne_die_porositaet_anzufassen`
schlägt fehl, sobald F4 die Porosität mitzieht — dann wäre es nicht mehr von F1
unterscheidbar, und die Triage-Aufgabe wäre trivial statt echt.

## LLM-Zugang

Die Agenten sprechen nicht direkt mit einer Modell-API, sondern über den Dienst
`zellwerk-llm`. Er stellt `POST /v1/messages` im Anthropic-Format bereit, sodass
das offizielle SDK unverändert benutzbar bleibt — es zeigt lediglich auf eine
andere Basis-URL.

Der Dienst ist ein **rein passiver Leser**: er benutzt ein Zugangstoken, das ihm
von außen bereitgestellt wird, und erneuert es **niemals selbst**. Der Grund ist
kein Stilfrage — eine Token-Erneuerung liefert ein neues Refresh-Token und macht
das alte serverseitig ungültig. Ein zweiter Prozess, der dasselbe Token
erneuert, legt damit den ersten lahm. Wer diesen Dienst nachbaut, sollte diese
Eigenschaft beibehalten.

Netzentwurf: alle Dienste hängen im Compose-Netz `backend`, das
`internal: true` gesetzt hat und **keinen** Internetzugang besitzt.
Ausschließlich `zellwerk-llm` hat ein zweites Bein im `egress`-Netz. Damit gibt
es genau einen kontrollierten Weg nach draußen, und kein anderer Dienst kann
versehentlich telefonieren.

## Abweichungen

`docs/decisions.md` hält fest, wo und warum die Umsetzung von der Spezifikation
abweicht — unter anderem, dass die Formierungs-Templates synthetisch aus
veröffentlichten Kennwerten erzeugt und nicht aus einem Messdatensatz abgeleitet
werden. Solche Abweichungen gehören dokumentiert, nicht stillschweigend gemacht.

## Lizenz

MIT — siehe [LICENSE](../LICENSE).
