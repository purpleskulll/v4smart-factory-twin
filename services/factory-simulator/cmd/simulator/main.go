// factory-simulator — 8 SCADA-Maschinen + MES-Generator (CLAUDE.md §13).
//
// Produziert TOTAL_RATE FlatBuffers-Readings/s auf sensor_raw, erzeugt
// MES-Aufträge auf mes_orders, reagiert auf machine_control und meldet
// Ereignisse auf system_events.
package main

import (
	"context"
	"flag"
	"log/slog"
	"math/rand"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	flatbuffers "github.com/google/flatbuffers/go"

	"v4smart/factory-simulator/internal/contract"
	"v4smart/factory-simulator/internal/control"
	"v4smart/factory-simulator/internal/gen/telemetry"
	"v4smart/factory-simulator/internal/httpx"
	"v4smart/factory-simulator/internal/machine"
	"v4smart/factory-simulator/internal/mes"
	"v4smart/factory-simulator/internal/produce"
)

const (
	tickDur     = 20 * time.Millisecond // §13: 20-ms-Ticks
	dt          = 0.02
	offlineRate = 1.0 // Hz Heartbeat je Maschine bei gestoppter Fabrik (§13)
)

type factory struct {
	mu       sync.Mutex
	machines map[uint16]*machine.Machine
	ids      []uint16
	running  bool

	ratePerMachine float64            // Readings/s je Maschine im Normalbetrieb
	due            map[uint16]float64 // Rest-Akkumulator für die Sendungsrate

	prod *produce.Producer
	gen  *mes.Generator
	rng  *rand.Rand
	bld  *flatbuffers.Builder
}

func main() {
	selfcheck := flag.Bool("selfcheck", false, "eigenen Health-Endpoint prüfen und beenden (CLAUDE.md §15)")
	flag.Parse()
	if *selfcheck {
		if err := httpx.Selfcheck(); err != nil {
			slog.Error("selfcheck fehlgeschlagen", "err", err)
			os.Exit(1)
		}
		os.Exit(0)
	}

	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})))

	brokers := strings.Split(env("KAFKA_BROKERS", "redpanda:9092"), ",")
	machineCount := envInt("MACHINE_COUNT", 8)
	totalRate := envInt("TOTAL_RATE", 2000)
	autoStart := env("FACTORY_AUTO_START", "true") == "true"

	prod, err := produce.New(brokers)
	if err != nil {
		slog.Error("Producer lässt sich nicht bauen", "err", err)
		os.Exit(1)
	}
	defer prod.Close()

	f := &factory{
		machines:       make(map[uint16]*machine.Machine, machineCount),
		running:        autoStart,
		ratePerMachine: float64(totalRate) / float64(machineCount),
		due:            make(map[uint16]float64, machineCount),
		prod:           prod,
		gen:            mes.NewGenerator(time.Now().UnixNano()),
		rng:            rand.New(rand.NewSource(time.Now().UnixNano())),
		bld:            flatbuffers.NewBuilder(256),
	}
	cfg := machine.DefaultConfig()
	for i := 1; i <= machineCount; i++ {
		id := uint16(i)
		m := machine.New(id, cfg, int64(i)*7919+time.Now().UnixNano())
		m.SetOffline(!autoStart)
		f.machines[id] = m
		f.ids = append(f.ids, id)
	}
	sort.Slice(f.ids, func(i, j int) bool { return f.ids[i] < f.ids[j] })

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Verbindung herstellen, bevor /healthz 200 meldet (§15).
	var ready sync.Once
	readyCh := make(chan struct{})
	go func() {
		for ctx.Err() == nil {
			pctx, cancel := context.WithTimeout(ctx, 5*time.Second)
			err := prod.Ping(pctx)
			cancel()
			if err == nil {
				ready.Do(func() { close(readyCh) })
				return
			}
			slog.Warn("Broker noch nicht erreichbar", "err", err)
			time.Sleep(2 * time.Second)
		}
	}()

	go func() {
		isReady := func() bool {
			select {
			case <-readyCh:
				return true
			default:
				return false
			}
		}
		info := func() map[string]any {
			sent, failed, dropped := prod.Stats()
			f.mu.Lock()
			running, machines, orders := f.running, len(f.machines), f.gen.ActiveCount()
			f.mu.Unlock()
			return map[string]any{
				"factory_running": running, "machines": machines, "active_orders": orders,
				"sent": sent, "failed": failed, "dropped": dropped,
			}
		}
		if err := httpx.Serve(isReady, info); err != nil {
			slog.Error("Health-Server beendet", "err", err)
		}
	}()

	go func() {
		if err := control.Consume(ctx, brokers, f.onControl); err != nil {
			slog.Error("Control-Consumer beendet", "err", err)
		}
	}()

	<-readyCh
	slog.Info("Simulator startet",
		"machines", machineCount, "total_rate", totalRate,
		"rate_per_machine", f.ratePerMachine, "factory_running", autoStart)
	f.emitFactoryState()

	ticker := time.NewTicker(tickDur)
	defer ticker.Stop()
	logEvery := time.NewTicker(30 * time.Second)
	defer logEvery.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("Shutdown, flushe Producer")
			fctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			_ = f.prod.Flush(fctx)
			cancel()
			return
		case <-logEvery.C:
			sent, failed, dropped := prod.Stats()
			slog.Info("stats", "sent", sent, "failed", failed, "dropped", dropped)
		case <-ticker.C:
			f.tick(time.Now())
		}
	}
}

