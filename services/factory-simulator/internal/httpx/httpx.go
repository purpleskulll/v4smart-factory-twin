// Package httpx stellt den Health-Endpoint und die --selfcheck-Prüfung bereit
// (CLAUDE.md §15). Der Compose-Healthcheck ruft das Executable `selfcheck`,
// das seinerseits das Binary mit --selfcheck startet.
package httpx

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"time"
)

// HealthAddr ist der Listener des Simulators (nur intern, kein publizierter Port).
const HealthAddr = ":8080"

// Ready meldet, ob der Producer verbunden ist — /healthz wird erst dann 200.
type Ready func() bool

// Serve startet den Health-Server. Blockiert bis zum Fehler.
func Serve(ready Ready, info func() map[string]any) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if !ready() {
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "reason": "producer not connected"})
			return
		}
		payload := map[string]any{"ok": true}
		for k, v := range info() {
			payload[k] = v
		}
		_ = json.NewEncoder(w).Encode(payload)
	})
	srv := &http.Server{
		Addr:              HealthAddr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	return srv.ListenAndServe()
}

// Selfcheck prüft den eigenen Health-Endpoint (§15). Bewusst ohne
// Zusatzbibliothek: eine rohe TCP-Anfrage genügt und hält das Image schlank.
func Selfcheck() error {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:8080", 3*time.Second)
	if err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
	if _, err := fmt.Fprintf(conn, "GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n"); err != nil {
		return fmt.Errorf("write: %w", err)
	}
	buf := make([]byte, 64)
	n, err := conn.Read(buf)
	if err != nil && n == 0 {
		return fmt.Errorf("read: %w", err)
	}
	if status := string(buf[:n]); len(status) < 12 || status[9:12] != "200" {
		return fmt.Errorf("healthz meldet: %q", status)
	}
	return nil
}
