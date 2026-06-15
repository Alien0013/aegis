"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from .config import Config
from .surface import SurfaceRunner
from .types import Message, new_id

_MAX_BODY_BYTES = 10 * 1024 * 1024


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


def _usage(source) -> dict[str, Any]:
    usage = source
    if not all(hasattr(usage, key) for key in ("input_tokens", "output_tokens")):
        usage = getattr(getattr(source, "budget", None), "usage", None)
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


def _message_payload(message: Message, index: int | None = None) -> dict[str, Any]:
    payload = message.to_dict()
    if index is not None:
        payload["index"] = index
    return payload


def _message_from_payload(payload: dict[str, Any]) -> Message:
    if "role" in payload:
        return Message.from_dict(payload)
    return Message.user(str(payload.get("content") or payload.get("text") or ""))


def _session_payload(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_id": session.parent_id,
        "profile": session.profile,
        "meta": session.meta,
        "todos": session.todos,
        "message_count": len(session.messages),
        "messages": [_message_payload(m, i) for i, m in enumerate(session.messages)],
    }


def _response_output(text: str) -> list[dict[str, Any]]:
    return [{
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text or ""}],
    }]


def _response_object(response_id: str, result, *, status: str = "completed") -> dict[str, Any]:
    text = getattr(result, "text", "") if result is not None else ""
    agent = getattr(result, "agent", None)
    provider = getattr(agent, "provider", None)
    session = getattr(result, "session", None)
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": getattr(provider, "model", ""),
        "output": _response_output(text),
        "output_text": text,
        "usage": _usage(getattr(result, "usage", None) or agent),
        "metadata": {
            "session_id": getattr(session, "id", ""),
            "trace_id": getattr(result, "trace_id", ""),
            "turn_id": getattr(result, "turn_id", ""),
            "run_id": getattr(result, "run_id", ""),
        },
    }


def _responses_messages(body: dict[str, Any]) -> tuple[list[Message], Message]:
    raw = body.get("messages", body.get("input", ""))
    messages: list[dict[str, Any]]
    if isinstance(raw, str):
        messages = [{"role": "user", "content": raw}]
    elif isinstance(raw, list):
        messages = []
        for item in raw:
            if isinstance(item, dict) and "role" in item:
                messages.append(item)
            else:
                messages.append({"role": "user", "content": item})
    else:
        messages = [{"role": "user", "content": str(raw or "")}]
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        messages.insert(0, {"role": "system", "content": instructions})
    return _convert(messages)


