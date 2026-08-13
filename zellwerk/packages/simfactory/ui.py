"""Bedienoberfläche der Musterfabrik.

Die REST-Schnittstelle allein reichte nicht: ein Aufruf der Adresse landete auf
`{"detail":"Not Found"}`, weil es schlicht keine Wurzel-Route gab. Wer eine
Adresse öffnet, erwartet eine Seite — nicht die Fehlermeldung eines Frameworks.

Bewusst ohne Frontend-Baukasten: eine Seite, die sich selbst alle drei Sekunden
aktualisiert, braucht kein Bundling. Das hält den Stack bei dem, was §2 der
Spezifikation erlaubt (kein eigenes Web-UI über das Nötige hinaus) und macht
die Fehlerszenarien trotzdem anklickbar.
"""

from __future__ import annotations

STIL = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       background:#0d0e12; color:#dcdde0; margin:0; padding:2rem 1rem;
       line-height:1.55; }
.hülle { max-width: 68rem; margin: 0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; font-weight:600; }
h2 { font-size:1rem; margin:2.2rem 0 .8rem; color:#9aa0a6; font-weight:600;
     text-transform:uppercase; letter-spacing:.04em; }
.unterzeile { color:#8b9096; margin:0 0 1.5rem; }
.raster { display:grid; gap:.9rem; grid-template-columns:repeat(auto-fill,minmax(15rem,1fr)); }
.karte { background:#16181d; border:1px solid #24262c; border-radius:8px; padding:1rem 1.1rem; }
.karte h3 { margin:0 0 .5rem; font-size:.82rem; color:#8b9096; font-weight:600;
            text-transform:uppercase; letter-spacing:.03em; }
.zahl { font-size:1.8rem; font-weight:600; font-variant-numeric:tabular-nums; }
.einheit { font-size:.85rem; color:#8b9096; margin-left:.3rem; }
.fehler { display:grid; gap:.7rem; grid-template-columns:repeat(auto-fill,minmax(19rem,1fr)); }
.fehler .karte { display:flex; flex-direction:column; gap:.6rem; }
.fehler p { margin:0; font-size:.88rem; color:#a8adb3; flex:1; }
button, .knopf { font:inherit; font-size:.9rem; padding:.5rem .9rem; border-radius:6px;
        border:1px solid #2f3b52; background:#1c2534; color:#9dbcff; cursor:pointer;
        text-decoration:none; display:inline-block; text-align:center; }
button:hover, .knopf:hover { background:#243049; }
button.aktiv { border-color:#7a3b3b; background:#331d1d; color:#ff9d9d; }
.marke { display:inline-block; padding:.15rem .5rem; border-radius:4px;
         font-size:.75rem; font-weight:600; }
.gut { background:#14301f; color:#6ee7a0; }
.warn { background:#332a12; color:#ffd479; }
.schlecht { background:#331b1b; color:#ff9d9d; }
a { color:#7aa7ff; }
.fuss { margin-top:2.5rem; padding-top:1.2rem; border-top:1px solid #24262c;
        color:#6f747a; font-size:.85rem; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th { text-align:left; color:#8b9096; font-weight:600; padding:.4rem .6rem;
     border-bottom:1px solid #24262c; font-size:.8rem; text-transform:uppercase; }
td { padding:.45rem .6rem; border-bottom:1px solid #1b1d22; font-variant-numeric:tabular-nums; }
"""

FEHLER = [
    ("F1", "Viskositätsdrift im Mischer",
     "Die Viskosität steigt über 45 Minuten von 4 auf 7 Pa·s. Die Folge zeigt sich "
     "drei Stationen später: die Schichtdicke streut, die Porosität fällt unter Soll, "
     "und die Zellen verlieren Kapazität."),
    ("F2", "Trocknertemperatur zu hoch",
     "Das Flächengewicht bleibt in Ordnung, aber die Haftung leidet. Sichtbar wird "
     "das erst in der Assemblierung als Delamination."),
    ("F3", "Übertemperatur in einem Formierkanal",
     "Ein Kanal läuft über 50 °C. Die Edge-Regel drosselt ihn in unter einer "
     "Sekunde; die betroffene Zelle geht in Quarantäne."),
    ("F4", "Elektrolyt-Unterdosierung",
     "Die Dosierpumpe gibt 5 % zu wenig. In der Station praktisch unsichtbar — "
     "auffällig erst in der Formierung, und dort nicht von einem Porositätsproblem "
     "zu unterscheiden. Nur die Genealogie trennt die beiden Fälle."),
    ("F5", "Ausfall eines Zykler-Kanals",
     "Der Kanal liefert keine gültigen Werte mehr. Das kostet Durchsatz, ist aber "
     "KEIN Qualitätsproblem — die Zellen gehören auf einen anderen Kanal, nicht "
     "in die Quarantäne."),
]


def startseite(zustand: dict, aktive_fehler: list[str], agenten_adresse: str = "") -> str:
    z = zustand
    nach_status = z.get("zellen_nach_status", {})
    warteschlangen = z.get("warteschlangen", {})

    karten = [
        ("Chargen", z.get("lose", 0), ""),
        ("Zellen gesamt", z.get("zellen", 0), ""),
        ("davon in Ordnung", nach_status.get("ok", 0), ""),
        ("Ausschuss", nach_status.get("ausschuss", 0), ""),
        ("Quarantäne", nach_status.get("quarantaene", 0), ""),
        ("in Arbeit", nach_status.get("in_prozess", 0), ""),
    ]
    kartenhtml = "".join(
        f'<div class="karte"><h3>{titel}</h3>'
        f'<div class="zahl">{wert}<span class="einheit">{einheit}</span></div></div>'
        for titel, wert, einheit in karten)

    auftraege = z.get("auftraege", [])
    if auftraege:
        zeilen = ""
        for a in auftraege:
            stand = "erfüllt" if a["erfuellt"] else f"{a['offen']} offen"
            zeilen += (
            f"<tr><td>{a['id']}</td><td>{a['produkt']}</td>"
            f"<td>{a['gefertigt']} / {a['sollmenge']}</td>"
            f"<td>{stand}</td></tr>")
        auftragstabelle = (
            "<table><thead><tr><th>Auftrag</th><th>Produkt</th>"
            "<th>Fortschritt</th><th>Status</th></tr></thead>"
            f"<tbody>{zeilen}</tbody></table>")
    else:
        auftragstabelle = '<p class="unterzeile">Keine Aufträge übernommen.</p>'

    laufzeiten = z.get("fehler_laufzeit_min", {})
    fehlerhtml = ""
    for kennung, titel, beschreibung in FEHLER:
        ist_aktiv = kennung in aktive_fehler
        if ist_aktiv:
            dauer = laufzeiten.get(kennung, 0)
            marke = f'<span class="marke schlecht">läuft seit {dauer:.0f} min</span>'
        else:
            marke = '<span class="marke gut">aus</span>'
        aktion = "zurücknehmen" if ist_aktiv else "einspielen"
        klasse = " aktiv" if ist_aktiv else ""
        fehlerhtml += f"""
        <div class="karte">
          <h3>{kennung} — {titel} {marke}</h3>
          <p>{beschreibung}</p>
          <form method="post" action="/ui/fault/{kennung}">
            <input type="hidden" name="zuruecknehmen" value="{'1' if ist_aktiv else '0'}">
            <button class="{klasse}" type="submit">{aktion}</button>
          </form>
        </div>"""

    stau = "".join(f"<b>{k}</b> {v} " for k, v in warteschlangen.items())
    simzeit = z.get("sim_zeit", "—")[:19].replace("T", " ")
    agentenlink = ""
    if agenten_adresse:
        agentenlink = (f'<p>Untersuchung starten: '
                       f'<a href="{agenten_adresse}">Agenten-Oberfläche</a></p>')

    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>zellwerk — Musterfabrik</title>
<meta http-equiv="refresh" content="5">
<style>{STIL}</style></head><body><div class="hülle">

<h1>Musterfabrik</h1>
<p class="unterzeile">Sechs Stationen · Simulationszeit {simzeit}</p>

<h2>Produktion</h2>
<div class="raster">{kartenhtml}</div>

<h2>Fertigungsaufträge</h2>
{auftragstabelle}

<h2>Fehlerszenarien</h2>
<p class="unterzeile">Jedes Szenario wirkt sich an einer anderen Station aus als dort,
wo es entsteht — genau das macht die Ursachensuche interessant.</p>

<div class="karte" style="margin-bottom:1rem">
  <h3>Wie ein Szenario wieder verschwindet</h3>
  <p>Ein Szenario läuft, bis es <b>hier zurückgenommen</b> wird — von allein
  endet keines. Das ist Absicht: eine Vorführung soll so lange laufen, wie sie
  gebraucht wird.</p>
  <p>Nach dem Zurücknehmen sind die <b>Messwerte sofort wieder normal</b>. Die
  Zellen aber nicht: Chargen, die den Fehler schon mitbekommen haben, wandern
  weiter durch die Linie und fallen später trotzdem durch. Bis die letzte
  betroffene Zelle die Formierung verlassen hat, vergehen rund
  <b>45 Minuten Simulationszeit</b> — Mischer, Coater und Kalander brauchen je
  zehn Minuten, Assemblierung zehn, die Formierung vier.</p>
  <p>Genau darum geht es bei diesem System: Der Fehler ist längst behoben, und
  der Ausschuss läuft trotzdem noch. Wer die betroffenen Chargen benennen kann,
  muss nicht die ganze Schicht sperren.</p>
  <p><b>Eine Ausnahme:</b> Bei F3 greift die Edge-Regel selbst ein und drosselt
  den überhitzten Kanal in unter einer Sekunde — ohne dass jemand etwas anklickt.
  Das Szenario bleibt trotzdem aktiv, bis es hier abgeschaltet wird.</p>
</div>
<div class="fehler">{fehlerhtml}</div>

<h2>Warteschlangen zwischen den Stationen</h2>
<p class="unterzeile">{stau or '—'}</p>

<div class="fuss">
{agentenlink}
<p>Diese Seite aktualisiert sich alle 5 Sekunden.
Schnittstelle: <code>GET /state</code> · <code>POST /faults/&lt;id&gt;</code> ·
<code>DELETE /faults/&lt;id&gt;</code></p>
</div>
</div></body></html>"""
