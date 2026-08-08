"""Kalibrier-Werkzeug für den Anomalie-Schwellwert (CLAUDE.md §14).

Misst für verschiedene Sicherheitsmargen BEIDE Seiten der Kette:
  * Fehlalarme in 10 Minuten reinem Normalbetrieb (müssen 0 sein)
  * Erkennungszeit nach Rampenstart (muss <= 20 s sein)

Aufruf im Wegwerf-Container:
  docker run --rm -v /workspace/services/predictive-ml:/s -w /s python:3.12-slim \
    sh -c "pip install -q -r requirements.txt && python tools/calibrate.py"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import numpy as np

from app import model as model_mod
from app.config import Config
from app.engine import Engine
from synth import Factory

CFG = Config()


FALSE_ALARM_SEEDS = (7, 42, 1234)


def run_case(margin_factor: float, rising: float, elevated_sigma: float) -> dict:
    model_mod.MARGIN_SPREAD_FACTOR = margin_factor
    model_mod.RISING_SLOPE = rising
    model_mod.ELEVATED_SIGMA = elevated_sigma

    # --- Fehlalarme im Normalbetrieb, über mehrere Seeds -------------------
    false_alarms = 0
    threshold = 0.0
    for seed in FALSE_ALARM_SEEDS:
        engine = Engine(CFG)
        factory = Factory.build(count=8, seed=seed)
        for mid, ts, temp, press, vib in factory.run(CFG.warmup_s + 5):
            engine.add_sample(mid, ts, temp, press, vib, "OK")
        threshold = engine.model.threshold
        for mid, ts, temp, press, vib in factory.run(600):
            if engine.add_sample(mid, ts, temp, press, vib, "OK").throttle:
                false_alarms += 1

    # --- Erkennungszeit ---------------------------------------------------
    engine2 = Engine(CFG)
    factory2 = Factory.build(count=8, seed=42)
    for mid, ts, temp, press, vib in factory2.run(CFG.warmup_s + 5):
        engine2.add_sample(mid, ts, temp, press, vib, "OK")

    factory2.inject(3)
    start = factory2.t
    detected, by_guard = None, None
    for mid, ts, temp, press, vib in factory2.run(90):
        d = engine2.add_sample(mid, ts, temp, press, vib, "OK")
        if d.throttle and d.throttle.machine_id == 3:
            detected = ts - start
            by_guard = "guard=1" in d.throttle.reason
            break

    return {
        "margin_factor": margin_factor,
        "rising": rising,
        "elevated_sigma": elevated_sigma,
        "threshold": round(threshold, 4),
        "false_alarms": false_alarms,
        "detected_s": None if detected is None else round(detected, 1),
        "by_guard": by_guard,
    }


def score_trace() -> None:
    """Zeigt, wie Score und Schwellwert während der Rampe auseinanderlaufen."""
    engine = Engine(CFG)
    factory = Factory.build(count=8, seed=42)
    for mid, ts, temp, press, vib in factory.run(CFG.warmup_s + 5):
        engine.add_sample(mid, ts, temp, press, vib, "OK")

    print(f"\nSchwellwert nach Training: {engine.model.threshold:.4f}")
    print("t_nach_rampe  vib_mean  vib_slope     score   anomal?")
    factory.inject(3)
    start = factory.t
    from app.features import WindowBuilder

    builder = WindowBuilder(3, CFG.window_s, CFG.step_s)
    for mid, ts, temp, press, vib in factory.run(40):
        if mid != 3:
            continue
        w = builder.add(ts, temp, press, vib)
        if not w:
            continue
        s = engine.model.score(w.vector)
        anomalous = engine.model.is_anomalous(s) and engine.model.is_dangerous(w.vector[0], w.vector[2])
        print(f"{ts - start:11.1f}  {w.vector[0]:8.2f}  {w.vector[2]:9.3f}  {s:8.4f}   {anomalous}")


if __name__ == "__main__":
    np.set_printoptions(precision=3)
    score_trace()
    print(f"\nSweep über {len(FALSE_ALARM_SEEDS)} Seeds "
          "(Ziel: fehlalarme == 0 UND erkannt <= 15 s OHNE Guard):")
    print(f"{'marge':>6} {'rising':>7} {'sigma':>6} {'schwelle':>9} {'fehlalarme':>11} {'erkannt_s':>10}  guard?")
    for margin in (1.0, 1.5):
        for rising in (0.05, 0.08):
            for sigma in (2.0, 3.0):
                r = run_case(margin, rising, sigma)
                print(f"{r['margin_factor']:6.1f} {r['rising']:7.2f} {r['elevated_sigma']:6.1f} "
                      f"{r['threshold']:9.4f} {r['false_alarms']:11d} "
                      f"{str(r['detected_s']):>10}  {r['by_guard']}")
