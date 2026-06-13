"""Test helper: run the FastAPI dashboard on a real loopback server in a thread.

The dashboard's HTTP surface used to be served by a hand-rolled BaseHTTPRequestHandler
(`aegis.dashboard.make_handler`). That layer was removed once the FastAPI backend
(`aegis.dashboard_fastapi`) became the only one `aegis dashboard` serves. These tests
still exercise the same `/api/*` and `/events` routes — they just point at the live
backend now. `serve_app(cfg)` returns an object with the same `.shutdown()` method the
old `ThreadingHTTPServer` had, so test bodies (which use `http.client`) are unchanged.
"""

from __future__ import annotations

import socket
import threading
import time


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ThreadedServer:
    def __init__(self, server, thread):
        self._server = server
        self._thread = thread

    def shutdown(self):
        self._server.should_exit = True
        self._thread.join(timeout=5)


def serve_app(config, *, timeout: float = 5.0):
    """Start `create_app(config)` under uvicorn on a free loopback port.

    Returns (server, port); `server.shutdown()` stops it. Loopback bind + no token
    means the dashboard's peer check authorizes the local test client, matching the
    old no-auth handler behaviour.
    """
    import uvicorn

    from aegis.dashboard_fastapi import create_app

    port = _free_port()
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while not getattr(server, "started", False) and time.time() < deadline:
        time.sleep(0.02)
    return _ThreadedServer(server, thread), port
