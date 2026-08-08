//! middleware-core — Routing-Engine, Historian-Writer und API des Digital Twin.
//!
//! Datenfluss (CLAUDE.md §1): sensor_raw -> validieren -> 1s-Aggregate (QuestDB)
//! + Downsample (sensor_clean) + Live-Zustand -> REST/WS für das Dashboard.

mod agg;
mod api;
mod config;
mod consume;
mod gen;
mod ilp;
mod state;
mod validate;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::config::Config;
use crate::consume::Shared;
use crate::state::{AppState, Counters};

const LISTEN_ADDR: &str = "0.0.0.0:8080";
/// Kapazität des Broadcast-Kanals — zugleich die Grenze für langsame
/// WS-Clients (§10: Send-Queue > 256 Frames => trennen).
const FRAME_CHANNEL_CAP: usize = 256;

fn main() -> anyhow::Result<()> {
    if std::env::args().any(|a| a == "--selfcheck") {
        return match selfcheck() {
            Ok(()) => Ok(()),
            Err(e) => {
                eprintln!("selfcheck fehlgeschlagen: {e}");
                std::process::exit(1);
            }
        };
    }

    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(run())
}

async fn run() -> anyhow::Result<()> {
    let cfg = Config::from_env();
    tracing::info!(?cfg, "middleware-core startet");

    let counters = Arc::new(Counters::default());
    let state = Arc::new(Mutex::new(AppState::new()));
    let (frames, _) = tokio::sync::broadcast::channel::<String>(FRAME_CHANNEL_CAP);
    let ilp = ilp::spawn(cfg.questdb_ilp.clone(), counters.clone());
    let producer = consume::build_producer(&cfg.brokers)?;

    let sh = Shared {
        cfg: cfg.clone(),
        state,
        counters,
        frames,
        ilp,
        producer,
        started: std::time::Instant::now(),
    };

    // Jeder Kafka-Task startet sich selbst neu: ein Broker-Ausfall darf den
    // Service nicht beenden (§18 Kill-Test).
    spawn_resilient("hot-path", sh.clone(), consume::run_hot_path);
    spawn_resilient("json-plane", sh.clone(), consume::run_json_plane);
    tokio::spawn(consume::run_stats_ticker(sh.clone()));

    let listener = tokio::net::TcpListener::bind(LISTEN_ADDR).await?;
    tracing::info!(addr = LISTEN_ADDR, "API bereit");
    axum::serve(listener, api::router(sh))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
            tracing::info!("Shutdown angefordert");
        })
        .await?;
    Ok(())
}

fn spawn_resilient<F, Fut>(name: &'static str, sh: Shared, f: F)
where
    F: Fn(Shared) -> Fut + Send + 'static,
    Fut: std::future::Future<Output = anyhow::Result<()>> + Send + 'static,
{
    tokio::spawn(async move {
        loop {
            match f(sh.clone()).await {
                Ok(()) => tracing::warn!(task = name, "Task beendet, starte neu"),
                Err(e) => tracing::error!(task = name, err = %e, "Task abgebrochen, starte neu"),
            }
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
    });
}

/// §15: prüft den eigenen Health-Endpoint ohne Zusatzbibliothek.
fn selfcheck() -> anyhow::Result<()> {
    let mut stream = TcpStream::connect("127.0.0.1:8080")?;
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    stream.set_write_timeout(Some(Duration::from_secs(3)))?;
    stream.write_all(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n")?;
    let mut buf = [0u8; 64];
    let n = stream.read(&mut buf)?;
    let status = String::from_utf8_lossy(&buf[..n]).to_string();
    if status.len() < 12 || &status[9..12] != "200" {
        anyhow::bail!("healthz meldet: {status}");
    }
    Ok(())
}
