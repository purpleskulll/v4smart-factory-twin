//! Kafka-Konsumenten: der Hot Path (sensor_raw) und die JSON-Plane
//! (mes_orders, machine_control, system_events).
//!
//! Alles, was pro Reading passiert, läuft in EINEM Task: validieren,
//! aggregieren, Snapshot pflegen, downsamplen und WS-Frames erzeugen. Der
//! geteilte Zustand wird nur kurz gesperrt, im Lock wird nie gewartet.

use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::Message;
use serde_json::{json, Value};

use crate::agg::Aggregator;
use crate::config::Config;
use crate::gen::telemetry::root_as_sensor_reading;
use crate::ilp::{self, IlpSender};
use crate::state::{status_str, AppState, Counters};

pub const GROUP: &str = "middleware-core";
pub const TOPIC_RAW: &str = "sensor_raw";
pub const TOPIC_CLEAN: &str = "sensor_clean";
pub const TOPIC_ORDERS: &str = "mes_orders";
pub const TOPIC_CONTROL: &str = "machine_control";
pub const TOPIC_EVENTS: &str = "system_events";

/// Von allen Tasks geteilte Handles.
#[derive(Clone)]
pub struct Shared {
    pub cfg: Config,
    pub state: Arc<Mutex<AppState>>,
    pub counters: Arc<Counters>,
    pub frames: tokio::sync::broadcast::Sender<String>,
    pub ilp: IlpSender,
    pub producer: FutureProducer,
    pub started: std::time::Instant,
}

impl Shared {
    /// Sendet einen Frame an alle WS-Clients (ohne Empfänger schadenlos).
    pub fn broadcast(&self, frame: Value) {
        let _ = self.frames.send(frame.to_string());
    }
}

pub fn build_consumer(brokers: &str, topics: &[&str], offset_reset: &str) -> anyhow::Result<StreamConsumer> {
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", brokers)
        .set("group.id", GROUP)
        .set("enable.auto.commit", "true")
        .set("auto.commit.interval.ms", "5000")
        .set("auto.offset.reset", offset_reset)
        .set("fetch.wait.max.ms", "50")
        .set("session.timeout.ms", "10000")
        .create()?;
    consumer.subscribe(topics)?;
    Ok(consumer)
}

pub fn build_producer(brokers: &str) -> anyhow::Result<FutureProducer> {
    Ok(ClientConfig::new()
        .set("bootstrap.servers", brokers)
        .set("compression.type", "snappy")
        .set("linger.ms", "5")
        .set("queue.buffering.max.messages", "200000")
        .create()?)
}

