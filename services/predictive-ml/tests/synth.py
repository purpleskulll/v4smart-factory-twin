"""Synthetischer Datenstrom für die Offline-Tests der Healing-Kette.

Bildet die Simulator-Physik aus CLAUDE.md §13 nach (OU-Rauschen, Kopplung
vib -> temp mit tau = 25 s, Fehlerprofil vibration_ramp +0.12 mm/s pro Sekunde),
damit `test_detection.py` ohne Kafka und ohne Echtzeit läuft.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

import numpy as np

VIB_MEAN = 2.2
TEMP_MEAN = 62.0
PRESS_MEAN = 5.2
TEMP_GAIN = 2.2
TEMP_EXP = 1.6
TEMP_TAU = 25.0
OU_THETA = 0.4


@dataclass
class SynthMachine:
    machine_id: int
    rng: np.random.Generator
    vib_sigma: float = 0.25
    temp_sigma: float = 0.5
    press_sigma: float = 0.15
    base_vib: float = VIB_MEAN
    press: float = PRESS_MEAN
    temp: float = TEMP_MEAN
    err_offset: float = 0.0
    ramping: bool = False
    ramp_per_s: float = 0.12

    def step(self, dt: float) -> Tuple[float, float, float]:
        phi = math.exp(-OU_THETA * dt)
        noise = math.sqrt(1 - phi * phi)
        self.base_vib = VIB_MEAN + phi * (self.base_vib - VIB_MEAN) + self.vib_sigma * noise * self.rng.normal()
        self.press = PRESS_MEAN + phi * (self.press - PRESS_MEAN) + self.press_sigma * noise * self.rng.normal()

        if self.ramping:
            self.err_offset = min(6.8, self.err_offset + self.ramp_per_s * dt)

        vib = max(0.0, self.base_vib + self.err_offset)
        target = TEMP_MEAN + TEMP_GAIN * max(0.0, vib - VIB_MEAN) ** TEMP_EXP
        self.temp += (target - self.temp) * (1 - math.exp(-dt / TEMP_TAU))
        self.temp += self.temp_sigma * math.sqrt(dt) * self.rng.normal() * 0.5
        return self.temp, self.press, vib


@dataclass
class Factory:
    machines: List[SynthMachine] = field(default_factory=list)
    t: float = 0.0

    @staticmethod
    def build(count: int = 8, seed: int = 42) -> "Factory":
        rng = np.random.default_rng(seed)
        return Factory(machines=[SynthMachine(machine_id=i + 1, rng=rng) for i in range(count)])

    def run(self, seconds: float, hz: float = 10.0) -> Iterator[Tuple[int, float, float, float, float]]:
        """Liefert (machine_id, ts_s, temp, press, vib) in Simulationszeit."""
        dt = 1.0 / hz
        steps = int(seconds * hz)
        for _ in range(steps):
            self.t += dt
            for m in self.machines:
                temp, press, vib = m.step(dt)
                yield m.machine_id, self.t, temp, press, vib

    def inject(self, machine_id: int) -> None:
        for m in self.machines:
            if m.machine_id == machine_id:
                m.ramping = True

    def throttle(self, machine_id: int) -> None:
        """Drosselung: der Fehleranteil zerfällt (tau = 8 s, §13)."""
        for m in self.machines:
            if m.machine_id == machine_id:
                m.ramping = False
                m.err_offset *= math.exp(-1.0 / 8.0)
