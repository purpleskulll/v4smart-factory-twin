"""Health-Endpoint auf :8000 und die --selfcheck-Prüfung (CLAUDE.md §15)."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8000


def serve(ready_flag, info) -> threading.Thread:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (Signatur der Basisklasse)
            if self.path != "/healthz":
                self.send_response(404)
                self.end_headers()
                return
            ok = ready_flag.is_set()
            body = json.dumps({"ok": ok, **(info() if ok else {})}).encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # Zugriffe nicht ins Log spülen
            return

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def selfcheck() -> int:
    """Prüft den eigenen Health-Endpoint ohne Zusatzbibliothek. 0 = ok."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=3) as s:
            s.sendall(b"GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n")
            head = s.recv(64).decode("utf-8", "replace")
        return 0 if " 200 " in head else 1
    except OSError:
        return 1