// tick rechnet einen 20-ms-Schritt: Physik, Readings, MES.
func (f *factory) tick(now time.Time) {
	f.mu.Lock()
	defer f.mu.Unlock()

	speeds := make(map[uint16]float64, len(f.ids))

	for _, id := range f.ids {
		m := f.machines[id]
		before := m.Status()
		m.Step(dt)
		if before != telemetry.MachineStatusERROR && m.Status() == telemetry.MachineStatusERROR {
			mid := id
			f.prod.Event(contract.NewEvent(contract.KindMachineError, &mid,
				map[string]any{"temp": round1(m.Temp)}))
			slog.Warn("Maschine in ERROR", "machine", id, "temp", round1(m.Temp))
		}
		speeds[id] = m.SpeedFactor

		// Sendungsrate: im Normalbetrieb ratePerMachine, bei gestoppter Fabrik
		// 1 Hz Heartbeat (§13). Akkumulator statt fester Anzahl, damit auch
		// krumme Raten exakt getroffen werden.
		rate := f.ratePerMachine
		if !f.running {
			rate = offlineRate
		}
		f.due[id] += rate * dt
		n := int(f.due[id])
		if n <= 0 {
			continue
		}
		f.due[id] -= float64(n)
		f.emitReadings(m, n, now)
	}

	for _, o := range f.gen.Step(dt, speeds, f.running) {
		f.prod.JSON(contract.TopicMesOrders, produce.MachineKey(o.MachineID), o)
	}
}

// emitReadings baut n FlatBuffers-Readings mit gleichmäßig über den Tick
// verteilten Zeitstempeln (§13).
func (f *factory) emitReadings(m *machine.Machine, n int, now time.Time) {
	step := tickDur / time.Duration(n)
	for i := 0; i < n; i++ {
		temp, press, vib := m.Sample()
		m.Seq++

		f.bld.Reset()
		telemetry.SensorReadingStart(f.bld)
		telemetry.SensorReadingAddTsNs(f.bld, now.Add(time.Duration(i)*step).UnixNano())
		telemetry.SensorReadingAddMachineId(f.bld, m.ID)
		telemetry.SensorReadingAddSeq(f.bld, m.Seq)
		telemetry.SensorReadingAddTemperatureC(f.bld, temp)
		telemetry.SensorReadingAddPressureBar(f.bld, press)
		telemetry.SensorReadingAddVibrationMms(f.bld, vib)
		telemetry.SensorReadingAddSpeedFactor(f.bld, float32(m.SpeedFactor))
		telemetry.SensorReadingAddStatus(f.bld, m.Status())
		off := telemetry.SensorReadingEnd(f.bld)
		telemetry.FinishSensorReadingBuffer(f.bld, off)

		// KOPIEREN: FinishedBytes() zeigt in den Builder-Puffer, den der
		// nächste Reset überschreibt — der Producer sendet aber asynchron.
		src := f.bld.FinishedBytes()
		payload := make([]byte, len(src))
		copy(payload, src)
		f.prod.Raw(m.ID, payload)
	}
}

