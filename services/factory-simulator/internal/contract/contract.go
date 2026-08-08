// Package contract enthält die JSON-Kontrakte der Control-/Event-Plane
// (CLAUDE.md §8 machine_control, §9 system_events, §11 mes_orders).
// Diese Strukturen sind verbindlich — middleware-core, predictive-ml und das
// Dashboard lesen exakt diese Felder.
package contract

import "time"

// SchemaVersion ist das Feld "v" in allen Nachrichten (immer 1).
const SchemaVersion = 1

// Quellen für das Feld "source".
const (
	SourceML         = "predictive-ml"
	SourceDashboard  = "dashboard"
	SourceMiddleware = "middleware-core"
	SourceSimulator  = "factory-simulator"
)

// Topic-Namen (§6).
const (
	TopicSensorRaw      = "sensor_raw"
	TopicSensorClean    = "sensor_clean"
	TopicMesOrders      = "mes_orders"
	TopicMachineControl = "machine_control"
	TopicSystemEvents   = "system_events"
)

// KeyFactory ist der Kafka-Key für fabrikweite Nachrichten (§6).
const KeyFactory = "factory"

// Now liefert den Zeitstempel im Format der Kontrakte (RFC3339).
func Now() string { return time.Now().UTC().Format(time.RFC3339) }

// Control ist eine Nachricht auf machine_control (§8). Nicht belegte Felder
// bleiben leer — unbekannte "type"-Werte werden geloggt und ignoriert.
type Control struct {
	V         int     `json:"v"`
	Ts        string  `json:"ts"`
	Type      string  `json:"type"`
	MachineID *uint16 `json:"machine_id,omitempty"`
	Factor    float64 `json:"factor,omitempty"`
	TTLS      float64 `json:"ttl_s,omitempty"`
	Profile   string  `json:"profile,omitempty"`
	Action    string  `json:"action,omitempty"`
	Source    string  `json:"source"`
	Reason    string  `json:"reason,omitempty"`
}

// Control-Typen (§8).
const (
	CtrlThrottle    = "throttle"
	CtrlInjectError = "inject_error"
	CtrlFactory     = "factory"
	CtrlReset       = "reset"
)

// Event ist eine Nachricht auf system_events (§9). Die kind-Werte sind
// abschließend.
type Event struct {
	V         int            `json:"v"`
	Ts        string         `json:"ts"`
	Kind      string         `json:"kind"`
	MachineID *uint16        `json:"machine_id,omitempty"`
	Detail    map[string]any `json:"detail,omitempty"`
}

// Event-Kinds (§9).
const (
	KindAnomalyDetected = "anomaly_detected"
	KindHealingApplied  = "healing_applied"
	KindHealed          = "healed"
	KindMachineError    = "machine_error"
	KindErrorInjected   = "error_injected"
	KindFactoryState    = "factory_state"
	KindInfo            = "info"
)

// NewEvent baut ein Event mit gesetztem v/ts.
func NewEvent(kind string, machineID *uint16, detail map[string]any) Event {
	return Event{V: SchemaVersion, Ts: Now(), Kind: kind, MachineID: machineID, Detail: detail}
}

// Order ist eine Nachricht auf mes_orders (§11).
type Order struct {
	V         int     `json:"v"`
	Ts        string  `json:"ts"`
	OrderID   string  `json:"order_id"`
	Product   string  `json:"product"`
	Qty       int     `json:"qty"`
	MachineID uint16  `json:"machine_id"`
	Status    string  `json:"status"`
	Progress  float64 `json:"progress"`
}

// Order-Status (§11).
const (
	OrderQueued  = "QUEUED"
	OrderRunning = "RUNNING"
	OrderDone    = "DONE"
)
