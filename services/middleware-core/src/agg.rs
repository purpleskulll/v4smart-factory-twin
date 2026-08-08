//! 1-Sekunden-Aggregate je Maschine (CLAUDE.md §12): pro Maschine und Sekunde
//! genau EINE Zeile in QuestDB.

use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub struct Bucket {
    pub machine: u16,
    pub second: i64, // Unix-Sekunde
    pub temp_sum: f64,
    pub temp_max: f32,
    pub press_sum: f64,
    pub vib_sum: f64,
    pub vib_max: f32,
    pub speed: f32,
    pub samples: u32,
}

impl Bucket {
    fn new(machine: u16, second: i64) -> Self {
        Self {
            machine,
            second,
            temp_sum: 0.0,
            temp_max: f32::MIN,
            press_sum: 0.0,
            vib_sum: 0.0,
            vib_max: f32::MIN,
            speed: 0.0,
            samples: 0,
        }
    }

    fn add(&mut self, temp: f32, press: f32, vib: f32, speed: f32) {
        self.temp_sum += temp as f64;
        self.press_sum += press as f64;
        self.vib_sum += vib as f64;
        self.temp_max = self.temp_max.max(temp);
        self.vib_max = self.vib_max.max(vib);
        self.speed = speed; // letzter Wert der Sekunde
        self.samples += 1;
    }

    pub fn temp_avg(&self) -> f64 { self.temp_sum / self.samples as f64 }
    pub fn press_avg(&self) -> f64 { self.press_sum / self.samples as f64 }
    pub fn vib_avg(&self) -> f64 { self.vib_sum / self.samples as f64 }

    /// ILP-Zeile im Format aus §12 (Timestamp in NANOSEKUNDEN als letztes Feld).
    pub fn to_ilp(&self) -> String {
        format!(
            "sensor_agg_1s,machine={} temp_avg={:.4},temp_max={:.4},press_avg={:.4},vib_avg={:.4},vib_max={:.4},speed={:.4},samples={}i {}\n",
            self.machine,
            self.temp_avg(),
            self.temp_max,
            self.press_avg(),
            self.vib_avg(),
            self.vib_max,
            self.speed,
            self.samples,
            self.second * 1_000_000_000
        )
    }
}

/// Aggregator über alle Maschinen. Wechselt eine Maschine in eine neue Sekunde,
/// gibt `add` den fertigen Bucket der Vorsekunde zurück.
#[derive(Default)]
pub struct Aggregator {
    open: HashMap<u16, Bucket>,
}

impl Aggregator {
    pub fn new() -> Self { Self::default() }

    pub fn add(&mut self, machine: u16, ts_ns: i64, temp: f32, press: f32, vib: f32, speed: f32) -> Option<Bucket> {
        let second = ts_ns.div_euclid(1_000_000_000);
        let mut finished = None;

        let bucket = self.open.entry(machine).or_insert_with(|| Bucket::new(machine, second));
        if bucket.second != second {
            // Späte Nachzügler der Vorsekunde verwerfen wir nicht, wir schließen
            // den alten Bucket ab und beginnen den neuen.
            let old = std::mem::replace(bucket, Bucket::new(machine, second));
            if old.samples > 0 {
                finished = Some(old);
            }
        }
        bucket.add(temp, press, vib, speed);
        finished
    }

    /// Schließt alle offenen Buckets, die älter als `now_second` sind (z. B.
    /// wenn eine Maschine ausfällt und keine neuen Readings mehr liefert).
    pub fn flush_older_than(&mut self, now_second: i64) -> Vec<Bucket> {
        let stale: Vec<u16> = self
            .open
            .iter()
            .filter(|(_, b)| b.second < now_second && b.samples > 0)
            .map(|(id, _)| *id)
            .collect();
        stale
            .into_iter()
            .filter_map(|id| self.open.remove(&id))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const S: i64 = 1_723_111_111;

    /// Vergleich mit der Genauigkeit, die f32-Eingaben hergeben.
    fn assert_close(got: f64, want: f64) {
        assert!((got - want).abs() < 1e-5, "want ≈{want}, got {got}");
    }

    #[test]
    fn aggregates_known_inputs_exactly() {
        let mut a = Aggregator::new();
        assert!(a.add(3, S * 1_000_000_000, 60.0, 5.0, 2.0, 1.0).is_none());
        assert!(a.add(3, S * 1_000_000_000 + 500_000_000, 64.0, 5.4, 3.0, 1.0).is_none());

        // Sekundenwechsel schließt den Bucket ab.
        let done = a.add(3, (S + 1) * 1_000_000_000, 62.0, 5.2, 2.2, 0.5).expect("Bucket fällig");
        assert_eq!(done.samples, 2);
        assert_eq!(done.temp_max, 64.0);
        assert_eq!(done.vib_max, 3.0);
        assert_eq!(done.second, S);
        // Mittelwerte mit Toleranz: die Eingaben sind f32 (5.4f32 ist als f64
        // 5.400000095…), Bit-Gleichheit wäre hier eine falsche Erwartung.
        assert_close(done.temp_avg(), 62.0);
        assert_close(done.press_avg(), 5.2);
        assert_close(done.vib_avg(), 2.5);
    }

    #[test]
    fn machines_are_independent() {
        let mut a = Aggregator::new();
        a.add(1, S * 1_000_000_000, 60.0, 5.0, 2.0, 1.0);
        a.add(2, S * 1_000_000_000, 70.0, 6.0, 3.0, 1.0);
        let done = a.add(1, (S + 1) * 1_000_000_000, 60.0, 5.0, 2.0, 1.0).unwrap();
        assert_eq!(done.machine, 1);
        assert_close(done.temp_avg(), 60.0);
    }

    // Das ILP-Format ist ein Vertrag (§12) — Abweichungen brechen den Historian.
    #[test]
    fn ilp_line_matches_contract() {
        let mut b = Bucket::new(3, S);
        b.add(62.1, 5.18, 2.21, 1.0);
        let line = b.to_ilp();
        assert!(line.starts_with("sensor_agg_1s,machine=3 "), "Zeile: {line}");
        assert!(line.contains("samples=1i"), "samples muss als long (i) geschrieben werden: {line}");
        assert!(line.ends_with(&format!(" {}\n", S * 1_000_000_000)), "Timestamp in ns am Ende: {line}");
        for field in ["temp_avg=", "temp_max=", "press_avg=", "vib_avg=", "vib_max=", "speed="] {
            assert!(line.contains(field), "Feld {field} fehlt: {line}");
        }
    }

    #[test]
    fn flush_closes_stale_buckets() {
        let mut a = Aggregator::new();
        a.add(5, S * 1_000_000_000, 62.0, 5.2, 2.2, 1.0);
        assert!(a.flush_older_than(S).is_empty(), "laufende Sekunde darf nicht geschlossen werden");
        let flushed = a.flush_older_than(S + 1);
        assert_eq!(flushed.len(), 1);
        assert_eq!(flushed[0].machine, 5);
    }
}
