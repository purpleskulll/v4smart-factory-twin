//! Live-Zustand: Maschinen-Snapshot, Ringpuffer für Aufträge/Ereignisse und
//! die gleitenden 5-Sekunden-Raten (CLAUDE.md §10).

use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use serde::Serialize;
use serde_json::Value;

pub const ORDERS_CAP: usize = 500;
pub const EVENTS_CAP: usize = 1000;
const RATE_WINDOW_S: usize = 5;

/// Ein Maschinen-Eintrag für `GET /api/machines` (§10).
#[derive(Debug, Clone, Serialize)]
pub struct MachineSnapshot {
    pub id: u16,
    pub status: &'static str,
    pub temp: f32,
    pub press: f32,
    pub vib: f32,
    pub speed: f32,
    pub last_seen_ms: i64,
    pub anomaly_score: Option<f64>,
}

/// Kennzahlen für `GET /api/stats` (§10).
#[derive(Debug, Clone, Serialize)]
pub struct Stats {
    pub in_rate: u64,
    pub valid_rate: u64,
    pub dropped_rate: u64,
    pub clean_rate: u64,
    pub db_rows_s: u64,
    pub ws_clients: usize,
    pub factory_running: bool,
    /// Additiv zu §10: verworfene Historian-Zeilen seit dem Start. Ohne diese
    /// Zahl bleibt ein QuestDB-Ausfall in den Kennzahlen unsichtbar.
    pub db_dropped_total: u64,
}

/// Gleitende Rate über RATE_WINDOW_S Sekunden.
#[derive(Debug, Default)]
struct RateMeter {
    window: VecDeque<u64>,
    current: u64,
}

impl RateMeter {
    fn inc(&mut self, n: u64) { self.current += n; }

    /// Einmal pro Sekunde aufrufen.
    fn tick(&mut self) {
        self.window.push_back(self.current);
        self.current = 0;
        while self.window.len() > RATE_WINDOW_S {
            self.window.pop_front();
        }
    }

    fn rate(&self) -> u64 {
        if self.window.is_empty() {
            return 0;
        }
        let sum: u64 = self.window.iter().sum();
        sum / self.window.len() as u64
    }
}

#[derive(Debug, Default)]
struct Meters {
    incoming: RateMeter,
    valid: RateMeter,
    dropped: RateMeter,
    clean: RateMeter,
    db_rows: RateMeter,
}

/// Gesamter geteilter Zustand. Wird hinter einem kurzen std-Mutex gehalten —
/// im Lock wird nie `.await` aufgerufen.
#[derive(Debug, Default)]
pub struct AppState {
    machines: BTreeMap<u16, MachineSnapshot>,
    orders: VecDeque<Value>,
    events: VecDeque<Value>,
    meters: Meters,
    factory_running: bool,
}

/// Zähler, die ohne Lock hochgezählt werden (Hot Path).
#[derive(Debug, Default)]
pub struct Counters {
    pub incoming: AtomicU64,
    pub valid: AtomicU64,
    pub dropped: AtomicU64,
    pub clean: AtomicU64,
    pub db_rows: AtomicU64,
    pub db_dropped: AtomicU64,
    pub ws_clients: AtomicUsize,
}

impl Counters {
    pub fn take(&self, c: &AtomicU64) -> u64 { c.swap(0, Ordering::Relaxed) }
}

pub fn status_str(status: crate::gen::telemetry::MachineStatus) -> &'static str {
    use crate::gen::telemetry::MachineStatus as S;
    match status {
        S::THROTTLED => "THROTTLED",
        S::ERROR => "ERROR",
        S::OFFLINE => "OFFLINE",
        _ => "OK",
    }
}

impl AppState {
    pub fn new() -> Self { Self::default() }

    /// Übernimmt ein gültiges Reading in den Snapshot.
    #[allow(clippy::too_many_arguments)]
    pub fn update_machine(
        &mut self,
        id: u16,
        status: &'static str,
        temp: f32,
        press: f32,
        vib: f32,
        speed: f32,
        ts_ns: i64,
    ) {
        let entry = self.machines.entry(id).or_insert(MachineSnapshot {
            id,
            status,
            temp,
            press,
            vib,
            speed,
            last_seen_ms: 0,
            anomaly_score: None,
        });
        entry.status = status;
        entry.temp = temp;
        entry.press = press;
        entry.vib = vib;
        entry.speed = speed;
        entry.last_seen_ms = ts_ns / 1_000_000;
    }

    pub fn machines(&self) -> Vec<MachineSnapshot> {
        self.machines.values().cloned().collect()
    }

    /// Maschinen mit Status OK — Grundlage für `inject_error` ohne machine_id (§10).
    pub fn running_machine_ids(&self) -> Vec<u16> {
        self.machines.values().filter(|m| m.status == "OK").map(|m| m.id).collect()
    }

    pub fn set_anomaly_score(&mut self, id: u16, score: f64) {
        if let Some(m) = self.machines.get_mut(&id) {
            m.anomaly_score = Some(score);
        }
    }

    pub fn push_order(&mut self, order: Value) {
        push_capped(&mut self.orders, order, ORDERS_CAP);
    }

    pub fn push_event(&mut self, event: Value) {
        // factory_state hält den Fabrik-Zustand für /api/stats nach (§9/§10).
        if event.get("kind").and_then(Value::as_str) == Some("factory_state") {
            if let Some(running) = event.pointer("/detail/running").and_then(Value::as_bool) {
                self.factory_running = running;
            }
        }
        push_capped(&mut self.events, event, EVENTS_CAP);
    }

