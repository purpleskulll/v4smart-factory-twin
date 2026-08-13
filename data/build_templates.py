#!/usr/bin/env python3
"""Erzeugt die Formierungs-Kurvenformen (SPEC §7.2, Abweichung docs/decisions.md D2).

WICHTIG — was diese Kurven sind und was nicht:

Die SPEC sah vor, die Profile aus dem Severson-et-al.-Datensatz abzuleiten.
Das passiert hier NICHT. Die Kurven werden aus veröffentlichten Kennwerten
MODELLIERT — C/10-Erstladung, Spannungsplateau bei ~3,7 V, typische
Kapazitätsstreuung. Die Gründe stehen in decisions.md D2; der wichtigere davon
ist inhaltlich: der Datensatz besteht aus LFP-Rundzellen unter
Schnellladeprotokollen und hätte für eine C/10-Erstformierung ohnehin
umgerechnet werden müssen.

Nirgends darf behauptet werden, diese Kurven seien Messdaten. Sie sind
plausible Modellkurven — genau wie die Prozessparameter in §7.1 ausdrücklich
„plausible Lehrbuch-Defaults" sind. Jede erzeugte CSV trägt diesen Hinweis in
ihrer Kopfzeile mit, damit er nicht verlorengeht, wenn die Datei weitergereicht
wird.

Aufruf:  python data/build_templates.py
Ergebnis: data/formation_templates/*.csv
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

AUSGABE = Path(__file__).resolve().parent / "formation_templates"

# Die drei Klassen, die der Simulator unterscheidet (SPEC §7.2):
#   gut       — gesunde Zelle, folgt dem Sollprofil
#   mittel    — noch brauchbar, leicht verringerte Kapazität
#   auffaellig— Zelle nach Fehler F3/F4, deutlich abweichend
PROFILE = {
    "gut": {
        "endkapazitaet_ah": 5.00, "plateau_v": 3.70, "steilheit": 3.2,
        "beschreibung": "gesunde Zelle, C/10-Erstladung, volles Plateau",
    },
    "gut_streuung": {
        "endkapazitaet_ah": 4.93, "plateau_v": 3.69, "steilheit": 3.1,
        "beschreibung": "gesunde Zelle am unteren Rand der normalen Streuung",
    },
    "mittel": {
        "endkapazitaet_ah": 4.72, "plateau_v": 3.67, "steilheit": 3.4,
        "beschreibung": "grenzwertig: Kapazität am unteren Rand, Plateau verkürzt",
    },
    "auffaellig_kapazitaet": {
        "endkapazitaet_ah": 4.25, "plateau_v": 3.64, "steilheit": 4.1,
        "beschreibung": "Kapazitätsdefizit (zu niedrige Porosität oder zu wenig Elektrolyt)",
    },
    "auffaellig_temperatur": {
        "endkapazitaet_ah": 4.40, "plateau_v": 3.61, "steilheit": 4.6,
        "beschreibung": "nach Übertemperatur: früheres, flacheres Plateau",
    },
}

SCHRITTE = 120          # Stützstellen über die Formierdauer
DAUER_S = 240.0         # passend zu Formation.dauer_s im Simulator


def spannung(fortschritt: float, plateau_v: float, steilheit: float) -> float:
    """3,0 V → 4,2 V mit Plateau.

    Dieselbe Form wie `Formation.spannungskurve` im Simulator — die Templates
    sollen zu dem passen, was die Anlage erzeugt, sonst vergleicht ein Agent
    später Äpfel mit Birnen.
    """
    basis = 3.0 + 1.2 * (1.0 - math.exp(-steilheit * fortschritt))
    # Leichte Anhebung im Plateaubereich, damit die Kurve nicht nur exponentiell
    # aussieht, sondern das Spannungsplateau erkennbar bleibt.
    if 0.25 < fortschritt < 0.75:
        basis += 0.04 * math.sin((fortschritt - 0.25) * math.pi / 0.5) * (plateau_v - 3.6)
    return min(4.2, basis)


def schreibe_profil(name: str, cfg: dict, rng: random.Random) -> Path:
    pfad = AUSGABE / f"{name}.csv"
    with open(pfad, "w", newline="", encoding="utf-8") as handle:
        handle.write(
            f"# {name}: {cfg['beschreibung']}\n"
            "# MODELLKURVE aus veroeffentlichten Kennwerten — KEINE Messdaten.\n"
            "# Erzeugt von data/build_templates.py (siehe docs/decisions.md, D2).\n"
        )
        writer = csv.writer(handle)
        writer.writerow(["t_s", "spannung_v", "strom_c", "kapazitaet_ah", "temp_c"])

        for i in range(SCHRITTE + 1):
            fortschritt = i / SCHRITTE
            t = fortschritt * DAUER_S
            u = spannung(fortschritt, cfg["plateau_v"], cfg["steilheit"]) + rng.gauss(0, 0.003)
            # C/10 bis kurz vor Ende, dann Abfall (CV-Phase).
            strom = 0.1 if fortschritt < 0.9 else 0.1 * (1.0 - (fortschritt - 0.9) / 0.1) ** 2
            kapazitaet = cfg["endkapazitaet_ah"] * min(1.0, fortschritt * 1.05)
            temp = 32.0 + 2.5 * math.sin(fortschritt * math.pi) + rng.gauss(0, 0.15)
            writer.writerow([round(t, 1), round(u, 4), round(strom, 4),
                             round(kapazitaet, 4), round(temp, 2)])
    return pfad


def main() -> None:
    AUSGABE.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)  # fester Seed: die Templates müssen reproduzierbar sein
    for name, cfg in PROFILE.items():
        pfad = schreibe_profil(name, cfg, rng)
        print(f"  {pfad.name:32} {cfg['endkapazitaet_ah']:.2f} Ah — {cfg['beschreibung']}")
    print(f"\n{len(PROFILE)} Modellkurven in {AUSGABE}")
    print("Hinweis: Modellkurven, keine Messdaten (docs/decisions.md D2).")


if __name__ == "__main__":
    main()
