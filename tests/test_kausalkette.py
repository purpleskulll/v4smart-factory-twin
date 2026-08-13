"""Der wichtigste Test der Musterfabrik: stimmen die Kausalketten aus SPEC §7.3?

Wenn diese Tests durchfallen, hat der Diagnose-Agent später NICHTS zu finden —
dann sind die Stationen nur sechs unabhängige Zufallsgeneratoren. Die Kette
selbst ist das Produkt.

Läuft komplett offline in Simulationszeit, ohne OPC UA, MQTT oder Datenbank.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from simfactory.material import in_window  # noqa: E402
from simfactory.stations import Factory  # noqa: E402


def werte_von(werte, station, name):
    """Holt einen Prozesswert aus dem Takt-Ergebnis."""
    for pv in werte.get(station, []):
        if pv.name == name:
            return pv
    return None


def lauf(factory: Factory, minuten: float):
    """Lässt die Fabrik laufen und sammelt Verläufe der interessanten Größen."""
    verlauf: dict[str, list[float]] = {}
    for werte in factory.run(minuten * 60):
        for station, pvs in werte.items():
            for pv in pvs:
                if isinstance(pv.value, (int, float)) and not isinstance(pv.value, bool):
                    verlauf.setdefault(f"{station}.{pv.name}", []).append(float(pv.value))
    return verlauf


# ---------------------------------------------------------------------------
# Normalbetrieb
# ---------------------------------------------------------------------------


def test_normalbetrieb_bleibt_im_prozessfenster():
    """Ohne Fehler muss alles im Sollfenster laufen — sonst sind Alarme wertlos."""
    factory = Factory(seed=42)
    verlauf = lauf(factory, 30)

    for schluessel in ("mixer01.viskositaet_pas", "calender01.porositaet_pct",
                       "coater01.nassschichtdicke_um", "filling01.dosiermenge_g"):
        name = schluessel.split(".", 1)[1]
        werte = verlauf[schluessel]
        ausreisser = [w for w in werte if not in_window(name, w)]
        anteil = len(ausreisser) / len(werte)
        assert anteil < 0.02, (
            f"{schluessel}: {anteil:.1%} der Werte außerhalb des Fensters "
            f"(Beispiele: {ausreisser[:3]})"
        )


def test_normalbetrieb_produziert_gute_zellen():
    factory = Factory(seed=42)
    list(factory.run(45 * 60))

    fertig = factory.formation.fertig
    assert len(fertig) >= 5, f"nur {len(fertig)} Zellen in 45 min formiert"
    ausschuss = [c for c in fertig if c.status == "ausschuss"]
    assert not ausschuss, f"Normalbetrieb erzeugte Ausschuss: {[c.grade for c in ausschuss]}"


# ---------------------------------------------------------------------------
# F1 — die lange Kette: Mischer -> Coater -> Kalander -> Formierung
# ---------------------------------------------------------------------------


def test_f1_viskositaet_steigt_und_verlaesst_das_fenster():
    factory = Factory(seed=42)
    list(factory.run(5 * 60))
    factory.inject("F1")
    verlauf = lauf(factory, 50)

    visk = verlauf["mixer01.viskositaet_pas"]
    assert visk[-1] > visk[0] + 2.0, f"Viskosität stieg nur von {visk[0]:.2f} auf {visk[-1]:.2f}"
    assert max(visk) > 6.0, "Viskosität verließ das Sollfenster (>6 Pa*s) nie"


def test_f1_pflanzt_sich_ueber_das_material_bis_zur_kapazitaet_fort():
    """Die vollständige Kette — das Kernversprechen der Demo.

    Viskosität hoch -> Schichtdicke streut -> Porosität unter Soll ->
    Zellen verlieren Kapazität. Jedes Glied wird einzeln geprüft.
    """
    factory = Factory(seed=42)
    factory.inject("F1")
    list(factory.run(180 * 60))

    # Glied 2: Streuung am Coater
    sigmas = [lot.traits.get("schichtdicke_sigma_um", 3.0)
              for lot in factory.coater.finished_lots]
    assert sigmas, "keine Elektroden-Lose fertiggestellt"
    assert max(sigmas) > 6.0, f"Schichtdicken-Streuung blieb bei {max(sigmas):.2f} µm"

    # Glied 3: Porosität am Kalander
    porositaeten = [lot.traits.get("porositaet_pct", 33.0)
                    for lot in factory.calender.finished_lots]
    assert porositaeten, "keine kalandrierten Lose fertiggestellt"
    assert min(porositaeten) < 28.0, (
        f"Porosität fiel nie unter das Sollfenster (min {min(porositaeten):.2f} %)"
    )

    # Glied 4: Kapazitätsdefizit in der Formierung
    fertig = factory.formation.fertig
    assert fertig, "keine Zellen formiert"
    schlecht = [c for c in fertig if c.traits.get("kapazitaet_ah", 5.0) < 4.6]
    assert schlecht, (
        "keine einzige Zelle mit Kapazitätsdefizit — die Kette reißt vor der Formierung"
    )


def test_f1_ist_ueber_die_genealogie_rueckwaerts_aufloesbar():
    """SPEC §6.2: von der Zelle rückwärts bis zur Slurry-Charge.

    Das ist die Grundlage von Playbook 10.1 — ohne diesen Pfad kann der Agent
    die Wurzelstation nicht benennen.
    """
    factory = Factory(seed=42)
    factory.inject("F1")
    list(factory.run(180 * 60))

    schlecht = [c for c in factory.formation.fertig
                if c.traits.get("kapazitaet_ah", 5.0) < 4.6]
    assert schlecht, "kein Ausschuss erzeugt, Test kann nichts zurückverfolgen"

    pfad = factory.genealogy.trace_back(schlecht[0].serial)
    stationen = [lot.station for lot in pfad]
    assert "mixer01" in stationen, (
        f"Pfad endet nicht beim Mischer, sondern bei {stationen}"
    )

    slurry = next(lot for lot in pfad if lot.station == "mixer01")
    assert slurry.traits["viskositaet_pas"] > 6.0, (
        "die Slurry-Charge trägt die erhöhte Viskosität nicht — "
        "die Ursache wäre nicht belegbar"
    )


# ---------------------------------------------------------------------------
# F4 — dasselbe Endsymptom, anderer Weg. Das muss unterscheidbar bleiben.
# ---------------------------------------------------------------------------


def test_f4_senkt_kapazitaet_ohne_die_porositaet_anzufassen():
    factory = Factory(seed=7)
    factory.inject("F4")
    list(factory.run(120 * 60))

    fertig = factory.formation.fertig
    assert fertig, "keine Zellen formiert"

    dosen = [c.traits.get("dosiermenge_g", 5.0) for c in fertig]
    assert max(dosen) < 4.925, f"Dosierung blieb im Toleranzband (max {max(dosen):.3f} g)"

    # Der entscheidende Unterschied zu F1: die Porosität ist unauffällig.
    porositaeten = [lot.traits.get("porositaet_pct", 33.0)
                    for lot in factory.calender.finished_lots]
    if porositaeten:
        assert min(porositaeten) > 28.0, (
            f"F4 hat die Porosität mitgezogen (min {min(porositaeten):.2f} %) — "
            "dann wäre F4 nicht mehr von F1 zu unterscheiden"
        )


# ---------------------------------------------------------------------------
# F3 und F5 — Anlagenproblem mit und ohne Qualitätsfolge (Playbook 10.2)
# ---------------------------------------------------------------------------


def test_f3_treibt_genau_einen_kanal_ueber_50_grad():
    factory = Factory(seed=42)
    factory.inject("F3")
    verlauf = lauf(factory, 20)

    betroffen = f"formation01.ch{factory.formation.f3_channel}_temp_c"
    assert max(verlauf[betroffen]) > 50.0, (
        f"Kanal {factory.formation.f3_channel} blieb unter 50 °C "
        f"(max {max(verlauf[betroffen]):.1f})"
    )

    for kanal in range(1, factory.formation.channel_count + 1):
        if kanal == factory.formation.f3_channel:
            continue
        schluessel = f"formation01.ch{kanal}_temp_c"
        assert max(verlauf[schluessel]) < 45.0, (
            f"Kanal {kanal} wurde mit heiß — F3 muss auf EINEN Kanal begrenzt sein"
        )


def test_f3_setzt_zellen_in_quarantaene_nicht_auf_ausschuss():
    factory = Factory(seed=42)
    factory.inject("F3")
    list(factory.run(120 * 60))

    quarantaene = [c for c in factory.formation.fertig if c.status == "quarantaene"]
    assert quarantaene, "F3 erzeugte keine Quarantäne-Zellen"
    assert all(c.grade == "uebertemperatur" for c in quarantaene)


def test_f5_liefert_bad_quality_aber_keinen_qualitaetsschaden():
    """F5 ist ein Durchsatzproblem, KEIN Qualitätsproblem (SPEC §7.3)."""
    factory = Factory(seed=42)
    factory.inject("F5")
    list(factory.run(120 * 60))

    werte = factory.formation.tick(factory.now)
    kanal = factory.formation.f5_channel
    bad = [pv for pv in werte if pv.name.startswith(f"ch{kanal}_") and pv.quality == "bad"]
    assert bad, f"Kanal {kanal} meldet keine schlechte Qualität"

    # Entscheidend: die Zellen, die durchliefen, sind in Ordnung.
    fertig = factory.formation.fertig
    ausschuss = [c for c in fertig if c.status == "ausschuss"]
    assert not ausschuss, (
        f"F5 erzeugte Qualitätsausschuss ({[c.grade for c in ausschuss]}) — "
        "dann wäre es nicht mehr von F3 zu unterscheiden"
    )


def test_f3_und_f5_sind_am_signal_unterscheidbar():
    """Das Akzeptanzkriterium von Playbook 10.2, auf Datenebene abgesichert."""
    f3 = Factory(seed=42)
    f3.inject("F3")
    list(f3.run(20 * 60))
    f3_werte = f3.formation.tick(f3.now)

    f5 = Factory(seed=42)
    f5.inject("F5")
    list(f5.run(20 * 60))
    f5_werte = f5.formation.tick(f5.now)

    f3_temp = next(pv for pv in f3_werte if pv.name == f"ch{f3.formation.f3_channel}_temp_c")
    f5_temp = next(pv for pv in f5_werte if pv.name == f"ch{f5.formation.f5_channel}_temp_c")

    assert f3_temp.quality == "good" and f3_temp.value > 50.0, (
        "F3 muss GUTE Werte liefern, die zu hoch sind"
    )
    assert f5_temp.quality == "bad", "F5 muss SCHLECHTE Qualität liefern"


# ---------------------------------------------------------------------------
# Edge-Rule-Wirkung (SPEC §8.3) — die Drosselung muss auch wirken
# ---------------------------------------------------------------------------


def test_drosselung_senkt_die_temperatur_wieder():
    factory = Factory(seed=42)
    factory.inject("F3")
    list(factory.run(15 * 60))

    kanal = factory.formation.f3_channel
    vorher = next(pv for pv in factory.formation.tick(factory.now)
                  if pv.name == f"ch{kanal}_temp_c").value

    assert factory.formation.derate_channel(kanal, 0.5)
    nachher = next(pv for pv in factory.formation.tick(factory.now)
                   if pv.name == f"ch{kanal}_temp_c").value

    assert nachher < vorher, (
        f"Drosselung ohne Wirkung: {vorher:.1f} °C -> {nachher:.1f} °C"
    )