// onControl verarbeitet ein Kommando von machine_control (§8).
func (f *factory) onControl(c contract.Control) {
	f.mu.Lock()
	defer f.mu.Unlock()

	switch c.Type {
	case contract.CtrlThrottle:
		m := f.pick(c.MachineID)
		if m == nil {
			slog.Warn("throttle ohne bekannte Maschine", "machine_id", c.MachineID)
			return
		}
		ttl := c.TTLS
		if ttl <= 0 {
			ttl = 120
		}
		m.Throttle(c.Factor, ttl)
		slog.Info("throttle angewendet", "machine", m.ID, "factor", c.Factor, "ttl_s", ttl,
			"source", c.Source, "reason", c.Reason)

	case contract.CtrlInjectError:
		m := f.pick(c.MachineID)
		if m == nil {
			m = f.randomRunning()
		}
		if m == nil {
			slog.Warn("inject_error: keine laufende Maschine verfügbar")
			return
		}
		m.Inject()
		mid := m.ID
		profile := c.Profile
		if profile == "" {
			profile = "vibration_ramp"
		}
		f.prod.Event(contract.NewEvent(contract.KindErrorInjected, &mid,
			map[string]any{"profile": profile}))
		slog.Info("Fehler injiziert", "machine", m.ID, "profile", profile, "source", c.Source)

	case contract.CtrlFactory:
		switch c.Action {
		case "start":
			f.setRunning(true)
		case "stop":
			f.setRunning(false)
		default:
			slog.Warn("factory: unbekannte action", "action", c.Action)
		}

	case contract.CtrlReset:
		m := f.pick(c.MachineID)
		if m == nil {
			slog.Warn("reset ohne bekannte Maschine", "machine_id", c.MachineID)
			return
		}
		m.Reset()
		slog.Info("Maschine zurückgesetzt", "machine", m.ID, "source", c.Source)

	default:
		// Vorwärtskompatibilität (§8): loggen und ignorieren, nie crashen.
		slog.Warn("unbekannter Kommandotyp ignoriert", "type", c.Type, "source", c.Source)
	}
}

func (f *factory) setRunning(run bool) {
	if f.running == run {
		return
	}
	f.running = run
	for _, id := range f.ids {
		f.machines[id].SetOffline(!run)
	}
	f.emitFactoryState()
	slog.Info("Fabrik-Zustand geändert", "running", run)
}

func (f *factory) emitFactoryState() {
	f.prod.Event(contract.NewEvent(contract.KindFactoryState, nil,
		map[string]any{"running": f.running}))
}

func (f *factory) pick(id *uint16) *machine.Machine {
	if id == nil {
		return nil
	}
	return f.machines[*id]
}

func (f *factory) randomRunning() *machine.Machine {
	var candidates []*machine.Machine
	for _, id := range f.ids {
		if m := f.machines[id]; m.Status() == telemetry.MachineStatusOK {
			candidates = append(candidates, m)
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	return candidates[f.rng.Intn(len(candidates))]
}

func round1(v float64) float64 { return float64(int(v*10+0.5)) / 10 }

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
		slog.Warn("ungültiger Zahlenwert in der Umgebung, nutze Vorgabe", "key", k, "value", v, "default", def)
	}
	return def
}
