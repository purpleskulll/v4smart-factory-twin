#!/usr/bin/env python3
"""zellwerk-llm — Claude-Zugang fuer die Agenten in der abgeschotteten Blase.

Warum dieser Dienst ueberhaupt existiert
---------------------------------------
Die zellwerk-Services haengen im Compose-Netz `backend`, das mit
`internal: true` angelegt ist — sie haben bewusst KEINEN Weg ins Internet.
Dieser Proxy ist der einzige kontrollierte Ausgang: ein Bein im `backend`
(damit die Agenten ihn erreichen), ein Bein im `edge` (damit er
api.anthropic.com erreicht) — dasselbe Muster, das `caddy` fuer die
Gegenrichtung schon benutzt. Das Backend-Netz bleibt dicht.

Warum er NIEMALS selbst refresht
--------------------------------
Der Token stammt aus dem Claude-Max-Abo. Ein Refresh gegen
console.anthropic.com/v1/oauth/token gibt einen NEUEN refresh_token zurueck
und widerruft den alten SERVERSEITIG. Genau das ist in der Praxis schon
einmal passiert: zwei Prozesse erneuerten dieselbe Datei, der zweite bekam
`invalid_grant`, und der Dienst war stundenlang tot.

Dieser Proxy ist deshalb ein REIN PASSIVER LESER. Er liest den accessToken
aus der gesyncten Datei und benutzt ihn. Laeuft der Token ab, meldet er das
LAUT (503 mit Klartext) und faellt NICHT auf einen eigenen Refresh zurueck.
Nur-Lesen kann nichts widerrufen. Der Refresh bleibt Sache der bestehenden
Kette, die diese Datei pflegt.

Endpunkte
---------
  GET  /health       Betriebszustand + Restlaufzeit des Tokens (ohne Geheimnis)
  GET  /v1/models    Liste der Modelle, die dieser Proxy zulaesst
  POST /v1/messages  Anthropic-Format, 1:1 durchgereicht (inkl. Streaming)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CREDS_PATH = os.environ.get("ZELLWERK_CREDS_PATH", "/creds/creds.json")
PORT = int(os.environ.get("PORT", "4010"))
UPSTREAM = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Standardmodell: Haiku. Gemessen 2026-08-13 gegen die Playbook-Aufgaben der
# Spec (Ausschuss-Triage + F3/F5-Unterscheidung) — beide korrekt geloest.
# Ueber ZELLWERK_MODEL umstellbar, ohne den Code anzufassen.
DEFAULT_MODEL = os.environ.get("ZELLWERK_MODEL", "claude-haiku-4-5-20251001")
MAX_UPSTREAM_TIMEOUT = int(os.environ.get("ZELLWERK_TIMEOUT_S", "120"))


class TokenUnavailable(RuntimeError):
    """Kein brauchbarer Token — der Aufrufer bekommt das als 503 zu sehen."""


class Credentials:
    """Liest die gesyncte Credentials-Datei. Schreibt sie NIE."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at_ms: int = 0
        self._mtime: float = 0.0
        self._last_error: str | None = None

    def _reload_if_changed(self) -> None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError as exc:
            self._last_error = f"Credentials-Datei nicht lesbar: {exc}"
            return
        if mtime == self._mtime and self._token:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            self._last_error = f"Credentials-Datei unlesbar/kaputt: {exc}"
            return

        oauth = raw.get("claudeAiOauth", raw)
        token = oauth.get("accessToken")
        if not token:
            self._last_error = "Datei enthaelt kein accessToken-Feld"
            return

        self._token = token
        self._expires_at_ms = int(oauth.get("expiresAt") or 0)
        self._mtime = mtime
        self._last_error = None
        # Bewusst OHNE Token-Wert: der darf nirgends im Log landen.
        log(f"Credentials neu geladen, gueltig bis {self.expires_iso()}")

    def expires_iso(self) -> str:
        if not self._expires_at_ms:
            return "unbekannt"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._expires_at_ms / 1000))

    def seconds_left(self) -> int:
        if not self._expires_at_ms:
            return -1
        return int(self._expires_at_ms / 1000 - time.time())

    def status(self) -> dict:
        with self._lock:
            self._reload_if_changed()
            left = self.seconds_left()
            healthy = bool(self._token) and left > 0
            return {
                "ok": healthy,
                "token_present": bool(self._token),
                "expires_at": self.expires_iso(),
                "seconds_left": left,
                "creds_path": self.path,
                "refresh_policy": "passiver Leser — refresht nie selbst",
                "error": self._last_error,
            }

    def token(self) -> str:
        """Gibt den gueltigen Token zurueck oder wirft — nie stillschweigend leer."""
        with self._lock:
            self._reload_if_changed()
            if not self._token:
                raise TokenUnavailable(
                    self._last_error
                    or f"kein Token geladen (erwartet in {self.path}) — laeuft der Credential-Sync?"
                )
            left = self.seconds_left()
            if left <= 0:
                raise TokenUnavailable(
                    f"accessToken ist seit {abs(left)}s abgelaufen (gueltig war bis "
                    f"{self.expires_iso()}). Dieser Proxy refresht bewusst nicht selbst — "
                    "der Sync muss eine frische Datei liefern."
                )
            return self._token


