# ============================================================================
# factory-simulator — multi-stage, schlank und non-root (CLAUDE.md §4.4/§15)
# ============================================================================
FROM golang:1.23-alpine AS build
WORKDIR /src

# Dependency-Layer zuerst: bleibt gecacht, solange sich go.mod/go.sum nicht ändern.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/simulator ./cmd/simulator

FROM alpine:3.20
RUN adduser -D -u 10001 app

COPY --from=build /out/simulator /usr/local/bin/simulator

# `selfcheck` ist der Vertrag für den Compose-Healthcheck (§15) — nicht umbenennen.
RUN printf '#!/bin/sh\nexec /usr/local/bin/simulator --selfcheck\n' > /usr/local/bin/selfcheck \
 && chmod +x /usr/local/bin/selfcheck

USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/simulator"]
