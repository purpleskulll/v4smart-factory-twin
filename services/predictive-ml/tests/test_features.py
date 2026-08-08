"""Feature-Berechnung auf synthetischen Reihen (CLAUDE.md §14)."""

from __future__ import annotations

import numpy as np
import pytest

from app.features import FEATURE_NAMES, WindowBuilder, slope


def test_slope_of_known_line_is_exact():
    t = np.arange(0, 10, 0.1)
    # +0.12 pro Sekunde = das Fehlerprofil "vibration_ramp" (§13).
    v = 2.2 + 0.12 * t
    assert slope(t, v) == pytest.approx(0.12, rel=1e-9)


def test_slope_of_constant_is_zero():
    t = np.arange(0, 10, 0.1)
    assert slope(t, np.full_like(t, 5.0)) == pytest.approx(0.0, abs=1e-12)


def test_slope_handles_degenerate_input():
    assert slope(np.array([1.0]), np.array([2.0])) == 0.0
    assert slope(np.array([]), np.array([])) == 0.0
    assert slope(np.array([5.0, 5.0]), np.array([1.0, 2.0])) == 0.0


def test_window_emits_only_after_full_window():
    b = WindowBuilder(1, window_s=10.0, step_s=2.0)
    emitted = []
    for i in range(300):  # 30 s bei 10 Hz
        ts = i * 0.1
        w = b.add(ts, 62.0, 5.2, 2.2)
        if w:
            emitted.append(w.ts_s)

    assert emitted, "kein Fenster erzeugt"
    assert emitted[0] >= 10.0, f"erstes Fenster zu früh: {emitted[0]}"
    gaps = [round(b - a, 1) for a, b in zip(emitted, emitted[1:])]
    assert all(g == pytest.approx(2.0, abs=0.15) for g in gaps), f"Schrittweite verletzt: {gaps}"


def test_feature_vector_shape_and_values():
    b = WindowBuilder(1, window_s=10.0, step_s=2.0)
    window = None
    for i in range(200):
        ts = i * 0.1
        vib = 2.2 + 0.12 * ts
        w = b.add(ts, 62.0 + 0.05 * ts, 5.2, vib)
        if w:
            window = w
            break

    assert window is not None
    assert len(window.vector) == len(FEATURE_NAMES) == 5
    vib_mean, vib_std, vib_slope, temp_slope, press_std = window.vector
    assert vib_slope == pytest.approx(0.12, rel=1e-6)
    assert temp_slope == pytest.approx(0.05, rel=1e-6)
    assert press_std == pytest.approx(0.0, abs=1e-9)
    assert vib_mean > 2.2
    assert vib_std > 0
