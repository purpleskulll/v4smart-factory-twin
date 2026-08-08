"""Warmup-Sammlung, IsolationForest und Schwellwert-Kalibrierung (CLAUDE.md §14).

Das Modell muss nicht perfekt sein — die KETTE muss laufen (§4.3). Deshalb ist
der Guard in `engine.py` bewusst modellunabhängig.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# Schwellwert = 5. Perzentil der Warmup-Scores minus Sicherheitsmarge (§14).
#
# Die Marge ist datengetrieben statt fest: 5 % der Trainingsfenster liegen per
# Definition unter dem 5. Perzentil, und weil die 10-s-Fenster im 2-s-Schritt zu
# 80 % überlappen, sind aufeinanderfolgende Scores stark korreliert — "2 Fenster
# in Folge" filtert diese Treffer also kaum. Mit fester Marge 0.02 ergab das
# gemessen 28 Fehlalarme in 10 Minuten reinem Normalbetrieb. Die Marge skaliert
# deshalb mit der Streuung der Warmup-Scores.
#
# Der Faktor ist gemessen, nicht geschätzt (`tools/calibrate.py`, 8 Maschinen,
# 10 min Normalbetrieb gegen eine 0.12 mm/s/s-Rampe):
#   Faktor 0.0 -> Schwelle +0.032 -> 19 Fehlalarme
#   Faktor 0.5 -> Schwelle +0.000 ->  5 Fehlalarme
#   Faktor 1.0 -> Schwelle -0.069 ->  3 Fehlalarme (erst über MEHRERE Seeds sichtbar)
#   Faktor 1.5 -> Schwelle -0.122 ->  0 Fehlalarme, Erkennung 16.2 s DURCH DAS MODELL
#   Faktor 2.0 -> Schwelle -0.157 ->  0 Fehlalarme, aber Erkennung erst 24.6 s (Guard)
# Der Score sättigt während der Rampe bei ca. -0.08; Schwellen darunter sind
# unerreichbar und degradieren die Kette still auf den Guard.
THRESHOLD_PERCENTILE = 5.0
MARGIN_MIN = 0.02
MARGIN_SPREAD_FACTOR = 1.5
MIN_WARMUP_WINDOWS = 20

# Richtungsfilter (siehe is_dangerous). Der Sweep zeigt: auf die Fehlalarm-RATE
# haben diese Werte keinen Einfluss (die Ausreißer zeigen ohnehin nach oben).
# Ihr Nutzen ist ein anderer und war messbar: ohne sie drosselte die Kette
# Maschinen, die RUHIGER liefen als der Normalwert.
RISING_SLOPE = 0.05
ELEVATED_SIGMA = 2.0


class AnomalyModel:
    """Ein IsolationForest über die Fenster ALLER Maschinen."""

    def __init__(self) -> None:
        self._warmup: List[List[float]] = []
        self._forest = None  # Pipeline(StandardScaler, IsolationForest)
        self._threshold: float = 0.0
        # Referenz des Normalbetriebs für den Richtungsfilter (siehe unten).
        self._baseline_vib_mean: float = 0.0
        self._baseline_vib_std: float = 0.0

    @property
    def trained(self) -> bool:
        return self._forest is not None

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def warmup_windows(self) -> int:
        return len(self._warmup)

    def observe(self, vector: List[float]) -> None:
        """Während des Warmups: Fenster als Normalbetrieb sammeln."""
        self._warmup.append(list(vector))

    def train(self) -> bool:
        """Trainiert das Modell. False, wenn zu wenige Fenster vorliegen."""
        if len(self._warmup) < MIN_WARMUP_WINDOWS:
            return False
        data = np.asarray(self._warmup, dtype=float)
        # StandardScaler VOR dem Wald: die Merkmale haben völlig verschiedene
        # Größenordnungen (vib_mean ≈ 2.2 gegen vib_slope ≈ 0…0.12). Unskaliert
        # dominiert vib_mean die Splits, und die vorauseilende Vibration — das
        # eigentliche Frühwarnsignal aus §13/§14 — geht unter: die Rampe wurde
        # dann erst vom Guard bei 24.6 s erkannt statt vom Modell.
        forest = make_pipeline(
            StandardScaler(),
            IsolationForest(n_estimators=100, contamination=0.01, random_state=42),
        )
        forest.fit(data)
        scores = forest.decision_function(data)

        p5 = float(np.percentile(scores, THRESHOLD_PERCENTILE))
        spread = float(np.median(scores) - p5)
        margin = max(MARGIN_MIN, MARGIN_SPREAD_FACTOR * spread)
        self._threshold = p5 - margin

        # vib_mean ist Feature 0 (features.FEATURE_NAMES).
        self._baseline_vib_mean = float(data[:, 0].mean())
        self._baseline_vib_std = float(data[:, 0].std())
        self._forest = forest
        return True

    @property
    def baseline_vib_mean(self) -> float:
        return self._baseline_vib_mean

    @property
    def baseline_vib_std(self) -> float:
        return self._baseline_vib_std

    def is_dangerous(self, vib_mean: float, vib_slope: float) -> bool:
        """Richtungsfilter: nur nach OBEN gerichtete Abweichungen sind eine Gefahr.

        Der IsolationForest bewertet Ungewöhnlichkeit in alle Richtungen — ein
        besonders ruhiger Lauf ist ihm genauso "anomal" wie ein Lagerschaden.
        Gedrosselt wird aber nur gegen steigende/erhöhte Vibration (§13/§14);
        ohne diesen Filter drosselte die Kette Maschinen mit vib_mean 2.07,
        also RUHIGER als der Normalwert 2.2 (gemessen).
        """
        if self._forest is None:
            return True
        elevated = vib_mean > self._baseline_vib_mean + ELEVATED_SIGMA * self._baseline_vib_std
        rising = vib_slope > RISING_SLOPE
        return elevated or rising

    def score(self, vector: List[float]) -> float:
        """Anomalie-Score; je kleiner, desto auffälliger. 0.0 ohne Modell."""
        if self._forest is None:
            return 0.0
        return float(self._forest.decision_function(np.asarray([vector], dtype=float))[0])

    def is_anomalous(self, score: float) -> bool:
        return self._forest is not None and score < self._threshold
