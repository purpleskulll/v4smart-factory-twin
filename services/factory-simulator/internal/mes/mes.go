// Package mes erzeugt die MES-Aufträge (CLAUDE.md §11/§13): alle 8–20 s ein
// neuer Auftrag auf der am wenigsten belasteten Maschine, Übergänge
// QUEUED -> RUNNING -> DONE, Fortschritt proportional zum speed_factor
// (eine gedrosselte Maschine arbeitet sichtbar langsamer).
package mes

import (
	"fmt"
	"math/rand"
	"sort"

	"v4smart/factory-simulator/internal/contract"
)

// Products sind die Produkte aus §11.
var Products = []string{"SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E"}

const (
	minGapS       = 8.0
	maxGapS       = 20.0
	progressEvery = 5.0  // Sekunden zwischen RUNNING-Updates (§11)
	secPerUnit    = 0.06 // qty=250 -> 15 s bei voller Geschwindigkeit
)

type order struct {
	msg        contract.Order
	duration   float64
	sinceMsg   float64
	published  bool
}

// Generator hält die laufenden Aufträge. Step ist eine reine Zustandsfunktion
// (kein Timer, keine Wanduhr) und damit testbar.
type Generator struct {
	rng     *rand.Rand
	nextIn  float64
	active  map[uint16]*order
	counter int
}

func NewGenerator(seed int64) *Generator {
	g := &Generator{rng: rand.New(rand.NewSource(seed)), active: map[uint16]*order{}}
	g.nextIn = g.gap()
	return g
}

func (g *Generator) gap() float64 {
	return minGapS + g.rng.Float64()*(maxGapS-minGapS)
}

// Step rechnet dt Sekunden weiter und liefert alle Nachrichten, die auf
// mes_orders zu publizieren sind. speeds enthält den speed_factor je Maschine;
// steht die Fabrik, entstehen keine neuen Aufträge (§13).
func (g *Generator) Step(dt float64, speeds map[uint16]float64, factoryRunning bool) []contract.Order {
	var out []contract.Order

	// Fortschritt bestehender Aufträge.
	for id, o := range g.active {
		speed := speeds[id]
		if !factoryRunning {
			speed = 0
		}
		if !o.published {
			o.msg.Status = contract.OrderRunning
			o.msg.Ts = contract.Now()
			out = append(out, o.msg)
			o.published = true
			o.sinceMsg = 0
			continue
		}
		if speed <= 0 {
			continue // gestoppte oder havarierte Maschine produziert nicht
		}
		o.msg.Progress += speed * dt / o.duration
		o.sinceMsg += dt

		if o.msg.Progress >= 1.0 {
			o.msg.Progress = 1.0
			o.msg.Status = contract.OrderDone
			o.msg.Ts = contract.Now()
			out = append(out, o.msg)
			delete(g.active, id)
			continue
		}
		if o.sinceMsg >= progressEvery {
			o.sinceMsg = 0
			o.msg.Ts = contract.Now()
			out = append(out, o.msg)
		}
	}

	// Neuen Auftrag einplanen.
	if !factoryRunning {
		return out
	}
	g.nextIn -= dt
	if g.nextIn > 0 {
		return out
	}
	g.nextIn = g.gap()

	target, ok := g.leastLoaded(speeds)
	if !ok {
		return out
	}
	g.counter++
	qty := 50 + g.rng.Intn(451)
	msg := contract.Order{
		V:         contract.SchemaVersion,
		Ts:        contract.Now(),
		OrderID:   fmt.Sprintf("PO-%06d", g.counter),
		Product:   Products[g.rng.Intn(len(Products))],
		Qty:       qty,
		MachineID: target,
		Status:    contract.OrderQueued,
		Progress:  0,
	}
	g.active[target] = &order{msg: msg, duration: float64(qty) * secPerUnit}
	out = append(out, msg) // QUEUED

	return out
}

// leastLoaded wählt eine laufende Maschine ohne aktiven Auftrag; gibt es keine,
// wird kein Auftrag erzeugt (Warteschlangen sind für den PoC nicht nötig).
func (g *Generator) leastLoaded(speeds map[uint16]float64) (uint16, bool) {
	ids := make([]uint16, 0, len(speeds))
	for id, sp := range speeds {
		if sp <= 0 {
			continue // ERROR/OFFLINE: keine neuen Aufträge
		}
		if _, busy := g.active[id]; busy {
			continue
		}
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		return 0, false
	}
	// Alle freien Maschinen haben dieselbe Last (0 aktive Aufträge) — bei
	// Gleichstand zufällig wählen. Nähme man stattdessen die kleinste ID,
	// bekämen faktisch nur M1/M2 je Aufträge (live beobachtet), und das
	// MES-Log zeigte eine Fabrik, in der 6 von 8 Maschinen nie produzieren.
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] }) // stabile Basis
	return ids[g.rng.Intn(len(ids))], true
}

// ActiveCount ist die Anzahl laufender Aufträge (für Logs/Health).
func (g *Generator) ActiveCount() int { return len(g.active) }