    /// Neueste zuerst (§10).
    pub fn orders(&self, limit: usize) -> Vec<Value> {
        self.orders.iter().rev().take(limit).cloned().collect()
    }

    pub fn events(&self, limit: usize) -> Vec<Value> {
        self.events.iter().rev().take(limit).cloned().collect()
    }

    /// Der Fabrik-Zustand wird PRIMÄR aus den Readings abgeleitet: läuft auch
    /// nur eine Maschine (Status != OFFLINE), läuft die Fabrik. Das
    /// `factory_state`-Event ist nur der Rückfall für den Moment, in dem noch
    /// kein Reading eingetroffen ist — verließe man sich allein darauf, meldete
    /// ein nach dem Simulator gestarteter Core dauerhaft "gestoppt", obwohl
    /// 2000 msg/s fließen (live beobachtet).
    pub fn factory_running(&self) -> bool {
        if self.machines.is_empty() {
            return self.factory_running;
        }
        self.machines.values().any(|m| m.status != "OFFLINE")
    }

    /// Übernimmt die lockfreien Zähler in die Ratenfenster (1× pro Sekunde).
    pub fn tick_rates(&mut self, c: &Counters) {
        self.meters.incoming.inc(c.take(&c.incoming));
        self.meters.valid.inc(c.take(&c.valid));
        self.meters.dropped.inc(c.take(&c.dropped));
        self.meters.clean.inc(c.take(&c.clean));
        self.meters.db_rows.inc(c.take(&c.db_rows));
        self.meters.incoming.tick();
        self.meters.valid.tick();
        self.meters.dropped.tick();
        self.meters.clean.tick();
        self.meters.db_rows.tick();
    }

    pub fn stats(&self, ws_clients: usize, db_dropped_total: u64) -> Stats {
        Stats {
            in_rate: self.meters.incoming.rate(),
            valid_rate: self.meters.valid.rate(),
            dropped_rate: self.meters.dropped.rate(),
            clean_rate: self.meters.clean.rate(),
            db_rows_s: self.meters.db_rows.rate(),
            ws_clients,
            factory_running: self.factory_running(),
            db_dropped_total,
        }
    }
}

fn push_capped(buf: &mut VecDeque<Value>, v: Value, cap: usize) {
    if buf.len() == cap {
        buf.pop_front();
    }
    buf.push_back(v);
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rate_is_average_over_window() {
        let mut m = RateMeter::default();
        for _ in 0..RATE_WINDOW_S {
            m.inc(2000);
            m.tick();
        }
        assert_eq!(m.rate(), 2000);

        // Fällt die Last auf 0, sinkt die gleitende Rate schrittweise.
        m.tick();
        assert_eq!(m.rate(), 1600);
    }

    #[test]
    fn ring_buffers_are_capped_and_newest_first() {
        let mut s = AppState::new();
        for i in 0..(ORDERS_CAP + 10) {
            s.push_order(json!({"order_id": i}));
        }
        assert_eq!(s.orders.len(), ORDERS_CAP);
        let newest = s.orders(3);
        assert_eq!(newest[0]["order_id"], json!(ORDERS_CAP + 9));
        assert_eq!(newest[2]["order_id"], json!(ORDERS_CAP + 7));
    }

    #[test]
    fn factory_state_event_updates_flag_before_first_reading() {
        let mut s = AppState::new();
        assert!(!s.factory_running());
        s.push_event(json!({"kind": "factory_state", "detail": {"running": true}}));
        assert!(s.factory_running());
        s.push_event(json!({"kind": "factory_state", "detail": {"running": false}}));
        assert!(!s.factory_running());
    }

    // Sobald Readings da sind, entscheiden sie — sonst meldet ein nach dem
    // Simulator gestarteter Core dauerhaft "gestoppt" (live beobachtet).
    #[test]
    fn readings_beat_a_missed_factory_state_event() {
        let mut s = AppState::new();
        s.update_machine(1, "OK", 62.0, 5.2, 2.2, 1.0, 1);
        assert!(s.factory_running(), "laufende Maschine => Fabrik läuft");

        s.update_machine(1, "OFFLINE", 62.0, 5.2, 2.2, 0.0, 2);
        assert!(!s.factory_running(), "alle Maschinen OFFLINE => Fabrik steht");

        s.update_machine(2, "THROTTLED", 70.0, 5.2, 4.0, 0.5, 3);
        assert!(s.factory_running(), "eine gedrosselte Maschine läuft ebenfalls");
    }

    #[test]
    fn anomaly_score_merges_into_snapshot() {
        let mut s = AppState::new();
        s.update_machine(3, "OK", 62.0, 5.2, 2.2, 1.0, 1_723_111_111_000_000_000);
        assert!(s.machines()[0].anomaly_score.is_none());
        s.set_anomaly_score(3, -0.62);
        assert_eq!(s.machines()[0].anomaly_score, Some(-0.62));
    }

    #[test]
    fn running_ids_only_include_ok_machines() {
        let mut s = AppState::new();
        s.update_machine(1, "OK", 62.0, 5.2, 2.2, 1.0, 1);
        s.update_machine(2, "ERROR", 90.0, 5.2, 9.0, 0.0, 1);
        s.update_machine(3, "THROTTLED", 70.0, 5.2, 4.0, 0.5, 1);
        assert_eq!(s.running_machine_ids(), vec![1]);
    }
}
