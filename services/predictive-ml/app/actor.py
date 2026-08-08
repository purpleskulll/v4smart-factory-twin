"""Publiziert die Entscheidungen der Engine (CLAUDE.md §8/§9).

Die Nachrichten-Erzeugung ist von Kafka getrennt, damit der Kontrakt ohne
Broker testbar ist.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from .engine import Event, Throttle

SCHEMA_VERSION = 1
SOURCE = "predictive-ml"

TOPIC_CONTROL = "machine_control"
TOPIC_EVENTS = "system_events"
TOPIC_CLEAN = "sensor_clean"

GROUP = "predictive-ml"


def now_rfc3339() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def throttle_message(t: Throttle, ts: Optional[str] = None) -> dict:
    """Kommando exakt nach §8."""
    return {
        "v": SCHEMA_VERSION,
        "ts": ts or now_rfc3339(),
        "type": "throttle",
        "machine_id": t.machine_id,
        "factor": t.factor,
        "ttl_s": t.ttl_s,
        "source": SOURCE,
        "reason": t.reason,
    }


def event_message(e: Event, ts: Optional[str] = None) -> dict:
    """Ereignis exakt nach §9."""
    msg = {"v": SCHEMA_VERSION, "ts": ts or now_rfc3339(), "kind": e.kind}
    if e.machine_id is not None:
        msg["machine_id"] = e.machine_id
    msg["detail"] = e.detail
    return msg


class Publisher:
    """Dünner Wrapper um den confluent-kafka-Producer."""

    def __init__(self, brokers: str) -> None:
        from confluent_kafka import Producer

        self._producer = Producer(
            {
                "bootstrap.servers": brokers,
                "compression.type": "snappy",
                "linger.ms": 5,
                "queue.buffering.max.messages": 100000,
            }
        )

    def _send(self, topic: str, key: str, payload: dict) -> None:
        self._producer.produce(topic, key=key.encode(), value=json.dumps(payload).encode())
        self._producer.poll(0)

    def throttle(self, t: Throttle) -> None:
        self._send(TOPIC_CONTROL, str(t.machine_id), throttle_message(t))

    def event(self, e: Event) -> None:
        key = str(e.machine_id) if e.machine_id is not None else "factory"
        self._send(TOPIC_EVENTS, key, event_message(e))

    def flush(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)
