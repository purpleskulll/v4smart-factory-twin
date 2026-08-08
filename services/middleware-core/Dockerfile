# ============================================================================
# middleware-core — multi-stage. Rust-Builds sind die teuersten im Projekt,
# deshalb liegt der Dependency-Layer VOR den Quellen (Prompt 03, Punkt 8).
#
# Toolchain 1.85 statt der in §5 genannten 1.83: transitive Abhängigkeiten der
# (ebenfalls gepinnten) rdkafka-0.36-Kette verlangen `edition2024`, das Cargo
# 1.83 nicht kennt. Begründung steht in Cargo.toml und PROGRESS.md.
# ============================================================================
FROM rust:1.85-bookworm AS build

# librdkafka wird aus den Quellen gebaut (Feature "cmake-build").
RUN apt-get update && apt-get install -y --no-install-recommends cmake build-essential && apt-get clean

WORKDIR /src
# 1) Nur das Manifest: Dependency-Layer bleibt gecacht, solange sich
#    Cargo.toml/Cargo.lock nicht ändern.
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo "fn main() {}" > src/main.rs && cargo build --release && rm -rf src

# 2) Echte Quellen.
COPY src ./src
# Ohne touch hält cargo das Dummy-Build für aktuell und baut den echten Code nicht.
RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && apt-get clean \
 && useradd -u 10001 -m app

COPY --from=build /src/target/release/middleware-core /usr/local/bin/middleware-core

# `selfcheck` ist der Vertrag für den Compose-Healthcheck (§15) — nicht umbenennen.
RUN printf '#!/bin/sh\nexec /usr/local/bin/middleware-core --selfcheck\n' > /usr/local/bin/selfcheck \
 && chmod +x /usr/local/bin/selfcheck

USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/middleware-core"]
