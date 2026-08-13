# zellwerk — Kurzbefehle
COMPOSE = docker compose

.PHONY: up up-ai down ps logs test lint fault state nuke

up:            ## Fabrik + Kern + Dashboards
	$(COMPOSE) --profile sim --profile core --profile obs up -d

up-ai:         ## zusätzlich die KI-Schicht
	$(COMPOSE) --profile sim --profile core --profile obs --profile ai up -d

down:
	$(COMPOSE) --profile sim --profile core --profile obs --profile ai down

ps:
	$(COMPOSE) --profile sim --profile core --profile obs --profile ai ps

logs:          ## make logs s=ingest
	$(COMPOSE) logs -f $(s)

test:
	.venv/bin/python -m pytest tests/ -q

lint:
	.venv/bin/python -m ruff check packages/ tests/

fault:         ## make fault id=F1
	curl -sS -X POST http://localhost:8001/faults/$(id) | python3 -m json.tool

state:
	curl -sS http://localhost:8001/state | python3 -m json.tool

nuke:          ## alles inkl. Daten entfernen
	$(COMPOSE) --profile sim --profile core --profile obs --profile ai down -v
