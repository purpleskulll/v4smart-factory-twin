# Demo script (~5 minutes)

Preparation: `make ps` shows everything healthy, and `predictive-ml` has been
running for more than 70 s so its warmup finished — check with
`curl -s http://predictive-ml:8000/healthz`, which should report
`"warmup_done": true`. Open the dashboard at your `TWIN_HOST` (basic auth).

All timings below are measured on the reference stack, not estimated.

## Act 1 — The factory is alive (~1 min)

1. **Control Center**: the stat tiles show ~2,000 msg/s ingest. The line to say:
   "Two thousand FlatBuffers messages per second through Redpanda, filtered by a
   Rust engine, historised in QuestDB."
2. **SCADA Live**: eight machines, all green, pulse dots firing on every frame,
   sparklines for vibration and temperature.
3. **MES/ERP Log**: orders flowing through QUEUED → RUNNING → DONE with progress
   bars.

## Act 2 — The fault and the self-healing loop (~2 min)

4. Control Center → **"Inject Random Hardware Error"**. Note which machine the
   response names.
5. SCADA Live: that machine's vibration climbs visibly while temperature only
   drifts up slowly — the classic early signature of a bearing fault.
6. After **20–28 s** the healing feed reports `anomaly_detected` with the
   Isolation Forest score, then `healing_applied` (throttle to 50 %). The machine
   turns amber (THROTTLED), speed drops to 0.5, and MES throughput visibly slows.
   Worth pointing out: the model fires at ~16 s, the deterministic guard would
   only trip at ~24 s — the prediction is real, not decorative.
7. Values normalise, a `healed` event appears (22–37 s later), and the machine
   goes green again. The full cycle back to `OK` takes ~150 s because the
   throttle runs out its 120 s TTL.
   Key line: "Detected and healed before the temperature ever saw 85 °C —
   measured peak across four runs: 66.0–68.7 °C, and the line never went down."

## Act 3 — The contrast: without the model (~1.5 min, optional but strong)

8. `docker compose stop predictive-ml`, then inject the fault again. This time
   nothing intervenes: vibration climbs to ~8.4 mm/s, temperature follows, and
   after **58 s at 85.7 °C** the machine trips into ERROR, stops, and its orders
   stall. "That is the difference the predictive engine makes."
9. `docker compose start predictive-ml` and bring the machine back with a `reset`
   command (or wait out the 120 s auto-reset). Optional bonus: run
   `make test-healing` in a terminal to show the same loop as an automated,
   reproducible test.

## Closing

10. QuestDB console → run:
    ```sql
    SELECT machine, avg(vib_avg), max(temp_max)
    FROM sensor_agg_1s
    WHERE timestamp > dateadd('h', -1, now())
    GROUP BY machine;
    ```
    "The whole incident is historised and queryable."
11. Optional, Redpanda Console: topics and consumer groups — "Kafka-compatible,
    stateless consumers, horizontally scalable toward 200k msg/s."