def _capabilities(config: Config) -> dict[str, Any]:
    from .providers import registry

    try:
        report = registry.provider_report(config)
    except Exception:  # noqa: BLE001
        report = {}
    return {
        "object": "capabilities",
        "server": "aegis",
        "endpoints": {
            "chat_completions": True,
            "responses": True,
            "models": True,
            "sessions": True,
            "runs": True,
            "streaming": True,
            "health": True,
        },
        "features": {
            "tools": True,
            "mcp": True,
            "sessions": True,
            "run_history": True,
            "trace_events": True,
            "cancellation": "active server-process runs",
        },
        "provider": report.get("active") or report.get("model") or {},
    }


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")
    runner = SurfaceRunner(config, include_mcp=True)
    response_store: dict[str, dict[str, Any]] = {}
    active_runs: dict[str, dict[str, Any]] = {}
    approvals: dict[str, dict[str, Any]] = {}
    state_lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _authed(self) -> bool:
            if not api_key:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {api_key}"

        def _json(self, code: int, obj: Any) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def _write_sse(self, obj: Any, *, event: str = "message") -> bool:
            try:
                if event:
                    self.wfile.write(f"event: {event}\n".encode())
                self.wfile.write(f"data: {json.dumps(obj, default=str)}\n\n".encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def _route(self) -> tuple[str, dict[str, list[str]]]:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            return path, parse_qs(parsed.query)

        def _read_json(self) -> tuple[dict[str, Any] | None, str]:
            try:
                n = int(self.headers.get("content-length", 0))
            except ValueError:
                return None, "invalid content-length"
            if n < 0:
                return None, "invalid content-length"
            if n > _MAX_BODY_BYTES:
                return None, "request body too large"
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return None, "invalid json"
            if not isinstance(body, dict):
                return None, "json body must be an object"
            return body, ""

        def _health(self, *, detailed: bool = False) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "ok": True,
                "status": "ok",
                "server": "aegis",
                "time": int(time.time()),
            }
            if detailed:
                payload.update({
                    "models": _models(config),
                    "capabilities": _capabilities(config),
                    "max_body_bytes": _MAX_BODY_BYTES,
                })
            return payload

        def _session_detail(self, session_id: str) -> tuple[int, dict[str, Any]]:
            from .session import SessionStore

            session = SessionStore().load(session_id)
            if session is None:
                return 404, {"ok": False, "error": "session not found", "id": session_id}
            return 200, {"ok": True, "session": _session_payload(session)}

        def _run_events(self, run_id: str) -> tuple[int, dict[str, Any]]:
            with state_lock:
                active = active_runs.get(run_id)
                if active is not None:
                    return 200, {"ok": True, "id": run_id, "events": list(active.get("events") or [])}
            from .runs import RunStore
            from .tracing import TraceStore

            run = RunStore().get(run_id)
            if run is None:
                return 404, {"ok": False, "error": "run not found", "id": run_id}
            trace_id = str(run.get("trace_id") or "")
            trace = TraceStore.from_config(config).get_trace(trace_id) if trace_id else None
            return 200, {"ok": True, "id": run_id, "events": (trace or {}).get("spans", []), "trace": trace}

        def _stream_run_events(self, run_id: str) -> None:
            code, payload = self._run_events(run_id)
            if code != 200:
                return self._json(code, payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            deadline = time.time() + float(config.get("server.run_events_timeout_seconds", 3600) or 3600)
            sent = 0
            while True:
                with state_lock:
                    active = active_runs.get(run_id)
                    if active is None:
                        events = list(payload.get("events") or [])
                        status = "completed"
                        detail = {"id": run_id, "status": status}
                    else:
                        events = list(active.get("events") or [])
                        status = str(active.get("status") or "running")
                        detail = {k: v for k, v in active.items() if k not in {"agent", "thread", "events"}}
                for event in events[sent:]:
                    if not self._write_sse(event, event="event"):
                        return
                sent = len(events)
                if status in {"completed", "error", "cancelled"} or active is None:
                    self._write_sse(detail, event="done")
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                if time.time() >= deadline:
                    self._write_sse({"id": run_id, "status": "timeout"}, event="timeout")
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                time.sleep(0.2)

        def _run_detail(self, run_id: str) -> tuple[int, dict[str, Any]]:
            with state_lock:
                active = active_runs.get(run_id)
                if active is not None:
                    return 200, {"ok": True, "run": {k: v for k, v in active.items()
                                                     if k not in {"agent", "thread"}}}
            from .runs import RunStore

            run = RunStore().get(run_id)
            if run is None:
                return 404, {"ok": False, "error": "run not found", "id": run_id}
            return 200, {"ok": True, "run": run}

        def do_GET(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, query = self._route()
            if path == "/health":
                return self._json(200, self._health())
            if path == "/health/detailed":
                return self._json(200, self._health(detailed=True))
            if path == "/v1/models":
                return self._json(200, {"object": "list", "data": _models(config)})
            if path == "/v1/capabilities":
                return self._json(200, _capabilities(config))
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
                with state_lock:
                    response = response_store.get(rid)
                if response is None:
                    return self._json(404, {"error": "response not found", "id": rid})
                return self._json(200, response)
            if path == "/v1/runs":
                from .runs import RunStore

                limit = int((query.get("limit") or ["50"])[0] or 50)
                return self._json(200, {"object": "list", "data": RunStore().list(limit=max(1, min(limit, 500)))})
            if path.startswith("/v1/runs/") and path.endswith("/events"):
                run_id = path.split("/")[-2]
                stream = (
                    "text/event-stream" in self.headers.get("Accept", "")
                    or str((query.get("stream") or [""])[0]).lower() in {"1", "true", "yes"}
                )
                if stream:
                    return self._stream_run_events(run_id)
                code, payload = self._run_events(run_id)
                return self._json(code, payload)
            if path.startswith("/v1/runs/") and path.endswith("/approval"):
                run_id = path.split("/")[-2]
                with state_lock:
                    pending = [dict(v, event=None) for v in approvals.values()
                               if v.get("run_id") == run_id and not v.get("answered")]
                return self._json(200, {"ok": True, "run_id": run_id, "pending": pending})
            if path.startswith("/v1/runs/"):
                code, payload = self._run_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            if path == "/api/sessions":
                from .session import SessionStore

                limit = int((query.get("limit") or ["100"])[0] or 100)
                return self._json(200, {"ok": True, "sessions": SessionStore().list(max(1, min(limit, 1000)))})
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[-2]
                code, payload = self._session_detail(session_id)
                if code != 200:
                    return self._json(code, payload)
                session = payload["session"]
                return self._json(200, {"ok": True, "id": session_id, "messages": session["messages"]})
            if path.startswith("/api/sessions/"):
                code, payload = self._session_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
                with state_lock:
                    existed = response_store.pop(rid, None) is not None
                return self._json(200 if existed else 404, {"ok": existed, "id": rid})
            if path.startswith("/api/sessions/"):
                from .session import SessionStore

                sid = path.rsplit("/", 1)[-1]
                ok = SessionStore().delete(sid)
                return self._json(200 if ok else 404, {"ok": ok, "id": sid})
            return self._json(404, {"error": "not found"})

        def do_PATCH(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            body, error = self._read_json()
            if error:
                return self._json(413 if error == "request body too large" else 400, {"error": error})
            if path.startswith("/api/sessions/"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.rsplit("/", 1)[-1]
                session = store.load(sid)
                if session is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                if "title" in body:
                    session.title = str(body.get("title") or session.title)
                if isinstance(body.get("meta"), dict):
                    session.meta.update(body["meta"])
                if isinstance(body.get("todos"), list):
                    session.todos = body["todos"]
                store.save(session)
                return self._json(200, {"ok": True, "session": _session_payload(session)})
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            body, error = self._read_json()
            if error:
                return self._json(413 if error == "request body too large" else 400, {"error": error})
            if path == "/v1/chat/completions":
                return self._post_chat_completion(body)
            if path == "/v1/responses":
                return self._post_response(body)
            if path.startswith("/v1/responses/") and path.endswith("/cancel"):
                rid = path.split("/")[-2]
                with state_lock:
                    response = response_store.get(rid)
                if response is not None:
                    response["status"] = "cancelled"
                    return self._json(200, response)
                return self._json(404, {"error": "response not found", "id": rid})
            if path == "/v1/runs":
                return self._post_run(body)
            if path.startswith("/v1/runs/") and path.endswith(("/stop", "/cancel")):
                run_id = path.split("/")[-2]
                return self._stop_run(run_id)
            if path.startswith("/v1/runs/") and path.endswith("/approval"):
                run_id = path.split("/")[-2]
                return self._post_approval(run_id, body)
            if path == "/api/sessions":
                from .session import Session, SessionStore

                session = Session.create(str(body.get("title") or "api session"))
                if isinstance(body.get("meta"), dict):
                    session.meta.update(body["meta"])
                SessionStore().save(session)
                return self._json(201, {"ok": True, "session": _session_payload(session)})
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.split("/")[-2]
                session = store.load(sid)
                if session is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                msg = _message_from_payload(body)
                session.messages.append(msg)
                store.save(session)
                return self._json(200, {"ok": True, "message": _message_payload(msg, len(session.messages) - 1)})
            if path.startswith("/api/sessions/") and path.endswith("/fork"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.split("/")[-2]
                parent = store.load(sid)
                if parent is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                child = store.fork(parent, carry_summary=bool(body.get("carry_summary", True)))
                if body.get("title"):
                    child.title = str(body["title"])
                    store.save(child)
                return self._json(201, {"ok": True, "session": _session_payload(child)})
            if path.startswith("/api/sessions/") and path.endswith(("/chat", "/chat/stream")):
                session_id = path.split("/")[-2]
                stream = path.endswith("/chat/stream") or bool(body.get("stream"))
                return self._post_session_chat(session_id, body, stream=stream)
            return self._json(404, {"error": "not found"})

        def _post_chat_completion(self, body: dict[str, Any]) -> None:
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
                    "usage": _usage(getattr(result, "usage", None) or result.agent),
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
                     "usage": _usage(getattr(result, "usage", None) or result.agent),
                     "metadata": {
                         "session_id": result.session.id,
                         "trace_id": result.trace_id,
                         "run_id": result.run_id,
                     }}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def _post_response(self, body: dict[str, Any]) -> None:
            history, last_user = _responses_messages(body)
            response_id = new_id("resp")
            model = body.get("model")
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            result = runner.run_prompt(
                last_user,
                session_id=metadata.get("session_id") or body.get("session_id"),
                history=history,
                model=model,
                provider_name=metadata.get("provider") or body.get("provider"),
                cwd=metadata.get("cwd") or body.get("cwd"),
                stream=bool(body.get("stream", False)),
                surface="serve",
                meta={"request_id": response_id, "api": "responses"},
            )
            response = _response_object(response_id, result)
            with state_lock:
                response_store[response_id] = response
            return self._json(200, response)

        def _post_session_chat(self, session_id: str, body: dict[str, Any], *, stream: bool = False) -> None:
            from .session import SessionStore

            store = SessionStore()
            session = store.load(session_id)
            if session is None:
                return self._json(404, {"ok": False, "error": "session not found", "id": session_id})
            prompt = body.get("prompt", body.get("input", body.get("message", "")))
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def emit(ev: dict[str, Any]) -> None:
                    try:
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                result = runner.run_prompt(
                    str(prompt),
                    session=session,
                    model=body.get("model"),
                    provider_name=body.get("provider"),
                    cwd=body.get("cwd"),
                    surface="serve",
                    stream=True,
                    on_event=emit,
                )
                emit({"type": "done", "text": result.text, "session_id": result.session.id,
                      "run_id": result.run_id, "trace_id": result.trace_id})
                self.wfile.write(b"data: [DONE]\n\n")
                return
            result = runner.run_prompt(
                str(prompt),
                session=session,
                model=body.get("model"),
                provider_name=body.get("provider"),
                cwd=body.get("cwd"),
                surface="serve",
                stream=False,
            )
            return self._json(200, {
                "ok": True,
                "id": result.session.id,
                "message": _message_payload(getattr(result, "message", Message.assistant(result.text))),
                "text": result.text,
                "run_id": result.run_id,
                "trace_id": result.trace_id,
            })

        def _post_run(self, body: dict[str, Any]) -> None:
            from .session import SessionStore

            run_id = new_id("run")
            prompt = str(body.get("prompt", body.get("input", "")) or "")
            session_id = str(body.get("session_id") or "") or None
            title = str(body.get("title") or prompt[:80] or run_id)
            store = SessionStore()
            session = runner.load_or_create_session(session_id, title=title, surface="serve", meta={
                "server_run_id": run_id,
            })
            record = {
                "id": run_id,
                "object": "run",
                "status": "queued",
                "created_at": int(time.time()),
                "session_id": session.id,
                "events": [],
                "result": "",
                "error": "",
                "trace_id": "",
                "surface_run_id": "",
            }
            with state_lock:
                active_runs[run_id] = record

            def approver(question: str) -> bool:
                approval_id = new_id("approval")
                event = threading.Event()
                pending = {
                    "id": approval_id,
                    "run_id": run_id,
                    "prompt": question,
                    "answered": False,
                    "approved": False,
                    "event": event,
                    "created_at": int(time.time()),
                }
                with state_lock:
                    approvals[approval_id] = pending
                timeout = float(config.get("server.approval_timeout_seconds", 3600) or 3600)
                event.wait(max(0.1, timeout))
                with state_lock:
                    return bool(approvals.get(approval_id, {}).get("approved"))

            def worker() -> None:
                try:
                    agent = runner.make_agent(
                        session=session,
                        model=body.get("model"),
                        provider_name=body.get("provider"),
                        cwd=body.get("cwd"),
                        approver=approver,
                    )
                    with state_lock:
                        active_runs[run_id]["status"] = "running"
                        active_runs[run_id]["agent"] = agent

                    def emit(ev: dict[str, Any]) -> None:
                        with state_lock:
                            active_runs.get(run_id, {}).setdefault("events", []).append(dict(ev))

                    result = runner.run_prompt(
                        prompt,
                        session=session,
                        agent=agent,
                        surface="serve",
                        meta={"server_run_id": run_id},
                        stream=bool(body.get("stream", False)),
                        on_event=emit,
                    )
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            rec.update({
                                "status": "completed",
                                "result": result.text,
                                "trace_id": result.trace_id,
                                "surface_run_id": result.run_id,
                                "session_id": result.session.id,
                            })
                except Exception as exc:  # noqa: BLE001
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            rec.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})

            thread = threading.Thread(target=worker, daemon=True, name=f"aegis-api-run-{run_id}")
            with state_lock:
                active_runs[run_id]["thread"] = thread
            thread.start()
            return self._json(202, {k: v for k, v in record.items() if k not in {"agent", "thread"}})

        def _stop_run(self, run_id: str) -> None:
            with state_lock:
                rec = active_runs.get(run_id)
                agent = (rec or {}).get("agent")
            if rec is None:
                return self._json(404, {"ok": False, "error": "active run not found", "id": run_id})
            if agent is not None:
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                elif getattr(agent, "cancel_event", None) is not None:
                    agent.cancel_event.set()
            with state_lock:
                rec["status"] = "cancelling"
            return self._json(200, {"ok": True, "id": run_id, "status": "cancelling"})

        def _post_approval(self, run_id: str, body: dict[str, Any]) -> None:
            approval_id = str(body.get("approval_id") or body.get("id") or "")
            approved = bool(body.get("approved", body.get("approve", False)))
            with state_lock:
                if approval_id:
                    pending = approvals.get(approval_id)
                else:
                    pending = next((v for v in approvals.values()
                                    if v.get("run_id") == run_id and not v.get("answered")), None)
                if pending is None:
                    return self._json(404, {"ok": False, "error": "approval not found", "run_id": run_id})
                pending["approved"] = approved
                pending["answered"] = True
                event = pending.get("event")
            if event is not None:
                event.set()
            return self._json(200, {"ok": True, "approval_id": pending["id"], "approved": approved})

    return Handler


def make_app(config: Config) -> web.Application:
    """Build the aiohttp OpenAI-compatible API adapter used by ``aegis serve``.

    ``make_handler`` remains for in-process tests and embedders; this adapter
    preserves the same route behavior by executing that handler behind aiohttp's
    transport instead of Python's stdlib HTTP server.
    """
    handler_cls = make_handler(config)

    async def dispatch(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        adapter = object.__new__(handler_cls)
        adapter.path = request.rel_url.raw_path_qs
        adapter.headers = request.headers
        adapter.rfile = BytesIO(body)
        adapter.wfile = BytesIO()
        adapter._aegis_status = 200
        adapter._aegis_headers: list[tuple[str, str]] = []

        def send_response(code: int, message: str | None = None) -> None:  # noqa: ARG001
            adapter._aegis_status = int(code)

        def send_header(name: str, value: str) -> None:
            adapter._aegis_headers.append((str(name), str(value)))

        def end_headers() -> None:
            return None

        adapter.send_response = send_response
        adapter.send_header = send_header
        adapter.end_headers = end_headers

        method = request.method.upper()
        func = getattr(adapter, f"do_{method}", None)
        if func is None:
            return web.json_response({"error": "method not allowed"}, status=405)
        await asyncio.to_thread(func)
        headers = {name: value for name, value in adapter._aegis_headers}
        return web.Response(status=adapter._aegis_status, headers=headers, body=adapter.wfile.getvalue())

    app = web.Application(client_max_size=_MAX_BODY_BYTES)
    app.router.add_route("*", "/{tail:.*}", dispatch)
    return app


def serve(config: Config, host: str = "127.0.0.1", port: int = 8790) -> None:
    print(f"AEGIS OpenAI-compatible aiohttp API on http://{host}:{port}/v1  (Ctrl+C to stop)")
    try:
        web.run_app(make_app(config), host=host, port=port, print=None)
    except KeyboardInterrupt:
        print("\nserver stopped.")
