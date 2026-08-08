"""Entscheidungslogik der Self-Healing-Kette (CLAUDE.md §14).

Bewusst OHNE Kafka: die komplette Kette (Fenster -> Modell -> Guard -> Aktion ->
Cooldown -> Eskalation -> healed) ist damit offline in Simulationszeit prüfbar.
`consumer.py` füttert diese Engine, `actor.py` publiziert ihre Ausgabe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import Config
from .features import WindowBuilder
from .model import AnomalyModel

STATUS_BLOCKING = {"OFFLINE", "ERROR"}


@dataclass
class Throttle:
    """Kommando für machine_control (§8)."""

    machine_id: int
    factor: float
    ttl_s: float
    reason: str


@dataclass
class Event:
    """Ereignis für system_events (§9)."""

    kind: str
    machine_id: Optional[int]
    detail: dict


@dataclass
class Decision:
    throttle: Optional[Throttle] = None
    events: List[Event] = field(default_factory=list)

    def is_empty(self) -> bool:
        return self.throttle is None and not self.events


@dataclass
class MachineState:
    builder: WindowBuilder
    consecutive_low: int = 0
    last_action_ts: Optional[float] = None
    acted_at: Optional[float] = None
    acted_factor: float = 0.0
    windows_since_action: int = 0
    anomalous_since_action: int = 0
    escalated: bool = False
    calm_since: Optional[float] = None
    last_score: float = 0.0


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.model = AnomalyModel()
        self.factory_running = True
        self._machines: Dict[int, MachineState] = {}
        self._start_ts: Optional[float] = None
        self._warmup_done = False

    # ---------------------------------------------------------------- Zustand
    def set_factory_running(self, running: bool) -> None:
        self.factory_running = running

    @property
    def warmup_done(self) -> bool:
        return self._warmup_done

    def _state(self, machine_id: int) -> MachineState:
        if machine_id not in self._machines:
            self._machines[machine_id] = MachineState(
                builder=WindowBuilder(machine_id, self.cfg.window_s, self.cfg.step_s)
            )
        return self._machines[machine_id]

    # ------------------------------------------------------------------ Kette
    def add_sample(
        self, machine_id: int, ts_s: float, temp: float, press: float, vib: float, status: str
    ) -> Decision:
        if self._start_ts is None:
            self._start_ts = ts_s

        st = self._state(machine_id)
        decision = Decision()

        # Heilung wird samplebasiert erkannt (nicht fensterbasiert), damit
        # `seconds_to_heal` die tatsächliche Beruhigung abbildet.
        self._track_healing(st, machine_id, ts_s, vib, decision)

        window = st.builder.add(ts_s, temp, press, vib)
        if window is None:
            return decision

        # Warmup: Normalbetrieb lernen. Gestörte oder stehende Maschinen dürfen
        # NICHT ins Trainingsmaterial (sonst gilt die Anomalie als normal).
        if not self._warmup_done:
            if status == "OK":
                self.model.observe(window.vector)
            if ts_s - self._start_ts >= self.cfg.warmup_s:
                if self.model.train():
                    self._warmup_done = True
                    decision.events.append(
                        Event(
                            "info",
                            None,
                            {
                                "msg": "warmup abgeschlossen",
                                "windows": self.model.warmup_windows,
                                "threshold": round(self.model.threshold, 4),
                            },
                        )
                    )

        score = self.model.score(window.vector)
        st.last_score = score

        # Modell-Anomalie zählt nur, wenn sie nach OBEN zeigt (steigende oder
        # erhöhte Vibration) — sonst drosselt die Kette besonders ruhige Läufe.
        model_says_anomaly = self.model.is_anomalous(score) and self.model.is_dangerous(
            window.vector[0], window.vector[2]
        )
        # Deterministisches Sicherheitsnetz: gilt auch im Warmup (§14) — die
        # Kette darf nie am Modell scheitern.
        guard_trips = window.vib_mean > self.cfg.vib_guard

        if model_says_anomaly:
            st.consecutive_low += 1
        else:
            st.consecutive_low = 0

        anomalous = guard_trips or st.consecutive_low >= self.cfg.consecutive_windows

        if st.acted_at is not None:
            st.windows_since_action += 1
            # "Bleibt anomal" heißt: KEINE Besserung. Ein fallender Trend ist
            # eine Besserung, auch wenn der 10-s-Mittelwert noch über dem Guard
            # liegt — das Fenster hinkt der Drosselung naturgemäß hinterher.
            # Ohne diese Bedingung eskalierte die Kette live auf factor=0.25,
            # obwohl der Score schon positiv und vib_slope negativ war.
            if anomalous and window.vector[2] >= 0.0:
                st.anomalous_since_action += 1
            elif window.vector[2] < 0.0:
                st.anomalous_since_action = 0

        if not anomalous:
            return decision
        if status in STATUS_BLOCKING or not self.factory_running:
            # Stehende/havarierte Maschine: drosseln bringt nichts (§14).
            return decision
        if not self._warmup_done and not guard_trips:
            # Während des Warmups feuert ausschließlich der Guard (§14).
            return decision

        reason = (
            f"isoforest score={score:.2f} vib_mean={window.vector[0]:.2f} "
            f"vib_slope={window.vector[2]:.2f}"
            + (" guard=1" if guard_trips else "")
        )

        # Eskalation: trotz Drosselung weiter anomal (§14).
        if st.acted_at is not None:
            # Vor der Eskalation muss sich das Fenster einmal komplett erneuert
            # haben (window_s/step_s Fenster), sonst bewertet man die Lage VOR
            # der Drosselung.
            settled = st.windows_since_action >= self.cfg.window_s / self.cfg.step_s
            if (
                not st.escalated
                and settled
                and st.anomalous_since_action >= self.cfg.escalate_after_windows
            ):
                st.escalated = True
                st.last_action_ts = ts_s
                st.acted_factor = self.cfg.escalate_factor
                st.anomalous_since_action = 0
                return self._act(decision, machine_id, ts_s, self.cfg.escalate_factor, score, window, reason, escalated=True)
            return decision

        # Cooldown nach einer Aktion (§14).
        if st.last_action_ts is not None and ts_s - st.last_action_ts < self.cfg.cooldown_s:
            return decision

        st.acted_at = ts_s
        st.acted_factor = self.cfg.throttle_factor
        st.last_action_ts = ts_s
        st.windows_since_action = 0
        st.anomalous_since_action = 0
        st.escalated = False
        st.calm_since = None
        return self._act(decision, machine_id, ts_s, self.cfg.throttle_factor, score, window, reason, escalated=False)

    def _act(
        self,
        decision: Decision,
        machine_id: int,
        ts_s: float,
        factor: float,
        score: float,
        window,
        reason: str,
        escalated: bool,
    ) -> Decision:
        decision.throttle = Throttle(
            machine_id=machine_id, factor=factor, ttl_s=self.cfg.throttle_ttl_s, reason=reason
        )
        decision.events.append(
            Event(
                "anomaly_detected",
                machine_id,
                {
                    "score": round(score, 4),
                    "vib_mean": round(window.vector[0], 3),
                    "vib_slope": round(window.vector[2], 3),
                    "temp_slope": round(window.vector[3], 3),
                    "escalated": escalated,
                },
            )
        )
        decision.events.append(
            Event(
                "healing_applied",
                machine_id,
                {"action": "throttle", "factor": factor, "ttl_s": self.cfg.throttle_ttl_s},
            )
        )
        return decision

    def _track_healing(
        self, st: MachineState, machine_id: int, ts_s: float, vib: float, decision: Decision
    ) -> None:
        if st.acted_at is None:
            return
        if vib < self.cfg.healed_vib:
            if st.calm_since is None:
                st.calm_since = ts_s
            elif ts_s - st.calm_since >= self.cfg.healed_hold_s:
                decision.events.append(
                    Event(
                        "healed",
                        machine_id,
                        {"seconds_to_heal": round(ts_s - st.acted_at, 1)},
                    )
                )
                st.acted_at = None
                st.acted_factor = 0.0
                st.calm_since = None
                st.escalated = False
                st.consecutive_low = 0
                st.windows_since_action = 0
                st.anomalous_since_action = 0
        else:
            st.calm_since = None

    # ------------------------------------------------------------------ Infos
    def snapshot(self) -> List[Tuple[int, float]]:
        return [(mid, st.last_score) for mid, st in sorted(self._machines.items())]
