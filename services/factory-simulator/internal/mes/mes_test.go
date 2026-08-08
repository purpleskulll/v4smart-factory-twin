package mes

import (
	"testing"

	"v4smart/factory-simulator/internal/contract"
)

const dt = 0.02

func speedsAllRunning(n int) map[uint16]float64 {
	s := make(map[uint16]float64, n)
	for i := 1; i <= n; i++ {
		s[uint16(i)] = 1.0
	}
	return s
}

// Ein Auftrag durchläuft genau QUEUED -> RUNNING -> DONE (§11).
func TestOrderLifecycle(t *testing.T) {
	g := NewGenerator(42)
	speeds := speedsAllRunning(8)

	var seen []string
	var orderID string
	for step := 0; step < int(300/dt); step++ {
		for _, o := range g.Step(dt, speeds, true) {
			if orderID == "" {
				orderID = o.OrderID
			}
			if o.OrderID == orderID {
				seen = append(seen, o.Status)
			}
		}
		if len(seen) > 0 && seen[len(seen)-1] == contract.OrderDone {
			break
		}
	}

	if len(seen) < 3 {
		t.Fatalf("zu wenige Nachrichten für %s: %v", orderID, seen)
	}
	if seen[0] != contract.OrderQueued {
		t.Errorf("erste Nachricht muss QUEUED sein, ist %s", seen[0])
	}
	if seen[1] != contract.OrderRunning {
		t.Errorf("zweite Nachricht muss RUNNING sein, ist %s", seen[1])
	}
	if last := seen[len(seen)-1]; last != contract.OrderDone {
		t.Errorf("letzte Nachricht muss DONE sein, ist %s", last)
	}
}

// Aufträge müssen über die Maschinen streuen — sonst zeigt das MES-Log eine
// Fabrik, in der nur eine Maschine arbeitet.
func TestOrdersSpreadAcrossMachines(t *testing.T) {
	g := NewGenerator(7)
	speeds := speedsAllRunning(8)

	used := map[uint16]bool{}
	for step := 0; step < int(600/dt); step++ {
		for _, o := range g.Step(dt, speeds, true) {
			if o.Status == contract.OrderQueued {
				used[o.MachineID] = true
			}
		}
	}
	if len(used) < 5 {
		t.Errorf("in 10 min bekamen nur %d von 8 Maschinen Aufträge: %v", len(used), used)
	}
}

// Eine gedrosselte Maschine arbeitet sichtbar langsamer (§13).
func TestThrottledMachineIsSlower(t *testing.T) {
	progressAfter := func(speed float64) float64 {
		g := NewGenerator(3)
		speeds := map[uint16]float64{1: speed}
		var last float64
		for step := 0; step < int(120/dt); step++ {
			for _, o := range g.Step(dt, speeds, true) {
				if o.Status == contract.OrderRunning || o.Status == contract.OrderDone {
					last = o.Progress
				}
			}
		}
		return last
	}
	full := progressAfter(1.0)
	half := progressAfter(0.5)
	if half >= full {
		t.Errorf("gedrosselt (%.2f) muss langsamer sein als voll (%.2f)", half, full)
	}
}

// Steht die Fabrik, entstehen keine Aufträge (§13).
func TestNoOrdersWhenFactoryStopped(t *testing.T) {
	g := NewGenerator(11)
	speeds := speedsAllRunning(8)
	for step := 0; step < int(120/dt); step++ {
		if msgs := g.Step(dt, speeds, false); len(msgs) != 0 {
			t.Fatalf("bei gestoppter Fabrik dürfen keine Nachrichten entstehen: %+v", msgs)
		}
	}
}

// Havarierte/gestoppte Maschinen (speed 0) bekommen keine neuen Aufträge.
func TestNoOrdersForStoppedMachine(t *testing.T) {
	g := NewGenerator(13)
	speeds := map[uint16]float64{1: 0, 2: 1.0}
	for step := 0; step < int(300/dt); step++ {
		for _, o := range g.Step(dt, speeds, true) {
			if o.MachineID == 1 {
				t.Fatalf("Maschine mit speed 0 bekam einen Auftrag: %+v", o)
			}
		}
	}
}