/// Hot Path: sensor_raw -> validate -> agg/ILP -> sensor_clean -> WS.
pub async fn run_hot_path(sh: Shared) -> anyhow::Result<()> {
    // "latest": nach einem Neustart interessiert nur das Jetzt. Mit "earliest"
    // würde der Core bis zu einer Stunde alter Readings aufholen (2000/s =
    // Millionen Nachrichten) und den Historian mit alten Aggregaten fluten.
    let consumer = build_consumer(&sh.cfg.brokers, &[TOPIC_RAW], "latest")?;
    let mut agg = Aggregator::new();

    let forward_gap_ns = (1_000_000_000.0 / sh.cfg.downsample_hz) as i64;
    let ws_gap_ns = (1_000_000_000.0 / sh.cfg.ws_telemetry_hz) as i64;
    let mut last_forward: HashMap<u16, i64> = HashMap::new();
    let mut last_ws: HashMap<u16, i64> = HashMap::new();

    tracing::info!(topic = TOPIC_RAW, downsample_hz = sh.cfg.downsample_hz, "Hot Path läuft");

    loop {
        let msg = match consumer.recv().await {
            Ok(m) => m,
            Err(e) => {
                // Broker weg: rdkafka verbindet selbst neu, wir loggen und
                // machen weiter — kein Abbruch des Services (§18 Kill-Test).
                tracing::warn!(err = %e, "sensor_raw: Empfangsfehler");
                continue;
            }
        };
        sh.counters.incoming.fetch_add(1, Ordering::Relaxed);

        let Some(payload) = msg.payload() else {
            sh.counters.dropped.fetch_add(1, Ordering::Relaxed);
            continue;
        };
        let Ok(reading) = root_as_sensor_reading(payload) else {
            // Kaputte oder fremdformatige Nachricht (z. B. JSON) — zählen,
            // niemals crashen (Hard Rule §4.1).
            sh.counters.dropped.fetch_add(1, Ordering::Relaxed);
            continue;
        };

        let now_ns = now_ns();
        if crate::validate::check(&reading, now_ns, sh.cfg.machine_count).is_err() {
            sh.counters.dropped.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        sh.counters.valid.fetch_add(1, Ordering::Relaxed);

        let id = reading.machine_id();
        let ts_ns = reading.ts_ns();
        let (temp, press, vib, speed) = (
            reading.temperature_c(),
            reading.pressure_bar(),
            reading.vibration_mms(),
            reading.speed_factor(),
        );
        let status = status_str(reading.status());

        if let Ok(mut st) = sh.state.lock() {
            st.update_machine(id, status, temp, press, vib, speed, ts_ns);
        }

        // 1-Sekunden-Aggregat -> QuestDB (einziger Schreiber, §4.6).
        if let Some(bucket) = agg.add(id, ts_ns, temp, press, vib, speed) {
            sh.ilp.send(bucket.to_ilp());
        }

        // Downsample: Original-Bytes unverändert weiterreichen (zero-copy Relay).
        let last = last_forward.entry(id).or_insert(0);
        if ts_ns - *last >= forward_gap_ns {
            *last = ts_ns;
            let key = id.to_string();
            let record = FutureRecord::to(TOPIC_CLEAN).key(&key).payload(payload);
            match sh.producer.send_result(record) {
                Ok(_) => { sh.counters.clean.fetch_add(1, Ordering::Relaxed); }
                Err(_) => { sh.counters.dropped.fetch_add(1, Ordering::Relaxed); }
            }
        }

        // Telemetrie an die Browser (WS_TELEMETRY_HZ je Maschine, §10).
        let last = last_ws.entry(id).or_insert(0);
        if ts_ns - *last >= ws_gap_ns {
            *last = ts_ns;
            sh.broadcast(json!({
                "t": "telemetry", "m": id, "ts_ms": ts_ns / 1_000_000,
                "temp": temp, "press": press, "vib": vib, "speed": speed, "status": status
            }));
        }
    }
}

/// JSON-Plane: Aufträge, Ereignisse und mitgelesene Kommandos.
pub async fn run_json_plane(sh: Shared) -> anyhow::Result<()> {
    // "earliest": diese Topics sind niederfrequent (24 h Retention, wenige
    // hundert Nachrichten). Beim ersten Start füllen sie die Ringpuffer, sodass
    // der Healing-Feed und das MES-Log sofort Inhalt haben statt leer zu sein
    // (live beobachtet: /api/events war nach einem Core-Neustart leer, obwohl
    // die Ereignisse im Topic lagen). Danach zählen die committeten Offsets.
    let consumer = build_consumer(&sh.cfg.brokers, &[TOPIC_ORDERS, TOPIC_EVENTS, TOPIC_CONTROL], "earliest")?;
    tracing::info!("JSON-Plane läuft");

    loop {
        let msg = match consumer.recv().await {
            Ok(m) => m,
            Err(e) => {
                tracing::warn!(err = %e, "JSON-Plane: Empfangsfehler");
                continue;
            }
        };
        let topic = msg.topic().to_string();
        let Some(payload) = msg.payload() else { continue };
        let Ok(value) = serde_json::from_slice::<Value>(payload) else {
            tracing::warn!(topic = %topic, "unlesbares JSON verworfen");
            continue;
        };

        match topic.as_str() {
            TOPIC_ORDERS => {
                if let Ok(mut st) = sh.state.lock() {
                    st.push_order(value.clone());
                }
                if let (Some(machine), Some(order_id), Some(status), Some(product)) = (
                    value.get("machine_id").and_then(Value::as_u64),
                    value.get("order_id").and_then(Value::as_str),
                    value.get("status").and_then(Value::as_str),
                    value.get("product").and_then(Value::as_str),
                ) {
                    sh.ilp.send(ilp::order_line(
                        machine as u16,
                        product,
                        status,
                        order_id,
                        value.get("qty").and_then(Value::as_i64).unwrap_or(0),
                        value.get("progress").and_then(Value::as_f64).unwrap_or(0.0),
                        now_ns(),
                    ));
                }
                sh.broadcast(json!({"t": "order", "order": value}));
            }

            TOPIC_EVENTS => {
                let kind = value.get("kind").and_then(Value::as_str).unwrap_or("info").to_string();
                let machine = value.get("machine_id").and_then(Value::as_u64).map(|m| m as u16);

                if let Ok(mut st) = sh.state.lock() {
                    // anomaly_score aus dem ML in den Maschinen-Snapshot mergen (§10).
                    if kind == "anomaly_detected" {
                        if let (Some(id), Some(score)) =
                            (machine, value.pointer("/detail/score").and_then(Value::as_f64))
                        {
                            st.set_anomaly_score(id, score);
                        }
                    }
                    st.push_event(value.clone());
                }

                if let Some(id) = machine {
                    let score = value.pointer("/detail/score").and_then(Value::as_f64).unwrap_or(0.0);
                    let action = value
                        .pointer("/detail/action")
                        .and_then(Value::as_str)
                        .unwrap_or("none")
                        .to_string();
                    sh.ilp.send(ilp::ml_event_line(id, &kind, score, &action, now_ns()));
                }
                sh.broadcast(json!({"t": "event", "event": value}));
            }

            TOPIC_CONTROL => {
                // Mitlesen, damit eine Drosselung sofort sichtbar ist, auch
                // bevor das erste Reading mit status=THROTTLED eintrifft (§10).
                if value.get("type").and_then(Value::as_str) == Some("throttle") {
                    if let Some(id) = value.get("machine_id").and_then(Value::as_u64) {
                        tracing::info!(machine = id, "throttle beobachtet");
                    }
                }
            }
            _ => {}
        }
    }
}

/// Aktualisiert 1× pro Sekunde die Raten und schickt den stats-Frame (§10).
pub async fn run_stats_ticker(sh: Shared) {
    let mut tick = tokio::time::interval(std::time::Duration::from_secs(1));
    loop {
        tick.tick().await;
        let ws_clients = sh.counters.ws_clients.load(Ordering::Relaxed);
        let db_dropped = sh.counters.db_dropped.load(Ordering::Relaxed);
        let stats = {
            let Ok(mut st) = sh.state.lock() else { continue };
            st.tick_rates(&sh.counters);
            st.stats(ws_clients, db_dropped)
        };
        sh.broadcast(json!({"t": "stats", "stats": stats}));
    }
}

pub fn now_ns() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0)
}
