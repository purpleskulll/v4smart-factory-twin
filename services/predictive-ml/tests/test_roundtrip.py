"""Roundtrip-Test des Hot-Path-Kontrakts (CLAUDE.md §7) für die Python-Seite.

Sichert ab, dass flatc 24.3.25 und die Python-Runtime zusammenpassen: encode ->
file_identifier -> decode -> Feldvergleich. predictive-ml liest `sensor_clean`
mit genau diesem Decoder.
"""

import flatbuffers
import pytest

from app.gen.telemetry import SensorReading as sr_mod
from app.gen.telemetry.MachineStatus import MachineStatus

# Werte aus dem Normalbetrieb (CLAUDE.md §13).
TS_NS = 1723111111000000000
MACHINE_ID = 3
SEQ = 4711
TEMP = 62.1
PRESS = 5.2
VIB = 2.2
SPEED = 1.0


def build(status=MachineStatus.THROTTLED) -> bytes:
    b = flatbuffers.Builder(128)
    sr_mod.SensorReadingStart(b)
    sr_mod.SensorReadingAddTsNs(b, TS_NS)
    sr_mod.SensorReadingAddMachineId(b, MACHINE_ID)
    sr_mod.SensorReadingAddSeq(b, SEQ)
    sr_mod.SensorReadingAddTemperatureC(b, TEMP)
    sr_mod.SensorReadingAddPressureBar(b, PRESS)
    sr_mod.SensorReadingAddVibrationMms(b, VIB)
    sr_mod.SensorReadingAddSpeedFactor(b, SPEED)
    sr_mod.SensorReadingAddStatus(b, status)
    off = sr_mod.SensorReadingEnd(b)
    # file_identifier explizit — die Python-Runtime schreibt ihn (anders als Go/Rust)
    # nicht automatisch aus dem Schema, sondern nur auf Anforderung.
    b.Finish(off, file_identifier=b"SNR1")
    return bytes(b.Output())


def test_roundtrip_sensor_reading():
    buf = build()

    # file_identifier "SNR1" in den Bytes 4..8 (§7).
    assert buf[4:8] == b"SNR1"
    assert sr_mod.SensorReading.SensorReadingBufferHasIdentifier(buf, 0)

    sr = sr_mod.SensorReading.GetRootAs(buf, 0)
    assert sr.TsNs() == TS_NS
    assert sr.MachineId() == MACHINE_ID
    assert sr.Seq() == SEQ
    # float32-Rundung: der Buffer speichert single precision.
    assert sr.TemperatureC() == pytest.approx(TEMP, rel=1e-6)
    assert sr.PressureBar() == pytest.approx(PRESS, rel=1e-6)
    assert sr.VibrationMms() == pytest.approx(VIB, rel=1e-6)
    assert sr.SpeedFactor() == pytest.approx(SPEED, rel=1e-6)
    assert sr.Status() == MachineStatus.THROTTLED


@pytest.mark.parametrize(
    "status",
    [MachineStatus.OK, MachineStatus.THROTTLED, MachineStatus.ERROR, MachineStatus.OFFLINE],
)
def test_roundtrip_all_status_values(status):
    sr = sr_mod.SensorReading.GetRootAs(build(status), 0)
    assert sr.Status() == status


def test_json_payload_has_no_identifier():
    """Fremdformat auf dem Hot Path muss auffallen (Hard Rule §4.1)."""
    json_bytes = b'{"machine_id":3,"temperature_c":62.1}'
    assert not sr_mod.SensorReading.SensorReadingBufferHasIdentifier(json_bytes, 0)
