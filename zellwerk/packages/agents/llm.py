"""LLM-Anbindung der Agenten (SPEC §10, docs/decisions.md D1).

Es gibt KEINEN Anthropic-API-Key. Der Zugang läuft über den Dienst
`zellwerk-llm`, der das Claude-Abo des Betreibers vermittelt. Das offizielle
Anthropic-SDK bleibt trotzdem die Bibliothek — es wird nur auf eine andere
Basis-URL gezeigt. Der Proxy spricht `POST /v1/messages` im Anthropic-Format,
also merkt das SDK keinen Unterschied.

Die Tool-Schleife steht hier und nicht in den Playbooks: jedes Playbook soll
seinen Ablauf beschreiben, nicht das Protokoll nachbauen.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

log = logging.getLogger("agents.llm")

BASE_URL = os.environ.get("ZW_LLM_BASE_URL", "http://zellwerk-llm:4010")
MODEL = os.environ.get("ZW_MODEL", "claude-haiku-4-5-20251001")
MAX_RUNDEN = int(os.environ.get("ZW_MAX_RUNDEN", "12"))


@dataclass
class ToolCall:
    """Ein Werkzeugaufruf mit seinem Ergebnis — die Evidenz eines Befunds."""

    name: str
    eingabe: dict
    ergebnis: Any


@dataclass
class Lauf:
    """Ergebnis eines Playbook-Laufs."""

    antwort: str
    aufrufe: list[ToolCall] = field(default_factory=list)
    runden: int = 0
    abgebrochen: bool = False

    def evidenz(self) -> list[dict]:
        """Die Tool-Aufrufe in Kurzform — für das Audit und den Bericht."""
        return [{"werkzeug": a.name, "eingabe": a.eingabe} for a in self.aufrufe]


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        # api_key wird vom Proxy ignoriert, das SDK verlangt aber einen Wert.
        self.client = Anthropic(base_url=base_url or BASE_URL, api_key="proxy-vermittelt")
        self.model = model or MODEL

    async def run_with_tools(
        self,
        system: str,
        aufgabe: str,
        tools: list[dict],
        dispatch: Callable[[str, dict], Awaitable[Any]],
        max_runden: int = MAX_RUNDEN,
        max_tokens: int = 4096,
    ) -> Lauf:
        """Führt die Werkzeugschleife bis zur Antwort.

        `dispatch(name, eingabe)` führt einen Werkzeugaufruf aus. Fehler werden
        dem Modell als Text zurückgegeben statt geworfen: ein Agent, der einen
        Tippfehler im Anlagennamen macht, soll ihn korrigieren können, statt den
        ganzen Lauf zu verlieren.
        """
        nachrichten: list[dict] = [{"role": "user", "content": aufgabe}]
        lauf = Lauf(antwort="")

        for runde in range(1, max_runden + 1):
            lauf.runden = runde
            antwort = self.client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system, tools=tools, messages=nachrichten,
            )

            werkzeug_bloecke = [b for b in antwort.content if b.type == "tool_use"]
            text_bloecke = [b.text for b in antwort.content if b.type == "text"]

            if not werkzeug_bloecke:
                lauf.antwort = "\n".join(text_bloecke).strip()
                return lauf

            nachrichten.append({"role": "assistant", "content": antwort.content})

            ergebnisse = []
            for block in werkzeug_bloecke:
                try:
                    ergebnis = await dispatch(block.name, dict(block.input))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Werkzeug %s scheiterte: %s", block.name, exc)
                    ergebnis = {"fehler": f"{type(exc).__name__}: {exc}"}

                lauf.aufrufe.append(ToolCall(block.name, dict(block.input), ergebnis))
                ergebnisse.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": json.dumps(ergebnis, default=str, ensure_ascii=False)[:20000],
                })

            nachrichten.append({"role": "user", "content": ergebnisse})

        # Rundenlimit erreicht: lieber ehrlich abbrechen als endlos weiterlaufen.
        lauf.abgebrochen = True
        lauf.antwort = (
            f"Abgebrochen: Rundenlimit ({max_runden}) erreicht, ohne zu einem Schluss "
            f"zu kommen. Bisher genutzte Werkzeuge: "
            f"{', '.join(a.name for a in lauf.aufrufe)}"
        )
        return lauf


def tool_schema(name: str, beschreibung: str, eigenschaften: dict,
                pflicht: list[str] | None = None) -> dict:
    """Kürzel für ein Anthropic-Tool-Schema."""
    return {
        "name": name,
        "description": beschreibung,
        "input_schema": {
            "type": "object",
            "properties": eigenschaften,
            "required": pflicht or [],
        },
    }
