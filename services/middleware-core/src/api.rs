//! REST- und WebSocket-API (CLAUDE.md §10). Der Kontrakt ist verbindlich:
//! `scripts/test_healing.sh` und das Dashboard bauen exakt darauf.

use std::sync::atomic::Ordering;

use axum::extract::ws::{Message as WsMessage, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use rand::seq::SliceRandom;
use rdkafka::producer::FutureRecord;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::consume::{Shared, TOPIC_CONTROL};

#[derive(Debug, Deserialize)]
pub struct LimitQuery {
    limit: Option<usize>,
}

pub fn router(sh: Shared) -> Router {
    Router::new()
        .route("/api/health", get(health))
        .route("/api/machines", get(machines))
        .route("/api/orders", get(orders))
        .route("/api/events", get(events))
        .route("/api/stats", get(stats))
        .route("/api/control", post(control))
        .route("/ws", get(ws_upgrade))
        .with_state(sh)
}

async fn health(State(sh): State<Shared>) -> Json<Value> {
    Json(json!({"ok": true, "uptime_s": sh.started.elapsed().as_secs()}))
}

async fn machines(State(sh): State<Shared>) -> Json<Value> {
    let list = sh.state.lock().map(|s| s.machines()).unwrap_or_default();
    Json(json!(list))
}

async fn orders(State(sh): State<Shared>, Query(q): Query<LimitQuery>) -> Json<Value> {
    let limit = q.limit.unwrap_or(50).min(crate::state::ORDERS_CAP);
    let list = sh.state.lock().map(|s| s.orders(limit)).unwrap_or_default();
    Json(json!(list))
}

async fn events(State(sh): State<Shared>, Query(q): Query<LimitQuery>) -> Json<Value> {
    let limit = q.limit.unwrap_or(100).min(crate::state::EVENTS_CAP);
    let list = sh.state.lock().map(|s| s.events(limit)).unwrap_or_default();
    Json(json!(list))
}

async fn stats(State(sh): State<Shared>) -> Json<Value> {
    let ws = sh.counters.ws_clients.load(Ordering::Relaxed);
    let db_dropped = sh.counters.db_dropped.load(Ordering::Relaxed);
    let s = sh.state.lock().map(|s| s.stats(ws, db_dropped)).ok();
    Json(json!(s))
}

/// POST /api/control — Body wie §8, aber ohne ts/source (setzt der Core).
async fn control(State(sh): State<Shared>, Json(body): Json<Value>) -> impl IntoResponse {
    let Some(kind) = body.get("type").and_then(Value::as_str).map(str::to_string) else {
        return (StatusCode::BAD_REQUEST, Json(json!({"ok": false, "error": "type fehlt"})));
    };

    let mut msg = body.clone();
    let mut chosen: Option<u16> = body.get("machine_id").and_then(Value::as_u64).map(|v| v as u16);

    match kind.as_str() {
        "throttle" | "reset" => {
            if chosen.is_none() {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"ok": false, "error": format!("{kind} verlangt machine_id")})),
                );
            }
        }
        "inject_error" => {
            if chosen.is_none() {
                // §10: ohne machine_id wählt der Core eine zufällige laufende
                // Maschine und gibt sie in der Antwort zurück.
                let candidates = sh.state.lock().map(|s| s.running_machine_ids()).unwrap_or_default();
                let Some(id) = candidates.choose(&mut rand::thread_rng()).copied() else {
                    return (
                        StatusCode::CONFLICT,
                        Json(json!({"ok": false, "error": "keine laufende Maschine verfügbar"})),
                    );
                };
                chosen = Some(id);
                msg["machine_id"] = json!(id);
            }
        }
        "factory" => {
            let action = body.get("action").and_then(Value::as_str).unwrap_or_default();
            if action != "start" && action != "stop" {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"ok": false, "error": "factory verlangt action start|stop"})),
                );
            }
        }
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": format!("unbekannter type: {other}")})),
            );
        }
    }

    msg["v"] = json!(1);
    msg["ts"] = json!(chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true));
    msg["source"] = json!("dashboard");

    let key = chosen.map(|id| id.to_string()).unwrap_or_else(|| "factory".to_string());
    let payload = msg.to_string();
    let record: FutureRecord<String, String> = FutureRecord::to(TOPIC_CONTROL).key(&key).payload(&payload);
    if let Err((e, _)) = sh.producer.send_result(record) {
        tracing::error!(err = %e, "Kommando ließ sich nicht publizieren");
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ok": false, "error": "Kafka nicht erreichbar"})),
        );
    }
    tracing::info!(kind = %kind, machine = ?chosen, "Kommando publiziert");

    match chosen {
        Some(id) if kind == "inject_error" => (StatusCode::OK, Json(json!({"ok": true, "machine_id": id}))),
        _ => (StatusCode::OK, Json(json!({"ok": true}))),
    }
}

async fn ws_upgrade(ws: WebSocketUpgrade, State(sh): State<Shared>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, sh))
}

async fn handle_socket(mut socket: WebSocket, sh: Shared) {
    sh.counters.ws_clients.fetch_add(1, Ordering::Relaxed);
    let mut rx = sh.frames.subscribe();

    // Ein Snapshot direkt nach dem Connect (§10).
    let snapshot = {
        let ws_clients = sh.counters.ws_clients.load(Ordering::Relaxed);
        let db_dropped = sh.counters.db_dropped.load(Ordering::Relaxed);
        match sh.state.lock() {
            Ok(s) => json!({
                "t": "snapshot",
                "machines": s.machines(),
                "orders": s.orders(50),
                "events": s.events(100),
                "stats": s.stats(ws_clients, db_dropped),
            }),
            Err(_) => json!({"t": "snapshot", "machines": [], "orders": [], "events": [], "stats": null}),
        }
    };
    if socket.send(WsMessage::Text(snapshot.to_string())).await.is_err() {
        sh.counters.ws_clients.fetch_sub(1, Ordering::Relaxed);
        return;
    }

    loop {
        tokio::select! {
            frame = rx.recv() => match frame {
                Ok(text) => {
                    if socket.send(WsMessage::Text(text)).await.is_err() {
                        break;
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    // §10: langsame Clients werden getrennt — niemals Backpressure
                    // auf den Hot Path.
                    tracing::warn!(missed = n, "WS-Client zu langsam, trenne Verbindung");
                    break;
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            },
            incoming = socket.recv() => match incoming {
                // Client->Server-Nachrichten gibt es im Kontrakt nicht (§10);
                // wir lesen nur, um Close/Fehler zu bemerken.
                None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            }
        }
    }

    sh.counters.ws_clients.fetch_sub(1, Ordering::Relaxed);
}
