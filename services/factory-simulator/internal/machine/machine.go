// Package machine enthält die Physik und den Zustandsautomaten einer SCADA-
// Maschine. Alle Richtwerte stammen aus CLAUDE.md §13 und sind hier als Config
// gebündelt, damit die Unit-Tests die Simulationszeit schnell vorspulen können
// (Step(dt) ist eine reine Funktion auf dem Maschinen-Zustand — kein Sleep,
// keine Wanduhr).
package machine

import (
	"math"
	"math/rand"

	"v4smart/factory-simulator/internal/gen/telemetry"
)

// Config bündelt die Physik-Richtwerte aus CLAUDE.md §13.
type Config struct {
	TempMean   float64 // 62 °C
	TempSigma  float64 // ≈0.5 (stationäre Streuung des Messrauschens)
	PressMean  float64 // 5.2 bar
	PressSigma float64 // ≈0.15
	VibMean    float64 // 2.2 mm/s
	VibSigma   float64 // ≈0.25

	// Kopplung vib -> temp: temp_target = TempMean + TempGain*max(0,vib-VibMean)^TempExp,
	// Temperatur folgt dem Ziel mit der Zeitkonstante TempTau.
	TempGain float64 // 2.2
	TempExp  float64 // 1.6
	TempTau  float64 // 25 s

	// Ornstein-Uhlenbeck: Rückkehrrate zum Mittelwert.
	OUTheta float64 // 1/s

	// Fehlerprofil "vibration_ramp".
	ErrRampPerS  float64 // +0.12 mm/s pro Sekunde (ungedrosselt)
	ErrOffsetMax float64 // Deckel, sodass vib ~9 mm/s nicht überschreitet
	ErrDecayTau  float64 // 8 s — Zerfall des Fehler-Offsets bei Drosselung

	// Unterhalb dieses Rest-Offsets gilt der Fehler als abgebaut und die
	// Injektion endet. Ohne das käme der Fehler nach jedem TTL-Ablauf zurück:
	// die Maschine liefe trotz erfolgreicher Heilung später doch in ERROR und
	// §13 ("mit Heilung darf 85 °C NIE erreicht werden") wäre nicht haltbar.
	ErrClearedBelow float64

	ErrorTempC   float64 // 85 °C -> Status ERROR
	ErrorHoldS   float64 // 120 s bis Auto-Reset auf OK
	SpeedRampPerS float64 // Rückkehr des speed_factor auf 1.0 nach TTL-Ablauf
}

// DefaultConfig liefert exakt die Werte aus CLAUDE.md §13.
func DefaultConfig() Config {
	return Config{
		TempMean: 62.0, TempSigma: 0.5,
		PressMean: 5.2, PressSigma: 0.15,
		VibMean: 2.2, VibSigma: 0.25,
		TempGain: 2.2, TempExp: 1.6, TempTau: 25.0,
		OUTheta:      0.4,
		ErrRampPerS:     0.12,
		ErrOffsetMax:    6.8, // 2.2 + 6.8 = 9.0 mm/s
		ErrDecayTau:     8.0,
		ErrClearedBelow: 0.05,
		ErrorTempC:   85.0,
		ErrorHoldS:   120.0,
		SpeedRampPerS: 0.25,
	}
}

// Machine ist der Zustand einer einzelnen Maschine.
type Machine struct {
	ID  uint16
	Seq uint64

	cfg Config
	rng *rand.Rand

	// Basiswerte ohne Fehleranteil (OU-Prozesse).
	baseVib   float64
	basePress float64

	Temp float64

	// ErrOffset ist der durch eine Injektion aufgebaute Vibrationsanteil.
	ErrOffset float64
	Injected  bool

	SpeedFactor float64

	throttleFactor    float64
	throttleRemaining float64 // Sekunden

	errorElapsed float64 // Sekunden im Status ERROR

	status  telemetry.MachineStatus
	offline bool
}

// New erzeugt eine Maschine im Normalbetrieb. seed macht die Physik
// reproduzierbar (Tests + identische Läufe).
func New(id uint16, cfg Config, seed int64) *Machine {
	return &Machine{
		ID:          id,
		cfg:         cfg,
		rng:         rand.New(rand.NewSource(seed)),
		baseVib:     cfg.VibMean,
		basePress:   cfg.PressMean,
		Temp:        cfg.TempMean,
		SpeedFactor: 1.0,
		status:      telemetry.MachineStatusOK,
	}
}

