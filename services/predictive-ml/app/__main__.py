"""predictive-ml — Anomalie-Erkennung und Self-Healing (CLAUDE.md §14).

Start:  python -m app            (Service)
        python -m app --selfcheck   (Healthcheck, §15)
"""

from __future__ import annotations

import logging
import sys
import threading

from . import consumer, health
from .actor import Publisher
from .config import Config
from .engine import Engine


def main() -> int:
    if "--selfcheck" in sys.argv:
        return health.selfcheck()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    log = logging.getLogger("predictive-ml")

    cfg = Config.from_env()
    engine = Engine(cfg)
    publisher = Publisher(cfg.brokers)
    ready = threading.Event()

    health.serve(
        ready,
        lambda: {
            "warmup_done": engine.warmup_done,
            "threshold": round(engine.model.threshold, 4),
            "warmup_windows": engine.model.warmup_windows,
            "factory_running": engine.factory_running,
        },
    )
    log.info(
        '{"msg": "predictive-ml startet", "warmup_s": %s, "cooldown_s": %s, "vib_guard": %s}'
        % (cfg.warmup_s, cfg.cooldown_s, cfg.vib_guard)
    )

    try:
        consumer.run(cfg, engine, publisher, ready)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
