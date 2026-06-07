"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agent.agent import Agent
from .config import Config
from .providers import list_providers
from .session import Session
from .types import Message, new_id


def _convert(messages: list[dict]) -> tuple[list[Message], str]:
    """Return (history_without_last_user, last_user_text)."""
    internal = [Message.from_dict({"role": m["role"], "content": m.get("content", "") or ""})
                for m in messages if m.get("role") != "system"]
    last_user = ""
    for m in reversed(internal):
        if m.role == "user":
            last_user = m.content
            internal.remove(m)
            break
    return internal, last_user


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _authed(self) -> bool:
            if not api_key:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {api_key}"

        def _json(self, code: int, obj: dict) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") == "/v1/models":
                models = [{"id": p, "object": "model", "owned_by": "aegis"} for p in list_providers()]
                return self._json(200, {"object": "list", "data": models})
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            history, last_user = _convert(body.get("messages", []))
            model = body.get("model")
            stream = bool(body.get("stream"))

            session = Session.create()
            session.messages = history
            agent = Agent.create(config, session=session, model=model)
            cid = new_id("chatcmpl")

            if not stream:
                result = agent.run(last_user)
                return self._json(200, {
                    "id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": agent.provider.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": result.content},
                                 "finish_reason": "stop"}],
                })

            # streaming
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def emit(e: dict) -> None:
                if e.get("type") == "assistant_delta":
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": agent.provider.model,
                             "choices": [{"index": 0, "delta": {"content": e["text"]}}]}
                    try:
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            agent.run(last_user, emit)
            self.wfile.write(b"data: [DONE]\n\n")

    return Handler


def serve(config: Config, host: str = "127.0.0.1", port: int = 8790) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(config))
    print(f"AEGIS OpenAI-compatible API on http://{host}:{port}/v1  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped.")
