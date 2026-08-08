//! Konfiguration aus der Umgebung (Werte siehe workspace/.env.example).

#[derive(Debug, Clone)]
pub struct Config {
    pub brokers: String,
    pub questdb_ilp: String,
    pub machine_count: u16,
    pub downsample_hz: f64,
    pub ws_telemetry_hz: f64,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            brokers: env_str("KAFKA_BROKERS", "redpanda:9092"),
            questdb_ilp: env_str("QUESTDB_ILP_ADDR", "questdb:9009"),
            machine_count: env_num("MACHINE_COUNT", 8.0) as u16,
            downsample_hz: env_num("DOWNSAMPLE_HZ", 10.0),
            ws_telemetry_hz: env_num("WS_TELEMETRY_HZ", 5.0),
        }
    }
}

fn env_str(key: &str, default: &str) -> String {
    std::env::var(key).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

fn env_num(key: &str, default: f64) -> f64 {
    match std::env::var(key).ok().filter(|v| !v.is_empty()) {
        None => default,
        Some(v) => match v.parse::<f64>() {
            Ok(n) if n > 0.0 => n,
            _ => {
                tracing::warn!(key, value = %v, default, "ungültiger Zahlenwert, nutze Vorgabe");
                default
            }
        },
    }
}
