# ============================================================================
# V4Smart Factory Digital Twin — zentrale Befehle
# Läuft im Dev-Container (docker spricht mit der inneren DinD-Engine).
# ============================================================================
SHELL := /bin/bash
COMPOSE ?= docker compose
ALL_PROFILES := --profile app --profile edge
NET := v4smart_backend
CURL := docker run --rm --network $(NET) curlimages/curl:8.10.1
FLATC_IMG := v4smart/flatc:24.3.25

.DEFAULT_GOAL := help
.PHONY: help up-infra up down build ps logs restart topics stats urls \
        hash-password codegen smoke-infra smoke-sim test-healing nuke

help:
	@echo ""
	@echo "V4Smart — make-Ziele"
	@echo "  make up-infra        Nur Infrastruktur (Redpanda, QuestDB, Console)"
	@echo "  make up              Alles bauen + starten (app + edge Profile)"
	@echo "  make down            Alles stoppen (Volumes bleiben)"
	@echo "  make nuke            ALLES löschen inkl. Volumes/Daten"
	@echo "  make ps              Status aller Container"
	@echo "  make logs s=<svc>    Logs folgen (z. B. make logs s=middleware-core)"
	@echo "  make restart s=<svc> Service neu starten"
	@echo "  make build           Alle Images (neu) bauen"
	@echo "  make topics          Topics + High Watermarks anzeigen"
	@echo "  make stats           Live-Kennzahlen der Middleware (msg/s, rows/s)"
	@echo "  make codegen         FlatBuffers-Code für Go/Rust/Python erzeugen"
	@echo "  make hash-password pw='...'   Basic-Auth-Hash für die .env erzeugen"
	@echo "  make smoke-infra     Smoke-Test Infrastruktur (Schritt 00)"
	@echo "  make smoke-sim       Smoke-Test Simulator (Schritt 02)"
	@echo "  make test-healing    E2E-Test der Self-Healing-Kette (Schritt 04/07)"
	@echo ""

up-infra:
	$(COMPOSE) up -d

up:
	$(COMPOSE) $(ALL_PROFILES) up -d --build

down:
	$(COMPOSE) $(ALL_PROFILES) down

nuke:
	@echo "!! Löscht ALLE Container UND Volumes (Redpanda-/QuestDB-Daten, Zertifikate)"
	$(COMPOSE) $(ALL_PROFILES) down -v

build:
	$(COMPOSE) $(ALL_PROFILES) build

ps:
	$(COMPOSE) $(ALL_PROFILES) ps

logs:
	$(COMPOSE) $(ALL_PROFILES) logs -f --tail=100 $(s)

restart:
	$(COMPOSE) $(ALL_PROFILES) restart $(s)

topics:
	$(COMPOSE) exec -T redpanda rpk topic list
	@echo ""
	$(COMPOSE) exec -T redpanda rpk topic describe sensor_raw -p || true

stats:
	$(CURL) -sf http://middleware-core:8080/api/stats | jq .

urls:
	@grep -E '^(TWIN_HOST|CONSOLE_HOST|QUESTDB_HOST)=' .env | sed 's/^/  https:\/\//; s/TWIN_HOST=//; s/CONSOLE_HOST=//; s/QUESTDB_HOST=//'

hash-password:
	@test -n "$(pw)" || { echo "Nutzung: make hash-password pw='DEIN_PASSWORT'"; exit 1; }
	@docker run --rm caddy:2.8-alpine caddy hash-password --plaintext '$(pw)'

# --user: sonst gehören die generierten Dateien root und der Workspace-User kann
# sie weder ändern noch löschen (Codegen läuft in einem Wegwerf-Container).
CODEGEN_RUN := docker run --rm --user $(shell id -u):$(shell id -g) -v /workspace:/w -w /w

codegen:
	docker build -t $(FLATC_IMG) -f tools/flatc.Dockerfile tools
	$(CODEGEN_RUN) $(FLATC_IMG) --go     -o services/factory-simulator/internal/gen schemas/sensor_reading.fbs
	$(CODEGEN_RUN) $(FLATC_IMG) --rust   -o services/middleware-core/src/gen        schemas/sensor_reading.fbs
	$(CODEGEN_RUN) $(FLATC_IMG) --python -o services/predictive-ml/app/gen          schemas/sensor_reading.fbs
	@echo "Codegen fertig: Go → factory-simulator/internal/gen | Rust → middleware-core/src/gen | Python → predictive-ml/app/gen"

smoke-infra:
	bash scripts/smoke_infra.sh

smoke-sim:
	bash scripts/smoke_sim.sh

test-healing:
	bash scripts/test_healing.sh
