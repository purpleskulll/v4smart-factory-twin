"""Tests der Edge-Rule-Engine (SPEC §8.3).

Der wichtigste Test ist `test_f5_loest_die_uebertemperatur_regel_nicht_aus`:
Ein ausgefallener Kanal meldet 0 °C mit quality=bad. Würde die Engine das als
Wert behandeln, wäre F5 nicht von F3 zu trennen — und Playbook 10.2 hätte keine
Grundlage mehr.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from core.rules.engine import RuleEngine  # noqa: E402

RULES = str(ROOT / "packages" / "core" / "rules" / "rules.yaml")
TEMP_TOPIC = "zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch3_temp_c"


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine.from_yaml(RULES)


def test_regeln_laden(engine: RuleEngine):
    ids = {r.id for r in engine.rules}
    assert "formation-overtemp" in ids
    assert len(engine.rules) >= 5


def test_uebertemperatur_loest_erst_nach_haltezeit_aus(engine: RuleEngine):
    """`for_s: 3` — ein einzelner Ausreißer darf die Anlage nicht drosseln."""
    t0 = time.time()
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0)
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0 + 1.0)
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0 + 2.9)

    treffer = engine.evaluate(TEMP_TOPIC, 55.0, t0 + 3.1)
    assert treffer, "Regel löste nach Ablauf der Haltezeit nicht aus"
    assert treffer[0].rule_id == "formation-overtemp"


def test_unterbrechung_setzt_die_haltezeit_zurueck(engine: RuleEngine):
    t0 = time.time()
    engine.evaluate(TEMP_TOPIC, 55.0, t0)
    engine.evaluate(TEMP_TOPIC, 55.0, t0 + 2.0)
    engine.evaluate(TEMP_TOPIC, 42.0, t0 + 2.5)          # wieder im grünen Bereich
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0 + 3.5), (
        "Haltezeit lief weiter, obwohl die Bedingung zwischendurch gebrochen war"
    )


def test_f5_loest_die_uebertemperatur_regel_nicht_aus(engine: RuleEngine):
    """Ein ausgefallener Kanal ist KEIN Temperaturproblem (SPEC §7.3, F5)."""
    t0 = time.time()
    topic = "zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch6_temp_c"
    for offset in (0.0, 1.5, 3.0, 4.5, 6.0):
        treffer = engine.evaluate(topic, 0.0, t0 + offset, quality="bad")
        assert not treffer, "Regel reagierte auf einen Kanal mit quality=bad"


def test_kanalnummer_wird_in_das_kommando_uebernommen(engine: RuleEngine):
    """Eine Regel muss für alle acht Kanäle reichen."""
    t0 = time.time()
    topic = "zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch7_temp_c"
    engine.evaluate(topic, 56.0, t0)
    treffer = engine.evaluate(topic, 56.0, t0 + 3.5)
    assert treffer

    aktionen = engine.resolve_actions(treffer[0])
    publish = [a for a in aktionen if a.kind == "publish"]
    assert publish, "kein Kommando erzeugt"
    assert publish[0].payload["channel"] == 7, (
        f"falscher Kanal im Kommando: {publish[0].payload}"
    )
    assert publish[0].payload["factor"] == 0.5


def test_cooldown_verhindert_dauerfeuer(engine: RuleEngine):
    t0 = time.time()
    engine.evaluate(TEMP_TOPIC, 55.0, t0)
    assert engine.evaluate(TEMP_TOPIC, 55.0, t0 + 3.5)
    # Innerhalb des Cooldowns (60 s) darf nichts mehr kommen.
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0 + 20.0)
    assert not engine.evaluate(TEMP_TOPIC, 55.0, t0 + 59.0)
    assert engine.evaluate(TEMP_TOPIC, 55.0, t0 + 65.0), "Regel blieb nach dem Cooldown stumm"


def test_andere_kanaele_bleiben_unberuehrt(engine: RuleEngine):
    """Zustand wird je (Regel, Topic) geführt — nicht global."""
    t0 = time.time()
    heiss = "zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch3_temp_c"
    kalt = "zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch4_temp_c"

    engine.evaluate(heiss, 55.0, t0)
    engine.evaluate(kalt, 33.0, t0 + 1.0)
    assert engine.evaluate(heiss, 55.0, t0 + 3.5), "heißer Kanal löste nicht aus"
    assert not engine.evaluate(kalt, 33.0, t0 + 3.5), "kalter Kanal löste mit aus"


def test_auswertung_ist_schnell_genug(engine: RuleEngine):
    """SPEC §8.3: Symptom→cmd unter 500 ms.

    Hier wird der Anteil gemessen, den die Engine selbst beisteuert. Der Rest
    ist Netzwerk/Broker und wird im laufenden Betrieb von rules/main.py
    gemessen. Wenn schon die reine Auswertung nennenswert Zeit kostet, ist die
    Anforderung nicht zu halten — deshalb diese Untergrenze.
    """
    topics = [f"zellwerk/v1/werk1/zelle/linie1/formation01/pv/ch{i}_temp_c"
              for i in range(1, 9)]
    start = time.perf_counter()
    runden = 500
    for n in range(runden):
        for topic in topics:
            engine.evaluate(topic, 33.0 + (n % 5), time.time())
    dauer_ms = (time.perf_counter() - start) * 1000.0
    je_wert_ms = dauer_ms / (runden * len(topics))

    assert je_wert_ms < 1.0, f"Auswertung braucht {je_wert_ms:.3f} ms je Wert"
    print(f"Auswertung: {je_wert_ms*1000:.1f} µs je Wert")
