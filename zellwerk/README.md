# zellwerk — Musterfabrik + KI-native Middleware für die Batterieproduktion

Eine simulierte Lithium-Ionen-Zellfertigung mit sechs Stationen, die Prozessdaten
über OPC UA ausgibt — und eine Middleware, die daraus ein semantisches
Fabrikmodell baut und es KI-Agenten als Werkzeuge anbietet.

Der Kern ist nicht die Simulation, sondern was sie ermöglicht: **Fehler pflanzen
sich über das Material fort, nicht über die Uhrzeit.** Eine Slurry-Charge, die
mit erhöhter Viskosität gemischt wurde, trägt diese Eigenschaft weiter — der
Coater streut, der Kalander verfehlt die Zielporosität, und die daraus gebauten
Zellen verlieren in der Formierung Kapazität. Erst diese Kette macht die
Genealogie zu mehr als einer Datenbanktabelle: sie ist der einzige Weg, eine
Ursache zu **belegen** statt zu vermuten.

---

## Inhalt

- [Was man sieht](#was-man-sieht) · [Schnellstart](#schnellstart)
- [Die sechs Stationen](#die-sechs-stationen) · [Fehlerszenarien](#fehlerszenarien)
- [Architektur](#architektur) · [Datenmodell](#datenmodell)
- [Die Werkzeuge der Agenten](#die-werkzeuge-der-agenten) · [Playbooks](#playbooks)
- [Gemessene Ergebnisse](#gemessene-ergebnisse) · [Tests](#tests)
- [Konfiguration](#konfiguration) · [Betrieb hinter einem Proxy](#betrieb-hinter-einem-reverse-proxy)
- [Entwurfsentscheidungen](#entwurfsentscheidungen-die-den-unterschied-machen)

---

## Was man sieht

Drei Oberflächen, jede mit einem eigenen Zweck:

**Grafana** — drei Dashboards
- *Linienübersicht*: die Anlage in Betrieb, Sollfenster als gestrichelte Linien
- *Zellen & Genealogie*: Aufträge mit Fortschritt, Rückverfolgung, und die
  Tabelle „Auffällige Zellen mit ihrer Herkunft" — Kapazität, Elektrolyt,
  Porosität und Viskosität nebeneinander
- *Agenten & Befunde*: was die KI untersucht, was sie vorschlägt, womit sie es belegt

**Musterfabrik** — Kennzahlen der Produktion, Fertigungsaufträge, und die fünf
Fehlerszenarien als Karten mit Knopf. Jedes mit der Erklärung, an welcher
Station es sich auswirkt — und das ist nie dieselbe, an der es entsteht.

**Agenten** — Playbooks per Klick starten, Bericht im Browser lesen. Ein Lauf
dauert ein bis zwei Minuten; die Ergebnisseite lädt sich selbst nach.

---

## Schnellstart

Voraussetzungen: Docker mit Compose v2, etwa 4 CPU-Kerne und 6 GB RAM.

```bash
cp .env.example .env        # für den lokalen Betrieb genügen die Vorgaben
make up                     # Fabrik + Kern + Dashboards
make ps                     # alle Dienste gesund?
```

Nach etwa **50 Minuten** hat die erste Zelle alle sechs Stationen durchlaufen.
Für Vorführungen lässt sich die Zeit raffen, ohne die Prozesslogik anzufassen:

```bash
ZW_SPEED=30 docker compose --profile sim up -d --force-recreate simfactory
```

Die KI-Schicht braucht einen LLM-Zugang (siehe [LLM-Zugang](#llm-zugang)):

```bash
make up-ai
```

Ein Fehlerszenario einspielen und beobachten:

```bash
make fault id=F1
make state
```

---

## Die sechs Stationen

Jede Station ist ein eigener OPC-UA-Server auf eigenem Port (4841–4846), Takt
1 s, Formierung 10 s.

| Station | Prozessvariablen (Auswahl) | Sollbereich |
|---|---|---|
| `mixer01` Mischen | Viskosität, Feststoffanteil, Temperatur, Mischdauer | 2–6 Pa·s · 45–55 % · 20–30 °C |
| `coater01` Beschichten | Nassschichtdicke, Streuung, Bahngeschwindigkeit, Trocknertemperatur, Flächengewicht, Haftungsindex | 120–200 µm · 20–60 m/min · 80–130 °C |
| `calender01` Kalandrieren | Liniendruck, Spaltmaß, Porosität | 300–1500 N/mm · 28–38 % |
| `assembly01` Wickeln/Stapeln | Zugspannung, Ausrichtungsfehler, Takt, Delaminationen | < 300 µm |
| `filling01` Elektrolyt | Dosiermenge, Vakuumdruck, Dichtheitsprüfung, Pumpe | 5 g ±1,5 % |
| `formation01` Formierung | je Kanal: Strom, Spannung, Temperatur, Status, Drosselung | C/10 · 3,0–4,2 V · 25–45 °C |

Die Parameter sind plausible Lehrbuch-Defaults, ausdrücklich **keine realen
Firmendaten**.

**Materialfluss.** Ein Fertigungsauftrag wird per REST aus dem Mock-ERP geholt
und steuert, unter welcher Nummer der Mischer ansetzt. Von dort wandert die
Auftragsnummer über jede Fertigungsstufe bis zur Zelle. Der Mischer erzeugt
Slurry-Lose, der Coater macht daraus Elektroden-Lose, der Kalander verdichtet
sie, die Assemblierung baut Zellen mit Seriennummern (`ZW-JAHR-LAUFNUMMER`),
Befüllung und Formierung verarbeiten sie einzeln.

**Die Linie läuft im Fließgleichgewicht:**

| Station | Taktzeit |
|---|---|
| Mischer | 10 min je Slurry-Los |
| Coater | 400 m bei 40 m/min = 10 min |
| Kalander | 400 m bei 40 m/min = 10 min |
| Assemblierung | 20 Zellen à 30 s = 10 min |
| Formierung | 8 Kanäle à 240 s = eine Zelle je 30 s |

Ohne diese Abstimmung staut sich das Material vor der Formierung, und
auffällige Chargen erreichen sie nie — die Kausalkette wäre dann nicht
nachweisbar.

---

## Fehlerszenarien

Auslösbar über die Oberfläche oder per `POST /faults/{id}`.

| ID | Szenario | Wo es sichtbar wird |
|---|---|---|
| F1 | Viskositätsdrift im Mischer | Streuung am Coater → Porosität unter Soll → Kapazitätsverlust in der Formierung |
| F2 | Trocknertemperatur zu hoch | Haftung sinkt, Delamination erst in der Assemblierung |
| F3 | Übertemperatur in einem Formierkanal | Ein Kanal > 50 °C, Zelle in Quarantäne, Edge-Regel drosselt |
| F4 | Elektrolyt-Unterdosierung | Unauffällig bis zur Formierung, dort niedrige Kapazität |
| F5 | Ausfall eines Zykler-Kanals | `quality=bad`, Durchsatzverlust **ohne** Qualitätsproblem |

**Zwei Paare sind die eigentlichen Prüfsteine:**

**F1 und F4** erzeugen dasselbe Endsymptom — zu wenig Kapazität — über
verschiedene Wege. In der Formierung sind sie nicht unterscheidbar. Nur die
Genealogie trennt sie: bei F1 liegt die Porosität unter Soll, bei F4 die
Dosiermenge.

**F3 und F5** sehen beide nach „Kanal auffällig" aus, verlangen aber
gegensätzliche Reaktionen. Der überhitzte Kanal liefert *gültige* Werte, die zu
hoch sind — die Zelle ist geschädigt und gehört in Quarantäne. Der ausgefallene
Kanal liefert *ungültige* Werte — die Zelle ist in Ordnung und gehört auf einen
anderen Kanal. Ein Klassifikator, der nur auf Zahlen schaut, verschrottet die
zweite grundlos.

**Wie ein Szenario endet.** Es läuft, bis es zurückgenommen wird — von allein
endet keines. Danach sind die **Messwerte sofort normal, die Zellen nicht**:
Chargen, die den Fehler mitbekommen haben, wandern weiter durch die Linie und
fallen später trotzdem durch. Bis die letzte betroffene Zelle die Formierung
verlassen hat, vergehen rund 45 Minuten Simulationszeit. Genau darum geht es:
Der Fehler ist längst behoben, und der Ausschuss läuft trotzdem noch.

---

## Architektur

```
  AGENTEN — Playbooks: Triage · Formierung · Rückverfolgung · Batteriepass
        │  Shadow Mode: sie schlagen vor, sie führen nicht aus
        ▼  MCP-Werkzeuge, jeder Aufruf im Audit-Log
  SEMANTISCHE SCHICHT — Anlagen · Aufträge · Lose · Zellen · Genealogie
        │                + Edge-Rule-Engine (deterministisch, < 1 s)
        ▼  SQL
  TimescaleDB — Zeitreihen (Hypertables) und Modell in EINER Datenbank
        ▲  MQTT subscribe
  UNIFIED NAMESPACE — EMQX
        │  zellwerk/v1/{site}/{area}/{line}/{station}/{kind}/{name}
        ▲  OPC UA → MQTT
  MUSTERFABRIK — 6 OPC-UA-Server + Fault-Injection + Mock-ERP
```

**Dienste** (Compose-Profile in Klammern)

| Dienst | Profil | Rolle |
|---|---|---|
| `emqx` | — | MQTT-Broker, Unified Namespace |
| `timescaledb` | — | Zeitreihen und semantisches Modell |
| `simfactory` | sim | sechs Stationen, Fault-Injection, Bedienoberfläche |
| `erp-mock` | sim | Fertigungsaufträge und Stammdaten |
| `connector` | core | OPC UA → UNS, konfigurationsgetrieben |
| `ingest` | core | UNS → TimescaleDB, gebündelt |
| `rules` | core | Edge-Rule-Engine, deterministisch |
| `grafana` | obs | drei Dashboards |
| `mcpserver` | ai | Fabrikmodell als MCP-Werkzeuge |
| `agents` | ai | Playbooks über HTTP |
| `zellwerk-llm` | ai | LLM-Zugang, einziger Weg nach draußen |
| `caddy` | edge | öffentliche Adressen |

Broker und Datenbank tragen **bewusst kein Profil**: mehrere Profile hängen von
ihnen ab, und eine Abhängigkeit über eine Profilgrenze hinweg ist in Compose
kein gültiges Projekt.

**Netzentwurf**

| Netz | Eigenschaft | Wer hängt dran |
|---|---|---|
| `backend` | `internal: true` — kein Internet | alle Dienste |
| `egress` | normal | ausschließlich `zellwerk-llm` |
| `edge` | normal, ohne Internetbedarf | ausschließlich `caddy` |

Genau eine Tür nach draußen, und die führt zur Modell-API. Ein Container, der
*nur* in einem `internal`-Netz hängt, kann übrigens keine Ports veröffentlichen
— deshalb hat der Edge ein eigenes Netz, obwohl er selbst nichts nach außen
sendet.

---

## Datenmodell

| Tabelle | Zweck |
|---|---|
| `asset` | Anlagenregister mit OPC-UA-Endpunkt |
| `production_order` | Fertigungsaufträge aus dem Mock-ERP |
| `lot` | Charge je Prozessschritt, mit `parent_id`, `order_id` und `traits` |
| `cell` | Einzelzelle mit Status, Grade, `order_id` und `traits` |
| `genealogy` | Kanten: Los → Los, Los → Zelle |
| `measurement` | Hypertable: Zeitreihen mit Qualitätskennzeichen |
| `event` | Hypertable: Alarme und Ereignisse, quittierbar |
| `action_log` | Audit: jeder Werkzeugaufruf eines Agenten |
| `process_window` | Sollbereiche — dieselbe Wahrheit für Werkzeuge und Dashboards |

`traits` trägt die Qualitätsmerkmale, die ein Los an die nächste Stufe
weitergibt. Dort liegt die Evidenz, auf die sich jede Ursachenaussage stützt.

**Topic-Baum:** `zellwerk/v1/{site}/{area}/{line}/{station}/{kind}/{name}` mit
`kind` ∈ `pv` · `state` · `event` · `trace` · `cmd`. Nutzlast immer
`{ts, value, quality, unit}`.

---

## Die Werkzeuge der Agenten

Alle read-only außer den zwei markierten. Jeder Aufruf landet im `action_log` —
ohne lückenloses Audit wäre „Shadow Mode" eine Behauptung statt einer
Eigenschaft.

| Werkzeug | Zweck |
|---|---|
| `get_factory_overview` | Einstieg: Anlagen, Zustände, offene Alarme |
| `get_asset_state` | eine Station im Detail, mit Fenster-Kennung |
| `query_timeseries` | Verlauf einer Kennzahl mit Kennwerten |
| `get_process_window` | Sollbereich, Ist-Verteilung, Cpk-Näherung |
| `trace_cell_genealogy` | Zelle rückwärts bis zur Slurry-Charge, mit Prozesswerten je Stufe |
| `find_similar_cells` | Betroffenheitsanalyse über Status, Charge, Kapazität oder Formierkanal |
| `get_active_alarms` | offene Ereignisse mit Kontext |
| `propose_action` | **schreibend (Shadow)** — protokolliert einen Vorschlag |
| `execute_action` | **schreibend (Live)** — nur mit Flag *und* Whitelist, per Default aus |
| `export_battery_pass` | Demo-Subset der Verordnung (EU) 2023/1542 |

**Jedes leere Ergebnis erklärt sich selbst.** Ein Werkzeug, das bei einer
erfolglosen Suche nur eine leere Liste zurückgibt, provoziert Wiederholungen:
der Aufrufer weiß nicht, ob sein Kriterium falsch war oder ob es nichts zu
finden gibt. Hier liefert eine leere Antwort mit, was tatsächlich vorhanden ist
— bei `query_timeseries` etwa die Liste der bekannten Kennzahlen dieser Anlage.

Der MCP-Server lässt sich auch direkt an Claude Desktop oder Claude Code
anbinden:

```bash
python -m mcpserver.server          # stdio
python -m mcpserver.server --http   # SSE, Port 8765
```

---

## Playbooks

Jedes hat einen festen Ablauf, einen klaren Auslöser und ein klares Endartefakt.
Ausgabe immer: **Befund, Evidenz, Empfehlung, Konfidenz**.

| Playbook | Aufgabe | Akzeptanzkriterium |
|---|---|---|
| Ausschuss-Triage | verursachende Station finden | benennt bei F1 und F4 die richtige Wurzel |
| Formierungs-Anomalie | Anlagen- von Zellproblem trennen | klassifiziert F3 und F5 gegensätzlich |
| Rückverfolgung | Frage in natürlicher Sprache | beantwortet die Testfragen aus `tests/trace_questions.yaml` |
| Batteriepass | JSON je Zelle | Demo-Subset, vollständige Genealogie |

Aufrufbar über die Weboberfläche oder auf der Kommandozeile:

```bash
docker compose exec agents python -m agents.runner triage
docker compose exec agents python -m agents.runner formierung
docker compose exec agents python -m agents.runner trace --frage "…"
docker compose exec agents python -m agents.runner pass --serial ZW-2026-000042
docker compose exec agents python -m agents.runner testfragen
```

Zwei Leitplanken stehen in jedem System-Prompt: **keine Behauptung ohne Beleg**
(ein Agent, der eine Ursache nennt, ohne den Messwert zu zeigen, ist wertlos —
niemand sperrt eine Charge auf ein Bauchgefühl hin) und **Shadow Mode** (der
Abschluss ist ein Vorschlag, nie eine Ausführung).

---

## Gemessene Ergebnisse

Alles aus echten Läufen, jeweils mit dem Weg, auf dem es gemessen wurde:

| | |
|---|---|
| Kausalkette F1 | Viskosität 6,99 Pa·s (Soll 2–6) → Streuung 10,95 µm → Porosität 20,62 % (Soll 28–38) → Kapazitätsausfall |
| Genealogie | von der Ausschusszelle per SQL bis zur Slurry-Charge, vier Stufen |
| Edge-Regel-Latenz | Symptom → Kommando: min 0,30 ms · Median 0,40 ms · max 0,50 ms (n = 8), Anforderung 500 ms |
| Geschlossener Regelkreis | Regel feuert bei 50,51 °C → Kommando in 0,5 ms → Drosselung auf Faktor 0,50 → Temperatur fällt auf 44,4 °C |
| Fließgleichgewicht | vier Stunden Normalbetrieb, alle Warteschlangen bei 0 |
| Ingest unter Last | 99.990 Werte in 20 s = 4999/s, **0 % Verlust** (der Lastgenerator selbst erreichte 4999/s — die Obergrenze des Schreibpfads wurde damit nicht ermittelt) |
| Ausschuss-Triage bei F4 | 8 Runden, 13 Werkzeuge; Dosierpumpe benannt, Cpk −0,141 als Beleg, Mischer/Coater/Kalander namentlich ausgeschlossen |
| F3 gegen F5 | korrekt gegensätzlich klassifiziert: Quarantäne vs. Umlagern |
| Batteriepass | 16 Prozess-Kennwerte über vier Fertigungsstufen |

---

## Tests

```bash
uv venv .venv
uv pip install --python .venv/bin/python pytest pytest-asyncio pyyaml ruff
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check packages/ tests/ data/
```

Die Tests laufen offline in Simulationszeit, ohne Broker und ohne Datenbank.

Der wichtigste ist
`test_f1_pflanzt_sich_ueber_das_material_bis_zur_kapazitaet_fort`: er prüft
jedes Glied der Kette einzeln. Fällt er durch, hat der Diagnose-Agent später
nichts zu finden — dann sind die sechs Stationen nur unabhängige
Zufallsgeneratoren.

Ebenso bewusst gesetzt: `test_f4_senkt_kapazitaet_ohne_die_porositaet_anzufassen`
schlägt fehl, sobald F4 die Porosität mitzieht. Dann wäre es nicht mehr von F1
unterscheidbar, und die Triage-Aufgabe wäre trivial statt echt.

Lastmessung gegen den laufenden Stack:

```bash
docker compose exec ingest python -m tests.load.ingest_load --rate 5000 --sekunden 30
```

---

## Konfiguration

Alles in `.env` (siehe `.env.example`): Zugangsdaten, Standort, Takt und
Zeitraffer, Modellwahl, öffentliche Adressen.

**Wichtig zu wissen:** `ZW_GRAFANA_PASSWORD` und `ZW_EMQX_PASSWORD` wirken **nur
beim ersten Start eines frischen Volumes**. Besteht das Volume bereits, kommt
der Container sauber mit gesetzter Variable hoch — und es gilt trotzdem das alte
Passwort. An einer bestehenden Installation:

```bash
docker compose exec grafana grafana cli admin reset-admin-password <neues>
docker compose exec emqx emqx ctl admins passwd admin <neues>
```

Der Zeitraffer `ZW_SPEED` muss in der `environment`-Sektion des Dienstes stehen
— ohne Eintrag erreicht ein `ZW_SPEED=30 docker compose up` den Container nicht,
und die Fabrik läuft weiter in Echtzeit, ohne jede Fehlermeldung.

---

## Betrieb hinter einem Reverse-Proxy

Das `edge`-Profil startet einen Caddy, der die Dienste unter eigenen Hostnamen
ausliefert. Alle Site-Adressen im `Caddyfile` tragen bewusst das Präfix
`http://`: TLS terminiert der **vorgelagerte** Proxy. Ohne dieses Präfix leitet
Caddy jede Anfrage mit 308 auf https um, und die beiden Proxys laufen im Kreis.

**Grafana und EMQX stehen NICHT hinter Basic Auth.** Beide bringen ein eigenes
Login mit und beantworten nicht angemeldete Aufrufe selbst mit `401` und
`WWW-Authenticate`. Steht davor noch Basic Auth, hält der Browser das für eine
erneute Aufforderung und zeigt den Anmeldedialog endlos — eine Schleife, die nur
mit Abbruch und 401 endet.

Grafana braucht außerdem `GF_SERVER_ROOT_URL` mit der öffentlichen Adresse,
sonst zeigen Weiterleitungen nach dem Login auf den internen Containernamen.

Standardmäßig zeigt Grafana nach dem Login seine Willkommensseite — mit „Add
your first data source". Das sieht aus wie eine leere Neuinstallation, obwohl
alles provisioniert ist. `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` setzt
stattdessen das Linien-Dashboard als Startseite.

---

## LLM-Zugang

Die Agenten sprechen nicht direkt mit einer Modell-API, sondern über den Dienst
`zellwerk-llm`. Er stellt `POST /v1/messages` im Anthropic-Format bereit, sodass
das offizielle SDK unverändert benutzbar bleibt — es zeigt lediglich auf eine
andere Basis-URL:

```python
from anthropic import Anthropic
client = Anthropic(base_url=os.environ["ZW_LLM_BASE_URL"], api_key="unused")
```

Der Dienst ist ein **rein passiver Leser**: er benutzt ein Zugangstoken, das ihm
von außen bereitgestellt wird, und erneuert es **niemals selbst**. Der Grund ist
keine Stilfrage — eine Erneuerung liefert ein neues Refresh-Token und macht das
alte serverseitig ungültig. Ein zweiter Prozess, der dasselbe Token erneuert,
legt damit den ersten lahm. Wer diesen Dienst nachbaut, sollte diese Eigenschaft
beibehalten.

Wie das Token in den Container kommt, ist bewusst nicht Teil dieses Repos: das
hängt von der jeweiligen Umgebung ab. Der Dienst erwartet es unter
`/creds/creds.json` im Format `{"claudeAiOauth": {"accessToken": …,
"expiresAt": …}}` und meldet über `/health` ehrlich rot, solange keins vorliegt.

---

## Entwurfsentscheidungen, die den Unterschied machen

**Der Konnektor weiß nichts über Batterien.** Was er abonniert und wohin er
publiziert, steht vollständig in `packages/connector/config.yaml`. Im echten
Einsatz zeigen dieselben Einträge auf reale Steuerungen — nichts am Code ändert
sich.

**Der Sekundenpfad kennt kein Sprachmodell.** Edge-Regeln werden deterministisch
im MQTT-Strom ausgewertet, ohne Datenbank-Roundtrip. Ein Modell im
Sicherheitspfad wäre weder schnell noch reproduzierbar. Die KI darf Regeln
*vorschlagen* — einführen darf sie ein Mensch.

**Ein Bad-StatusCode ist eine Aussage, kein Fehler.** asyncua wirft bei einem
`Bad`-Status eine Exception. Behandelt man die als Lesefehler und überspringt
den Wert, verstummt ein ausgefallener Kanal stillschweigend — und ist von einer
echten Störung nicht mehr zu unterscheiden. Genau diese Unterscheidung ist das
Akzeptanzkriterium des Formierungs-Playbooks.

**Alarme werden entprellt.** Ein Alarmsystem, das jede Grenzverletzung einzeln
meldet, erzeugt bei einem schwankenden Messwert hunderte Einträge — und wird
deshalb ignoriert. Genau dann ist es wertlos, wenn es gebraucht wird.

**Zeitstempel sind Wanduhrzeit, auch im Zeitraffer.** Trüge ein Los seine
Simulationszeit, liefen die Achsen auseinander, und die Abfrage „Prozesswerte im
Fertigungszeitraum dieses Loses" fände nichts.

**Die Fertigung hält nicht an, wenn das ERP hakt.** Der Auftragsclient ist
fehlertolerant: fällt das ERP aus, produziert die Fabrik ohne Auftragsbezug
weiter. Der Ausfall wird protokolliert, nicht verschluckt.

`docs/decisions.md` hält fest, wo und warum die Umsetzung von der Spezifikation
abweicht — unter anderem, dass die Formierungs-Templates synthetisch aus
veröffentlichten Kennwerten erzeugt und nicht aus einem Messdatensatz abgeleitet
werden. Solche Abweichungen gehören dokumentiert, nicht stillschweigend gemacht.

`docs/demo-drehbuch.md` beschreibt einen vollständigen Vorführungsablauf in fünf
Akten, mit den Sätzen dazu — und einem Abschnitt „Was man nicht behaupten
sollte".

---

## Lizenz

MIT — siehe [LICENSE](../LICENSE).
