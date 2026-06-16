"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from . import config as cfg_paths
from .config import Config
from .surface import SurfaceRunner
from .types import Message, new_id

_MAX_BODY_BYTES = 10 * 1024 * 1024


def _coerce_request_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": (
            "Authorization, Content-Type, Accept, OpenAI-Beta, "
            "X-Aegis-Session, X-Aegis-Provider, X-Aegis-Cwd, "
            "X-Hermes-Session-Id, X-Hermes-Session-Key"
        ),
        "Access-Control-Max-Age": "86400",
    }


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, default=str).encode()


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

    by_id: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    def add(model: str | None, provider: str = "") -> None:
        mid = str(model or "").strip()
        if not mid:
            return
        if mid in by_id:
            row = by_id[mid]
            providers = row.setdefault("providers", [])
            if provider and provider not in providers:
                providers.append(provider)
            if provider and not row.get("provider"):
                row["provider"] = provider
                row["owned_by"] = provider
            return
        row = {"id": mid, "object": "model", "owned_by": provider or "aegis"}
        if provider:
            row["provider"] = provider
            row["providers"] = [provider]
        else:
            row["providers"] = []
        by_id[mid] = row
        rows.append(row)

    add(config.get("model.default"), config.get("model.provider", ""))
    for row in registry.model_inventory(config):
        add(row.get("id"), row.get("provider", ""))
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


def _response_object(
    response_id: str,
    result,
    *,
    status: str = "completed",
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = getattr(result, "text", "") if result is not None else ""
    agent = getattr(result, "agent", None)
    provider = getattr(agent, "provider", None)
    session = getattr(result, "session", None)
    metadata = {
        "session_id": getattr(session, "id", ""),
        "trace_id": getattr(result, "trace_id", ""),
        "turn_id": getattr(result, "turn_id", ""),
        "run_id": getattr(result, "run_id", ""),
    }
    metadata.update(metadata_extra or {})
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": getattr(provider, "model", ""),
        "output": _response_output(text),
        "output_text": text,
        "usage": _usage(getattr(result, "usage", None) or agent),
        "metadata": metadata,
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


def _skills_payload(config: Config) -> dict[str, Any]:
    from .skills import SkillsLoader

    rows = []
    for skill in sorted(SkillsLoader(config).available(), key=lambda s: s.name):
        rows.append({
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.path),
            "source": getattr(skill, "source", ""),
            "toolsets": list(getattr(skill, "toolsets", []) or []),
        })
    return {"object": "list", "data": rows}


def _toolsets_payload(config: Config) -> dict[str, Any]:
    from .dashboard import _dashboard_toolsets

    return {"object": "list", "data": _dashboard_toolsets(config)}


def _job_payload(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "object": "job",
        "name": getattr(job, "name", "") or "",
        "schedule": job.schedule,
        "prompt": job.prompt,
        "enabled": bool(job.enabled),
        "channel": job.channel,
        "deliver": job.deliver,
        "script": job.script,
        "skills": list(job.skills or []),
        "context_from": list(getattr(job, "context_from", []) or []),
        "no_agent": bool(job.no_agent),
        "model": getattr(job, "model", "") or "",
        "enabled_toolsets": list(getattr(job, "enabled_toolsets", []) or []),
        "workdir": getattr(job, "workdir", "") or "",
        "state": job.state,
        "last_error": job.last_error,
        "last_run": job.last_run,
        "next_run": job.next_run,
        "run_count": int(getattr(job, "run_count", 0) or 0),
        "max_runs": int(getattr(job, "max_runs", 0) or 0),
        "runs": list(getattr(job, "runs", []) or []),
    }


def _coerce_csv_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, list):
        raw = value
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _conversation_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("conversation_id")
    text = str(value or "").strip()
    return text


