"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .surface import SurfaceRunner
from .types import Message, new_id


def _content(value: Any) -> tuple[str, list[str]]:
    """OpenAI content string/parts -> AEGIS text + image references."""
    if isinstance(value, str):
        return value, []
    if not isinstance(value, list):
        return "" if value is None else str(value), []
    texts: list[str] = []
    images: list[str] = []
    for part in value:
        if not isinstance(part, dict):
            texts.append(str(part))
            continue
        ptype = part.get("type")
        if ptype in ("text", "input_text"):
            texts.append(str(part.get("text", "")))
        elif ptype in ("image_url", "input_image"):
            image = part.get("image_url") or part.get("image")
            if isinstance(image, dict):
                image = image.get("url")
            if image:
                images.append(str(image))
    return "\n".join(t for t in texts if t), images


def _convert(messages: list[dict]) -> tuple[list[Message], Message]:
    """Return (history_without_last_user, last_user_message)."""
    internal: list[Message] = []
    for m in messages:
        role = str(m.get("role") or "user")
        text, images = _content(m.get("content", ""))
        if role in ("system", "developer"):
            if text:
                internal.append(Message.user(f"<{role}_instructions>\n{text}\n</{role}_instructions>"))
        elif role == "assistant":
            internal.append(Message.assistant(text))
        elif role == "tool":
            internal.append(Message(
                role="tool",
                content=text,
                tool_call_id=m.get("tool_call_id"),
                name=m.get("name"),
            ))
        else:
            internal.append(Message.user(text, images=images))
    last_user = Message.user("")
    for i in range(len(internal) - 1, -1, -1):
        if internal[i].role == "user":
            last_user = internal.pop(i)
            break
    return internal, last_user


def _usage(agent) -> dict[str, Any]:
    usage = getattr(getattr(agent, "budget", None), "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": int(getattr(usage, "cache_read", 0) or 0)},
        "completion_tokens_details": {},
    }


def _models(config: Config) -> list[dict[str, Any]]:
    from .providers import registry

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    def add(model: str | None, provider: str = "") -> None:
        mid = str(model or "").strip()
        if not mid or mid in seen:
            return
        seen.add(mid)
        row = {"id": mid, "object": "model", "owned_by": provider or "aegis"}
        if provider:
            row["provider"] = provider
        rows.append(row)

    add(config.get("model.default"), config.get("model.provider", ""))
    for provider in registry.list_providers(config):
        for model in registry.known_models_for(provider, config):
            add(model, provider)
    return rows


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    keys = ("type", "name", "tool_name", "status", "summary", "preview", "is_error", "duration_ms")
    return {key: event[key] for key in keys if key in event}


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")
    runner = SurfaceRunner(config, include_mcp=True)

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
                return self._json(200, {"object": "list", "data": _models(config)})
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
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or None
            )
            provider_name = (
                metadata.get("provider")
                or body.get("provider")
                or self.headers.get("X-Aegis-Provider")
                or None
            )
            cwd = (
                metadata.get("cwd")
                or body.get("cwd")
                or self.headers.get("X-Aegis-Cwd")
                or None
            )

            cid = new_id("chatcmpl")

            if not stream:
                result = runner.run_prompt(
                    last_user,
                    session_id=session_id,
                    history=history,
                    model=model,
                    provider_name=provider_name,
                    cwd=cwd,
                    stream=False,
                    surface="serve",
                    meta={"request_id": cid},
                )
                return self._json(200, {
                    "id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": result.agent.provider.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text},
                                 "finish_reason": "stop"}],
                    "usage": _usage(result.agent),
                    "metadata": {
                        "session_id": result.session.id,
                        "trace_id": result.trace_id,
                        "run_id": result.run_id,
                    },
                })

            # streaming
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def emit(e: dict) -> None:
                if e.get("type") == "assistant_delta":
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model or config.get("model.default", ""),
                             "choices": [{"index": 0, "delta": {"content": e["text"]}}]}
                else:
                    meta = _event_metadata(e)
                    if not meta:
                        return
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model or config.get("model.default", ""),
                             "choices": [{"index": 0, "delta": {}}],
                             "metadata": {"event": meta}}
                try:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            result = runner.run_prompt(
                last_user,
                session_id=session_id,
                history=history,
                model=model,
                provider_name=provider_name,
                cwd=cwd,
                stream=True,
                surface="serve",
                meta={"request_id": cid},
                on_event=emit,
            )
            final = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": result.agent.provider.model,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "metadata": {
                         "session_id": result.session.id,
                         "trace_id": result.trace_id,
                         "run_id": result.run_id,
                     }}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    return Handler


def serve(config: Config, host: str = "127.0.0.1", port: int = 8790) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(config))
    print(f"AEGIS OpenAI-compatible API on http://{host}:{port}/v1  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped.")
