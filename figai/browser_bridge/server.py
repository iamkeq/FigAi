"""Localhost-only HTTP server the Chrome extension talks to.

Security: binds 127.0.0.1 only; every request needs the shared secret in an
X-Fig-Token header (a custom header forces a CORS preflight, which this server
never approves, so an ordinary web page can't call it); and the Host header is
checked against the loopback names to blunt DNS-rebinding. Tab titles and URLs
are untrusted input and are sanitized and length-capped on the way in."""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from figai.browser_bridge.state import BridgeState
from figai.logging_setup import get_logger

logger = get_logger("browser_bridge")

MAX_BODY_BYTES = 1_000_000
MAX_TABS = 500
MAX_TITLE_CHARS = 300
MAX_URL_CHARS = 2000
MAX_WAIT_SECONDS = 25


def sanitize_tabs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tabs: list[dict[str, Any]] = []
    for item in raw[:MAX_TABS]:
        if not isinstance(item, dict):
            continue
        tabs.append(
            {
                "id": item.get("id") if isinstance(item.get("id"), int) else None,
                "window": item.get("windowId") if isinstance(item.get("windowId"), int) else None,
                "title": str(item.get("title") or "")[:MAX_TITLE_CHARS],
                "url": str(item.get("url") or "")[:MAX_URL_CHARS],
                "active": bool(item.get("active")),
            }
        )
    return tabs


def make_handler(state: BridgeState, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        allowed_hosts: set[str] = set()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            logger.debug("bridge: " + format, *args)

        def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
            payload = json.dumps(body or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            if self.headers.get("Host", "") not in self.allowed_hosts:
                self._send(403, {"error": "bad host"})
                return False
            supplied = self.headers.get("X-Fig-Token", "")
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                self._send(401, {"error": "bad token"})
                return False
            state.touch()
            return True

        def do_POST(self) -> None:  # noqa: N802 - stdlib name
            if not self._authorized():
                return
            if urlparse(self.path).path != "/tabs":
                self._send(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY_BYTES:
                    self._send(413, {"error": "too large"})
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "bad json"})
                return
            state.set_tabs(sanitize_tabs(body.get("tabs") if isinstance(body, dict) else None))
            self._send(200, {"ok": True})

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            if parsed.path != "/commands":
                self._send(404)
                return
            try:
                wait = float(parse_qs(parsed.query).get("wait", ["0"])[0])
            except ValueError:
                wait = 0
            wait = max(0.0, min(wait, MAX_WAIT_SECONDS))
            self._send(200, {"commands": state.wait_for_commands(wait)})

        def do_OPTIONS(self) -> None:  # noqa: N802 - never approve a CORS preflight
            self._send(405)

    return Handler


def make_server(state: BridgeState, token: str, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = make_handler(state, token)
    server = ThreadingHTTPServer((host, port), handler)
    bound = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
    server.daemon_threads = True
    return server


def start_bridge_server(state: BridgeState, token: str, port: int) -> ThreadingHTTPServer:
    server = make_server(state, token, port)
    threading.Thread(target=server.serve_forever, daemon=True, name="browser-bridge").start()
    logger.info("Browser bridge listening on 127.0.0.1:%d", server.server_address[1])
    return server
