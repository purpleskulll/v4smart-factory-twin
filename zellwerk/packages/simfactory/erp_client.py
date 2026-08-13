"""Auftragsanbindung an das Mock-ERP (SPEC §7.1).

Die SPEC verlangt ausdrücklich: „Ein `production_order` (aus dem Mock-ERP per
REST abgeholt) erzeugt Slurry-Lose im Mischer." Ohne diese Kopplung trägt kein
Los eine Auftragsnummer — und damit lässt sich die naheliegendste Frage der
Fertigung nicht beantworten: *für welchen Auftrag* wurde eine auffällige Charge
gefahren, und welche weiteren Zellen desselben Auftrags sind betroffen?

Der Client ist bewusst fehlertolerant: Fällt das ERP aus, produziert die Fabrik
weiter (mit `order_id = None`), statt anzuhalten. Eine Fertigung, die stehen
bleibt, weil ein Auftragssystem hakt, wäre in der Realität unbrauchbar. Der
Ausfall wird aber protokolliert, nicht verschluckt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("simfactory.erp")


@dataclass
class Order:
    id: str
    produkt: str
    sollmenge: int
    status: str
    gefertigt: int = 0

    @property
    def offen(self) -> int:
        return max(0, self.sollmenge - self.gefertigt)

    @property
    def erfuellt(self) -> bool:
        return self.gefertigt >= self.sollmenge


class ErpClient:
    """Holt Fertigungsaufträge und reicht sie der Fabrik der Reihe nach zu."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.orders: list[Order] = []
        self._index = 0
        self.last_error: str | None = None

    async def refresh(self) -> int:
        """Lädt die freigegebenen und laufenden Aufträge. Gibt deren Anzahl zurück."""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                antwort = await client.get(f"{self.base_url}/orders")
                antwort.raise_for_status()
                roh = antwort.json()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.warning("Mock-ERP nicht erreichbar (%s) — Fertigung läuft ohne Auftragsbezug", exc)
            return 0

        self.last_error = None
        bekannt = {o.id for o in self.orders}
        for eintrag in roh:
            if eintrag["id"] in bekannt:
                continue
            if eintrag.get("status") not in ("freigegeben", "laufend"):
                continue
            self.orders.append(Order(
                id=eintrag["id"], produkt=eintrag["produkt"],
                sollmenge=int(eintrag["sollmenge"]), status=eintrag["status"],
            ))
        if self.orders:
            log.info("Aufträge übernommen: %s",
                     ", ".join(f"{o.id} ({o.offen} offen)" for o in self.orders))
        return len(self.orders)

    def current(self) -> Order | None:
        """Der Auftrag, für den gerade gefertigt wird.

        Ist er erfüllt, rückt der nächste nach. Sind alle erfüllt, liefert die
        Methode `None` — die Fabrik läuft dann ohne Auftragsbezug weiter, statt
        die Produktion einzustellen.
        """
        while self._index < len(self.orders):
            order = self.orders[self._index]
            if not order.erfuellt:
                return order
            log.info("Auftrag %s erfüllt (%d/%d Zellen)",
                     order.id, order.gefertigt, order.sollmenge)
            self._index += 1
        return None

    def count_cell(self) -> str | None:
        """Bucht eine gefertigte Zelle auf den laufenden Auftrag."""
        order = self.current()
        if order is None:
            return None
        order.gefertigt += 1
        return order.id

    def summary(self) -> list[dict]:
        return [
            {"id": o.id, "produkt": o.produkt, "sollmenge": o.sollmenge,
             "gefertigt": o.gefertigt, "offen": o.offen, "erfuellt": o.erfuellt}
            for o in self.orders
        ]
