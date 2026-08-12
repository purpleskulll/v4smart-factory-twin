"""Material und Genealogie — das Rückgrat der Musterfabrik (SPEC §6.2, §7.1).

Der zentrale Entwurfsgedanke: Fehler pflanzen sich über das MATERIAL fort, nicht
über die Uhrzeit.

Ein Slurry-Los, das mit erhöhter Viskosität gemischt wurde, trägt diese
Eigenschaft mit sich. Der Coater liest sie aus dem Los und streut deshalb in der
Schichtdicke; der Kalander bekommt eine ungleichmäßige Elektrode und erreicht die
Zielporosität nicht; die daraus gebauten Zellen zeigen in der Formierung ein
Kapazitätsdefizit.

Das ist der Unterschied zwischen einer Demo, in der ein Agent etwas FINDEN kann,
und einer, in der alle Stationen nur zufällig gleichzeitig auffällig sind. Nur
wenn die Kette über `parent`-Referenzen läuft, ist sie über die Genealogie
rückwärts auflösbar — und genau das ist die Kernthese des Produkts (§1).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# Sollwerte je Prozessgröße (SPEC §7.1) — „plausible Lehrbuch-Defaults",
# ausdrücklich KEINE realen Firmendaten.
# ---------------------------------------------------------------------------

PROCESS_WINDOWS: dict[str, tuple[float, float]] = {
    # mixer01
    "viskositaet_pas": (2.0, 6.0),
    "feststoffanteil_pct": (45.0, 55.0),
    "mixer_temp_c": (20.0, 30.0),
    # coater01
    "nassschichtdicke_um": (120.0, 200.0),
    "bahngeschwindigkeit_m_min": (20.0, 60.0),
    "trocknertemp_c": (80.0, 130.0),
    "flaechengewicht_g_m2": (140.0, 180.0),
    # calender01
    "liniendruck_n_mm": (300.0, 1500.0),
    "porositaet_pct": (28.0, 38.0),
    # assembly01
    "ausrichtungsfehler_um": (0.0, 300.0),
    "zugspannung_n": (8.0, 14.0),
    # filling01
    "dosiermenge_g": (4.925, 5.075),  # 5 g ±1,5 %
    "vakuumdruck_mbar": (0.5, 5.0),
    # formation01
    "spannung_v": (3.0, 4.2),
    "form_temp_c": (25.0, 45.0),
    "kapazitaet_ah": (4.6, 5.4),
}


def in_window(name: str, value: float) -> bool:
    """Liegt ein Wert im Sollfenster? Unbekannte Größen gelten als in Ordnung."""
    window = PROCESS_WINDOWS.get(name)
    if window is None:
        return True
    return window[0] <= value <= window[1]


# ---------------------------------------------------------------------------
# Lose und Zellen
# ---------------------------------------------------------------------------

_LOT_COUNTER = itertools.count(1)
_CELL_COUNTER = itertools.count(1)


@dataclass
class Lot:
    """Eine Charge an einem Prozessschritt (SPEC §6.2: `lot`).

    `traits` trägt die Qualitätsmerkmale, die an die nächste Stufe weitergegeben
    werden — das ist der Mechanismus, über den sich ein Fehler fortpflanzt.
    """

    id: str
    station: str
    material: str
    started_at: datetime
    parent_id: str | None = None
    finished_at: datetime | None = None
    traits: dict[str, float] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        station: str,
        material: str,
        now: datetime,
        parent: Lot | None = None,
        prefix: str = "L",
        **traits: float,
    ) -> Lot:
        lot = cls(
            id=f"{prefix}-{next(_LOT_COUNTER):04d}",
            station=station,
            material=material,
            started_at=now,
            parent_id=parent.id if parent else None,
            traits=dict(traits),
        )
        # Merkmale der Vorstufe erben, sofern die neue Stufe sie nicht überschreibt.
        # So erreicht die Viskosität des Mischers noch die Formierung.
        if parent:
            for key, value in parent.traits.items():
                lot.traits.setdefault(f"vorstufe_{key}", value)
        return lot


@dataclass
class Cell:
    """Eine Einzelzelle (SPEC §6.2: `cell`)."""

    serial: str
    lot_id: str
    created_at: datetime
    status: str = "in_prozess"  # in_prozess | ok | ausschuss | quarantaene
    grade: str | None = None
    traits: dict[str, float] = field(default_factory=dict)

    @classmethod
    def create(cls, lot: Lot, now: datetime) -> Cell:
        serial = f"ZW-{now.year}-{next(_CELL_COUNTER):06d}"
        cell = cls(serial=serial, lot_id=lot.id, created_at=now, traits=dict(lot.traits))
        return cell


@dataclass
class GenealogyEdge:
    """Kante im Genealogiegraph (SPEC §6.2: `genealogy`)."""

    parent_kind: str  # "lot"
    parent_id: str
    child_kind: str  # "lot" | "cell"
    child_id: str
    created_at: datetime


class Genealogy:
    """Hält Lose, Zellen und ihre Kanten — und löst sie rückwärts auf.

    SPEC §6.2: „Die Genealogie muss von jeder fertigen Zelle rückwärts bis zur
    Slurry-Charge auflösbar sein." Genau dafür ist `trace_back` da; es ist die
    Grundlage von `trace_cell_genealogy` (§9) und des Battery-Pass (§10.4).
    """

    def __init__(self) -> None:
        self.lots: dict[str, Lot] = {}
        self.cells: dict[str, Cell] = {}
        self.edges: list[GenealogyEdge] = []

    def add_lot(self, lot: Lot, parent: Lot | None, now: datetime) -> Lot:
        self.lots[lot.id] = lot
        if parent is not None:
            self.edges.append(GenealogyEdge("lot", parent.id, "lot", lot.id, now))
        return lot

    def add_cell(self, cell: Cell, lot: Lot, now: datetime) -> Cell:
        self.cells[cell.serial] = cell
        self.edges.append(GenealogyEdge("lot", lot.id, "cell", cell.serial, now))
        return cell

    def parent_of(self, lot_id: str) -> Lot | None:
        lot = self.lots.get(lot_id)
        if lot is None or lot.parent_id is None:
            return None
        return self.lots.get(lot.parent_id)

    def trace_back(self, serial: str) -> list[Lot]:
        """Kompletter Pfad einer Zelle rückwärts: Zell-Los → … → Slurry-Los."""
        cell = self.cells.get(serial)
        if cell is None:
            return []
        path: list[Lot] = []
        current = self.lots.get(cell.lot_id)
        seen: set[str] = set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            path.append(current)
            current = self.parent_of(current.id)
        return path

    def cells_from_lot(self, lot_id: str) -> list[Cell]:
        """Alle Zellen, die (auch mittelbar) aus einem Los hervorgegangen sind.

        Das ist die Betroffenheitsanalyse aus Playbook 10.3: „Welche Zellen sind
        von Slurry-Charge L-0815 betroffen?"
        """
        betroffen: list[Cell] = []
        for serial in self.cells:
            if any(lot.id == lot_id for lot in self.trace_back(serial)):
                betroffen.append(self.cells[serial])
        return betroffen
