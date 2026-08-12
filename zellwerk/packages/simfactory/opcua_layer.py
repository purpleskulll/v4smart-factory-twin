"""OPC-UA-Schnittstelle der Musterfabrik (SPEC §7.1).

Jede Station ist ein eigener OPC-UA-Server auf eigenem Port (4841–4846). Die
Prozesslogik selbst steht in `stations.py` und kennt OPC UA nicht — diese Datei
ist reine Schnittstelle. Dadurch bleibt die Fabrik ohne Netzwerk testbar.

Der Namensraum ist flach gehalten: ein Objekt je Station, darunter eine Variable
je Prozesswert. Für den Konnektor (§8.1) ist das der einfachste denkbare
Node-Baum, und mehr braucht die Demo nicht.
"""

from __future__ import annotations

import logging

from asyncua import Server, ua

from .stations import PV, Station

log = logging.getLogger("simfactory.opcua")

NAMESPACE = "http://zellwerk.local/simfactory"


class StationServer:
    """Ein OPC-UA-Server für genau eine Station."""

    def __init__(self, station: Station, host: str = "0.0.0.0") -> None:
        self.station = station
        self.host = host
        self.server = Server()
        self.variables: dict[str, ua.Node] = {}
        self._idx: int | None = None

    @property
    def endpoint(self) -> str:
        return f"opc.tcp://{self.host}:{self.station.port}/zellwerk/{self.station.station_id}"

    async def start(self, initial: list[PV]) -> None:
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name(f"zellwerk {self.station.station_id}")
        # Keine Verschlüsselung: MVP-Schutzzaun (SPEC §2, kein Login/RBAC).
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        self._idx = await self.server.register_namespace(NAMESPACE)
        obj = await self.server.nodes.objects.add_object(self._idx, self.station.station_id)

        for pv in initial:
            node = await obj.add_variable(self._idx, pv.name, self._as_ua(pv.value))
            # Beschreibbar, damit Kommandos (z. B. Drosselung) denselben Weg
            # nehmen können wie in einer echten Anlage.
            await node.set_writable()
            self.variables[pv.name] = node

        await self.server.start()
        log.info("OPC-UA-Server für %s auf %s", self.station.station_id, self.endpoint)

    @staticmethod
    def _as_ua(value):
        """OPC UA ist streng typisiert — der erste Wert legt den Typ fest."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return float(value)
        return value

    # OPC UA kennt echte Qualitätskennzeichen. Die MÜSSEN übertragen werden:
    # ein ausgefallener Formierkanal (F5) meldet `Bad`, ein zu heißer Kanal (F3)
    # meldet `Good` mit hohem Wert. Ginge nur die Zahl über die Leitung, wären
    # die beiden Fälle nicht mehr unterscheidbar — und genau diese
    # Unterscheidung ist das Akzeptanzkriterium von Playbook 10.2.
    QUALITY_TO_STATUS = {
        "good": ua.StatusCodes.Good,
        "bad": ua.StatusCodes.Bad,
        "uncertain": ua.StatusCodes.Uncertain,
    }

    async def publish(self, pvs: list[PV]) -> None:
        for pv in pvs:
            node = self.variables.get(pv.name)
            if node is None:
                continue
            try:
                # Feldname ist `StatusCode` (asyncua 2.0.1, nachgeprüft — nicht
                # `StatusCode_`, wie ältere Beispiele im Netz zeigen).
                datenwert = ua.DataValue(
                    Value=ua.Variant(self._as_ua(pv.value)),
                    StatusCode=ua.StatusCode(
                        self.QUALITY_TO_STATUS.get(pv.quality, ua.StatusCodes.Good)
                    ),
                )
                await node.write_value(datenwert)
            except Exception as exc:  # noqa: BLE001
                # Ein einzelner Schreibfehler darf den Takt nicht anhalten,
                # aber er wird sichtbar gemacht statt verschluckt.
                log.warning("Schreibfehler %s.%s: %s", self.station.station_id, pv.name, exc)

    async def stop(self) -> None:
        await self.server.stop()
