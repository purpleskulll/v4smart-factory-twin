// Package produce kapselt den Kafka-Producer (franz-go).
//
// Heißt bewusst nicht "telemetry": diesen Paketnamen belegt bereits der
// generierte FlatBuffers-Code (internal/gen/telemetry).
//
// Wichtig (CLAUDE.md §13/Prompt 02): Der Produce-Pfad darf den Physik-Takt NIE
// blockieren — deshalb async mit großem Puffer, Fehler werden gezählt statt
// zurückgestaut.
package produce

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"v4smart/factory-simulator/internal/contract"
)

// Producer ist ein dünner Wrapper um den kgo-Client.
type Producer struct {
	cl *kgo.Client

	sent    atomic.Uint64
	failed  atomic.Uint64
	dropped atomic.Uint64
}

// New baut den Producer. RequiredAcks(LeaderAck) verlangt in franz-go
// ausdrücklich das Abschalten der idempotenten Writes — sonst verweigert der
// Client den Start.
func New(brokers []string) (*Producer, error) {
	cl, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.RequiredAcks(kgo.LeaderAck()),
		kgo.DisableIdempotentWrite(),
		kgo.ProducerBatchCompression(kgo.SnappyCompression()),
		kgo.ProducerLinger(5*time.Millisecond),
		kgo.MaxBufferedRecords(250_000),
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
	)
	if err != nil {
		return nil, err
	}
	return &Producer{cl: cl}, nil
}

func (p *Producer) Close() { p.cl.Close() }

// Flush wartet, bis der Puffer geleert ist (sauberes Herunterfahren).
func (p *Producer) Flush(ctx context.Context) error { return p.cl.Flush(ctx) }

// Ping prüft die Verbindung zum Broker (Grundlage für /healthz).
func (p *Producer) Ping(ctx context.Context) error { return p.cl.Ping(ctx) }

// Stats liefert die Zähler für Logs und /healthz.
func (p *Producer) Stats() (sent, failed, dropped uint64) {
	return p.sent.Load(), p.failed.Load(), p.dropped.Load()
}

// MachineKey ist die Key-Konvention aus §6: ASCII-Dezimal der machine_id.
func MachineKey(id uint16) []byte {
	return []byte(strconv.FormatUint(uint64(id), 10))
}

// Raw schickt einen fertigen FlatBuffers-Payload auf sensor_raw. Nicht
// blockierend: Ist der Puffer voll, wird verworfen und gezählt — der
// Physik-Takt läuft weiter (Backpressure würde die Simulation verfälschen).
func (p *Producer) Raw(machineID uint16, payload []byte) {
	rec := &kgo.Record{
		Topic: contract.TopicSensorRaw,
		Key:   MachineKey(machineID),
		Value: payload,
	}
	// TryProduce blockiert nicht: ist der Puffer voll, kommt ErrMaxBuffered im
	// Callback an und wird als "dropped" gezählt.
	p.cl.TryProduce(context.Background(), rec, p.onProduce)
}

// JSON schickt eine JSON-Nachricht auf ein niederfrequentes Topic (§6).
func (p *Producer) JSON(topic string, key []byte, v any) {
	b, err := json.Marshal(v)
	if err != nil {
		p.failed.Add(1)
		return
	}
	p.cl.Produce(context.Background(), &kgo.Record{Topic: topic, Key: key, Value: b}, p.onProduce)
}

// Event ist die Kurzform für system_events (§9).
func (p *Producer) Event(ev contract.Event) {
	key := []byte(contract.KeyFactory)
	if ev.MachineID != nil {
		key = MachineKey(*ev.MachineID)
	}
	p.JSON(contract.TopicSystemEvents, key, ev)
}

func (p *Producer) onProduce(_ *kgo.Record, err error) {
	switch {
	case err == nil:
		p.sent.Add(1)
	case errors.Is(err, kgo.ErrMaxBuffered):
		// Puffer voll — bewusst verwerfen statt den Physik-Takt zu bremsen.
		p.dropped.Add(1)
	default:
		p.failed.Add(1)
	}
}
