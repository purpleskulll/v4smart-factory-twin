// Roundtrip-Test für den Hot-Path-Kontrakt (CLAUDE.md §7).
// Sichert die Schema-/Runtime-Achse ab: encode -> file_identifier -> decode ->
// Feldvergleich. Schlägt dieser Test fehl, passen flatc und die Go-Runtime
// nicht zusammen (beide MUSS 24.3.25 sein, §5).
package gen

import (
	"testing"

	flatbuffers "github.com/google/flatbuffers/go"

	"v4smart/factory-simulator/internal/gen/telemetry"
)

// Werte aus dem Normalbetrieb (CLAUDE.md §13).
const (
	wantTsNs  int64   = 1723111111000000000
	wantMID   uint16  = 3
	wantSeq   uint64  = 4711
	wantTemp  float32 = 62.1
	wantPress float32 = 5.2
	wantVib   float32 = 2.2
	wantSpeed float32 = 1.0
)

func buildReading(t *testing.T, status telemetry.MachineStatus) []byte {
	t.Helper()
	b := flatbuffers.NewBuilder(128)
	telemetry.SensorReadingStart(b)
	telemetry.SensorReadingAddTsNs(b, wantTsNs)
	telemetry.SensorReadingAddMachineId(b, wantMID)
	telemetry.SensorReadingAddSeq(b, wantSeq)
	telemetry.SensorReadingAddTemperatureC(b, wantTemp)
	telemetry.SensorReadingAddPressureBar(b, wantPress)
	telemetry.SensorReadingAddVibrationMms(b, wantVib)
	telemetry.SensorReadingAddSpeedFactor(b, wantSpeed)
	telemetry.SensorReadingAddStatus(b, status)
	off := telemetry.SensorReadingEnd(b)
	telemetry.FinishSensorReadingBuffer(b, off)
	return b.FinishedBytes()
}

func TestSensorReadingRoundtrip(t *testing.T) {
	buf := buildReading(t, telemetry.MachineStatusTHROTTLED)

	// file_identifier "SNR1" steht in den Bytes 4..8 (§7 / smoke_sim.sh prüft
	// exakt diese Position auf dem echten Kafka-Payload).
	if got := string(buf[4:8]); got != "SNR1" {
		t.Fatalf("file_identifier: want SNR1, got %q", got)
	}
	if !telemetry.SensorReadingBufferHasIdentifier(buf) {
		t.Fatal("SensorReadingBufferHasIdentifier() = false")
	}

	sr := telemetry.GetRootAsSensorReading(buf, 0)
	if sr.TsNs() != wantTsNs {
		t.Errorf("ts_ns: want %d, got %d", wantTsNs, sr.TsNs())
	}
	if sr.MachineId() != wantMID {
		t.Errorf("machine_id: want %d, got %d", wantMID, sr.MachineId())
	}
	if sr.Seq() != wantSeq {
		t.Errorf("seq: want %d, got %d", wantSeq, sr.Seq())
	}
	if sr.TemperatureC() != wantTemp {
		t.Errorf("temperature_c: want %v, got %v", wantTemp, sr.TemperatureC())
	}
	if sr.PressureBar() != wantPress {
		t.Errorf("pressure_bar: want %v, got %v", wantPress, sr.PressureBar())
	}
	if sr.VibrationMms() != wantVib {
		t.Errorf("vibration_mms: want %v, got %v", wantVib, sr.VibrationMms())
	}
	if sr.SpeedFactor() != wantSpeed {
		t.Errorf("speed_factor: want %v, got %v", wantSpeed, sr.SpeedFactor())
	}
	if sr.Status() != telemetry.MachineStatusTHROTTLED {
		t.Errorf("status: want THROTTLED, got %v", sr.Status())
	}
}

// Alle vier Status-Werte müssen verlustfrei durch den Buffer gehen — der
// Middleware-Core leitet den Maschinen-Status aus genau diesem Feld ab (§10).
func TestMachineStatusRoundtrip(t *testing.T) {
	for _, st := range []telemetry.MachineStatus{
		telemetry.MachineStatusOK,
		telemetry.MachineStatusTHROTTLED,
		telemetry.MachineStatusERROR,
		telemetry.MachineStatusOFFLINE,
	} {
		buf := buildReading(t, st)
		if got := telemetry.GetRootAsSensorReading(buf, 0).Status(); got != st {
			t.Errorf("status %v: roundtrip ergab %v", st, got)
		}
	}
}
