//! Plausibilitätsprüfung des Hot Path (Prompt 03, Punkt validate.rs).
//! Verworfene Readings zählen als `dropped` — sie dürfen weder in die
//! Aggregate noch in den Downsample-Strom gelangen.

use crate::gen::telemetry::SensorReading;

/// Gültigkeitsbereiche laut Prompt 03.
pub const TEMP_MIN: f32 = -50.0;
pub const TEMP_MAX: f32 = 200.0;
pub const PRESS_MIN: f32 = 0.0;
pub const PRESS_MAX: f32 = 20.0;
pub const VIB_MIN: f32 = 0.0;
pub const VIB_MAX: f32 = 50.0;
/// Zeitstempel dürfen maximal 10 Minuten von der eigenen Uhr abweichen.
pub const TS_TOLERANCE_NS: i64 = 10 * 60 * 1_000_000_000;

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Reject {
    Temperature,
    Pressure,
    Vibration,
    Timestamp,
    MachineId,
}

/// Prüft ein Reading gegen die Bereiche. `now_ns` ist die eigene Uhr.
pub fn check(r: &SensorReading, now_ns: i64, machine_count: u16) -> Result<(), Reject> {
    if r.machine_id() == 0 || r.machine_id() > machine_count {
        return Err(Reject::MachineId);
    }
    let t = r.temperature_c();
    if !t.is_finite() || !(TEMP_MIN..=TEMP_MAX).contains(&t) {
        return Err(Reject::Temperature);
    }
    let p = r.pressure_bar();
    if !p.is_finite() || !(PRESS_MIN..=PRESS_MAX).contains(&p) {
        return Err(Reject::Pressure);
    }
    let v = r.vibration_mms();
    if !v.is_finite() || !(VIB_MIN..=VIB_MAX).contains(&v) {
        return Err(Reject::Vibration);
    }
    if (r.ts_ns() - now_ns).abs() > TS_TOLERANCE_NS {
        return Err(Reject::Timestamp);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gen::telemetry::{
        finish_sensor_reading_buffer, root_as_sensor_reading, MachineStatus, SensorReading,
        SensorReadingArgs,
    };

    const NOW: i64 = 1_723_111_111_000_000_000;

    fn buf(args: SensorReadingArgs) -> Vec<u8> {
        let mut fbb = flatbuffers::FlatBufferBuilder::with_capacity(128);
        let root = SensorReading::create(&mut fbb, &args);
        finish_sensor_reading_buffer(&mut fbb, root);
        fbb.finished_data().to_vec()
    }

    fn healthy() -> SensorReadingArgs {
        SensorReadingArgs {
            ts_ns: NOW,
            machine_id: 3,
            seq: 1,
            temperature_c: 62.1,
            pressure_bar: 5.2,
            vibration_mms: 2.2,
            speed_factor: 1.0,
            status: MachineStatus::OK,
        }
    }

    fn check_args(args: SensorReadingArgs) -> Result<(), Reject> {
        let b = buf(args);
        let r = root_as_sensor_reading(&b).unwrap();
        check(&r, NOW, 8)
    }

    #[test]
    fn accepts_normal_reading() {
        assert_eq!(check_args(healthy()), Ok(()));
    }

    #[test]
    fn rejects_out_of_range() {
        assert_eq!(check_args(SensorReadingArgs { temperature_c: 250.0, ..healthy() }), Err(Reject::Temperature));
        assert_eq!(check_args(SensorReadingArgs { temperature_c: -60.0, ..healthy() }), Err(Reject::Temperature));
        assert_eq!(check_args(SensorReadingArgs { pressure_bar: 25.0, ..healthy() }), Err(Reject::Pressure));
        assert_eq!(check_args(SensorReadingArgs { vibration_mms: 51.0, ..healthy() }), Err(Reject::Vibration));
        assert_eq!(check_args(SensorReadingArgs { vibration_mms: f32::NAN, ..healthy() }), Err(Reject::Vibration));
    }

    #[test]
    fn accepts_range_boundaries() {
        assert_eq!(check_args(SensorReadingArgs { temperature_c: TEMP_MAX, ..healthy() }), Ok(()));
        assert_eq!(check_args(SensorReadingArgs { temperature_c: TEMP_MIN, ..healthy() }), Ok(()));
        assert_eq!(check_args(SensorReadingArgs { vibration_mms: VIB_MIN, ..healthy() }), Ok(()));
    }

    #[test]
    fn rejects_implausible_timestamp() {
        let one_hour = 3_600 * 1_000_000_000_i64;
        assert_eq!(check_args(SensorReadingArgs { ts_ns: NOW + one_hour, ..healthy() }), Err(Reject::Timestamp));
        assert_eq!(check_args(SensorReadingArgs { ts_ns: NOW - one_hour, ..healthy() }), Err(Reject::Timestamp));
    }

    #[test]
    fn rejects_unknown_machine() {
        assert_eq!(check_args(SensorReadingArgs { machine_id: 0, ..healthy() }), Err(Reject::MachineId));
        assert_eq!(check_args(SensorReadingArgs { machine_id: 99, ..healthy() }), Err(Reject::MachineId));
    }
}
