"""Kafka-Anbindung: liest sensor_clean (FlatBuffers) und system_events (JSON),
füttert die Engine und publiziert deren Entscheidungen (CLAUDE.md §14).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from confluent_kafka import Consumer, KafkaError

from .actor import GROUP, Publisher, TOPIC_CLEAN, TOPIC_EVENTS
from .config import Config
from .engine import Engine
from .gen.telemetry import SensorReading as sr_mod
from .gen.telemetry.MachineStatus import MachineStatus

log = logging.getLogger("predictive-ml")

_STATUS = {
    MachineStatus.OK: "OK",
    MachineStatus.THROTTLED: "THROTTLED",
    MachineStatus.ERROR: "ERROR",
    MachineStatus.OFFLINE: "OFFLINE",
}


def decode(payload: bytes) -> Optional[tuple]:
    """FlatBuffers-Reading dekodieren. None bei kaputtem Payload (§14: zählen,
    überspringen, niemals crashen)."""
    try:
        if not sr_mod.SensorReading.SensorReadingBufferHasIdentifier(payload, 0):
            return None
        r = sr_mod.SensorReading.GetRootAs(payload, 0)
        return (
            int(r.MachineId()),
            r.TsNs() / 1_000_000_000.0,
            float(r.TemperatureC()),
            float(r.PressureBar()),
            float(r.VibrationMms()),
            _STATUS.get(r.Status(), "OK"),
        )
    except Exception:  # defekte Bytes dürfen den Service nie beenden
        return None


def run(cfg: Config, engine: Engine, publisher: Publisher, ready_flag) -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": cfg.brokers,
            "group.id": GROUP,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "session.timeout.ms": 10000,
        }
    )
    consumer.subscribe([TOPIC_CLEAN, TOPIC_EVENTS])
    ready_flag.set()
    log.info(json.dumps({"msg": "consumer läuft", "topics": [TOPIC_CLEAN, TOPIC_EVENTS]}))

    decode_errors = 0
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.warning(json.dumps({"msg": "kafka-fehler", "err": str(msg.error())}))
            continue

        if msg.topic() == TOPIC_EVENTS:
            _handle_event(engine, msg.value())
            continue

        parsed = decode(msg.value())
        if parsed is None:
            decode_errors += 1
            if decode_errors % 100 == 1:
                log.warning(json.dumps({"msg": "decode-fehler", "gesamt": decode_errors}))
            continue

        machine_id, ts_s, temp, press, vib, status = parsed
        decision = engine.add_sample(machine_id, ts_s, temp, press, vib, status)
        if decision.is_empty():
            continue

        if decision.throttle:
            t = decision.throttle
            publisher.throttle(t)
            # Ein JSON-Log je Entscheidung (§14, Punkt 6).
            log.info(
                json.dumps(
                    {
                        "msg": "entscheidung",
                        "machine": t.machine_id,
                        "factor": t.factor,
                        "threshold": round(engine.model.threshold, 4),
                        "warmup_done": engine.warmup_done,
                        "reason": t.reason,
                    }
                )
            )
        for event in decision.events:
            publisher.event(event)
            if event.kind in ("healed", "info"):
                log.info(json.dumps({"msg": event.kind, "machine": event.machine_id, **event.detail}))


def _handle_event(engine: Engine, raw: bytes) -> None:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return
    if value.get("kind") == "factory_state":
        running = value.get("detail", {}).get("running")
        if isinstance(running, bool):
            engine.set_factory_running(running)
            log.info(json.dumps({"msg": "fabrik-zustand", "running": running}))
