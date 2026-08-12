"""Healthcheck der Musterfabrik — grün nur, wenn der Takt wirklich läuft.

Ein Container, der antwortet, aber dessen Fabrik steht, ist nicht gesund: die
Prüfung vergleicht deshalb zwei Messwerte des Tick-Zählers.
"""

import json
import sys
import time
import urllib.request


def health() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=4) as response:
        return json.loads(response.read())


try:
    erst = health()
    time.sleep(2.0)
    zweit = health()
except Exception as exc:  # noqa: BLE001
    print(f"selfcheck: /health nicht erreichbar: {exc}", file=sys.stderr)
    sys.exit(1)

if not zweit.get("ok"):
    print("selfcheck: Fabrik meldet sich als nicht laufend", file=sys.stderr)
    sys.exit(1)

if zweit["ticks"] <= erst["ticks"]:
    print(f"selfcheck: Takt steht (ticks {erst['ticks']} -> {zweit['ticks']})", file=sys.stderr)
    sys.exit(1)

print(f"selfcheck: ok, {zweit['ticks']} Takte, Stationen: {len(zweit['stationen'])}")
