"""Kontrakt der publizierten Nachrichten + Cooldown/Eskalation (§8/§9/§14)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.actor import event_message, throttle_message
from app.config import Config
from app.engine import Engine, Event, Throttle
from synth import Factory

CFG = Config()


def test_throttle_message_matches_contract():
    msg = throttle_message(
        Throttle(machine_id=3, factor=0.5, ttl_s=120, reason="isoforest score=-0.62"),
        ts="2026-08-08T12:00:00Z",
    )
    assert msg == {
        "v": 1,
        "ts": "2026-08-08T12:00:00Z",
        "type": "throttle",
        "machine_id": 3,
        "factor": 0.5,
        "ttl_s": 120,
        "source": "predictive-ml",
        "reason": "isoforest score=-0.62",
    }


def test_event_message_matches_contract():
    msg = event_message(
        Event("anomaly_detected", 3, {"score": -0.62, "vib_mean": 5.1}), ts="2026-08-08T12:00:00Z"
    )
    assert msg["v"] == 1
    assert msg["kind"] == "anomaly_detected"
    assert msg["machine_id"] == 3
    assert msg["detail"]["score"] == -0.62

    # Fabrikweite Ereignisse tragen keine machine_id (§9).
    factory_wide = event_message(Event("info", None, {"msg": "warmup"}), ts="2026-08-08T12:00:00Z")
    assert "machine_id" not in factory_wide


def _warm(engine: Engine, factory: Factory) -> None:
    for mid, ts, temp, press, vib in factory.run(CFG.warmup_s + 5):
        engine.add_sample(mid, ts, temp, press, vib, "OK")


def test_cooldown_prevents_immediate_second_action():
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    _warm(engine, factory)

    factory.inject(2)
    actions = []
    for mid, ts, temp, press, vib in factory.run(40):
        d = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if d.throttle and d.throttle.machine_id == 2:
            actions.append((ts, d.throttle.factor))

    assert actions, "keine Aktion ausgelöst"
    first_ts = actions[0][0]
    # Innerhalb des Cooldowns darf nur eine ESKALATION folgen (0.25), keine
    # erneute Erst-Drosselung (§14).
    for ts, factor in actions[1:]:
        if ts - first_ts < CFG.cooldown_s:
            assert factor == CFG.escalate_factor, (
                f"zweite Drosselung mit factor={factor} nach {ts - first_ts:.1f}s "
                "— Cooldown verletzt"
            )


def test_no_escalation_while_vibration_is_already_falling():
    """Wirkt die Drosselung, darf NICHT eskaliert werden.

    Live beobachtet: die Kette eskalierte 4 s nach der Drosselung auf 0.25,
    obwohl der Score bereits positiv und vib_slope negativ war — der 10-s-
    Mittelwert lag nur noch über dem Guard, weil er der Drosselung nachhinkt.
    """
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    _warm(engine, factory)

    factory.inject(2)
    factors = []
    throttled = False
    for mid, ts, temp, press, vib in factory.run(90):
        if throttled and mid == 2:
            factory.throttle(2)  # Simulator drosselt -> Vibration fällt
        d = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if d.throttle and d.throttle.machine_id == 2:
            factors.append(d.throttle.factor)
            throttled = True
            factory.throttle(2)

    assert factors, "keine Aktion ausgelöst"
    assert CFG.escalate_factor not in factors, (
        f"eskaliert, obwohl die Drosselung wirkte: {factors}"
    )


def test_escalation_when_machine_stays_anomalous():
    """Bleibt die Maschine trotz Drosselung anomal, wird auf 0.25 eskaliert (§14)."""
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    _warm(engine, factory)

    factory.inject(6)
    factors = []
    # Bewusst NICHT drosseln: die Rampe läuft weiter, die Maschine bleibt anomal.
    for mid, ts, temp, press, vib in factory.run(60):
        d = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if d.throttle and d.throttle.machine_id == 6:
            factors.append(d.throttle.factor)

    assert factors, "keine Aktion ausgelöst"
    assert factors[0] == CFG.throttle_factor
    assert CFG.escalate_factor in factors, f"keine Eskalation erfolgt: {factors}"
