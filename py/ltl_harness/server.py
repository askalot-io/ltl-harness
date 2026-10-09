"""Guardrail dashboard server (Python port).

Serves the bundled ui/ and the same runs/ as the JS harness — both engines'
flight records appear side by side. Stdlib only.

    GET /              the UI
    GET /api/runs      run list (newest first)
    GET /api/runs/:id  parsed flight record
    GET /api/rulebook  rulebook compiled via LTLf2DFA + live lint result

Meant to sit behind the nginx reverse proxy (see nginx site config); binds
localhost only by default.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from .engine.lint import lint_rulebook
from .engine.rulebook import load_rulebook
from .paths import RULEBOOK_PATH, RUNS_DIR, UI_DIR
from .recorder import list_runs, read_run

MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}

_rulebook_cache: dict = {}


def rulebook_payload() -> dict:
    """Compile the rulebook (cached by file mtime — MONA runs once per edit)."""
    mtime = RULEBOOK_PATH.stat().st_mtime
    if _rulebook_cache.get("mtime") != mtime:
        try:
            rb = load_rulebook(str(RULEBOOK_PATH))
            _rulebook_cache["payload"] = {
                "name": rb.name,
                "version": rb.version,
                "activities": rb.activities,
                "rules": rb.machines_json(),
                "lint": lint_rulebook(rb),
                "engine": "LTLf2DFA/MONA",
            }
        except Exception as err:  # noqa: BLE001 — surfaced to the UI
            _rulebook_cache["payload"] = {"error": str(err)}
        _rulebook_cache["mtime"] = mtime
    return _rulebook_cache["payload"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _json(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])

        if path == "/api/runs":
            return self._json(200, list_runs(RUNS_DIR))

        if path.startswith("/api/runs/"):
            records = read_run(RUNS_DIR, path[len("/api/runs/"):])
            if records is None:
                return self._json(404, {"error": "no such run"})
            return self._json(200, records)

        if path == "/api/rulebook":
            return self._json(200, rulebook_payload())

        # static UI
        rel = "index.html" if path == "/" else path.lstrip("/")
        file = (UI_DIR / rel).resolve()
        if not str(file).startswith(str(UI_DIR)) or not file.is_file():
            return self._json(404, {"error": "not found"})
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("content-type", MIME.get(file.suffix, "application/octet-stream"))
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8471"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"guardrail dashboard (python engine): http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
