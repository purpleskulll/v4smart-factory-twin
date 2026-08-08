# ============================================================================
# flatc — FlatBuffers-Compiler, exakt gepinnt auf v24.3.25
# ----------------------------------------------------------------------------
# Die Version MUSS zu den Runtime-Libraries passen (siehe CLAUDE.md §5):
#   Go:      github.com/google/flatbuffers v24.3.25+incompatible
#   Rust:    flatbuffers = "=24.3.25"
#   Python:  flatbuffers==24.3.25
#
# Build & Nutzung übernimmt:  make codegen
# ============================================================================
FROM alpine:3.20 AS build
RUN apk add --no-cache build-base cmake git
RUN git clone --depth 1 --branch v24.3.25 https://github.com/google/flatbuffers.git /src
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
      -DFLATBUFFERS_BUILD_TESTS=OFF -DFLATBUFFERS_BUILD_FLATLIB=OFF \
 && cmake --build /build -j"$(nproc)" --target flatc

FROM alpine:3.20
# flatc ist ein C++-Binary und dynamisch gegen libstdc++/libgcc gelinkt. Nacktes
# alpine bringt beides NICHT mit — ohne diese Zeile startet flatc nicht
# ("Error relocating /usr/local/bin/flatc: _ZSt4cout: symbol not found").
RUN apk add --no-cache libstdc++
COPY --from=build /build/flatc /usr/local/bin/flatc
ENTRYPOINT ["flatc"]