func (m *Machine) Status() telemetry.MachineStatus { return m.status }

// Vib ist die aktuelle Vibration inkl. Fehleranteil.
func (m *Machine) Vib() float64 { return m.baseVib + m.ErrOffset }

// Press ist der aktuelle Druck.
func (m *Machine) Press() float64 { return m.basePress }

// SetOffline schaltet zwischen Normalbetrieb und gestoppter Fabrik um (§13).
func (m *Machine) SetOffline(off bool) {
	m.offline = off
	if off {
		m.status = telemetry.MachineStatusOFFLINE
		m.SpeedFactor = 0
		return
	}
	// Zurück in den Normalbetrieb: Fehlerzustände sind mit dem Stopp erledigt.
	m.status = telemetry.MachineStatusOK
	m.SpeedFactor = 1.0
	m.Injected = false
	m.ErrOffset = 0
	m.errorElapsed = 0
	m.throttleRemaining = 0
}

// Inject startet das Fehlerprofil "vibration_ramp".
func (m *Machine) Inject() {
	if m.offline {
		return
	}
	m.Injected = true
}

// Reset nimmt Injektion und Fehleranteil zurück (§8 "reset").
func (m *Machine) Reset() {
	m.Injected = false
	m.ErrOffset = 0
	m.errorElapsed = 0
	if !m.offline {
		m.status = telemetry.MachineStatusOK
		m.throttleRemaining = 0
		m.SpeedFactor = 1.0
	}
}

// Throttle drosselt die Maschine für ttl Sekunden auf factor (§8).
func (m *Machine) Throttle(factor float64, ttl float64) {
	if m.offline || factor <= 0 || factor > 1 {
		return
	}
	m.throttleFactor = factor
	// Eine erneute (eskalierende) Drosselung verlängert nicht, sondern ersetzt.
	m.throttleRemaining = ttl
}

// Throttled meldet, ob gerade eine Drosselung aktiv ist.
func (m *Machine) Throttled() bool { return m.throttleRemaining > 0 }

// Step rechnet die Physik um dt Sekunden weiter. Reine Zustandsfunktion:
// keine Wanduhr, damit Tests Simulationszeit vorspulen können.
func (m *Machine) Step(dt float64) {
	if m.offline {
		// Gestoppte Fabrik: Werte kühlen zum Ruhezustand, keine Fehlerdynamik.
		m.baseVib = m.cfg.VibMean
		m.basePress = m.cfg.PressMean
		m.Temp += (m.cfg.TempMean - m.Temp) * (1 - math.Exp(-dt/m.cfg.TempTau))
		m.status = telemetry.MachineStatusOFFLINE
		m.SpeedFactor = 0
		return
	}

	// --- Status ERROR: Maschine steht, kühlt ab, Auto-Reset nach ErrorHoldS ---
	if m.status == telemetry.MachineStatusERROR {
		m.errorElapsed += dt
		m.SpeedFactor = 0
		m.ErrOffset *= math.Exp(-dt / m.cfg.ErrDecayTau)
		m.stepBase(dt)
		m.stepTemp(dt)
		if m.errorElapsed >= m.cfg.ErrorHoldS {
			m.Injected = false
			m.ErrOffset = 0
			m.errorElapsed = 0
			m.status = telemetry.MachineStatusOK
			m.SpeedFactor = 1.0
		}
		return
	}

	// --- Drosselung: TTL herunterzählen, danach sanft zurück auf 1.0 ---
	if m.throttleRemaining > 0 {
		m.throttleRemaining -= dt
		m.SpeedFactor = m.throttleFactor
		m.status = telemetry.MachineStatusTHROTTLED
	} else if m.SpeedFactor < 1.0 {
		m.SpeedFactor = math.Min(1.0, m.SpeedFactor+m.cfg.SpeedRampPerS*dt)
		if m.SpeedFactor >= 1.0 {
			m.SpeedFactor = 1.0
			m.status = telemetry.MachineStatusOK
		}
	} else {
		m.status = telemetry.MachineStatusOK
	}

	// --- Fehler-Offset: baut auf, solange ungedrosselt; zerfällt bei Drosselung ---
	if m.Injected {
		if m.SpeedFactor >= 1.0 {
			m.ErrOffset = math.Min(m.cfg.ErrOffsetMax, m.ErrOffset+m.cfg.ErrRampPerS*dt)
		} else {
			// Gedrosselt: der mechanische Fehleranteil klingt ab (τ ≈ 8 s).
			m.ErrOffset *= math.Exp(-dt / m.cfg.ErrDecayTau)
			// Vollständig abgebaut = Fehler behoben. Sonst liefe die Rampe nach
			// TTL-Ablauf erneut hoch und die Heilung wäre nur ein Aufschub.
			if m.ErrOffset < m.cfg.ErrClearedBelow {
				m.ErrOffset = 0
				m.Injected = false
			}
		}
	} else if m.ErrOffset > 0 {
		m.ErrOffset *= math.Exp(-dt / m.cfg.ErrDecayTau)
		if m.ErrOffset < m.cfg.ErrClearedBelow {
			m.ErrOffset = 0
		}
	}

	m.stepBase(dt)
	m.stepTemp(dt)

	// --- Übertemperatur: Maschine geht in ERROR (§13) ---
	if m.Temp >= m.cfg.ErrorTempC {
		m.status = telemetry.MachineStatusERROR
		m.SpeedFactor = 0
		m.errorElapsed = 0
		m.throttleRemaining = 0
	}
}

