"""Fenster-Features je Maschine (CLAUDE.md §14).

Fenster 10 s, Schritt 2 s, Merkmale:
    [vib_mean, vib_std, vib_slope, temp_slope, press_std]

Die Steigungen sind der Kern der Vorhersage: die Vibration steigt ZUERST,
die Temperatur folgt verzögert (§13) — `vib_slope` schlägt also an, bevor
`temp` kritisch wird.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

import numpy as np

FEATURE_NAMES = ["vib_mean", "vib_std", "vib_slope", "temp_slope", "press_std"]


def slope(times: np.ndarray, values: np.ndarray) -> float:
    """Steigung je Sekunde (lineare Regression). Zu wenige/entartete Punkte -> 0."""
    if times.size < 2:
        return 0.0
    span = times[-1] - times[0]
    if span <= 0:
        return 0.0
    t = times - times[0]
    denom = float(((t - t.mean()) ** 2).sum())
    if denom <= 0:
        return 0.0
    return float(((t - t.mean()) * (values - values.mean())).sum() / denom)


@dataclass
class Window:
    machine_id: int
    ts_s: float
    vector: List[float]

    @property
    def vib_mean(self) -> float:
        return self.vector[0]


class WindowBuilder:
    """Sammelt Samples einer Maschine und liefert alle `step_s` ein Fenster."""

    def __init__(self, machine_id: int, window_s: float, step_s: float) -> None:
        self.machine_id = machine_id
        self.window_s = window_s
        self.step_s = step_s
        self._samples: Deque[Tuple[float, float, float, float]] = deque()
        self._next_emit: Optional[float] = None

    def add(self, ts_s: float, temp: float, press: float, vib: float) -> Optional[Window]:
        self._samples.append((ts_s, temp, press, vib))
        cutoff = ts_s - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        if self._next_emit is None:
            # Erstes Fenster erst, wenn es wirklich `window_s` abdeckt — sonst
            # wären Mittelwert und Steigung aus zwei Punkten gerechnet.
            self._next_emit = ts_s + self.window_s
            return None
        if ts_s < self._next_emit:
            return None
        self._next_emit = ts_s + self.step_s

        arr = np.asarray(self._samples, dtype=float)
        times, temps, press_v, vibs = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        vector = [
            float(vibs.mean()),
            float(vibs.std()),
            slope(times, vibs),
            slope(times, temps),
            float(press_v.std()),
        ]
        return Window(machine_id=self.machine_id, ts_s=ts_s, vector=vector)
