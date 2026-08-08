# ============================================================================
# predictive-ml — schlank und non-root (CLAUDE.md §4.4/§15)
# ============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependency-Layer zuerst (bleibt gecacht, solange requirements.txt gleich bleibt).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# COPY übernimmt die Rechte aus dem Build-Kontext. Die generierten
# gen-Verzeichnisse hatten dort 2770 (kein Zugriff für "other") — der Container
# läuft aber als appuser und bekam prompt ein ModuleNotFoundError für
# app.gen.telemetry. Das Image darf nicht von den Host-Rechten abhängen.
RUN chmod -R a+rX /app

# `selfcheck` ist der Vertrag für den Compose-Healthcheck (§15) — nicht umbenennen.
RUN printf '#!/bin/sh\nexec python -m app --selfcheck\n' > /usr/local/bin/selfcheck \
 && chmod +x /usr/local/bin/selfcheck \
 && useradd -u 10001 -m appuser

USER appuser
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app"]
