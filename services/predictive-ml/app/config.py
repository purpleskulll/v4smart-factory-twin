"""Konfiguration aus der Umgebung (Werte siehe workspace/.env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _num(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Config:
    brokers: str = "redpanda:9092"
    machine_count: int = 8
    warmup_s: float = 60.0
    cooldown_s: float = 45.0
    vib_guard: float = 4.5

    # Fenster-Parameter aus CLAUDE.md §14.
    window_s: float = 10.0
    step_s: float = 2.0

    # Aktion (§14/§8).
    throttle_factor: float = 0.5
    escalate_factor: float = 0.25
    throttle_ttl_s: float = 120.0

    # Heilung gilt als erreicht, wenn die Vibration so lange unter der Schwelle
    # bleibt (§14: vib < 3 für 10 s).
    healed_vib: float = 3.0
    healed_hold_s: float = 10.0

    # Anomalie erst nach so vielen Fenstern in Folge unter dem Schwellwert.
    consecutive_windows: int = 2
    # Eskalation, wenn die Maschine trotz Drosselung weiter anomal ist.
    escalate_after_windows: int = 2

    @staticmethod
    def from_env() -> "Config":
        return Config(
            brokers=os.getenv("KAFKA_BROKERS") or "redpanda:9092",
            machine_count=int(_num("MACHINE_COUNT", 8)),
            warmup_s=_num("ML_WARMUP_S", 60.0),
            cooldown_s=_num("ML_COOLDOWN_S", 45.0),
            vib_guard=_num("ML_VIB_GUARD", 4.5),
        )
