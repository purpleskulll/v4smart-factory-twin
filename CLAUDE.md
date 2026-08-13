# zellwerk — Arbeitsregeln

- Lies SPEC.md vollständig, bevor du Struktur änderst. SPEC.md ist die Wahrheit;
  Abweichungen nur mit Eintrag in docs/decisions.md.
- Python 3.12 + uv. Lint: ruff. Tests: pytest. Jede Funktionalität mit Test.
- Nach jedem Arbeitsschritt muss `docker compose up` fehlerfrei starten.
- Kleine, thematisch geschlossene Commits (conventional commits, deutsch ok).
- Keine Secrets ins Repo; .env.example aktuell halten.
- Scope-Zaun: §2 der SPEC beachten. Im Zweifel: nicht bauen, fragen.
- LLM-Aufrufe nur in packages/agents; Rest bleibt deterministisch.

## Zusätzlich für dieses Deployment

- Der LLM-Zugang läuft über `zellwerk-llm`, NICHT über einen API-Schlüssel
  (docs/decisions.md D1). Dieser Dienst erneuert das Token NIE selbst — der
  Grund steht im Kopfkommentar von `services/zellwerk-llm/proxy.py` und ist
  nicht verhandelbar: ein zweiter Erneuerer macht das Token serverseitig
  ungültig, von dem auch andere Dienste abhängen.
- Das Netz `backend` ist `internal: true`. Nur `zellwerk-llm` hat ein zweites
  Bein nach draußen. Kein weiterer Dienst bekommt Egress.
- Der Stack teilt sich die Laufzeitumgebung mit einem anderen Projekt. Dessen
  Container, Netze und Volumes werden nicht angefasst.
