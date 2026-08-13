# Ein Image für alle Python-Dienste (simfactory, connector, ingest, mcpserver,
# agents). Sie teilen sich die Abhängigkeiten; welcher Dienst läuft, entscheidet
# das `command` in docker-compose.yml.
FROM python:3.12-slim

# uv als Paketmanager (SPEC §4). Kein pip-install-Wildwuchs.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Erst nur die Abhängigkeitsdatei kopieren: solange sie sich nicht ändert,
# bleibt der Installationslayer im Build-Cache.
COPY pyproject.toml /app/pyproject.toml
RUN uv pip install --system --no-cache -r pyproject.toml

COPY packages/ /app/
COPY data/ /app/data/
# Nur der Lastgenerator, nicht die ganze Testsuite: er wird gegen den LAUFENDEN
# Stack ausgeführt (docs/decisions.md D4) und muss deshalb im Image liegen.
# Die übrigen Tests laufen offline auf dem Entwicklungsrechner.
COPY tests/load/ /app/tests/load/
RUN touch /app/tests/__init__.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin zellwerk \
    && chown -R zellwerk:zellwerk /app
USER zellwerk

CMD ["python", "-c", "print('kein command gesetzt — siehe docker-compose.yml')"]
