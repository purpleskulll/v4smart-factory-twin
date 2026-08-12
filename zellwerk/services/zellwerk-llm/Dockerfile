# zellwerk-llm — der einzige kontrollierte Ausgang der Blase Richtung Anthropic.
# Bewusst ohne Abhaengigkeiten: reine Python-Standardbibliothek, damit dieses
# Image auch dann baut, wenn die innere Engine kein PyPI erreicht.
FROM python:3.12-slim

WORKDIR /app
COPY proxy.py /app/proxy.py
COPY selfcheck /usr/local/bin/selfcheck
RUN chmod +x /usr/local/bin/selfcheck

# Nicht als root laufen — der Dienst braucht nur Lesezugriff auf /creds.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin zellwerk
USER zellwerk

ENV PORT=4010 \
    ZELLWERK_CREDS_PATH=/creds/creds.json \
    PYTHONUNBUFFERED=1

EXPOSE 4010

# Wie die uebrigen Dienste des Stacks: der Container ist erst "healthy",
# wenn sein eigener Endpunkt antwortet — hier zusaetzlich nur dann, wenn
# wirklich ein GUELTIGER Token vorliegt (/health gibt sonst 503).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/usr/local/bin/selfcheck"]

CMD ["python3", "/app/proxy.py"]
