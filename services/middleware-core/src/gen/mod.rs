//! Generierter FlatBuffers-Code (aus `schemas/sensor_reading.fbs`, flatc 24.3.25).
//! Nicht von Hand ändern — `make codegen` erzeugt die Datei neu (CLAUDE.md §7).

#[allow(unused_imports, dead_code, clippy::all, non_snake_case)]
pub mod sensor_reading_generated;

// Kurzer Pfad für die Konsumenten ab Schritt 03 (consume.rs/forward.rs):
// `use crate::gen::telemetry::SensorReading;`
#[allow(unused_imports)]
pub use sensor_reading_generated::telemetry;

#[cfg(test)]
mod tests {
    use super::sensor_reading_generated::telemetry::{
        finish_sensor_reading_buffer, root_as_sensor_reading,
        sensor_reading_buffer_has_identifier, MachineStatus, SensorReading, SensorReadingArgs,
    };

    // Werte aus dem Normalbetrieb (CLAUDE.md §13).
    const TS_NS: i64 = 1_723_111_111_000_000_000;
    const MACHINE_ID: u16 = 3;
    const SEQ: u64 = 4711;

    fn build(status: MachineStatus) -> Vec<u8> {
        let mut fbb = flatbuffers::FlatBufferBuilder::with_capacity(128);
        let root = SensorReading::create(
            &mut fbb,
            &SensorReadingArgs {
                ts_ns: TS_NS,
                machine_id: MACHINE_ID,
                seq: SEQ,
                temperature_c: 62.1,
                pressure_bar: 5.2,
                vibration_mms: 2.2,
                speed_factor: 1.0,
                status,
            },
        );
        finish_sensor_reading_buffer(&mut fbb, root);
        fbb.finished_data().to_vec()
    }

    #[test]
    fn roundtrip_sensor_reading() {
        let buf = build(MachineStatus::THROTTLED);

        // file_identifier "SNR1" in den Bytes 4..8 (§7).
        assert_eq!(&buf[4..8], b"SNR1", "file_identifier fehlt/falsch");
        assert!(sensor_reading_buffer_has_identifier(&buf));

        let sr = root_as_sensor_reading(&buf).expect("gültiger FlatBuffer");
        assert_eq!(sr.ts_ns(), TS_NS);
        assert_eq!(sr.machine_id(), MACHINE_ID);
        assert_eq!(sr.seq(), SEQ);
        assert_eq!(sr.temperature_c(), 62.1_f32);
        assert_eq!(sr.pressure_bar(), 5.2_f32);
        assert_eq!(sr.vibration_mms(), 2.2_f32);
        assert_eq!(sr.speed_factor(), 1.0_f32);
        assert_eq!(sr.status(), MachineStatus::THROTTLED);
    }

    #[test]
    fn roundtrip_all_status_values() {
        for st in [
            MachineStatus::OK,
            MachineStatus::THROTTLED,
            MachineStatus::ERROR,
            MachineStatus::OFFLINE,
        ] {
            let buf = build(st);
            let sr = root_as_sensor_reading(&buf).expect("gültiger FlatBuffer");
            assert_eq!(sr.status(), st, "Status überlebt den Roundtrip nicht");
        }
    }

    // Fremdformat auf dem Hot Path muss AUFFALLEN, nicht still durchrutschen
    // (Hard Rule §4.1: kein JSON auf sensor_raw/sensor_clean).
    #[test]
    fn json_payload_is_rejected() {
        let json = br#"{"machine_id":3,"temperature_c":62.1}"#;
        assert!(!sensor_reading_buffer_has_identifier(json));
        assert!(root_as_sensor_reading(json).is_err());
    }
}