// stepBase führt die OU-Prozesse für Vibration und Druck weiter (exakte
// Diskretisierung: x <- mu + phi*(x-mu) + N(0, sigma*sqrt(1-phi^2))).
func (m *Machine) stepBase(dt float64) {
	phi := math.Exp(-m.cfg.OUTheta * dt)
	noise := math.Sqrt(1 - phi*phi)
	m.baseVib = m.cfg.VibMean + phi*(m.baseVib-m.cfg.VibMean) + m.cfg.VibSigma*noise*m.rng.NormFloat64()
	m.basePress = m.cfg.PressMean + phi*(m.basePress-m.cfg.PressMean) + m.cfg.PressSigma*noise*m.rng.NormFloat64()
	m.baseVib = clamp(m.baseVib, 0, 50)
	m.basePress = clamp(m.basePress, 0, 20)
}

// stepTemp zieht die Temperatur dem gekoppelten Ziel nach (τ = TempTau).
// Kern des Demos: die Vibration steigt ZUERST, die Temperatur folgt verzögert —
// genau dieses Fenster nutzt die Anomalie-Erkennung.
func (m *Machine) stepTemp(dt float64) {
	target := TempTarget(m.cfg, m.Vib())
	m.Temp += (target - m.Temp) * (1 - math.Exp(-dt/m.cfg.TempTau))
	m.Temp += m.cfg.TempSigma * math.Sqrt(dt) * m.rng.NormFloat64() * 0.5
	m.Temp = clamp(m.Temp, -50, 200)
}

// TempTarget ist die Kopplung aus CLAUDE.md §13:
// temp_target = 62 + 2.2 * max(0, vib-2.2)^1.6
func TempTarget(cfg Config, vib float64) float64 {
	excess := math.Max(0, vib-cfg.VibMean)
	return cfg.TempMean + cfg.TempGain*math.Pow(excess, cfg.TempExp)
}

// Sample liefert ein einzelnes Messwert-Tripel inkl. leichtem Sensorrauschen.
// Die Physik selbst wird pro Tick gerechnet, die Samples innerhalb eines Ticks
// unterscheiden sich nur um dieses Messrauschen.
func (m *Machine) Sample() (temp, press, vib float32) {
	const sensorNoise = 0.02
	return float32(m.Temp + sensorNoise*m.rng.NormFloat64()),
		float32(m.Press() + sensorNoise*m.rng.NormFloat64()),
		float32(math.Max(0, m.Vib()+sensorNoise*m.rng.NormFloat64()))
}

func clamp(v, lo, hi float64) float64 {
	return math.Max(lo, math.Min(hi, v))
}
