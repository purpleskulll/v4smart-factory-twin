# Demo-Drehbuch

Der komplette Durchlauf: Fehler einspielen → Agent findet die Ursache →
Vorschlag → Rückverfolgung → Batteriepass. Dauer etwa zwölf Minuten.

Die Zahlen unten stammen aus einem echten Lauf, nicht aus der Planung. Wer die
Demo hält, sollte sie vorher einmal selbst durchspielen — die Werte schwanken
leicht, die Größenordnungen nicht.

---

## Vorbereitung (vor dem Gespräch, nicht währenddessen)

```bash
make up-ai
make ps          # alle Dienste healthy?
```

Die Fabrik braucht rund 30 Minuten, bis die erste Zelle vollständig
durchgelaufen ist. Für eine Demo lässt sich das raffen:

```bash
ZW_SPEED=30 docker compose --profile sim up -d --force-recreate simfactory
```

Damit entspricht eine Minute Wanduhrzeit einer halben Stunde Fertigung. **Vor
dem Gespräch mindestens fünf Minuten laufen lassen**, sonst gibt es keine
Historie, auf die sich ein Agent stützen könnte — und ein Agent ohne Daten
sagt zu Recht „kann ich nicht beantworten".

---

## Akt 1 — Die Linie läuft (2 min)

Grafana öffnen, Dashboard „zellwerk — Linienübersicht".

Zu zeigen: sechs Stationen, jede mit ihrem Sollfenster als gestrichelte Linie.
Alles läuft im grünen Bereich. Die Formierung zeigt acht Kanaltemperaturen
übereinander.

Der Satz dazu: *Das ist eine Zellfertigung von der Slurry bis zur formierten
Zelle. Jeder Wert hat ein Sollfenster, jede Charge eine Nummer, jede Zelle eine
Seriennummer.*

---

## Akt 2 — Ein Fehler entsteht (3 min)

```bash
make fault id=F1
```

Im Dashboard verfolgen — in dieser Reihenfolge, das ist der Punkt:

| Zeit | Was passiert |
|---|---|
| t+0 | Viskosität beginnt zu steigen, noch im Fenster |
| ~t+90 s | Viskosität verlässt das Sollfenster (6,6 Pa·s) |
| ~t+120 s | Porosität am Kalander fällt (30,4 %), erster Alarm `MIX_VISC_HIGH` |
| ~t+150 s | Porosität unter Soll (20,0 %), Alarm `CAL_POROSITY_LOW` |
| danach | Zellen fallen in der Formierung durch den Kapazitätstest |

Der Satz dazu: *Der Fehler entsteht im Mischer. Sichtbar wird er drei Stationen
später. Das ist der Normalfall in der Fertigung — und der Grund, warum
Ursachensuche dort Wochen dauert.*

**Nicht vorwegnehmen, was der Agent gleich findet.** Die Demo lebt davon, dass
das Publikum die Frage selbst im Kopf hat.

---

## Akt 3 — Der Agent (4 min, das Herzstück)

```bash
docker compose exec agents python -m agents.runner triage
```

Der Agent arbeitet sichtbar: er verschafft sich einen Überblick, sucht
betroffene Zellen, verfolgt eine davon zurück, vergleicht jede Station mit ihrem
Sollfenster — und prüft ausdrücklich auch die **zweite mögliche Ursache**.

Das ist der Teil, auf den es ankommt. Zu wenig Kapazität kann zwei Ursachen
haben: zu niedrige Porosität *oder* zu wenig Elektrolyt. In der Formierung sehen
beide gleich aus. Der Agent schließt die Elektrolyt-Spur mit dem Prozessfenster
der Dosierung aus und belegt die andere über die Genealogie.

Aus einem echten Lauf:

> Die verursachende Station ist **mixer01**. Charge SLURRY-0020 wurde mit
> Viskosität 7,087 Pa·s verarbeitet (Sollbereich 2–6). Das führte zu einer
> Porosität von 19,46 % beim Kalandrieren (Soll 28–38), was sich ab der
> Formierung als Kapazitätsausfall zeigt. Betroffen: ZW-2026-000131 bis
> ZW-2026-000135 mit 4,08–4,11 Ah. Elektrolytzufuhr ausgeschlossen (Cpk 1,323).

