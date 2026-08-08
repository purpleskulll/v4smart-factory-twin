package machine

import (
	"math"
	"testing"

	"v4smart/factory-simulator/internal/gen/telemetry"
)

const dt = 0.02 // 20-ms-Tick wie im Produktivbetrieb (§13)

// quietConfig ist die Produktiv-Physik ohne Rauschen — damit sind die
// Zeitschranken der Healing-Kette exakt prüfbar statt statistisch.
func quietConfig() Config {
	c := DefaultConfig()
	c.TempSigma, c.PressSigma, c.VibSigma = 0, 0, 0
	return c
}

// advance spult die Simulationszeit in 20-ms-Schritten vor (kein Echtzeit-Sleep).
func advance(m *Machine, seconds float64) {
	for t := 0.0; t < seconds; t += dt {
		m.Step(dt)
	}
}

// Vibration eilt der Temperatur voraus: genau dieses Fenster nutzt das ML (§13/§14).
func TestRampVibrationLeadsTemperature(t *testing.T) {
	m := New(1, quietConfig(), 42)
	m.Inject()

	var vibAt25, tempAt20 float64
	for step := 1; step <= int(25/dt); step++ {
		m.Step(dt)
		switch step {
		case int(20 / dt):
			tempAt20 = m.Temp
		case int(25 / dt):
			vibAt25 = m.Vib()
		}
	}

	if vibAt25 < 4.5 {
		t.Errorf("vib nach 25 s: want >= 4.5, got %.2f", vibAt25)
	}
	if tempAt20 >= 75 {
		t.Errorf("temp nach 20 s: want < 75 (Vibration muss vorauseilen), got %.2f", tempAt20)
	}
	if m.Status() != telemetry.MachineStatusOK {
		t.Errorf("Status nach 25 s Rampe: want OK (noch keine Übertemperatur), got %v", m.Status())
	}
}

// Drosselung heilt: der Fehleranteil zerfällt mit τ≈8 s (§13).
func TestThrottleHealsVibration(t *testing.T) {
	m := New(2, quietConfig(), 42)
	m.Inject()
	advance(m, 25) // Rampe hochlaufen lassen
	if m.Vib() < 4.5 {
		t.Fatalf("Vorbedingung: vib sollte >= 4.5 sein, ist %.2f", m.Vib())
	}

	m.Throttle(0.5, 120)

	healedAfter := -1.0
	for step := 1; step <= int(30/dt); step++ {
		m.Step(dt)
		if m.Vib() < 3.0 {
			healedAfter = float64(step) * dt
			break
		}
	}
	if healedAfter < 0 {
		t.Fatalf("vib fiel in 30 s nicht unter 3.0 (ist %.2f)", m.Vib())
	}
	t.Logf("vib < 3.0 nach %.1f s Drosselung", healedAfter)

	if m.Status() != telemetry.MachineStatusTHROTTLED {
		t.Errorf("Status während TTL: want THROTTLED, got %v", m.Status())
	}
	if m.SpeedFactor != 0.5 {
		t.Errorf("speed_factor während Drosselung: want 0.5, got %.2f", m.SpeedFactor)
	}
}

// Ohne Heilung läuft die Maschine in Übertemperatur -> ERROR (§13).
func TestUnhealedRampReachesError(t *testing.T) {
	m := New(3, quietConfig(), 42)
	m.Inject()

	errorAfter := -1.0
	maxTemp := 0.0
	for step := 1; step <= int(300/dt); step++ {
		m.Step(dt)
		maxTemp = math.Max(maxTemp, m.Temp)
		if m.Status() == telemetry.MachineStatusERROR {
			errorAfter = float64(step) * dt
			break
		}
	}
	if errorAfter < 0 {
		t.Fatalf("kein ERROR in 300 s (max Temp %.1f)", maxTemp)
	}
	t.Logf("ERROR nach %.0f s, max Temp %.1f °C", errorAfter, maxTemp)

	// §13: 85 °C werden nach ~60–90 s erreicht. Die Grenzen sind bewusst weit,
	// aber sie fangen eine grob verstellte Physik ab (zu schnell = das ML hat
	// kein Erkennungsfenster, zu langsam = der Healing-Test läuft in den Timeout).
	if errorAfter < 40 || errorAfter > 150 {
		t.Errorf("ERROR nach %.0f s — erwartet 40..150 s (§13: ~60-90 s)", errorAfter)
	}
	if m.SpeedFactor != 0 {
		t.Errorf("ERROR: Maschine muss stehen (speed 0), got %.2f", m.SpeedFactor)
	}
}

