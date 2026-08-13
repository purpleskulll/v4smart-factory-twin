"""Mock-ERP (SPEC §7, §2).

Zwei Endpunkte, mehr braucht der MVP nicht: Fertigungsaufträge und Stammdaten.
Ausdrücklich KEINE echte SAP-Anbindung — die kommt laut Schutzzaun (§2) erst
nach dem MVP. Die Daten liegen im Speicher; ein Neustart setzt sie zurück.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="zellwerk Mock-ERP")


class ProductionOrder(BaseModel):
    id: str
    produkt: str
    sollmenge: int
    status: str  # geplant | freigegeben | laufend | abgeschlossen
    erstellt_am: datetime
    faellig_am: datetime


def _seed_orders() -> list[ProductionOrder]:
    jetzt = datetime.now(UTC)
    return [
        ProductionOrder(
            id="PO-2026-0801", produkt="ZW-NMC-5Ah", sollmenge=500, status="laufend",
            erstellt_am=jetzt - timedelta(days=2), faellig_am=jetzt + timedelta(days=3),
        ),
        ProductionOrder(
            id="PO-2026-0802", produkt="ZW-NMC-5Ah", sollmenge=750, status="freigegeben",
            erstellt_am=jetzt - timedelta(days=1), faellig_am=jetzt + timedelta(days=6),
        ),
        ProductionOrder(
            id="PO-2026-0803", produkt="ZW-LFP-4Ah", sollmenge=300, status="geplant",
            erstellt_am=jetzt, faellig_am=jetzt + timedelta(days=10),
        ),
    ]


ORDERS: list[ProductionOrder] = _seed_orders()

STAMMDATEN = {
    "ZW-NMC-5Ah": {
        "chemie": "NMC811 / Graphit",
        "nennkapazitaet_ah": 5.0,
        "nennspannung_v": 3.7,
        "format": "Pouch",
        "sollporositaet_pct": [28, 38],
        "soll_elektrolyt_g": 5.0,
    },
    "ZW-LFP-4Ah": {
        "chemie": "LFP / Graphit",
        "nennkapazitaet_ah": 4.0,
        "nennspannung_v": 3.2,
        "format": "Pouch",
        "sollporositaet_pct": [30, 40],
        "soll_elektrolyt_g": 4.2,
    },
}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "auftraege": len(ORDERS)}


@app.get("/orders")
async def list_orders(status: str | None = None) -> list[ProductionOrder]:
    if status is None:
        return ORDERS
    return [o for o in ORDERS if o.status == status]


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> ProductionOrder:
    for order in ORDERS:
        if order.id == order_id:
            return order
    raise HTTPException(status_code=404, detail=f"Auftrag {order_id} unbekannt")


@app.get("/master-data")
async def master_data() -> dict:
    return STAMMDATEN


@app.get("/master-data/{produkt}")
async def master_data_for(produkt: str) -> dict:
    if produkt not in STAMMDATEN:
        raise HTTPException(status_code=404, detail=f"Produkt {produkt} unbekannt")
    return STAMMDATEN[produkt]
