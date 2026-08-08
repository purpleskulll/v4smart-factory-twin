// Package control konsumiert machine_control (Consumer-Group "simulator-control",
// CLAUDE.md §6/§8) und reicht jedes Kommando an den Handler weiter.
// Unbekannte "type"-Werte werden geloggt und ignoriert (Vorwärtskompatibilität).
package control

import (
	"context"
	"encoding/json"
	"log/slog"

	"github.com/twmb/franz-go/pkg/kgo"

	"v4smart/factory-simulator/internal/contract"
)

// Group ist die Consumer-Group aus §6.
const Group = "simulator-control"

// Handler verarbeitet ein einzelnes Kommando.
type Handler func(contract.Control)

// Consume läuft bis der Kontext endet. Der Consumer startet am ENDE des Topics:
// alte Kommandos (z. B. ein throttle von vor dem Neustart) dürfen nicht
// nachträglich ausgeführt werden.
func Consume(ctx context.Context, brokers []string, h Handler) error {
	cl, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.ConsumerGroup(Group),
		kgo.ConsumeTopics(contract.TopicMachineControl),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtEnd()),
	)
	if err != nil {
		return err
	}
	defer cl.Close()

	for {
		fetches := cl.PollFetches(ctx)
		if ctx.Err() != nil {
			return nil
		}
		fetches.EachError(func(t string, p int32, err error) {
			slog.Warn("control: fetch-Fehler", "topic", t, "partition", p, "err", err)
		})
		fetches.EachRecord(func(r *kgo.Record) {
			var c contract.Control
			if err := json.Unmarshal(r.Value, &c); err != nil {
				slog.Warn("control: unlesbares JSON verworfen", "err", err, "bytes", len(r.Value))
				return
			}
			h(c)
		})
	}
}