Der Satz dazu: *Elf Werkzeugaufrufe, unter zwei Minuten. Und wichtiger als die
Geschwindigkeit: die Aussage ist belegt. Jede Zahl steht mit Station, Charge und
Zeitraum da — damit kann ein Fertigungsleiter arbeiten.*

**Auf die letzte Zeile zeigen.** Der Agent hat nichts gesperrt. Er hat einen
Vorschlag gemacht, der im Audit-Log liegt und auf eine Freigabe wartet.

---

## Akt 4 — Die Gegenprobe (2 min)

Der interessantere Fall, weil er zeigt, dass der Agent nicht bloß Muster rät:

```bash
make fault id=F3        # Übertemperatur in einem Formierkanal
make fault id=F5        # ein anderer Kanal fällt aus
docker compose exec agents python -m agents.runner formierung
```

Beide sehen im Dashboard nach „Kanal auffällig" aus. Sie verlangen aber das
Gegenteil voneinander:

| | Kanal mit Übertemperatur | Ausgefallener Kanal |
|---|---|---|
| Messwerte | gültig, zu hoch (58 °C) | ungültig (`quality=bad`) |
| Zelle | geschädigt | in Ordnung |
| Maßnahme | Quarantäne + drosseln | auf anderen Kanal umlagern |

Der Agent trennt die beiden Fälle und begründet die Trennung mit der
Messqualität — nicht mit der Höhe der Werte.

Der Satz dazu: *Ein Klassifikator, der „Kanal auffällig" meldet, hätte hier
beide Male dasselbe gesagt. Die Zelle im zweiten Fall wäre grundlos verschrottet
worden.*

---

## Akt 5 — Rückverfolgung und Batteriepass (2 min)

```bash
docker compose exec agents python -m agents.runner trace \
  --frage "Welche Zellen stammen aus Slurry-Charge SLURRY-0020 und wo sind sie jetzt?"

docker compose exec agents python -m agents.runner pass --serial ZW-2026-000207
```

Der Pass enthält Kennung, Chemie, gemessene Kapazität, die vollständige
Genealogie über alle vier Stufen und die Prozess-Kennwerte je Stufe.

**Ausdrücklich sagen:** Das ist ein Demo-Subset der Verordnung (EU) 2023/1542,
nicht rechtsverbindlich, und der CO₂-Fußabdruck ist ein Platzhalter. Der Pass
sagt das auch selbst — in seinem ersten Feld.

Der Satz dazu: *Das ist kein Extra-Feature. Es ist dieselbe Genealogie, die eben
die Ursache gefunden hat, nur anders herum gelesen.*

---

## Was man nicht behaupten sollte

Ein paar Grenzen, die man besser selbst nennt, bevor jemand nachfragt:

- **Die Fabrik ist simuliert.** Die Prozessparameter sind plausible
  Lehrbuchwerte, keine realen Anlagendaten. Der Konnektor ist so gebaut, dass
  er auf echte Steuerungen zeigen kann — bewiesen ist das damit nicht.
- **Die Formierungskurven sind Modellkurven**, nicht aus einem Messdatensatz
  abgeleitet (siehe `decisions.md`, D2).
- **Der Agent bekommt aufbereitete Daten.** Das semantische Modell ist die
  eigentliche Arbeit; ohne es hätte dasselbe Modell nur Tabellen.
- **Cpk ist eine Näherung** und bei laufendem Drift kein Fähigkeitsnachweis.
  Das Werkzeug sagt das dazu.

---

## Zurücksetzen

```bash
curl -X DELETE http://localhost:8001/faults/F1
docker compose --profile sim up -d --force-recreate simfactory   # zurück auf Echtzeit
```

Für einen komplett sauberen Stand: `make nuke && make up-ai`. Danach braucht es
wieder Vorlauf, bevor Daten da sind.