class ResponseStore:
    """Small SQLite-backed store for the OpenAI-compatible Responses surface."""

    def __init__(self, config: Config):
        self.path = cfg_paths.sub("server_responses.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, "
                "status TEXT NOT NULL, body TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=30)

    def put(self, response: dict[str, Any]) -> None:
        body = json.dumps(response, default=str)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO responses (id, created_at, status, body) VALUES (?, ?, ?, ?)",
                (
                    str(response.get("id") or ""),
                    int(response.get("created_at") or time.time()),
                    str(response.get("status") or ""),
                    body,
                ),
            )

    def get(self, response_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT body FROM responses WHERE id = ?", (response_id,)).fetchone()
        if row is None:
            return None
        try:
            body = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def delete(self, response_id: str) -> bool:
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            return cur.rowcount > 0


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")
    runner = SurfaceRunner(config, include_mcp=True)
    response_store = ResponseStore(config)
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
            for name, value in _cors_headers().items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(_json_bytes(obj))

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

        def _send_sse_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            for name, value in _cors_headers().items():
                self.send_header(name, value)
            self.end_headers()

        def _job_detail(self, job_id: str) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore

            job = CronStore().get(job_id)
            if job is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            return 200, {"ok": True, "job": _job_payload(job)}

        def _create_job(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, _scan_cron_prompt

            if not body.get("schedule") or not body.get("prompt"):
                return 400, {"ok": False, "error": "schedule and prompt are required"}
            prompt_error = _scan_cron_prompt(str(body.get("prompt") or ""))
            if prompt_error:
                return 400, {"ok": False, "error": prompt_error}
            store = CronStore()
            job = store.add(
                str(body["schedule"]),
                str(body["prompt"]),
                name=str(body.get("name") or ""),
                channel=str(body.get("channel") or ""),
                script=str(body.get("script") or ""),
                skills=_coerce_csv_list(body.get("skills")),
                context_from=_coerce_csv_list(body.get("context_from")),
                deliver=str(body.get("deliver") or ""),
                no_agent=_coerce_request_bool(body.get("no_agent"), False),
                model=str(body.get("model") or ""),
                enabled_toolsets=_coerce_csv_list(body.get("enabled_toolsets") or body.get("toolsets")),
                workdir=str(body.get("workdir") or ""),
                max_runs=int(body.get("max_runs") or 0),
            )
            return 201, {"ok": True, "id": job.id, "job": _job_payload(job)}

        def _update_job(self, job_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, _scan_cron_prompt

            updates = {key: body[key] for key in (
                "schedule", "prompt", "name", "channel", "enabled", "script", "skills", "context_from",
                "deliver", "no_agent", "max_runs", "model", "enabled_toolsets", "workdir",
            ) if key in body}
            if "toolsets" in body and "enabled_toolsets" not in updates:
                updates["enabled_toolsets"] = body["toolsets"]
            if "prompt" in updates:
                prompt_error = _scan_cron_prompt(str(updates.get("prompt") or ""))
                if prompt_error:
                    return 400, {"ok": False, "error": prompt_error}
            if "skills" in updates:
                updates["skills"] = _coerce_csv_list(updates["skills"])
            if "context_from" in updates:
                updates["context_from"] = _coerce_csv_list(updates["context_from"])
            if "enabled_toolsets" in updates:
                updates["enabled_toolsets"] = _coerce_csv_list(updates["enabled_toolsets"])
            if "enabled" in updates:
                updates["enabled"] = _coerce_request_bool(updates["enabled"], True)
            if "no_agent" in updates:
                updates["no_agent"] = _coerce_request_bool(updates["no_agent"], False)
            job = CronStore().update(job_id, **updates)
            if job is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            return 200, {"ok": True, "id": job.id, "job": _job_payload(job)}

        def _run_job_now(self, job_id: str) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, build_delivery_sink, run_job

            store = CronStore()
            if store.get(job_id) is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            sink = build_delivery_sink(config, verbose=False)
            return 200, run_job(config, job_id, sink=sink, store=store, verbose=False)

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
            self._send_sse_headers()

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

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            for name, value in _cors_headers().items():
                self.send_header(name, value)
            self.end_headers()

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
            if path in {"/health", "/v1/health"}:
                return self._json(200, self._health())
            if path in {"/health/detailed", "/v1/health/detailed"}:
                return self._json(200, self._health(detailed=True))
            if path == "/v1/models":
                return self._json(200, {"object": "list", "data": _models(config)})
            if path == "/v1/capabilities":
                return self._json(200, _capabilities(config))
            if path == "/v1/skills":
                return self._json(200, _skills_payload(config))
            if path == "/v1/toolsets":
                return self._json(200, _toolsets_payload(config))
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
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
                    or _coerce_request_bool((query.get("stream") or [None])[0], False)
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
            if path == "/api/jobs":
                from .cron import CronStore

                return self._json(200, {"ok": True, "object": "list",
                                        "data": [_job_payload(job) for job in CronStore().list()]})
            if path.startswith("/api/jobs/"):
                code, payload = self._job_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
                existed = response_store.delete(rid)
                return self._json(200 if existed else 404, {"ok": existed, "id": rid})
            if path.startswith("/api/jobs/"):
                from .cron import CronStore

                job_id = path.rsplit("/", 1)[-1]
                ok = CronStore().remove(job_id)
                return self._json(200 if ok else 404, {"ok": ok, "id": job_id})
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
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                code, payload = self._update_job(job_id, body)
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_PUT(self):  # noqa: N802
            return self.do_PATCH()

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
                response = response_store.get(rid)
                if response is not None:
                    response["status"] = "cancelled"
                    response_store.put(response)
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
                child = store.fork(parent, carry_summary=_coerce_request_bool(body.get("carry_summary"), True))
                if body.get("title"):
                    child.title = str(body["title"])
                    store.save(child)
                return self._json(201, {"ok": True, "session": _session_payload(child)})
            if path.startswith("/api/sessions/") and path.endswith(("/chat", "/chat/stream")):
                session_id = path.split("/")[-2]
                stream = path.endswith("/chat/stream") or _coerce_request_bool(body.get("stream"), False)
                return self._post_session_chat(session_id, body, stream=stream)
            if path == "/api/jobs":
                code, payload = self._create_job(body)
                return self._json(code, payload)
            if path.startswith("/api/jobs/") and path.endswith(("/run", "/trigger")):
                job_id = path.split("/")[-2]
                code, payload = self._run_job_now(job_id)
                return self._json(code, payload)
            if path.startswith("/api/jobs/") and path.endswith(("/pause", "/resume")):
                from .cron import CronStore

                parts = path.split("/")
                job_id = parts[-2]
                enabled = parts[-1] == "resume"
                ok = CronStore().set_enabled(job_id, enabled)
                code, payload = self._job_detail(job_id) if ok else (404, {"ok": False, "error": "job not found", "id": job_id})
                if ok:
                    payload["ok"] = True
                    payload["paused"] = not enabled
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def _post_chat_completion(self, body: dict[str, Any]) -> None:
            history, last_user = _convert(body.get("messages", []))
            model = body.get("model")
            stream = _coerce_request_bool(body.get("stream"), False)
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or self.headers.get("X-Hermes-Session-Id")
                or self.headers.get("X-Hermes-Session-Key")
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
            self._send_sse_headers()

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
            metadata = dict(metadata)
            previous_id = str(body.get("previous_response_id") or "").strip()
            previous = response_store.get(previous_id) if previous_id else None
            if previous is not None:
                prev_meta = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
                if not metadata.get("session_id") and not body.get("session_id"):
                    inherited_session = prev_meta.get("session_id")
                    if inherited_session:
                        metadata["session_id"] = inherited_session
                prev_text = str(previous.get("output_text") or "")
                if prev_text:
                    history.append(Message.assistant(prev_text))
            conversation = _conversation_id(body.get("conversation"))
            if conversation:
                metadata["conversation"] = conversation
                if not metadata.get("session_id") and not body.get("session_id"):
                    metadata["session_id"] = f"conversation:{conversation}"
            if previous_id:
                metadata["previous_response_id"] = previous_id
            store_response = _coerce_request_bool(body.get("store"), True)
            stream = _coerce_request_bool(body.get("stream"), False)
            session_id = metadata.get("session_id") or body.get("session_id")
            provider_name = metadata.get("provider") or body.get("provider")
            cwd = metadata.get("cwd") or body.get("cwd")
            if stream:
                self._send_sse_headers()
                self._write_sse({
                    "type": "response.created",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "created_at": int(time.time()),
                        "status": "in_progress",
                        "metadata": metadata,
                    },
                }, event="response.created")

                def emit(e: dict[str, Any]) -> None:
                    if e.get("type") == "assistant_delta":
                        self._write_sse({
                            "type": "response.output_text.delta",
                            "response_id": response_id,
                            "delta": str(e.get("text") or ""),
                        }, event="response.output_text.delta")
                        return
                    meta = _event_metadata(e)
                    if meta:
                        self._write_sse({
                            "type": "aegis.event",
                            "response_id": response_id,
                            "event": meta,
                        }, event="aegis.event")

                result = runner.run_prompt(
                    last_user,
                    session_id=session_id,
                    history=history,
                    model=model,
                    provider_name=provider_name,
                    cwd=cwd,
                    stream=True,
                    surface="serve",
                    meta={"request_id": response_id, "api": "responses"},
                    on_event=emit,
                )
                response = _response_object(response_id, result, metadata_extra=metadata)
                response["store"] = store_response
                if store_response:
                    response_store.put(response)
                self._write_sse({
                    "type": "response.completed",
                    "response": response,
                }, event="response.completed")
                self.wfile.write(b"data: [DONE]\n\n")
                return
            result = runner.run_prompt(
                last_user,
                session_id=session_id,
                history=history,
                model=model,
                provider_name=provider_name,
                cwd=cwd,
                stream=False,
                surface="serve",
                meta={"request_id": response_id, "api": "responses"},
            )
            response = _response_object(response_id, result, metadata_extra=metadata)
            response["store"] = store_response
            if store_response:
                response_store.put(response)
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
                        stream=_coerce_request_bool(body.get("stream"), False),
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
            approved = _coerce_request_bool(body.get("approved", body.get("approve")), False)
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

    class _AiohttpWFile:
        def __init__(self, loop: asyncio.AbstractEventLoop, chunks: asyncio.Queue[bytes]):
            self._loop = loop
            self._chunks = chunks
            self._buffer = bytearray()

        def write(self, data: bytes | bytearray | memoryview) -> int:
            payload = bytes(data)
            if not payload:
                return 0
            self._buffer.extend(payload)
            self._loop.call_soon_threadsafe(self._chunks.put_nowait, payload)
            return len(payload)

        def flush(self) -> None:
            return None

        def getvalue(self) -> bytes:
            return bytes(self._buffer)

    async def dispatch(request: web.Request) -> web.StreamResponse:
        if request.method.upper() == "OPTIONS":
            return web.Response(status=204, headers=_cors_headers())
        body = await request.read()
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[bytes] = asyncio.Queue()
        headers_ready = asyncio.Event()
        adapter = object.__new__(handler_cls)
        adapter.path = request.rel_url.raw_path_qs
        adapter.headers = request.headers
        adapter.rfile = BytesIO(body)
        adapter.wfile = _AiohttpWFile(loop, chunks)
        adapter._aegis_status = 200
        adapter._aegis_headers: list[tuple[str, str]] = []
        adapter._aegis_headers_sent = False

        def send_response(code: int, message: str | None = None) -> None:  # noqa: ARG001
            adapter._aegis_status = int(code)

        def send_header(name: str, value: str) -> None:
            adapter._aegis_headers.append((str(name), str(value)))

        def end_headers() -> None:
            adapter._aegis_headers_sent = True
            loop.call_soon_threadsafe(headers_ready.set)
            return None

        adapter.send_response = send_response
        adapter.send_header = send_header
        adapter.end_headers = end_headers

        method = request.method.upper()
        func = getattr(adapter, f"do_{method}", None)
        if func is None:
            return web.json_response({"error": "method not allowed"}, status=405, headers=_cors_headers())

        task = asyncio.create_task(asyncio.to_thread(func))
        header_wait = asyncio.create_task(headers_ready.wait())
        done, _pending = await asyncio.wait({task, header_wait}, return_when=asyncio.FIRST_COMPLETED)
        if task in done and task.exception() is not None:
            header_wait.cancel()
            return web.json_response(
                {"error": f"{type(task.exception()).__name__}: {task.exception()}"},
                status=500,
                headers=_cors_headers(),
            )
        if not adapter._aegis_headers_sent:
            await task
            header_wait.cancel()
        headers = {name: value for name, value in adapter._aegis_headers}
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        if "text/event-stream" not in content_type.lower():
            await task
            header_wait.cancel()
            if task.exception() is not None:
                return web.json_response(
                    {"error": f"{type(task.exception()).__name__}: {task.exception()}"},
                    status=500,
                    headers=_cors_headers(),
                )
            merged_headers = {**_cors_headers(), **headers}
            return web.Response(status=adapter._aegis_status, headers=merged_headers, body=adapter.wfile.getvalue())

        response = web.StreamResponse(status=adapter._aegis_status, headers={**_cors_headers(), **headers})
        await response.prepare(request)
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(chunks.get(), timeout=0.1)
                    await response.write(chunk)
                except asyncio.TimeoutError:
                    if task.done():
                        break
            while True:
                try:
                    chunk = chunks.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await response.write(chunk)
            if task.exception() is not None:
                payload = {"error": f"{type(task.exception()).__name__}: {task.exception()}"}
                await response.write(f"event: error\ndata: {json.dumps(payload)}\n\n".encode())
        finally:
            header_wait.cancel()
        await response.write_eof()
        return response

    app = web.Application(client_max_size=_MAX_BODY_BYTES)
    app.router.add_route("*", "/{tail:.*}", dispatch)
    return app


def serve(config: Config, host: str = "127.0.0.1", port: int = 8790) -> None:
    print(f"AEGIS OpenAI-compatible aiohttp API on http://{host}:{port}/v1  (Ctrl+C to stop)")
    try:
        web.run_app(make_app(config), host=host, port=port, print=None)
    except KeyboardInterrupt:
        print("\nserver stopped.")
