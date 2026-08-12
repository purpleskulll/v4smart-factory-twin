"""Die sechs Stationen der Musterfabrik (SPEC §7.1).

Bewusst OHNE OPC UA und ohne MQTT: die komplette Prozesslogik — Werte, Drifts,
Materialfluss, Fehlerwirkung — ist damit offline und in Simulationszeit testbar.
`opcua_layer.py` stülpt die Schnittstelle darüber, `main.py` treibt die Uhr.

Zufall ist immer seed-gesteuert. Ein Test, der bei jedem Lauf andere Werte
sieht, kann keine Kausalkette nachweisen.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .material import Cell, Genealogy, Lot

# ---------------------------------------------------------------------------
# Prozesswert
# ---------------------------------------------------------------------------


@dataclass
class PV:
    """Ein Prozesswert, wie er in den UNS geht (SPEC §6.1)."""

    name: str
    value: float | str | bool
    unit: str = ""
    quality: str = "good"  # good | bad | uncertain


@dataclass
class Station:
    """Basis: Takt, Rauschen, Fehlerzustand."""

    station_id: str
    area: str
    line: str
    port: int
    tick_s: float = 1.0
    rng: random.Random = field(default_factory=lambda: random.Random(42))
    faults: set[str] = field(default_factory=set)
    _fault_started: dict[str, datetime] = field(default_factory=dict)

    def inject(self, fault_id: str, now: datetime) -> None:
        self.faults.add(fault_id)
        self._fault_started[fault_id] = now

    def clear(self, fault_id: str) -> None:
        self.faults.discard(fault_id)
        self._fault_started.pop(fault_id, None)

    def fault_minutes(self, fault_id: str, now: datetime) -> float:
        """Wie lange läuft ein Fehler schon? Treibt langsame Drifts."""
        started = self._fault_started.get(fault_id)
        if started is None:
            return 0.0
        return (now - started).total_seconds() / 60.0

    def noise(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma)

    def tick(self, now: datetime) -> list[PV]:  # pragma: no cover - Basisklasse
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1) Mischen — Ursprung der Kausalkette F1
# ---------------------------------------------------------------------------


@dataclass
class Mixer(Station):
    station_id: str = "mixer01"
    area: str = "elektrode"
    line: str = "linie1"
    port: int = 4841

    # 10 min je Los — abgestimmt auf Coater und Kalander (je 400 m bei
    # 40 m/min). Bei 8 min lief der Mischer der Linie davon und die
    # Slurry-Warteschlange wuchs unbegrenzt.
    batch_minutes: float = 10.0
    _batch_elapsed: float = 0.0
    current_lot: Lot | None = None
    finished_lots: list[Lot] = field(default_factory=list)

    def viskositaet(self, now: datetime) -> float:
        """Basisviskosität 4,0 Pa·s.

        F1 (SPEC §7.3): steigt über 45 min linear von 4 auf 7 Pa·s. Das
        Sollfenster endet bei 6 — der Fehler wird also erst nach rund 30 min
        als Grenzverletzung sichtbar, vorher nur als Trend. Genau das macht ihn
        zu einer interessanten Aufgabe: wer nur Grenzwerte prüft, sieht ihn spät.
        """
        base = 4.0 + 0.15 * math.sin(now.timestamp() / 600.0)
        if "F1" in self.faults:
            minutes = min(self.fault_minutes("F1", now), 45.0)
            base += 3.0 * (minutes / 45.0)
        return base + self.noise(0.05)

    def tick(self, now: datetime) -> list[PV]:
        visk = self.viskositaet(now)
        feststoff = 50.0 + self.noise(0.4)
        temp = 25.0 + 1.5 * math.sin(now.timestamp() / 900.0) + self.noise(0.2)

        self._batch_elapsed += self.tick_s / 60.0
        if self.current_lot is None:
            self.current_lot = Lot.create(
                "mixer01", "slurry", now, prefix="SLURRY", viskositaet_pas=visk
            )
            self._batch_elapsed = 0.0
        else:
            # Laufender Mittelwert: das Los trägt am Ende die Viskosität, mit
            # der es tatsächlich gemischt wurde — nicht den letzten Messwert.
            alt = self.current_lot.traits.get("viskositaet_pas", visk)
            n = max(self._batch_elapsed * 60.0 / self.tick_s, 1.0)
            self.current_lot.traits["viskositaet_pas"] = alt + (visk - alt) / n

        return [
            PV("viskositaet_pas", round(visk, 3), "Pa*s"),
            PV("feststoffanteil_pct", round(feststoff, 2), "%"),
            PV("mixer_temp_c", round(temp, 2), "degC"),
            PV("mischdauer_min", round(self._batch_elapsed, 2), "min"),
            PV("aktuelles_los", self.current_lot.id if self.current_lot else ""),
        ]

    def release_lot(self, now: datetime) -> Lot | None:
        """Gibt ein fertiges Slurry-Los frei, wenn die Mischdauer erreicht ist."""
        if self.current_lot is None or self._batch_elapsed < self.batch_minutes:
            return None
        lot = self.current_lot
        lot.finished_at = now
        self.finished_lots.append(lot)
        self.current_lot = None
        self._batch_elapsed = 0.0
        return lot


# ---------------------------------------------------------------------------
# 2) Beschichten — F1 wird hier zur Streuung, F2 entsteht hier
# ---------------------------------------------------------------------------


@dataclass
class Coater(Station):
    station_id: str = "coater01"
    area: str = "elektrode"
    line: str = "linie1"
    port: int = 4842

    input_lot: Lot | None = None
    current_lot: Lot | None = None
    _coated_m: float = 0.0
    meter_per_lot: float = 400.0
    finished_lots: list[Lot] = field(default_factory=list)

    def schichtdicke_sigma(self) -> float:
        """Streuung der Nassschichtdicke.

        Das ist der Übertragungspunkt von F1: eine zähere Slurry lässt sich
        nicht gleichmäßig ausziehen. Unter 6 Pa·s (Sollfenster) bleibt die
        Streuung bei ~3 µm, darüber wächst sie stark an.
        """
        visk = 4.0
        if self.input_lot is not None:
            visk = self.input_lot.traits.get("viskositaet_pas", 4.0)
        if visk <= 6.0:
            return 3.0
        return 3.0 + 8.0 * (visk - 6.0)

    def tick(self, now: datetime) -> list[PV]:
        sigma = self.schichtdicke_sigma()
        dicke = 160.0 + self.rng.gauss(0.0, sigma)
        speed = 40.0 + self.noise(0.6)

        trockner = 105.0 + self.noise(0.8)
        if "F2" in self.faults:
            # F2: Trocknertemperatur zu hoch. Flächengewicht bleibt in Ordnung,
            # aber die Haftung leidet — sichtbar wird das erst in der Assemblierung.
            trockner += 28.0 + 4.0 * min(self.fault_minutes("F2", now) / 10.0, 1.0)

        haftungsindex = 1.0
        if trockner > 130.0:
            haftungsindex = max(0.35, 1.0 - (trockner - 130.0) / 45.0)

        flaechengewicht = 160.0 * (dicke / 160.0) + self.noise(1.2)

        self._coated_m += speed * (self.tick_s / 60.0)
        if self.current_lot is not None:
            t = self.current_lot.traits
            t["schichtdicke_sigma_um"] = sigma
            t["haftungsindex"] = min(t.get("haftungsindex", 1.0), haftungsindex)

        return [
            PV("nassschichtdicke_um", round(dicke, 2), "um"),
            PV("schichtdicke_sigma_um", round(sigma, 2), "um"),
            PV("bahngeschwindigkeit_m_min", round(speed, 2), "m/min"),
            PV("trocknertemp_c", round(trockner, 2), "degC"),
            PV("flaechengewicht_g_m2", round(flaechengewicht, 2), "g/m2"),
            PV("haftungsindex", round(haftungsindex, 3), ""),
            PV("aktuelles_los", self.current_lot.id if self.current_lot else ""),
        ]

    def feed(self, slurry: Lot, now: datetime) -> Lot:
        """Nimmt ein Slurry-Los an und beginnt ein Elektroden-Los daraus."""
        self.input_lot = slurry
        self.current_lot = Lot.create(
            "coater01", "elektrode", now, parent=slurry, prefix="ELEK"
        )
        self._coated_m = 0.0
        return self.current_lot

    def release_lot(self, now: datetime) -> Lot | None:
        if self.current_lot is None or self._coated_m < self.meter_per_lot:
            return None
        lot = self.current_lot
        lot.finished_at = now
        self.finished_lots.append(lot)
        self.current_lot = None
        return lot


# ---------------------------------------------------------------------------
# 3) Kalandrieren — hier wird aus Streuung ein Porositätsfehler
# ---------------------------------------------------------------------------


@dataclass
class Calender(Station):
    station_id: str = "calender01"
    area: str = "elektrode"
    line: str = "linie1"
    port: int = 4843

    input_lot: Lot | None = None
    current_lot: Lot | None = None
    _pressed_m: float = 0.0
    meter_per_lot: float = 400.0
    finished_lots: list[Lot] = field(default_factory=list)

    def porositaet(self) -> float:
        """Zielporosität 33 % (Fenster 28–38 %).

        Eine ungleichmäßig beschichtete Elektrode wird beim Kalandrieren an den
        dicken Stellen stärker verdichtet — die mittlere Porosität sinkt. Ab
        ~6 µm Streuung verlässt sie das Fenster nach unten. Das ist das dritte
        Glied der F1-Kette und der Grund, warum die Zellen später Kapazität
        verlieren: zu wenig Porenvolumen für den Elektrolyten.
        """
        sigma = 3.0
        if self.input_lot is not None:
            sigma = self.input_lot.traits.get("schichtdicke_sigma_um", 3.0)
        poros = 33.0 - 1.55 * max(0.0, sigma - 3.0)
        return poros + self.noise(0.25)

    def tick(self, now: datetime) -> list[PV]:
        poros = self.porositaet()
        druck = 900.0 + self.noise(12.0)
        spalt = 75.0 + self.noise(0.8)

        self._pressed_m += 40.0 * (self.tick_s / 60.0)
        if self.current_lot is not None:
            alt = self.current_lot.traits.get("porositaet_pct")
            self.current_lot.traits["porositaet_pct"] = (
                poros if alt is None else alt + (poros - alt) * 0.05
            )

        return [
            PV("liniendruck_n_mm", round(druck, 1), "N/mm"),
            PV("spaltmass_um", round(spalt, 2), "um"),
            PV("porositaet_pct", round(poros, 2), "%"),
            PV("aktuelles_los", self.current_lot.id if self.current_lot else ""),
        ]

    def feed(self, elektrode: Lot, now: datetime) -> Lot:
        self.input_lot = elektrode
        self.current_lot = Lot.create(
            "calender01", "elektrode_kalandriert", now, parent=elektrode, prefix="KAL"
        )
        self._pressed_m = 0.0
        return self.current_lot

    def release_lot(self, now: datetime) -> Lot | None:
        if self.current_lot is None or self._pressed_m < self.meter_per_lot:
            return None
        lot = self.current_lot
        lot.finished_at = now
        self.finished_lots.append(lot)
        self.current_lot = None
        return lot


# ---------------------------------------------------------------------------
# 4) Assemblierung — erzeugt die Zellen; F2 wird hier sichtbar
# ---------------------------------------------------------------------------


@dataclass
class Assembly(Station):
    station_id: str = "assembly01"
    area: str = "zelle"
    line: str = "linie1"
    port: int = 4844

    input_lot: Lot | None = None
    current_lot: Lot | None = None
    _seconds_since_cell: float = 0.0
    _cells_from_lot: int = 0

    # Taktabstimmung der ganzen Linie — bewusst so gewählt, dass die Fabrik im
    # Fließgleichgewicht läuft und sich keine Warteschlange unbegrenzt aufbaut:
    #   Mischer    10 min je Slurry-Los
    #   Coater     400 m bei 40 m/min      = 10 min
    #   Kalander   400 m bei 40 m/min      = 10 min
    #   Assembly   20 Zellen à 30 s        = 10 min
    #   Formierung 8 Kanäle à 240 s        = 1 Zelle je 30 s
    # Ohne diese Abstimmung staute sich das Material vor der Formierung (455
    # wartende Zellen nach 3 h), und die auffälligen Chargen erreichten die
    # Formierung nie — die Kausalkette war dann nicht nachweisbar.
    cell_interval_s: float = 30.0
    cells_per_lot: int = 20

    produced: list[Cell] = field(default_factory=list)
    delaminationen: int = 0

    def tick(self, now: datetime) -> list[PV]:
        haftung = 1.0
        if self.input_lot is not None:
            haftung = self.input_lot.traits.get("vorstufe_haftungsindex",
                                                self.input_lot.traits.get("haftungsindex", 1.0))
        # Schlechte Haftung → Delamination beim Wickeln → Ausrichtungsfehler.
        ausrichtung = 120.0 + self.noise(18.0) + (1.0 - haftung) * 900.0
        zug = 11.0 + self.noise(0.4)
        takt = 3600.0 / self.cell_interval_s

        return [
            PV("ausrichtungsfehler_um", round(max(0.0, ausrichtung), 1), "um"),
            PV("zugspannung_n", round(zug, 2), "N"),
            PV("takt_zellen_h", round(takt, 1), "1/h"),
            PV("delaminationen_gesamt", self.delaminationen, ""),
            PV("aktuelles_los", self.current_lot.id if self.current_lot else ""),
        ]

    def feed(self, elektrode: Lot, now: datetime) -> Lot:
        self.input_lot = elektrode
        self.current_lot = Lot.create(
            "assembly01", "zelle_roh", now, parent=elektrode, prefix="ZELL"
        )
        self._cells_from_lot = 0
        return self.current_lot

    def maybe_build_cell(self, now: datetime, genealogy: Genealogy) -> Cell | None:
        """Baut im Takt eine Zelle aus dem laufenden Los.

        Ein Los ist nach `cells_per_lot` Zellen AUFGEBRAUCHT — danach wird das
        nächste angefordert. Ohne diese Erschöpfung blieb das erste Los für immer
        aktiv, alle Zellen erbten dessen Merkmale, und nachfolgende (auffällige)
        Chargen erreichten die Formierung nie.
        """
        if self.current_lot is None:
            return None
        self._seconds_since_cell += self.tick_s
        if self._seconds_since_cell < self.cell_interval_s:
            return None
        self._seconds_since_cell = 0.0

        cell = Cell.create(self.current_lot, now)
        haftung = self.current_lot.traits.get("vorstufe_haftungsindex", 1.0)
        if haftung < 0.6 and self.rng.random() < 0.35:
            # Delamination: die Zelle ist direkt Ausschuss (F2-Symptom).
            cell.status = "ausschuss"
            cell.grade = "delamination"
            self.delaminationen += 1
        genealogy.add_cell(cell, self.current_lot, now)
        self.produced.append(cell)

        self._cells_from_lot += 1
        if self._cells_from_lot >= self.cells_per_lot:
            self.current_lot.finished_at = now
            self.current_lot = None  # Los aufgebraucht — das nächste darf kommen
        return cell


# ---------------------------------------------------------------------------
# 5) Elektrolytbefüllung — F4 entsteht hier
# ---------------------------------------------------------------------------


@dataclass
class Filling(Station):
    station_id: str = "filling01"
    area: str = "zelle"
    line: str = "linie1"
    port: int = 4845

    soll_dosis_g: float = 5.0
    pumpe: str = "P-01"
    befuellt: list[Cell] = field(default_factory=list)

    def dosis(self) -> float:
        """5 g ±1,5 %. F4: die Pumpe dosiert 5 % zu wenig.

        Die Abweichung liegt außerhalb der Toleranz, ist aber klein genug, dass
        sie in der Station selbst unauffällig bleibt — sie fällt erst in der
        Formierung als Kapazitätsdefizit auf. Zuordenbar ist sie nur über die
        Genealogie (welche Zellen liefen über diese Pumpe?), genau wie in §7.3
        beschrieben.
        """
        dosis = self.soll_dosis_g + self.noise(0.02)
        if "F4" in self.faults:
            dosis *= 0.95
        return dosis

    def tick(self, now: datetime) -> list[PV]:
        dosis = self.dosis()
        vakuum = 2.0 + self.noise(0.15)
        dicht = "pass" if self.rng.random() > 0.01 else "fail"
        return [
            PV("dosiermenge_g", round(dosis, 4), "g"),
            PV("vakuumdruck_mbar", round(vakuum, 3), "mbar"),
            PV("dichtheitspruefung", dicht, ""),
            PV("pumpe", self.pumpe, ""),
        ]

    def fill(self, cell: Cell) -> None:
        dosis = self.dosis()
        cell.traits["dosiermenge_g"] = dosis
        cell.traits["dosierpumpe"] = float(int(self.pumpe.split("-")[1]))
        self.befuellt.append(cell)


# ---------------------------------------------------------------------------
# 6) Formierung — hier wird alles sichtbar; F3 und F5 entstehen hier
# ---------------------------------------------------------------------------


@dataclass
class FormationChannel:
    index: int
    cell: Cell | None = None
    elapsed_s: float = 0.0
    derate_factor: float = 1.0
    offline: bool = False


@dataclass
class Formation(Station):
    station_id: str = "formation01"
    area: str = "zelle"
    line: str = "linie1"
    port: int = 4846
    tick_s: float = 10.0

    channel_count: int = 8
    dauer_s: float = 240.0
    channels: list[FormationChannel] = field(default_factory=list)
    fertig: list[Cell] = field(default_factory=list)
    f3_channel: int = 3
    f5_channel: int = 6

    def __post_init__(self) -> None:
        if not self.channels:
            self.channels = [FormationChannel(i) for i in range(1, self.channel_count + 1)]

    # -- Kapazität: das Endsymptom aller Materialfehler ---------------------
    def erwartete_kapazitaet(self, cell: Cell) -> float:
        """Nennkapazität 5,0 Ah, gemindert durch die Vorgeschichte der Zelle.

        Hier laufen die beiden Fehlerpfade zusammen — und genau hier müssen sie
        unterscheidbar bleiben:
          * F1 wirkt über die Porosität (zu wenig Porenvolumen).
          * F4 wirkt über die Elektrolytmenge (zu wenig Ionenreservoir).
        Das Endsymptom ist beide Male „zu wenig Kapazität". Wer nur die
        Formierung ansieht, kann sie nicht trennen — man braucht die
        Genealogie. Das ist die Aufgabe von Playbook 10.1.
        """
        kapazitaet = 5.0

        porositaet = cell.traits.get("vorstufe_porositaet_pct",
                                     cell.traits.get("porositaet_pct"))
        if porositaet is not None and porositaet < 28.0:
            kapazitaet -= min(0.9, (28.0 - porositaet) * 0.22)

        dosis = cell.traits.get("dosiermenge_g")
        if dosis is not None and dosis < 4.925:
            kapazitaet -= min(0.8, (4.925 - dosis) * 2.4)

        return kapazitaet + self.noise(0.02)

    def spannungskurve(self, fortschritt: float, gestoert: bool) -> float:
        """C/10-Erstladung: 3,0 V → 4,2 V mit Plateau (SPEC §7.1, §7.2)."""
        basis = 3.0 + 1.2 * (1.0 - math.exp(-3.2 * fortschritt))
        if gestoert:
            # Auffälliges Profil: früheres, flacheres Plateau.
            basis = 3.0 + 1.05 * (1.0 - math.exp(-4.6 * fortschritt))
        return min(4.2, basis) + self.noise(0.004)

    def tick(self, now: datetime) -> list[PV]:
        pvs: list[PV] = []
        for ch in self.channels:
            offline = ch.offline or ("F5" in self.faults and ch.index == self.f5_channel)
            if offline:
                # F5: Kanalausfall. Keine Werte, quality=bad — ein reines
                # Anlagenproblem OHNE Qualitätsfolge. Die Unterscheidung zu F3
                # ist das Akzeptanzkriterium von Playbook 10.2.
                pvs.extend([
                    PV(f"ch{ch.index}_spannung_v", 0.0, "V", quality="bad"),
                    PV(f"ch{ch.index}_strom_c", 0.0, "C", quality="bad"),
                    PV(f"ch{ch.index}_temp_c", 0.0, "degC", quality="bad"),
                    PV(f"ch{ch.index}_status", "offline"),
                ])
                continue

            temp = 32.0 + self.noise(0.6)
            gestoert = False
            if "F3" in self.faults and ch.index == self.f3_channel:
                # F3: Übertemperatur. Steigt bis ~58 °C, die Edge-Rule muss
                # unter 1 s drosseln (SPEC §7.3/§8.3).
                minuten = self.fault_minutes("F3", now)
                temp = 32.0 + min(26.0, 9.0 * minuten) + self.noise(0.5)
                gestoert = temp > 50.0
            temp *= 1.0 - 0.35 * (1.0 - ch.derate_factor)

            fortschritt = min(1.0, ch.elapsed_s / self.dauer_s) if ch.cell else 0.0
            spannung = self.spannungskurve(fortschritt, gestoert) if ch.cell else 0.0
            strom = 0.1 * ch.derate_factor if ch.cell else 0.0

            pvs.extend([
                PV(f"ch{ch.index}_spannung_v", round(spannung, 4), "V"),
                PV(f"ch{ch.index}_strom_c", round(strom, 4), "C"),
                PV(f"ch{ch.index}_temp_c", round(temp, 2), "degC"),
                PV(f"ch{ch.index}_status", "belegt" if ch.cell else "frei"),
                PV(f"ch{ch.index}_derate", round(ch.derate_factor, 2), ""),
            ])

            if ch.cell is not None:
                ch.elapsed_s += self.tick_s

        pvs.append(PV("kanaele_belegt", sum(1 for c in self.channels if c.cell), ""))
        return pvs

    def load_cell(self, cell: Cell) -> bool:
        for ch in self.channels:
            offline = ch.offline or ("F5" in self.faults and ch.index == self.f5_channel)
            if ch.cell is None and not offline:
                ch.cell = cell
                ch.elapsed_s = 0.0
                return True
        return False

    def derate_channel(self, index: int, factor: float) -> bool:
        """Wird von der Edge-Rule gerufen (§8.3) — deterministisch, ohne LLM."""
        for ch in self.channels:
            if ch.index == index:
                ch.derate_factor = max(0.1, min(1.0, factor))
                return True
        return False

    def collect_finished(self, now: datetime) -> list[Cell]:
        """Gibt fertig formierte Zellen zurück und bewertet sie."""
        fertige: list[Cell] = []
        for ch in self.channels:
            if ch.cell is None or ch.elapsed_s < self.dauer_s:
                continue
            cell = ch.cell
            kapazitaet = self.erwartete_kapazitaet(cell)
            cell.traits["kapazitaet_ah"] = kapazitaet
            cell.traits["formationskanal"] = float(ch.index)

            if "F3" in self.faults and ch.index == self.f3_channel:
                cell.status = "quarantaene"
                cell.grade = "uebertemperatur"
            elif kapazitaet < 4.6:
                cell.status = "ausschuss"
                cell.grade = "kapazitaet_zu_niedrig"
            elif cell.status == "in_prozess":
                cell.status = "ok"
                cell.grade = "A" if kapazitaet >= 4.9 else "B"

            ch.cell = None
            ch.elapsed_s = 0.0
            self.fertig.append(cell)
            fertige.append(cell)
        return fertige


# ---------------------------------------------------------------------------
# Fabrik: Takt + Materialfluss über alle Stationen
# ---------------------------------------------------------------------------


@dataclass
class Factory:
    """Hält alle Stationen und schiebt das Material durch (SPEC §7.1, Absatz Materialfluss)."""

    seed: int = 42
    started_at: datetime = field(default_factory=lambda: datetime(2026, 8, 13, 6, 0, 0))
    genealogy: Genealogy = field(default_factory=Genealogy)
    now: datetime = field(init=False)

    mixer: Mixer = field(init=False)
    coater: Coater = field(init=False)
    calender: Calender = field(init=False)
    assembly: Assembly = field(init=False)
    filling: Filling = field(init=False)
    formation: Formation = field(init=False)

    _slurry_queue: list[Lot] = field(default_factory=list)
    _elektrode_queue: list[Lot] = field(default_factory=list)
    _kalander_queue: list[Lot] = field(default_factory=list)
    _befuell_queue: list[Cell] = field(default_factory=list)
    _formier_queue: list[Cell] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.now = self.started_at
        make = lambda offset: random.Random(self.seed + offset)  # noqa: E731
        self.mixer = Mixer(rng=make(1))
        self.coater = Coater(rng=make(2))
        self.calender = Calender(rng=make(3))
        self.assembly = Assembly(rng=make(4))
        self.filling = Filling(rng=make(5))
        self.formation = Formation(rng=make(6))

    @property
    def stations(self) -> list[Station]:
        return [self.mixer, self.coater, self.calender,
                self.assembly, self.filling, self.formation]

    def station_by_id(self, station_id: str) -> Station | None:
        return next((s for s in self.stations if s.station_id == station_id), None)

    def inject(self, fault_id: str) -> str:
        """Fehler einspielen (SPEC §7.3). Gibt die betroffene Station zurück."""
        ziel = {
            "F1": self.mixer, "F2": self.coater, "F3": self.formation,
            "F4": self.filling, "F5": self.formation,
        }.get(fault_id)
        if ziel is None:
            raise ValueError(f"unbekanntes Fehlerszenario: {fault_id}")
        ziel.inject(fault_id, self.now)
        return ziel.station_id

    def clear(self, fault_id: str) -> None:
        for station in self.stations:
            station.clear(fault_id)

    def step(self, dt_s: float = 1.0) -> dict[str, list[PV]]:
        """Ein Takt: Werte erzeugen und Material weiterschieben."""
        self.now += timedelta(seconds=dt_s)
        werte: dict[str, list[PV]] = {}

        for station in self.stations:
            if station is self.formation:
                # Formierung taktet mit 10 s (SPEC §7.1).
                if int((self.now - self.started_at).total_seconds()) % int(station.tick_s) != 0:
                    continue
            werte[station.station_id] = station.tick(self.now)

        self._move_material()
        return werte

    def _move_material(self) -> None:
        now = self.now

        if (lot := self.mixer.release_lot(now)) is not None:
            self.genealogy.add_lot(lot, None, now)
            self._slurry_queue.append(lot)

        if self.coater.current_lot is None and self._slurry_queue:
            neu = self.coater.feed(self._slurry_queue.pop(0), now)
            self.genealogy.add_lot(neu, self.coater.input_lot, now)
        if (lot := self.coater.release_lot(now)) is not None:
            self._elektrode_queue.append(lot)

        if self.calender.current_lot is None and self._elektrode_queue:
            neu = self.calender.feed(self._elektrode_queue.pop(0), now)
            self.genealogy.add_lot(neu, self.calender.input_lot, now)
        if (lot := self.calender.release_lot(now)) is not None:
            self._kalander_queue.append(lot)

        if self.assembly.current_lot is None and self._kalander_queue:
            neu = self.assembly.feed(self._kalander_queue.pop(0), now)
            self.genealogy.add_lot(neu, self.assembly.input_lot, now)
        if (cell := self.assembly.maybe_build_cell(now, self.genealogy)) is not None:
            if cell.status != "ausschuss":
                self._befuell_queue.append(cell)

        while self._befuell_queue:
            cell = self._befuell_queue.pop(0)
            self.filling.fill(cell)
            self._formier_queue.append(cell)

        while self._formier_queue and self.formation.load_cell(self._formier_queue[0]):
            self._formier_queue.pop(0)

        self.formation.collect_finished(now)

    def run(self, seconds: float, dt_s: float = 1.0):
        """Generator für Tests: läuft `seconds` in Simulationszeit."""
        for _ in range(int(seconds / dt_s)):
            yield self.step(dt_s)
