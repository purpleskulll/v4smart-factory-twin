"""Playbook-Runner (SPEC §10).

    python -m agents.runner triage
    python -m agents.runner formierung
    python -m agents.runner trace --frage "Welche Zellen stammen aus SLURRY-0003?"
    python -m agents.runner pass --serial ZW-2026-000042
    python -m agents.runner testfragen     # die Akzeptanzfragen aus §10.3

Jeder Lauf landet vollständig im `action_log` — inklusive der Werkzeuge, die der
Agent benutzt hat. Ohne diese Spur wäre nicht nachvollziehbar, worauf ein
Vorschlag beruht.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from .llm import LLM
from .playbooks import PLAYBOOKS, Traceability

TESTFRAGEN = Path(__file__).resolve().parents[1].parent / "tests" / "trace_questions.yaml"


def zeige(ergebnis) -> None:
    print("=" * 72)
    print(f"Playbook: {ergebnis.playbook}   ({ergebnis.runden} Runden)")
    print("=" * 72)
    print(ergebnis.bericht)
    if ergebnis.evidenz:
        print("\n--- benutzte Werkzeuge ---")
        for schritt in ergebnis.evidenz:
            print(f"  {schritt['werkzeug']}({json.dumps(schritt['eingabe'], ensure_ascii=False)})")
    if ergebnis.abgebrochen:
        print("\nHINWEIS: Lauf wurde am Rundenlimit abgebrochen.")


async def run_testfragen(llm: LLM) -> int:
    """Die zehn Akzeptanzfragen aus SPEC §10.3."""
    if not TESTFRAGEN.exists():
        print(f"Testfragen nicht gefunden: {TESTFRAGEN}", file=sys.stderr)
        return 1

    with open(TESTFRAGEN, encoding="utf-8") as handle:
        fragen = yaml.safe_load(handle)["fragen"]

    playbook = Traceability(llm)
    bestanden = 0
    for i, eintrag in enumerate(fragen, 1):
        print(f"\n[{i}/{len(fragen)}] {eintrag['frage']}")
        ergebnis = await playbook.run(frage=eintrag["frage"])
        antwort = ergebnis.bericht.lower()

        erwartet = [s.lower() for s in eintrag.get("muss_enthalten", [])]
        fehlt = [s for s in erwartet if s not in antwort]
        if fehlt:
            print(f"  NICHT BESTANDEN — fehlt: {fehlt}")
            print(f"  Antwort war: {ergebnis.bericht[:300]}")
        else:
            print("  bestanden")
            bestanden += 1

    print(f"\n{bestanden}/{len(fragen)} Testfragen bestanden")
    return 0 if bestanden == len(fragen) else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="zellwerk Playbook-Runner")
    parser.add_argument("playbook", choices=[*PLAYBOOKS.keys(), "testfragen"])
    parser.add_argument("--frage", help="für das Traceability-Playbook")
    parser.add_argument("--serial", help="für den Batteriepass")
    parser.add_argument("--modell", help="Modell überschreiben")
    args = parser.parse_args()

    llm = LLM(model=args.modell)

    if args.playbook == "testfragen":
        return await run_testfragen(llm)

    klasse = PLAYBOOKS[args.playbook]
    playbook = klasse(llm)

    kwargs = {}
    if args.playbook == "trace":
        if not args.frage:
            parser.error("--frage wird für 'trace' gebraucht")
        kwargs["frage"] = args.frage
    if args.playbook == "pass":
        if not args.serial:
            parser.error("--serial wird für 'pass' gebraucht")
        kwargs["serial"] = args.serial

    ergebnis = await playbook.run(**kwargs)
    zeige(ergebnis)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
