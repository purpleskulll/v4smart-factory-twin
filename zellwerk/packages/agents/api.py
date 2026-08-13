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
    zeilen = "".join(
        f'<li><a href="/playbook/{k}">{k}</a> — '
        f'{(v.__doc__ or "").splitlines()[0]}</li>'
        for k, v in PLAYBOOKS.items() if k != "pass"
    )
    letzte = "".join(
        f'<li>#{n}: {eintrag["playbook"]} — <b>{eintrag["status"]}</b>'
        f' <a href="/runs/{n}">ansehen</a></li>'
        for n, eintrag in sorted(LAEUFE.items(), reverse=True)[:8]
    ) or "<li>noch keine</li>"
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>zellwerk — Agenten</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:52rem;margin:3rem auto;padding:0 1rem;
       background:#111217;color:#d8d9da;line-height:1.6}}
 a{{color:#6e9fff}} h1{{font-size:1.4rem}} h2{{font-size:1.05rem;margin-top:2rem}}
 li{{margin:.4rem 0}} code{{background:#22242b;padding:.15rem .4rem;border-radius:3px}}
</style></head><body>
<h1>zellwerk — Agenten</h1>
<p>Die Agenten arbeiten im <b>Shadow Mode</b>: sie untersuchen und schlagen vor,
ausgeführt wird nichts. Ein Lauf dauert ein bis zwei Minuten.</p>
<h2>Playbook starten</h2><ul>{zeilen}</ul>
<h2>Letzte Läufe</h2><ul>{letzte}</ul>
<h2>Fehler einspielen</h2>
<p>Über die Musterfabrik: <code>POST /faults/F1</code> … <code>F5</code>.
F1 = Viskositätsdrift, F3 = Übertemperatur, F4 = Elektrolyt-Unterdosierung.</p>
<p>Die Ergebnisse erscheinen auch im Dashboard <i>Agenten &amp; Befunde</i>.</p>
</body></html>"""


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
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>Lauf {nummer}</title>
<meta http-equiv="refresh" content="{10 if aktiv else 600}">
<style>
 body{{font-family:system-ui,sans-serif;max-width:56rem;margin:3rem auto;padding:0 1rem;
       background:#111217;color:#d8d9da;line-height:1.6}}
 pre{{white-space:pre-wrap;background:#191b20;padding:1rem;border-radius:6px}}
 a{{color:#6e9fff}}
</style></head><body>
<p><a href="/">&larr; zurück</a></p>
<h1>Lauf {nummer} — {lauf_daten['playbook']}</h1>
<p>Status: <b>{lauf_daten['status']}</b> · Runden: {lauf_daten.get('runden','—')}</p>
<h2>Bericht</h2><pre>{bericht}</pre>
<h2>Werkzeuge, die der Agent selbst gewählt hat</h2>
<ol>{werkzeuge}</ol>
<p style="color:#888">Die Reihenfolge ist nicht vorgegeben — sie ergibt sich
aus dem, was der Agent unterwegs findet.</p>
</body></html>"""


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "laeufe": len(LAEUFE),
            "playbooks": sorted(PLAYBOOKS)}
