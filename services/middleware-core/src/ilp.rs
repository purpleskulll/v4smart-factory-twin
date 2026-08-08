//! QuestDB-Writer über das Line Protocol (TCP, CLAUDE.md §12).
//!
//! Hard Rule: Ein Ausfall der Datenbank darf den Hot Path NIE stoppen. Der
//! Writer läuft als eigener Task hinter einem begrenzten Kanal; ist der Kanal
//! voll oder die Verbindung weg, werden Zeilen verworfen und `db_dropped`
//! hochgezählt — niemals gewartet.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;
use tokio::sync::mpsc;

use crate::state::Counters;

pub const CHANNEL_CAP: usize = 10_000;
const BACKOFF_MAX: Duration = Duration::from_secs(5);
const FLUSH_EVERY: Duration = Duration::from_millis(500);

/// Sender-Seite: nicht blockierend, verwirft bei vollem Puffer.
#[derive(Clone)]
pub struct IlpSender {
    tx: mpsc::Sender<String>,
    counters: Arc<Counters>,
}

impl IlpSender {
    pub fn send(&self, line: String) {
        match self.tx.try_send(line) {
            Ok(()) => {}
            Err(_) => {
                self.counters.db_dropped.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

/// Startet den Writer-Task und liefert den Sender.
pub fn spawn(addr: String, counters: Arc<Counters>) -> IlpSender {
    let (tx, rx) = mpsc::channel::<String>(CHANNEL_CAP);
    let sender = IlpSender { tx, counters: counters.clone() };
    tokio::spawn(writer_loop(addr, rx, counters));
    sender
}

async fn writer_loop(addr: String, mut rx: mpsc::Receiver<String>, counters: Arc<Counters>) {
    let mut backoff = Duration::from_millis(200);

    loop {
        let mut stream = match TcpStream::connect(&addr).await {
            Ok(s) => {
                tracing::info!(addr = %addr, "QuestDB-ILP verbunden");
                backoff = Duration::from_millis(200);
                s
            }
            Err(e) => {
                tracing::warn!(addr = %addr, err = %e, backoff_ms = backoff.as_millis(), "QuestDB nicht erreichbar");
                // Während der Trennung anfallende Zeilen verwerfen, damit der
                // Kanal nicht zuläuft und den Hot Path ausbremst.
                drain(&mut rx, &counters);
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(BACKOFF_MAX);
                continue;
            }
        };

        let mut batch = String::with_capacity(16 * 1024);
        let mut flush_timer = tokio::time::interval(FLUSH_EVERY);
        flush_timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

        loop {
            tokio::select! {
                maybe_line = rx.recv() => {
                    match maybe_line {
                        Some(line) => {
                            batch.push_str(&line);
                            if batch.len() >= 8 * 1024 {
                                if !write_batch(&mut stream, &mut batch, &counters).await { break; }
                            }
                        }
                        None => return, // Kanal geschlossen: Prozess fährt herunter
                    }
                }
                _ = flush_timer.tick() => {
                    if !batch.is_empty() && !write_batch(&mut stream, &mut batch, &counters).await { break; }
                }
            }
        }
    }
}

/// Prüft, ob die Gegenstelle die Verbindung geschlossen hat.
///
/// Nötig, weil `write_all` auf einen toten ILP-Socket weiterhin Ok liefert: der
/// Kernel nimmt die Bytes in den Sendepuffer, auch wenn QuestDB längst weg ist.
/// Ohne diese Prüfung meldete `/api/stats` während eines DB-Ausfalls munter
/// `db_rows_s: 8`, während die Zeilen in Wahrheit verloren gingen (live
/// gemessen: ~200 fehlende Zeilen bei unverändertem Zähler). Ein Ausfall, den
/// die Kennzahlen nicht zeigen, ist schlimmer als der Ausfall selbst.
fn peer_gone(stream: &TcpStream) -> bool {
    let mut probe = [0u8; 1];
    match stream.try_read(&mut probe) {
        Ok(0) => true,                                              // FIN empfangen
        Ok(_) => false,                                             // Daten (unerwartet) – lebt
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => false, // Normalfall
        Err(_) => true,
    }
}

/// Schreibt den Batch; `false` bedeutet Verbindungsverlust (neu verbinden).
async fn write_batch(stream: &mut TcpStream, batch: &mut String, counters: &Arc<Counters>) -> bool {
    let lines = batch.lines().count() as u64;

    if peer_gone(stream) {
        tracing::warn!(lines, "QuestDB-Verbindung ist tot — Batch verworfen, verbinde neu");
        counters.db_dropped.fetch_add(lines, Ordering::Relaxed);
        batch.clear();
        return false;
    }

    match stream.write_all(batch.as_bytes()).await {
        Ok(()) => {
            batch.clear();
            counters.db_rows.fetch_add(lines, Ordering::Relaxed);
            true
        }
        Err(e) => {
            tracing::warn!(err = %e, lines, "ILP-Schreibfehler, verwerfe Batch und verbinde neu");
            counters.db_dropped.fetch_add(lines, Ordering::Relaxed);
            batch.clear();
            false
        }
    }
}

fn drain(rx: &mut mpsc::Receiver<String>, counters: &Arc<Counters>) {
    let mut n = 0u64;
    while rx.try_recv().is_ok() {
        n += 1;
    }
    if n > 0 {
        counters.db_dropped.fetch_add(n, Ordering::Relaxed);
    }
}

/// ILP-Zeile für einen MES-Auftrag (§12).
pub fn order_line(machine: u16, product: &str, status: &str, order_id: &str, qty: i64, progress: f64, ts_ns: i64) -> String {
    format!(
        "mes_orders,machine={machine},product={product},status={status} order_id=\"{order_id}\",qty={qty}i,progress={progress:.4} {ts_ns}\n"
    )
}

/// ILP-Zeile für ein ML-Ereignis (§12).
pub fn ml_event_line(machine: u16, kind: &str, score: f64, action: &str, ts_ns: i64) -> String {
    format!("ml_events,machine={machine},kind={kind} score={score:.4},action=\"{action}\" {ts_ns}\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn order_line_matches_contract() {
        let line = order_line(4, "SKU-A", "RUNNING", "PO-000123", 250, 0.44, 1_723_111_111_000_000_000);
        assert_eq!(
            line,
            "mes_orders,machine=4,product=SKU-A,status=RUNNING order_id=\"PO-000123\",qty=250i,progress=0.4400 1723111111000000000\n"
        );
    }

    #[test]
    fn ml_event_line_matches_contract() {
        let line = ml_event_line(3, "anomaly_detected", -0.62, "throttle", 1_723_111_111_000_000_000);
        assert_eq!(
            line,
            "ml_events,machine=3,kind=anomaly_detected score=-0.6200,action=\"throttle\" 1723111111000000000\n"
        );
    }
}
