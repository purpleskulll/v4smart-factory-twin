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

1. [Voraussetzungen](#1-voraussetzungen)
2. [Installation Schritt für Schritt](#2-installation-schritt-für-schritt)
3. [Wo welcher Befehl eingegeben wird](#3-wo-welcher-befehl-eingegeben-wird)
4. [Die erste Stunde: was wann passiert](#4-die-erste-stunde-was-wann-passiert)
5. [Zeitraffer für Vorführungen](#5-zeitraffer-für-vorführungen)
6. [Die Oberflächen](#6-die-oberflächen)
7. [Fehlerszenarien einspielen](#7-fehlerszenarien-einspielen)
8. [Die Agenten benutzen](#8-die-agenten-benutzen)
9. [Die sechs Stationen](#9-die-sechs-stationen)
10. [Architektur](#10-architektur)
11. [Datenmodell](#11-datenmodell)
12. [Die Werkzeuge der Agenten](#12-die-werkzeuge-der-agenten)
13. [Konfiguration](#13-konfiguration)
14. [Betrieb hinter einem Reverse-Proxy](#14-betrieb-hinter-einem-reverse-proxy)
15. [LLM-Zugang](#15-llm-zugang)
16. [Tests](#16-tests)
17. [Fehlersuche](#17-fehlersuche)
18. [Gemessene Ergebnisse](#18-gemessene-ergebnisse)
19. [Entwurfsentscheidungen](#19-entwurfsentscheidungen)

---

## 1. Voraussetzungen

| | |
|---|---|
| Docker | Version 24 oder neuer, **mit Compose v2** (`docker compose`, nicht `docker-compose`) |
| Arbeitsspeicher | mindestens 6 GB frei — TimescaleDB, EMQX und Grafana zusammen brauchen etwa 2 GB, der Rest ist Puffer |
| CPU | 4 Kerne empfohlen; mit 2 läuft es, der Zeitraffer wird dann ungenau |
| Plattenplatz | etwa 4 GB für die Images, dazu wachsende Messdaten (~250 MB je Betriebstag) |
| Internetzugang | **nur beim Bauen** (Image-Pull). Im Betrieb braucht kein Dienst Internet außer dem LLM-Zugang |

Prüfen, ob Docker passt — **auf dem Rechner, auf dem der Stack laufen soll**:

```bash
docker --version          # erwartet: Docker version 24.x oder höher
docker compose version    # erwartet: Docker Compose version v2.x
```

Gibt der zweite Befehl „unknown command" aus, ist nur das alte `docker-compose`
installiert. Dann funktionieren die Befehle in dieser Anleitung nicht — Compose
v2 nachinstallieren.

---

## 2. Installation Schritt für Schritt

**Alle Befehle in diesem Abschnitt werden im Wurzelverzeichnis des Repos
eingegeben** — also dort, wo die `docker-compose.yml` liegt.

### Schritt 1 — Repo holen

```bash
git clone <repo-url> zellwerk
cd zellwerk
```

Ab jetzt bleibst du in diesem Verzeichnis. Jeder `docker compose`-Befehl sucht
die `docker-compose.yml` im aktuellen Verzeichnis; von woanders aufgerufen
findet er sie nicht.

### Schritt 2 — Konfiguration anlegen

```bash
cp .env.example .env
```

Für den lokalen Betrieb sind die Vorgaben ausreichend. Wer den Stack über den
eigenen Rechner hinaus erreichbar macht, muss mindestens die beiden Passwörter
ändern — siehe [Konfiguration](#13-konfiguration).

### Schritt 3 — Starten

```bash
make up
```

Das entspricht `docker compose --profile sim --profile core --profile obs up -d`
und startet elf Container. Der erste Aufruf dauert **fünf bis fünfzehn Minuten**,
weil Images geladen und drei Python-Images gebaut werden. Danach geht es in
Sekunden.

### Schritt 4 — Prüfen, ob alles läuft

```bash
make ps
```

Erwartet werden elf Zeilen. Die Spalte `STATUS` sollte bei `emqx`,
`timescaledb`, `erp-mock` und `simfactory` **`(healthy)`** zeigen — die übrigen
haben keine Gesundheitsprüfung und stehen einfach auf `Up`.

Steht dort `(health: starting)`, warte eine Minute: EMQX und TimescaleDB
brauchen beim ersten Start länger, weil sie ihre Datenverzeichnisse anlegen.

### Schritt 5 — Dashboard öffnen

```
http://localhost:3000
```

Anmeldung mit `admin` und dem Passwort aus deiner `.env`
(`ZW_GRAFANA_PASSWORD`, Vorgabe `zellwerk-demo`).

> **Portkonflikt?** Alle Oberflächen sind an `127.0.0.1` gebunden — erreichbar
> vom eigenen Rechner, nicht aus dem Netz. Ist einer der Ports schon belegt,
> ändere ihn in der `.env`:
>
> ```bash
> ZW_GRAFANA_PORT=3001
> ZW_FACTORY_PORT=8002
> ZW_AGENTS_PORT=8011
> ZW_EMQX_PORT=18084
> ```
>
> Danach `docker compose --profile sim --profile core --profile obs up -d --force-recreate`.

---

## 3. Wo welcher Befehl eingegeben wird

Das ist die häufigste Stolperfalle: es gibt **drei verschiedene Orte**, an denen
Befehle laufen. Jeder Codeblock in dieser Anleitung sagt dazu, welcher gemeint
ist.

### Ort A — dein Rechner, im Repo-Verzeichnis

Für alles, was den Stack als Ganzes betrifft: starten, stoppen, Logs ansehen.
Erkennbar an `docker compose …` oder `make …`.

```bash
cd /pfad/zu/zellwerk     # dort, wo docker-compose.yml liegt
make ps
```

### Ort B — in einem laufenden Container

Für alles, was ein einzelner Dienst tun soll: ein Playbook starten, die
Lastmessung, ein SQL-Kommando. Erkennbar an `docker compose exec <dienst> …`.

```bash
docker compose exec agents python -m agents.runner triage
#               ^^^^^^ Dienstname aus der docker-compose.yml
```

Der Befehl wird **im Container** ausgeführt, aber **von deinem Rechner aus**
eingegeben — du musst dich nirgends einloggen.

### Ort C — im Browser

Für die Oberflächen. Adressen siehe [Abschnitt 6](#6-die-oberflächen).

---

## 4. Die erste Stunde: was wann passiert

Nach `make up` läuft die Fabrik in **Echtzeit**. Das bedeutet konkret:

| Zeit nach dem Start | Was passiert | Wo sichtbar |
|---|---|---|
| sofort | Alle sechs Stationen senden Messwerte im Sekundentakt | Dashboard *Linienübersicht* |
| nach ~10 min | Das erste Slurry-Los ist fertig gemischt | Fabrik-Oberfläche, Zähler „Chargen" |
| nach ~20 min | Erstes Elektroden-Los beschichtet | dito |
| nach ~30 min | Erstes Los kalandriert | dito |
| nach ~40 min | Die ersten Zellen werden gebaut | Zähler „Zellen gesamt" |
| nach ~50 min | Erste Zelle vollständig formiert | Zähler „davon in Ordnung" |

**Vorher sind Zellen- und Chargenzähler null — das ist kein Fehler.** Messwerte
liegen sofort vor, das semantische Modell braucht seine Durchlaufzeit.

Wer nicht warten will, nimmt den Zeitraffer.

---

## 5. Zeitraffer für Vorführungen

**Ort A — dein Rechner, im Repo-Verzeichnis:**

```bash
ZW_SPEED=30 docker compose --profile sim up -d --force-recreate simfactory
```

Was der Befehl macht, Wort für Wort:

| Teil | Bedeutung |
|---|---|
| `ZW_SPEED=30` | setzt den Zeitraffer für **diesen einen Aufruf** auf Faktor 30 |
| `docker compose` | Compose v2 |
| `--profile sim` | nötig, weil `simfactory` im Profil `sim` liegt; ohne diese Angabe kennt Compose den Dienst nicht |
| `up -d` | starten, im Hintergrund |
| `--force-recreate` | den Container **neu erzeugen** — nur so wird die geänderte Variable übernommen. Ein einfaches `restart` genügt **nicht** |
| `simfactory` | nur diesen einen Dienst, alle anderen laufen weiter |

**Wirkung:** Eine Minute Wanduhrzeit entspricht dann einer halben Stunde
Fertigung. Die erste vollständige Zelle liegt nach etwa **100 Sekunden** vor
statt nach 50 Minuten.

**Prüfen, ob es gegriffen hat** (Ort A):

```bash
docker compose exec simfactory printenv ZW_SPEED
# erwartet: 30
```

Kommt hier nichts oder `1.0`, wurde der Container nicht neu erzeugt — dann
`--force-recreate` vergessen.

**Zurück auf Echtzeit** (Ort A):

```bash
docker compose --profile sim up -d --force-recreate simfactory
```

Ohne die Variable davor gilt wieder der Wert aus der `.env` beziehungsweise die
Vorgabe 1.0.

> **Achtung bei der Vorführung:** Der Zeitraffer setzt den Simulator zurück.
> Chargen und Zellen im Speicher gehen verloren — die bereits in der Datenbank
> gespeicherten bleiben. Deshalb den Zeitraffer **vor** dem Gespräch einschalten
> und laufen lassen, nicht mittendrin.

**Dauerhaft in der `.env` setzen** (statt bei jedem Aufruf):

```bash
# .env
ZW_SPEED=30
```

Danach einmal `make up` — dann gilt der Wert für alle künftigen Starts.
---

## 6. Die Oberflächen

Alle drei sind **Ort C — Browser**. Ohne Reverse-Proxy erreichst du sie über
`localhost` und den jeweiligen Port; hinter dem Edge-Profil über eigene
Hostnamen (siehe [Abschnitt 14](#14-betrieb-hinter-einem-reverse-proxy)).

### Grafana — die Messwerte

| | |
|---|---|
| Adresse | `http://localhost:3000` (Port über `ZW_GRAFANA_PORT` änderbar) |
| Anmeldung | `admin` / Wert von `ZW_GRAFANA_PASSWORD` aus deiner `.env` |
| Startseite | das Dashboard *Linienübersicht* — nicht Grafanas Willkommensseite |

Drei Dashboards, erreichbar oben links über **Dashboards → zellwerk**:

**Linienübersicht** — die Anlage in Betrieb. Sechs Panels: Viskosität am
Mischer, Streuung am Coater, Porosität am Kalander, Dosiermenge an der
Befüllung, alle acht Kanaltemperaturen der Formierung, dazu Zellstatus und
offene Ereignisse. Die gestrichelten Linien sind die Sollgrenzen aus der
Tabelle `process_window` — dieselbe Quelle, aus der auch die Agenten lesen.

**Zellen & Genealogie** — das semantische Modell. Aufträge mit Fortschritt,
Zellen nach Status, und die Tabelle *Auffällige Zellen mit ihrer Herkunft*: sie
zeigt je Zelle Kapazität, Elektrolytmenge, Porosität und Viskosität
nebeneinander. **Das ist die wichtigste Ansicht für eine Vorführung**, weil man
daran zeigen kann, warum Genealogie nötig ist: zwei verschiedene Ursachen
erzeugen dieselbe niedrige Kapazität, und nur diese Spalten trennen sie.

**Agenten & Befunde** — was die KI getan hat. Die Vorschläge mit ihrer
Begründung, die Werkzeugketten in der Reihenfolge, in der der Agent sie selbst
gewählt hat, und die Playbook-Läufe.

### Musterfabrik — die Anlage bedienen

| | |
|---|---|
| Adresse | `http://localhost:8001` (Port über `ZW_FACTORY_PORT` änderbar) |
| Anmeldung | keine (nur hinter dem Edge-Profil steht Basic Auth davor) |
| Aktualisierung | alle 5 Sekunden von selbst |

Zeigt Produktionskennzahlen, die Fertigungsaufträge mit Fortschritt, die
Warteschlangen zwischen den Stationen — und die fünf Fehlerszenarien als Karten
mit Knopf.

### Agenten — Untersuchungen starten

| | |
|---|---|
| Adresse | `http://localhost:8010` (Port über `ZW_AGENTS_PORT` änderbar) |
| Anmeldung | keine (nur hinter dem Edge-Profil) |
| Voraussetzung | Profil `ai` läuft (`make up-ai`) und ein LLM-Zugang ist eingerichtet |

Drei Playbooks als Karten. Ein Klick startet einen Lauf; du landest automatisch
auf der Ergebnisseite, die sich alle zehn Sekunden selbst nachlädt, bis der
Bericht fertig ist.

### EMQX-Konsole — der Broker

| | |
|---|---|
| Adresse | `http://localhost:18083` (Port über `ZW_EMQX_PORT` änderbar) |
| Anmeldung | `admin` / Wert von `ZW_EMQX_PASSWORD` aus deiner `.env` |

Zeigt Nachrichtenraten, Verbindungen, Topics und den Zustand des Brokers.
Nützlich, um zu prüfen, ob Daten fließen: unter *Cluster Overview* sollten bei
laufender Fabrik etwa 60 Nachrichten je Sekunde eingehen.

---

## 7. Fehlerszenarien einspielen

Es gibt **drei Wege**, dasselbe zu tun. Alle wirken sofort.

### Weg 1 — im Browser (Ort C)

`http://localhost:8001` öffnen, zum Abschnitt *Fehlerszenarien* scrollen, bei
einem Szenario auf **einspielen** klicken. Die Karte springt auf „läuft seit
N min". Derselbe Knopf heißt dann **zurücknehmen**.

### Weg 2 — über den Makefile-Kurzbefehl (Ort A)

```bash
make fault id=F1
```

Das ruft im Hintergrund `curl -X POST http://localhost:8001/faults/F1` auf und
gibt die Antwort formatiert aus.

### Weg 3 — direkt über die Schnittstelle (Ort A)

```bash
curl -X POST http://localhost:8001/faults/F1     # einspielen
curl -X DELETE http://localhost:8001/faults/F1   # zurücknehmen
curl http://localhost:8001/faults                # welche laufen gerade?
```

### Die fünf Szenarien

| ID | Was passiert | Wo es sichtbar wird | Wie lange bis zur Wirkung |
|---|---|---|---|
| **F1** | Viskosität im Mischer steigt über 45 min von 4 auf 7 Pa·s | verlässt nach ~30 min das Sollfenster; Streuung am Coater, Porosität unter Soll, Kapazitätsverlust in der Formierung | ~75 min bis zur ersten Ausschusszelle |
| **F2** | Trocknertemperatur am Coater zu hoch | Flächengewicht bleibt in Ordnung, Haftung sinkt; Delamination erst in der Assemblierung | ~30 min |
| **F3** | Ein Formierkanal überhitzt | Kanal 3 steigt über 50 °C; die Edge-Regel drosselt ihn in unter einer Sekunde, Zelle geht in Quarantäne | ~2 min |
| **F4** | Dosierpumpe gibt 5 % zu wenig Elektrolyt | in der Station praktisch unsichtbar; niedrige Kapazität erst in der Formierung | ~15 min |
| **F5** | Ein Zykler-Kanal fällt aus | Kanal 6 liefert `quality=bad`; Durchsatzverlust **ohne** Qualitätsproblem | sofort |

Die Zeitangaben gelten für **Echtzeit**. Mit `ZW_SPEED=30` durch 30 teilen.

### Warum F1/F4 und F3/F5 die interessanten Paare sind

**F1 und F4** erzeugen dasselbe Endsymptom — zu wenig Kapazität — über
verschiedene Wege. In der Formierung sind sie nicht unterscheidbar. Nur die
Genealogie trennt sie: bei F1 liegt die Porosität unter Soll, bei F4 die
Dosiermenge. Wer beide gleichzeitig einspielt, bekommt eine Aufgabe, die ein
einfacher Grenzwertwächter nicht lösen kann.

**F3 und F5** sehen beide nach „Kanal auffällig" aus, verlangen aber
gegensätzliche Reaktionen. Der überhitzte Kanal liefert *gültige* Werte, die zu
hoch sind — die Zelle ist geschädigt und gehört in Quarantäne. Der ausgefallene
Kanal liefert *ungültige* Werte — die Zelle ist in Ordnung und gehört auf einen
anderen Kanal. Ein Klassifikator, der nur Zahlen ansieht, verschrottet die
zweite grundlos.

### Wie ein Szenario endet

**Es endet nie von allein.** Ein Szenario läuft, bis es zurückgenommen wird —
das ist Absicht, damit eine Vorführung so lange laufen kann, wie sie gebraucht
wird.

Nach dem Zurücknehmen sind die **Messwerte sofort wieder normal**. Die Zellen
aber nicht: Chargen, die den Fehler schon mitbekommen haben, wandern weiter
durch die Linie und fallen später trotzdem durch. Bis die letzte betroffene
Zelle die Formierung verlassen hat, vergehen rund **45 Minuten Simulationszeit**.

Genau darum geht es bei diesem System: Der Fehler ist längst behoben, und der
Ausschuss läuft trotzdem noch. Wer die betroffenen Chargen benennen kann, muss
nicht die ganze Schicht sperren.

**Eine Ausnahme:** Bei F3 greift die Edge-Regel selbst ein und drosselt den
überhitzten Kanal, ohne dass jemand etwas anklickt. Das Szenario bleibt trotzdem
aktiv, bis es abgeschaltet wird.

---

## 8. Die Agenten benutzen

### Voraussetzung

Die KI-Schicht läuft nicht mit `make up`, sondern braucht das Profil `ai`
(Ort A):

```bash
make up-ai
```

Zusätzlich muss ein LLM-Zugang eingerichtet sein — siehe
[Abschnitt 15](#15-llm-zugang). Ohne ihn starten die Container, aber jeder
Playbook-Lauf endet mit einem Fehler.

**Prüfen** (Ort A):

```bash
docker compose exec agents python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://zellwerk-llm:4010/health')))"
```

Erwartet wird `'ok': True`. Steht dort `'ok': False`, fehlt das Zugangstoken.

### Weg 1 — im Browser (Ort C)

`http://localhost:8010` öffnen, auf **Untersuchung starten** klicken. Du landest
auf der Ergebnisseite; sie lädt sich selbst nach, bis der Bericht fertig ist.
Ein Lauf dauert **ein bis zwei Minuten**.

### Weg 2 — auf der Kommandozeile (Ort B)

```bash
docker compose exec agents python -m agents.runner triage
docker compose exec agents python -m agents.runner formierung
docker compose exec agents python -m agents.runner trace --frage "Welche Zellen stammen aus SLURRY-0003?"
docker compose exec agents python -m agents.runner pass --serial ZW-2026-BJQTG-000042
docker compose exec agents python -m agents.runner testfragen
```

Hier siehst du den Bericht direkt im Terminal. Der Aufruf blockiert, bis der
Agent fertig ist.

### Was die vier Playbooks tun

| Playbook | Aufgabe | Woran man erkennt, dass es funktioniert |
|---|---|---|
| `triage` | Findet die Station, die den Ausschuss verursacht | benennt bei F1 den Mischer, bei F4 die Dosierpumpe — **und schließt die jeweils andere Ursache ausdrücklich aus** |
| `formierung` | Trennt Anlagen- von Zellproblem | klassifiziert F3 und F5 gegensätzlich: Quarantäne gegen Umlagern |
| `trace` | Beantwortet eine Frage in natürlicher Sprache | liefert eine Tabelle betroffener Zellen mit Herkunft |
| `pass` | Erzeugt den Batteriepass einer Zelle | JSON mit vollständiger Genealogie und Prozess-Kennwerten |

### Wie ein Bericht aufgebaut ist

Immer vier Abschnitte:

- **BEFUND** — was der Fall ist, in ein bis zwei Sätzen
- **EVIDENZ** — die konkreten Werte mit Station, Charge und Zeitbezug
- **EMPFEHLUNG** — was zu tun ist und warum genau das
- **KONFIDENZ** — hoch/mittel/niedrig, mit der Angabe, was die Aussage
  widerlegen oder erhärten würde

Der Agent arbeitet im **Shadow Mode**: er schlägt vor, er führt nichts aus. Sein
Vorschlag landet im `action_log` und wartet auf einen Menschen.

### Wo die Ergebnisse landen

An drei Stellen — sie gehen also nicht verloren, wenn du das Browserfenster
schließt:

1. Auf der Ergebnisseite unter `http://localhost:8010/runs/<nummer>`
2. Im Grafana-Dashboard *Agenten & Befunde*
3. In der Tabelle `action_log` der Datenbank

Direkt in der Datenbank nachsehen (Ort B):

```bash
docker compose exec timescaledb psql -U zellwerk -d zellwerk -c \
  "SELECT ts, tool, begruendung FROM action_log WHERE tool LIKE 'propose%' ORDER BY ts DESC LIMIT 5;"
```

---

## 9. Die sechs Stationen

Jede Station ist ein eigener OPC-UA-Server auf eigenem Port (4841–4846), Takt
1 s, Formierung 10 s. Die OPC-UA-Ports sind **nicht veröffentlicht** — der Konnektor erreicht sie
über das interne Netz. Von außen zugänglich sind nur die vier Oberflächen, und
auch die nur über `127.0.0.1`.

| Station | Port | Prozessvariablen | Sollbereich |
|---|---|---|---|
| `mixer01` Mischen | 4841 | Viskosität, Feststoffanteil, Temperatur, Mischdauer | 2–6 Pa·s · 45–55 % · 20–30 °C |
| `coater01` Beschichten | 4842 | Nassschichtdicke, Streuung, Bahngeschwindigkeit, Trocknertemperatur, Flächengewicht, Haftungsindex | 120–200 µm · 20–60 m/min · 80–130 °C |
| `calender01` Kalandrieren | 4843 | Liniendruck, Spaltmaß, Porosität | 300–1500 N/mm · 28–38 % |
| `assembly01` Wickeln/Stapeln | 4844 | Zugspannung, Ausrichtungsfehler, Takt, Delaminationen | < 300 µm |
| `filling01` Elektrolyt | 4845 | Dosiermenge, Vakuumdruck, Dichtheitsprüfung, Pumpe | 5 g ±1,5 % |
| `formation01` Formierung | 4846 | je Kanal: Strom, Spannung, Temperatur, Status, Drosselung | C/10 · 3,0–4,2 V · 25–45 °C |

Die Parameter sind plausible Lehrbuch-Defaults, ausdrücklich **keine realen
Firmendaten**.

### Materialfluss

Ein Fertigungsauftrag wird beim Start per REST aus dem Mock-ERP geholt und
steuert, unter welcher Nummer der Mischer ansetzt. Von dort wandert die
Auftragsnummer über jede Fertigungsstufe bis zur einzelnen Zelle.

**Nummernformat.** Los- und Seriennummern enthalten eine **Lauf-Kennung**
(fünf Zeichen, aus der Startzeit abgeleitet): `SLURRY-BJQTG-0001`,
`ZW-2026-BJQTG-000042`. Ohne sie begännen die Zähler nach jedem Neustart wieder
bei 1 — neue Zellen hießen dann wie alte, und die Datenbank überschriebe die
bestehenden Einträge, statt neue anzulegen. Die Fabrik produzierte sichtbar
weiter, während die Zellzahl stillstünde.

```
Auftrag (PO-JAHR-NUMMER)
  └─ SLURRY-nnnn   Mischer      10 min je Los
      └─ ELEK-nnnn   Coater       400 m bei 40 m/min = 10 min
          └─ KAL-nnnn  Kalander     400 m bei 40 m/min = 10 min
              └─ ZELL-nnnn Assemblierung  20 Zellen à 30 s = 10 min
                  └─ ZW-JAHR-nnnnnn   einzelne Zellen
                       → Befüllung → Formierung (8 Kanäle à 240 s)
```

**Die Linie läuft im Fließgleichgewicht:** jede Station braucht zehn Minuten je
Los, und die Formierung schafft mit acht Kanälen genau eine Zelle je 30 Sekunden
— dasselbe Tempo, in dem die Assemblierung sie baut. Ohne diese Abstimmung
staut sich das Material vor der Formierung, und auffällige Chargen erreichen sie
nie. Die Kausalkette wäre dann nicht nachweisbar.

**Zustand ansehen** (Ort A):

```bash
make state
# oder:
curl http://localhost:8001/state | python3 -m json.tool
```
---

## 10. Architektur

```
  AGENTEN — Playbooks: Triage · Formierung · Rückverfolgung · Batteriepass
        │  Shadow Mode: sie schlagen vor, sie führen nicht aus
        ▼  Werkzeuge, jeder Aufruf im Audit-Log
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

### Die zwölf Dienste

| Dienst | Profil | Rolle | Port (intern) |
|---|---|---|---|
| `emqx` | — | MQTT-Broker, Unified Namespace | 1883 intern · 18083 lokal |
| `timescaledb` | — | Zeitreihen und semantisches Modell | 5432 |
| `simfactory` | sim | sechs Stationen, Fault-Injection, Bedienoberfläche | 8001 · 4841–4846 |
| `erp-mock` | sim | Fertigungsaufträge und Stammdaten | 8000 |
| `connector` | core | OPC UA → UNS, konfigurationsgetrieben | — |
| `ingest` | core | UNS → TimescaleDB, gebündelt | — |
| `rules` | core | Edge-Rule-Engine; meldet über `core/events` | — |
| `grafana` | obs | drei Dashboards | 3000 |
| `mcpserver` | ai | Fabrikmodell als MCP-Werkzeuge | 8765 |
| `agents` | ai | Playbooks über HTTP | 8010 |
| `zellwerk-llm` | ai | LLM-Zugang, einziger Weg nach draußen | 4010 |
| `caddy` | edge | öffentliche Adressen | 80 · 443 |

**Broker und Datenbank tragen bewusst kein Profil.** Mehrere Profile hängen von
ihnen ab, und eine Abhängigkeit über eine Profilgrenze hinweg ist in Compose
kein gültiges Projekt — `--profile sim` allein bricht dann mit „depends on
undefined service emqx" ab.

### Profile: was womit startet

| Befehl | Startet |
|---|---|
| `make up` | sim + core + obs — die Fabrik läuft und wird sichtbar |
| `make up-ai` | zusätzlich die KI-Schicht |
| `docker compose --profile edge up -d caddy` | zusätzlich die öffentlichen Adressen |
| `make down` | alles anhalten (Daten bleiben) |
| `make nuke` | alles entfernen **inklusive Daten** |

### Netzentwurf

| Netz | Eigenschaft | Wer hängt dran |
|---|---|---|
| `backend` | `internal: true` — **kein Internet** | alle Dienste |
| `egress` | normal | ausschließlich `zellwerk-llm` |
| `edge` | normal, ohne Internetbedarf | ausschließlich `caddy` |

Genau eine Tür nach draußen, und die führt zur Modell-API. Kein anderer Dienst
kann versehentlich telefonieren.

Zwei Dinge, die man dabei wissen sollte: Ein Container, der **nur** in einem
`internal`-Netz hängt, kann keine Ports veröffentlichen — Docker hat dann keine
Route aus dem Host-Namensraum, und der Container startet klaglos ganz ohne
Portbindung. Deshalb hat der Edge ein eigenes Netz, obwohl er selbst nichts nach
außen sendet.

---

## 11. Datenmodell

Alles in **einer** Datenbank: die Genealogie-Abfragen verbinden Stammdaten mit
Messwerten, und ein Join über zwei Systeme hinweg wäre der teuerste Teil jeder
Anfrage.

| Tabelle | Zweck | Besonderheit |
|---|---|---|
| `asset` | Anlagenregister | mit OPC-UA-Endpunkt |
| `production_order` | Fertigungsaufträge aus dem Mock-ERP | |
| `lot` | Charge je Prozessschritt | `parent_id` bildet die Kette, `traits` trägt die Merkmale |
| `cell` | Einzelzelle | `order_id` gespiegelt, damit „welche Zellen gehören zu Auftrag X" ohne rekursiven Durchlauf geht |
| `genealogy` | Kanten Los→Los und Los→Zelle | |
| `measurement` | Hypertable, Zeitreihen | mit Qualitätskennzeichen `good`/`bad`/`uncertain` |
| `event` | Hypertable, Alarme | entprellt, quittierbar |
| `action_log` | Audit jedes Werkzeugaufrufs | ohne das wäre „Shadow Mode" eine Behauptung |
| `process_window` | Sollbereiche | dieselbe Wahrheit für Werkzeuge und Dashboards |

**`traits` ist der Schlüssel zum Verständnis.** Dort stehen die Merkmale, mit
denen ein Los gefertigt wurde — und die es an die nächste Stufe weitergibt. Ein
Slurry-Los trägt `viskositaet_pas`, das Elektroden-Los daraus erbt es als
`vorstufe_viskositaet_pas`, und so weiter bis zur Zelle. **Dort liegt die
Evidenz, auf die sich jede Ursachenaussage stützt.**

### Selbst in die Datenbank schauen (Ort B)

```bash
docker compose exec timescaledb psql -U zellwerk -d zellwerk
```

Danach bist du in der SQL-Konsole. Nützliche Abfragen:

```sql
-- Zellen nach Status
SELECT status, count(*) FROM cell GROUP BY status;

-- Genealogie einer Zelle rückwärts bis zur Slurry-Charge
WITH RECURSIVE pfad AS (
    SELECT c.serial, l.id AS lot_id, l.station, l.parent_id, l.traits, 0 AS tiefe
    FROM cell c JOIN lot l ON l.id = c.lot_id
    WHERE c.serial = 'ZW-2026-BJQTG-000042'
  UNION ALL
    SELECT p.serial, l.id, l.station, l.parent_id, l.traits, p.tiefe+1
    FROM pfad p JOIN lot l ON l.id = p.parent_id
)
SELECT tiefe, station, lot_id, traits FROM pfad ORDER BY tiefe;

-- Was hat ein Agent zuletzt vorgeschlagen?
SELECT ts, tool, begruendung FROM action_log
WHERE tool LIKE 'propose%' ORDER BY ts DESC LIMIT 3;
```

Verlassen mit `\q`.

### Topic-Baum

`zellwerk/v1/{site}/{area}/{line}/{station}/{kind}/{name}` mit `kind` ∈
`pv` · `state` · `event` · `trace` · `cmd`. Nutzlast immer
`{ts, value, quality, unit}`.

Mitlesen, was gerade fließt (Ort B):

```bash
docker compose exec ingest python -c "
import asyncio, aiomqtt, json
async def main():
    async with aiomqtt.Client('emqx', 1883) as c:
        await c.subscribe('zellwerk/v1/#')
        n = 0
        async for m in c.messages:
            print(m.topic, json.loads(m.payload).get('value'))
            n += 1
            if n >= 20: break
asyncio.run(main())"
```

---

## 12. Die Werkzeuge der Agenten

Alle read-only außer den zwei markierten. Jeder Aufruf landet im `action_log`.

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
— `query_timeseries` etwa die Liste der bekannten Kennzahlen dieser Anlage.

### An Claude Desktop oder Claude Code anbinden

Der MCP-Server spricht zwei Transporte. Für Claude Desktop (stdio), Eintrag in
dessen Konfigurationsdatei:

```json
{
  "mcpServers": {
    "zellwerk": {
      "command": "docker",
      "args": ["compose", "-f", "/pfad/zu/zellwerk/docker-compose.yml",
               "exec", "-T", "mcpserver", "python", "-m", "mcpserver.server"]
    }
  }
}
```

Für Dienste im selben Netz läuft er zusätzlich über SSE auf Port 8765 — das ist
die Betriebsart im Container (`--http`).

---

## 13. Konfiguration

Alles in `.env` im Wurzelverzeichnis. Nach jeder Änderung müssen die betroffenen
Container **neu erzeugt** werden (Ort A):

```bash
docker compose --profile sim --profile core --profile obs up -d --force-recreate
```

| Variable | Vorgabe | Bedeutung |
|---|---|---|
| `ZW_DB_PASSWORD` | `zellwerk` | Datenbankpasswort |
| `ZW_GRAFANA_PASSWORD` | `zellwerk-demo` | Grafana-Anmeldung, Benutzer `admin` |
| `ZW_EMQX_PASSWORD` | `zellwerk-demo` | EMQX-Konsole, Benutzer `admin` |
| `ZW_SITE` | `werk1` | Wurzel des Topic-Baums |
| `ZW_TICK_S` | `1.0` | Takt der Fabrik in Sekunden |
| `ZW_SPEED` | `1.0` | Zeitraffer; 30 = eine Minute entspricht einer halben Stunde |
| `ZW_MODEL` | `claude-haiku-4-5-20251001` | Modell für die Agenten |
| `ZW_LLM_BASE_URL` | `http://zellwerk-llm:4010` | Basis-URL des LLM-Zugangs |
| `ZW_LIVE_ACTIONS` | `false` | ausführende Aktionen; im MVP bewusst aus |
| `ZW_MAX_RUNDEN` | `16` | Rundenlimit je Playbook-Lauf |

### Die wichtigste Falle: Passwörter wirken nur beim ersten Start

`ZW_GRAFANA_PASSWORD` und `ZW_EMQX_PASSWORD` werden **nur beim allerersten Start
eines frischen Volumes** ausgewertet. Besteht das Volume bereits, kommt der
Container sauber mit gesetzter Variable hoch — und es gilt trotzdem das alte
Passwort. Am laufenden System (Ort B):

```bash
docker compose exec grafana grafana cli admin reset-admin-password <neues>
docker compose exec emqx emqx ctl admins passwd admin <neues>
```

### Die zweite Falle: `ZW_SPEED` muss durchgereicht werden

Der Zeitraffer steht in der `environment`-Sektion des Dienstes. Fehlt dort der
Eintrag, erreicht ein `ZW_SPEED=30 docker compose up` den Container **nicht** —
und die Fabrik läuft weiter in Echtzeit, ohne jede Fehlermeldung. In diesem Repo
ist der Eintrag gesetzt; wer die Compose-Datei anpasst, sollte ihn nicht
entfernen.

---

## 14. Betrieb hinter einem Reverse-Proxy

Das Profil `edge` startet einen Caddy, der die Dienste unter eigenen Hostnamen
ausliefert.

**Schritt 1 — Adressen in der `.env` eintragen:**

```bash
ZELLWERK_HOST=dashboard.example.com
ZW_AGENTS_HOST=agenten.example.com
ZW_FACTORY_HOST=fabrik.example.com
ZW_CONSOLE_HOST=broker.example.com
ZW_GRAFANA_ROOT_URL=https://dashboard.example.com
ZW_GRAFANA_DOMAIN=dashboard.example.com
BASIC_AUTH_USER=benutzername
BASIC_AUTH_HASH=$2a$14$...
```

Den Hash erzeugen (Ort A):

```bash
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'DEIN_PASSWORT'
```

> **Achtung bei `$` im Hash:** In einer `.env`-Datei, die Compose interpretiert,
> müssen `$` als `$$` geschrieben werden. Sonst kürzt Compose den Hash
> stillschweigend und jede Anmeldung scheitert mit 401 — bei korrektem Passwort.

**Schritt 2 — starten:**

```bash
docker compose --profile edge up -d caddy
```

### Was dabei zu beachten ist

Alle Site-Adressen im `Caddyfile` tragen das Präfix **`http://`**. Das ist
Absicht: TLS terminiert der *vorgelagerte* Proxy. Ohne dieses Präfix leitet
Caddy jede Anfrage mit 308 auf https um, und die beiden Proxys laufen im Kreis.

**Grafana und EMQX stehen NICHT hinter Basic Auth.** Beide bringen ein eigenes
Login mit und beantworten nicht angemeldete Aufrufe selbst mit `401` und
`WWW-Authenticate`. Steht davor noch Basic Auth, hält der Browser das für eine
erneute Aufforderung und zeigt den Anmeldedialog endlos — eine Schleife, die nur
mit Abbruch und 401 endet. Musterfabrik und Agenten haben kein eigenes Login und
stehen deshalb dahinter.

Grafana braucht außerdem `GF_SERVER_ROOT_URL` mit der öffentlichen Adresse,
sonst zeigen Weiterleitungen nach dem Login auf den internen Containernamen.

---

## 15. LLM-Zugang

Die Agenten sprechen nicht direkt mit einer Modell-API, sondern über den Dienst
`zellwerk-llm`. Er stellt `POST /v1/messages` im Anthropic-Format bereit, sodass
das offizielle SDK unverändert benutzbar bleibt:

```python
from anthropic import Anthropic
client = Anthropic(base_url=os.environ["ZW_LLM_BASE_URL"], api_key="unused")
```

Der Dienst erwartet ein Zugangstoken unter `/creds/creds.json` im Format:

```json
{"claudeAiOauth": {"accessToken": "...", "expiresAt": 1786580000000}}
```

**Er erneuert das Token niemals selbst.** Das ist keine Stilfrage: eine
Erneuerung liefert ein neues Refresh-Token und macht das alte serverseitig
ungültig. Ein zweiter Prozess, der dasselbe Token erneuert, legt damit den
ersten lahm. Wer diesen Dienst nachbaut, sollte diese Eigenschaft beibehalten.

Wie das Token in den Container gelangt, hängt von der Umgebung ab und ist
bewusst nicht Teil dieses Repos. Solange keins vorliegt, meldet `/health`
ehrlich rot:

```bash
docker compose exec zellwerk-llm python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://127.0.0.1:4010/health')))"
```

Alternativ lässt sich `ZW_LLM_BASE_URL` auf einen beliebigen anderen
Anthropic-kompatiblen Endpunkt zeigen — dann wird `zellwerk-llm` nicht
gebraucht.

---

## 16. Tests

Die Tests laufen **ohne Docker**, offline in Simulationszeit, ohne Broker und
ohne Datenbank. **Ort A — im Repo-Verzeichnis:**

```bash
uv venv .venv
uv pip install --python .venv/bin/python pytest pytest-asyncio pyyaml ruff
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check packages/ tests/ data/
```

Erwartet: **19 bestanden**, ruff ohne Beanstandung.

### Welche Tests warum wichtig sind

`test_f1_pflanzt_sich_ueber_das_material_bis_zur_kapazitaet_fort` prüft jedes
Glied der Kausalkette einzeln. **Fällt er durch, hat der Diagnose-Agent später
nichts zu finden** — dann sind die sechs Stationen nur unabhängige
Zufallsgeneratoren.

`test_f4_senkt_kapazitaet_ohne_die_porositaet_anzufassen` schlägt fehl, sobald
F4 die Porosität mitzieht. Dann wäre es nicht mehr von F1 unterscheidbar, und
die Triage-Aufgabe wäre trivial statt echt.

`test_normalbetrieb_produziert_gute_zellen` stellt sicher, dass ohne Fehler auch
kein Ausschuss entsteht — sonst wären alle Alarme wertlos.

### Lastmessung gegen den laufenden Stack (Ort B)

```bash
docker compose exec ingest python -m tests.load.ingest_load --rate 5000 --sekunden 30 --aufraeumen
```

Misst, wie viele Werte je Sekunde tatsächlich in der Datenbank ankommen — nicht,
wie viele gesendet wurden. Das Skript meldet außerdem, wenn es selbst der
Flaschenhals war, statt seine eigene Grenze als Ergebnis auszugeben.

---

## 17. Fehlersuche

| Symptom | Ursache | Abhilfe |
|---|---|---|
| Zellen- und Chargenzähler bleiben null | normal in der ersten Stunde — die Durchlaufzeit beträgt ~50 min | warten oder Zeitraffer einschalten |
| `make up` bricht mit „depends on undefined service" ab | ein Profil wurde einzeln gestartet, dessen Abhängigkeit in einem anderen liegt | `make up` benutzt alle nötigen Profile |
| Grafana zeigt „Welcome to Grafana" statt des Dashboards | `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` fehlt | ist in diesem Repo gesetzt; nach eigenen Änderungen prüfen |
| Grafana-Panels zeigen „No data" | Datenquelle kann sich nicht anmelden | `ZW_DB_PASSWORD` muss **auch in der Grafana-Umgebung** stehen, nicht nur in der von Compose |
| Anmeldung an Grafana/EMQX scheitert trotz richtigem Passwort | Variable wirkt nur beim ersten Start eines frischen Volumes | Passwort per CLI setzen, siehe [Abschnitt 13](#13-konfiguration) |
| Endlose Anmeldeaufforderung im Browser | Basic Auth vor einem Dienst mit eigenem Login | Basic Auth für diesen Host entfernen |
| Zeitraffer wirkt nicht | Container wurde nicht neu erzeugt | `--force-recreate` verwenden und mit `printenv ZW_SPEED` prüfen |
| Agenten-Lauf endet sofort mit Fehler | kein LLM-Zugang | `/health` des LLM-Dienstes prüfen |
| Messwerte kommen als `null` an | Konnektor hat die Verbindung verloren | Logs ansehen: `docker compose logs connector` |

### Logs ansehen (Ort A)

```bash
docker compose logs -f simfactory     # einen Dienst verfolgen
docker compose logs --tail=50 rules   # letzte 50 Zeilen
make logs s=ingest                    # Kurzform
```

### Alles zurücksetzen (Ort A)

```bash
make nuke     # entfernt Container UND Volumes — alle Daten weg
make up       # frisch starten
```

---

## 18. Gemessene Ergebnisse

Alles aus echten Läufen:

| | |
|---|---|
| Kausalkette F1 | Viskosität 6,99 Pa·s (Soll 2–6) → Streuung 10,95 µm → Porosität 20,62 % (Soll 28–38) → Kapazitätsausfall |
| Genealogie | von der Ausschusszelle per SQL bis zur Slurry-Charge, vier Stufen |
| Edge-Regel-Latenz | Symptom → Kommando: min 0,30 ms · Median 0,40 ms · max 0,50 ms (n = 8), Anforderung 500 ms |
| Geschlossener Regelkreis | Regel feuert bei 50,51 °C → Kommando in 0,5 ms → Drosselung auf Faktor 0,50 → Temperatur fällt auf 44,4 °C |
| Fließgleichgewicht | vier Stunden Normalbetrieb, alle Warteschlangen bei 0 |
| Ingest unter Last | 99.990 Werte in 20 s = 4999/s, **0 % Verlust** (der Lastgenerator selbst erreichte 4999/s — die Obergrenze des Schreibpfads wurde damit nicht ermittelt) |
| Ausschuss-Triage bei F4 | 8 Runden, 13 Werkzeuge; Dosierpumpe benannt, Cpk −0,141 als Beleg, Mischer/Coater/Kalander namentlich ausgeschlossen |
| F3 gegen F5 | korrekt gegensätzlich klassifiziert: Quarantäne gegen Umlagern |
| Batteriepass | 16 Prozess-Kennwerte über vier Fertigungsstufen |

---

## 19. Entwurfsentscheidungen

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

**Alarme werden entprellt** (`packages/core/events`). Ein Alarmsystem, das jede
Grenzverletzung einzeln meldet, erzeugt bei einem schwankenden Messwert hunderte
Einträge — und wird deshalb ignoriert. Genau dann ist es wertlos, wenn es
gebraucht wird.

**Zeitstempel sind Wanduhrzeit, auch im Zeitraffer.** Trüge ein Los seine
Simulationszeit, liefen die Achsen auseinander, und die Abfrage „Prozesswerte im
Fertigungszeitraum dieses Loses" fände nichts.

**Die Fertigung hält nicht an, wenn das ERP hakt.** Der Auftragsclient ist
fehlertolerant: fällt das ERP aus, produziert die Fabrik ohne Auftragsbezug
weiter. Der Ausfall wird protokolliert, nicht verschluckt.

Weiterführend:
- `docs/decisions.md` — wo und warum die Umsetzung von der Spezifikation abweicht
- `docs/demo-drehbuch.md` — Vorführungsablauf in fünf Akten, mit einem Abschnitt
  „Was man nicht behaupten sollte"

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).