// Nach ErrorHoldS stellt sich die Maschine selbst wieder auf OK (§13).
func TestErrorAutoResets(t *testing.T) {
	m := New(4, quietConfig(), 42)
	m.Inject()
	for m.Status() != telemetry.MachineStatusERROR {
		m.Step(dt)
	}
	advance(m, m.cfg.ErrorHoldS+1)
	if m.Status() != telemetry.MachineStatusOK {
		t.Errorf("Auto-Reset nach %.0f s: want OK, got %v", m.cfg.ErrorHoldS, m.Status())
	}
	if m.Injected {
		t.Error("Auto-Reset muss die Injektion beenden")
	}
	if m.SpeedFactor != 1.0 {
		t.Errorf("nach Auto-Reset: speed_factor want 1.0, got %.2f", m.SpeedFactor)
	}
}

// Rechtzeitige Drosselung verhindert die Übertemperatur — das ist die
// Kernaussage der Self-Healing-Kette (§4.3).
func TestThrottleInTimePreventsError(t *testing.T) {
	m := New(5, quietConfig(), 42)
	m.Inject()
	advance(m, 30) // so spät, wie das ML realistisch reagiert
	m.Throttle(0.5, 120)

	maxTemp := m.Temp
	for step := 1; step <= int(240/dt); step++ {
		m.Step(dt)
		maxTemp = math.Max(maxTemp, m.Temp)
		if m.Status() == telemetry.MachineStatusERROR {
			t.Fatalf("trotz Drosselung in ERROR gelaufen (max Temp %.1f)", maxTemp)
		}
	}
	t.Logf("max Temp mit Drosselung: %.1f °C", maxTemp)
	if maxTemp >= 85 {
		t.Errorf("max Temp %.1f — muss unter 85 °C bleiben", maxTemp)
	}
	if m.Status() != telemetry.MachineStatusOK {
		t.Errorf("nach TTL-Ablauf: want OK, got %v", m.Status())
	}
}

// Fabrik-Stopp: OFFLINE, Maschine steht; Start stellt den Normalbetrieb her (§13).
func TestFactoryStopAndStart(t *testing.T) {
	m := New(6, quietConfig(), 42)
	m.Inject()
	advance(m, 10)

	m.SetOffline(true)
	m.Step(dt)
	if m.Status() != telemetry.MachineStatusOFFLINE {
		t.Errorf("nach Stopp: want OFFLINE, got %v", m.Status())
	}
	if m.SpeedFactor != 0 {
		t.Errorf("OFFLINE: speed_factor want 0, got %.2f", m.SpeedFactor)
	}

	m.SetOffline(false)
	m.Step(dt)
	if m.Status() != telemetry.MachineStatusOK {
		t.Errorf("nach Start: want OK, got %v", m.Status())
	}
	if m.ErrOffset != 0 {
		t.Errorf("Start muss den Fehleranteil löschen, got %.2f", m.ErrOffset)
	}
}

// Die Kopplungsformel selbst (§13) — Referenzwerte von Hand nachgerechnet.
func TestTempTargetCoupling(t *testing.T) {
	cfg := DefaultConfig()
	if got := TempTarget(cfg, 2.2); got != 62.0 {
		t.Errorf("bei vib=2.2 (Mittelwert) muss das Ziel 62 sein, got %.2f", got)
	}
	if got := TempTarget(cfg, 1.0); got != 62.0 {
		t.Errorf("unterhalb des Mittelwerts kein Aufheizen, got %.2f", got)
	}
	// vib=5.2 -> excess 3.0 -> 62 + 2.2*3^1.6 = 62 + 2.2*5.7995 ≈ 74.76
	if got := TempTarget(cfg, 5.2); math.Abs(got-74.76) > 0.1 {
		t.Errorf("bei vib=5.2: want ≈74.76, got %.2f", got)
	}
}

// Mit realem Rauschen darf die Kette nicht kippen (Regression gegen zu große Sigmas).
func TestNoisyRunStaysHealthyWithoutInjection(t *testing.T) {
	m := New(7, DefaultConfig(), 7)
	maxVib, maxTemp := 0.0, 0.0
	for step := 1; step <= int(600/dt); step++ {
		m.Step(dt)
		maxVib = math.Max(maxVib, m.Vib())
		maxTemp = math.Max(maxTemp, m.Temp)
		if m.Status() != telemetry.MachineStatusOK {
			t.Fatalf("Normalbetrieb ohne Injektion wurde %v (t=%.0fs)", m.Status(), float64(step)*dt)
		}
	}
	t.Logf("10 min Normalbetrieb: max vib %.2f, max temp %.1f", maxVib, maxTemp)
	// ML_VIB_GUARD liegt bei 4.5 — Normalbetrieb muss klar darunter bleiben,
	// sonst feuert das deterministische Sicherheitsnetz auf gesunden Maschinen.
	if maxVib >= 4.0 {
		t.Errorf("Normalbetrieb erreicht vib %.2f — zu nah am Guard (4.5)", maxVib)
	}
}
