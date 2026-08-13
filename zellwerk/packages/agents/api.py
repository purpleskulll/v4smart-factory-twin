"""HTTP-Zugang zu den Playbooks (SPEC §10).

Ohne diesen Dienst waren die Agenten nur über die Kommandozeile erreichbar —
und damit in einer Vorführung praktisch unsichtbar. Das Interessante an diesem
System ist aber gerade der Agent, der eine Ursache findet und belegt; wenn man
den nicht zeigen kann, zeigt man nur eine Zeitreihendatenbank.

Ein Lauf dauert ein bis zwei Minuten. Er wird deshalb im Hintergrund
gestartet, und der Aufrufer bekommt sofort eine Laufnummer zurück. Das
Ergebnis landet im `action_log` und erscheint im Dashboard „Agenten &
Befunde" — es geht also nicht verloren, wenn niemand auf die Antwort wartet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from simfactory.ui import STIL

from .llm import LLM
from .playbooks import PLAYBOOKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("agents.api")

app = FastAPI(title="zellwerk Agenten")

# Laufende und abgeschlossene Läufe dieser Prozesslebenszeit. Die dauerhafte
# Ablage ist das action_log in der Datenbank — das hier ist nur der schnelle
# Blick für den, der gerade wartet.
LAEUFE: dict[int, dict] = {}
_zaehler = 0
_sperre = asyncio.Lock()


async def _ausfuehren(nummer: int, name: str, kwargs: dict) -> None:
    LAEUFE[nummer]["status"] = "laeuft"
    try:
        playbook = PLAYBOOKS[name](LLM())
        ergebnis = await playbook.run(**kwargs)
        LAEUFE[nummer].update({
            "status": "fertig",
            "bericht": ergebnis.bericht,
            "werkzeuge": [e["werkzeug"] for e in ergebnis.evidenz],
            "runden": ergebnis.runden,
            "beendet": datetime.now(UTC).isoformat(),
        })
        log.info("Lauf %d (%s) fertig: %d Runden, %d Werkzeuge",
                 nummer, name, ergebnis.runden, len(ergebnis.evidenz))
    except Exception as exc:  # noqa: BLE001
        # Fehler sichtbar machen, nicht verschlucken: ein Lauf, der still
        # scheitert, sieht aus wie einer, der noch arbeitet.
        LAEUFE[nummer].update({"status": "fehler", "fehler": f"{type(exc).__name__}: {exc}"})
        log.exception("Lauf %d (%s) gescheitert", nummer, name)


@app.get("/", response_class=HTMLResponse)
async def start() -> str:
    """Eine schlichte Seite zum Auslösen — kein Terminal nötig."""
    beschreibungen = {
        "triage": ("Ausschuss-Triage", "Sucht die Station, die den Ausschuss "
                   "verursacht — und prüft ausdrücklich auch die zweite mögliche "
                   "Ursache, statt bei der erstbesten zu bleiben."),
        "formierung": ("Formierungs-Anomalie", "Unterscheidet ein Anlagenproblem "
                       "von einem Zellproblem. Beide sehen im Dashboard gleich aus, "
                       "verlangen aber gegenteilige Maßnahmen."),
        "trace": ("Rückverfolgung", "Beantwortet eine Frage in natürlicher Sprache "
                  "über Chargen, Zellen und ihre Herkunft."),
    }
    zeilen = ""
    for k in PLAYBOOKS:
        if k == "pass":
            continue
        titel, text = beschreibungen.get(k, (k, ""))
        if k == "trace":
            feld = ("width:100%;padding:.5rem;margin-bottom:.5rem;background:#0d0e12;"
                    "border:1px solid #2f3b52;border-radius:6px;color:#dcdde0;font:inherit")
            zeilen += f'''<div class="karte"><h3>{titel}</h3><p>{text}</p>
              <form method="get" action="/playbook/trace">
                <input name="frage" style="{feld}"
                       placeholder="z. B. Welche Zellen stammen aus SLURRY-0003?">
                <button type="submit">starten</button>
              </form></div>'''
        else:
            zeilen += (f'<div class="karte"><h3>{titel}</h3><p>{text}</p>'
                       f'<a class="knopf" href="/playbook/{k}">starten</a></div>')
    marken = {"fertig": "gut", "laeuft": "warn", "wartet": "warn", "fehler": "schlecht"}
    if LAEUFE:
        zeilen_laeufe = "".join(
            f'<tr><td>#{n}</td><td>{e["playbook"]}</td>'
            f'<td><span class="marke {marken.get(e["status"],"warn")}">{e["status"]}</span></td>'
            f'<td>{e.get("runden","—")}</td>'
            f'<td><a href="/runs/{n}">Bericht ansehen</a></td></tr>'
            for n, e in sorted(LAEUFE.items(), reverse=True)[:10])
        letzte = ("<table><thead><tr><th>Nr.</th><th>Playbook</th><th>Status</th>"
                  "<th>Runden</th><th></th></tr></thead>"
                  f"<tbody>{zeilen_laeufe}</tbody></table>")
    else:
        letzte = '<p class="unterzeile">Noch kein Lauf gestartet.</p>'
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>zellwerk — Agenten</title>
<style>{STIL}</style></head><body><div class="hülle">

<h1>Agenten</h1>
<p class="unterzeile">Shadow Mode — die Agenten untersuchen und schlagen vor.
Ausgeführt wird nichts.</p>

<h2>Untersuchung starten</h2>
<div class="fehler">{zeilen}</div>

<h2>Läufe</h2>
{letzte}

<div class="fuss">
<p>Ein Lauf dauert ein bis zwei Minuten. Jeder Werkzeugaufruf landet im
Audit-Log und erscheint zusätzlich im Grafana-Dashboard
<i>Agenten &amp; Befunde</i>.</p>
</div>
</div></body></html>"""