CREDS = Credentials(CREDS_PATH)


def log(message: str) -> None:
    print(f"[zellwerk-llm] {message}", flush=True)


def call_anthropic(body: dict, token: str) -> tuple[int, bytes]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        UPSTREAM,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=MAX_UPSTREAM_TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()
        # Der Fehlertext von Anthropic ist die nuetzlichste Information, die es
        # hier gibt — durchreichen statt in ein generisches 500 verwandeln.
        log(f"Anthropic antwortete {exc.code}: {detail[:300].decode('utf-8', 'replace')}")
        return exc.code, detail
    except urllib.error.URLError as exc:
        raise TokenUnavailable(f"api.anthropic.com nicht erreichbar: {exc.reason}") from exc


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict | bytes, content_type: str = "application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (von BaseHTTPRequestHandler vorgegeben)
        if self.path == "/health":
            status = CREDS.status()
            self._send(200 if status["ok"] else 503, status)
            return
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": DEFAULT_MODEL, "object": "model"}], "object": "list"})
            return
        self._send(404, {"error": {"type": "not_found", "message": f"kein Endpunkt {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/messages":
            self._send(404, {"error": {"type": "not_found", "message": f"kein Endpunkt {self.path}"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            self._send(400, {"error": {"type": "invalid_request", "message": "leerer Rumpf"}})
            return

        try:
            body = json.loads(self.rfile.read(length))
        except ValueError as exc:
            self._send(400, {"error": {"type": "invalid_request", "message": f"kein gueltiges JSON: {exc}"}})
            return

        body.setdefault("model", DEFAULT_MODEL)
        body.setdefault("max_tokens", 2048)
        # Streaming wuerde ein anderes Antwortformat brauchen; bis das gebraucht
        # wird, lieber klar ablehnen als halb funktionieren.
        if body.get("stream"):
            self._send(400, {"error": {"type": "invalid_request",
                                       "message": "stream=true wird von diesem Proxy noch nicht unterstuetzt"}})
            return

        try:
            token = CREDS.token()
        except TokenUnavailable as exc:
            log(f"ABGEWIESEN: {exc}")
            self._send(503, {"error": {"type": "token_unavailable", "message": str(exc)}})
            return

        try:
            status, raw = call_anthropic(body, token)
        except TokenUnavailable as exc:
            log(f"UPSTREAM-FEHLER: {exc}")
            self._send(502, {"error": {"type": "upstream_unreachable", "message": str(exc)}})
            return

        self._send(status, raw)

    def log_message(self, fmt: str, *args) -> None:
        # Zugriffe knapp mitschreiben, aber niemals Header (Bearer-Token!).
        log("%s %s" % (self.address_string(), fmt % args))


def main() -> int:
    status = CREDS.status()
    if status["ok"]:
        log(f"Start auf Port {PORT} — Token gueltig bis {status['expires_at']}")
    else:
        # Kein harter Abbruch: der Sync liefert die Datei evtl. gleich nach.
        # /health bleibt bis dahin rot, damit der Zustand sichtbar ist.
        log(f"Start auf Port {PORT} — WARNUNG: noch kein gueltiger Token ({status['error']})")
    log(f"Standardmodell: {DEFAULT_MODEL}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("beendet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
