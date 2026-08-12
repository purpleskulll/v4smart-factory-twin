"""Szenario-Test der Self-Healing-Kette — der wichtigste Test des Services.

Läuft komplett offline (Kafka gemockt/nicht vorhanden) in Simulationszeit:
60 s Normalbetrieb (Warmup) -> Vibrations-Rampe -> Erkennung -> Drosselung ->
Beruhigung -> healed. Zusätzlich der False-Positive-Schutz: 10 Minuten reiner
Normalbetrieb dürfen KEINEN einzigen Alarm auslösen (CLAUDE.md §14).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Config
from app.engine import Engine
from synth import Factory

CFG = Config()


def feed(engine: Engine, factory: Factory, seconds: float, status_by_machine=None):
    """Füttert die Engine und sammelt alle Entscheidungen."""
    out = []
    for mid, ts, temp, press, vib in factory.run(seconds):
        status = (status_by_machine or {}).get(mid, "OK")
        decision = engine.add_sample(mid, ts, temp, press, vib, status)
        if not decision.is_empty():
            out.append((ts, mid, decision))
    return out


def test_detects_ramp_within_20s_and_heals():
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)

    # 1) Warmup + etwas Nachlauf: durchgehend reiner Normalbetrieb, hier darf
    #    nichts gedrosselt werden (auch nicht direkt nach dem Training).
    warmup_decisions = feed(engine, factory, CFG.warmup_s + 5)
    throttles = [d for _, _, d in warmup_decisions if d.throttle]
    assert not throttles, f"Normalbetrieb wurde gedrosselt: {throttles}"
    assert engine.warmup_done, "Modell muss nach dem Warmup trainiert sein"

    # 2) Fehler injizieren und beobachten.
    target = 3
    factory.inject(target)
    ramp_start = factory.t

    detected_at = None
    for mid, ts, temp, press, vib in factory.run(60):
        decision = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if decision.throttle and decision.throttle.machine_id == target:
            detected_at = ts - ramp_start
            factory.throttle(target)  # der Simulator würde jetzt drosseln
            break

    assert detected_at is not None, "Rampe wurde in 60 s nicht erkannt"
    assert detected_at <= 20.0, f"Erkennung erst nach {detected_at:.1f}s (erlaubt: 20 s)"
    print(f"Erkennung nach {detected_at:.1f}s")

    # 3) Nach der Drosselung beruhigt sich die Maschine -> healed-Ereignis.
    healed = None
    for mid, ts, temp, press, vib in factory.run(90):
        if mid == target:
            factory.throttle(target)  # Zerfall des Fehleranteils fortschreiben
        decision = engine.add_sample(mid, ts, temp, press, vib, "OK")
        for ev in decision.events:
            if ev.kind == "healed" and ev.machine_id == target:
                healed = ev
                break
        if healed:
            break

    assert healed is not None, "kein healed-Ereignis nach der Beruhigung"
    assert healed.detail["seconds_to_heal"] > 0


@pytest.mark.parametrize("seed", [7, 42, 1234])
def test_no_false_positive_over_10_minutes(seed):
    """Mehrere Seeds: der Schwellwert darf nicht auf einen Zufall kalibriert sein."""
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=seed)

    feed(engine, factory, CFG.warmup_s + 5)
    assert engine.warmup_done

    decisions = feed(engine, factory, 600)
    alarms = [(ts, mid) for ts, mid, d in decisions if d.throttle]
    assert not alarms, f"Normalbetrieb (seed={seed}) löste {len(alarms)} Alarm(e) aus: {alarms[:5]}"


def test_model_detects_before_the_guard_would():
    """Das Modell muss die Rampe erkennen, BEVOR der Guard greift.

    Sonst ist die Vorhersage nur Kosmetik: der Guard schlägt erst an, wenn
    vib_mean > 4.5 ist (rund 24 s nach Rampenstart) — dann bleibt kaum noch
    Vorlauf bis zur kritischen Temperatur.
    """
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    feed(engine, factory, CFG.warmup_s + 5)

    factory.inject(3)
    start = factory.t
    for mid, ts, temp, press, vib in factory.run(60):
        d = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if d.throttle and d.throttle.machine_id == 3:
            assert "guard=1" not in d.throttle.reason, (
                f"nur der Guard erkannte die Rampe (t+{ts - start:.1f}s) — "
                "das Modell ist zu unempfindlich"
            )
            assert ts - start <= 20.0, f"Modell-Erkennung erst nach {ts - start:.1f}s"
            return
    raise AssertionError("Rampe wurde nicht erkannt")


def test_warmup_waits_out_a_stopped_factory():
    """Der Warmup darf nicht ablaufen, während die Fabrik steht.

    Live aufgetreten: der ML-Service startete neu, während die Fabrik gestoppt
    war. Der Warmup lief nach 60 s Wanduhrzeit trotzdem ab und kalibrierte auf
    26 statt gut 200 Fenstern — die daraus entstandene Schwelle (-0.067 statt
    -0.127) liegt laut tools/calibrate.py bei ~3 Fehlalarmen je 10 Minuten.
    """
    engine = Engine(CFG)
    engine.set_factory_running(False)
    factory = Factory.build(count=8, seed=42)

    # Stillstand: Heartbeat-Rate, Maschinen melden OFFLINE.
    for mid, ts, temp, press, vib in factory.run(CFG.warmup_s * 3, hz=1.0):
        engine.add_sample(mid, ts, temp, press, vib, "OFFLINE")
    assert not engine.warmup_done, "Warmup lief trotz stehender Fabrik ab"

    # Fabrik startet: jetzt zählt die Zeit und es kommen echte Fenster.
    engine.set_factory_running(True)
    feed(engine, factory, CFG.warmup_s + 15)
    assert engine.warmup_done, "Warmup wurde nach dem Start nicht abgeschlossen"
    assert engine.model.warmup_windows >= engine.min_warmup_windows, (
        f"nur {engine.model.warmup_windows} Fenster, "
        f"Mindestmaß ist {engine.min_warmup_windows}"
    )


def test_warmup_needs_enough_windows_not_just_time():
    """Genug Zeit allein reicht nicht — die Datenbasis muss auch tragen."""
    engine = Engine(CFG)
    factory = Factory.build(count=1, seed=42)

    # Eine einzige Maschine liefert in warmup_s deutlich zu wenige Fenster.
    for mid, ts, temp, press, vib in factory.run(CFG.warmup_s + 5):
        engine.add_sample(mid, ts, temp, press, vib, "OK")
    assert not engine.warmup_done, (
        f"trainiert mit nur {engine.model.warmup_windows} Fenstern "
        f"(Mindestmaß {engine.min_warmup_windows})"
    )

    # Mit genug Laufzeit kommt die Datenbasis zusammen.
    for mid, ts, temp, press, vib in factory.run(300):
        engine.add_sample(mid, ts, temp, press, vib, "OK")
    assert engine.warmup_done


def test_guard_fires_even_without_trained_model():
    """Deterministisches Sicherheitsnetz: die Kette darf nie am Modell scheitern."""
    engine = Engine(CFG)
    factory = Factory.build(count=1, seed=11)
    factory.inject(1)

    fired = None
    for mid, ts, temp, press, vib in factory.run(CFG.warmup_s - 10):
        decision = engine.add_sample(mid, ts, temp, press, vib, "OK")
        if decision.throttle:
            fired = (ts, vib)
            break

    assert fired is not None, "Guard hat im Warmup nicht ausgelöst"
    assert fired[1] > CFG.vib_guard - 0.5, f"Guard feuerte bei vib={fired[1]:.2f}"
    assert not engine.warmup_done, "dieser Test soll VOR dem Trainingsende greifen"


def test_no_action_on_offline_or_error_machine():
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    feed(engine, factory, CFG.warmup_s + 5)

    factory.inject(4)
    # Maschine meldet ERROR -> drosseln bringt nichts (§14).
    decisions = feed(engine, factory, 60, status_by_machine={4: "ERROR"})
    assert not [d for _, mid, d in decisions if d.throttle and mid == 4]


def test_no_action_when_factory_stopped():
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    feed(engine, factory, CFG.warmup_s + 5)

    engine.set_factory_running(False)
    factory.inject(5)
    decisions = feed(engine, factory, 60)
    assert not [d for _, _, d in decisions if d.throttle], "bei gestoppter Fabrik keine Aktionen"