@app.get("/playbook/{name}")
@app.post("/playbook/{name}")
async def starte_playbook(name: str, hintergrund: BackgroundTasks, request: Request,
                          frage: str | None = None, serial: str | None = None):
    """Startet ein Playbook. Antwortet sofort mit der Laufnummer.

    Ein Klick im Browser landet direkt auf der Ergebnisseite; ein Aufruf per
    Programm bekommt JSON. Wer im Browser klickt, will das Ergebnis sehen und
    keine JSON-Zeile lesen müssen.
    """
    global _zaehler
    if name not in PLAYBOOKS:
        raise HTTPException(404, f"unbekanntes Playbook: {name}. "
                                 f"Verfügbar: {', '.join(PLAYBOOKS)}")

    kwargs: dict = {}
    if name == "trace":
        if not frage:
            raise HTTPException(400, "für 'trace' wird ?frage=… gebraucht")
        kwargs["frage"] = frage
    if name == "pass":
        if not serial:
            raise HTTPException(400, "für 'pass' wird ?serial=… gebraucht")
        kwargs["serial"] = serial

    async with _sperre:
        _zaehler += 1
        nummer = _zaehler
    LAEUFE[nummer] = {"playbook": name, "status": "wartet",
                      "gestartet": datetime.now(UTC).isoformat(), "parameter": kwargs}
    hintergrund.add_task(_ausfuehren, nummer, name, kwargs)

    antwort = {"lauf": nummer, "playbook": name, "status": "gestartet",
               "abrufen": f"/runs/{nummer}",
               "hinweis": "Ein Lauf dauert ein bis zwei Minuten. Das Ergebnis "
                          "erscheint auch im Dashboard 'Agenten & Befunde'."}
    if "text/html" in (request.headers.get("accept") or ""):
        return RedirectResponse(f"/runs/{nummer}", status_code=303)
    return antwort


@app.get("/runs")
async def alle_laeufe() -> dict:
    return {"laeufe": [
        {"nummer": n, **{k: v for k, v in eintrag.items() if k != "bericht"}}
        for n, eintrag in sorted(LAEUFE.items(), reverse=True)]}


@app.get("/runs/{nummer}", response_class=HTMLResponse)
async def lauf(nummer: int) -> str:
    if nummer not in LAEUFE:
        raise HTTPException(404, f"Lauf {nummer} unbekannt")
    lauf_daten = LAEUFE[nummer]
    bericht = lauf_daten.get("bericht") or lauf_daten.get("fehler") or "— läuft noch —"
    werkzeuge = "".join(
        f"<li>{w}</li>" for i, w in enumerate(lauf_daten.get("werkzeuge", []), 1)
    ) or "<li>— noch keine —</li>"
    aktiv = lauf_daten["status"] in ("wartet", "laeuft")
    marke = {"fertig": "gut", "fehler": "schlecht"}.get(lauf_daten["status"], "warn")
    hinweis = ("<p class=\"unterzeile\">Der Lauf arbeitet — diese Seite lädt sich "
               "alle 10 Sekunden neu.</p>" if aktiv else "")
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lauf {nummer} — zellwerk</title>
<meta http-equiv="refresh" content="{10 if aktiv else 600}">
<style>{STIL}
 pre{{white-space:pre-wrap;background:#16181d;border:1px solid #24262c;
      padding:1.1rem;border-radius:8px;font-size:.9rem;line-height:1.6}}
 ol{{padding-left:1.4rem}} ol li{{margin:.3rem 0}}
</style></head><body><div class="hülle">
<p><a href="/">&larr; Übersicht</a></p>
<h1>Lauf {nummer} — {lauf_daten['playbook']}</h1>
<p class="unterzeile">Status <span class="marke {marke}">{lauf_daten['status']}</span>
 · {lauf_daten.get('runden','—')} Runden</p>
{hinweis}
<h2>Bericht</h2>
<pre>{bericht}</pre>
<h2>Werkzeuge, die der Agent selbst gewählt hat</h2>
<ol>{werkzeuge}</ol>
<p class="unterzeile">Die Reihenfolge ist nicht vorgegeben — sie ergibt sich aus
dem, was der Agent unterwegs findet.</p>
</div></body></html>"""


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "laeufe": len(LAEUFE),
            "playbooks": sorted(PLAYBOOKS)}
