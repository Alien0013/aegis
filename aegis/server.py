"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from . import config as cfg_paths
from .config import Config
from .surface import SurfaceRunner, runtime_controls_meta
from .types import Message, ToolCall, new_id

_MAX_BODY_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_STORED_RESPONSES = 100
_TERMINAL_RUN_STATUSES = {"completed", "error", "cancelled"}
_MAX_SESSION_KEY_CHARS = 256


def _api_session_id_from_body(
    body: dict[str, Any],
    *,
    default: str | None = None,
) -> tuple[str | None, tuple[int, dict[str, Any]] | None]:
    raw = body.get("id") or body.get("session_id")
    if raw is None or raw == "":
        return default, None
    session_id = str(raw).strip()
    if (
        not session_id
        or any(ch in session_id for ch in "\r\n\x00")
        or "/" in session_id
        or "?" in session_id
        or "#" in session_id
    ):
        return None, (400, {"ok": False, "error": "Invalid session ID", "code": "invalid_session_id"})
    if len(session_id) > _MAX_SESSION_KEY_CHARS:
        return None, (400, {"ok": False, "error": "Session ID too long", "code": "invalid_session_id"})
    return session_id, None


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


def _parse_cors_origins(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    return tuple(str(item).strip() for item in items if str(item).strip())


def _configured_cors_origins(config: Config | None) -> tuple[str, ...]:
    value = None
    if config is not None:
        value = config.get("server.cors_origins")
    if not value:
        value = os.environ.get("AEGIS_SERVER_CORS_ORIGINS") or os.environ.get("API_SERVER_CORS_ORIGINS")
    return _parse_cors_origins(value)


def _cors_headers(config: Config | None, origin: str = "") -> dict[str, str] | None:
    origin = str(origin or "").strip()
    if not origin:
        return {}
    origins = _configured_cors_origins(config)
    if not origins:
        return None
    headers = {
        "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": (
            "Authorization, Content-Type, Accept, OpenAI-Beta, Idempotency-Key, "
            "X-Aegis-Session, X-Aegis-Provider, X-Aegis-Cwd, "
            "X-Hermes-Session-Id, X-Hermes-Session-Key"
        ),
        "Access-Control-Max-Age": "600",
    }
    if "*" in origins:
        headers["Access-Control-Allow-Origin"] = "*"
        return headers
    if origin not in origins:
        return None
    headers["Access-Control-Allow-Origin"] = origin
    headers["Vary"] = "Origin"
    return headers


def _origin_allowed(config: Config | None, origin: str = "") -> bool:
    return not origin or _cors_headers(config, origin) is not None


def _security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "0",
        "Referrer-Policy": "no-referrer",
    }


def _response_headers(config: Config | None, origin: str = "") -> dict[str, str]:
    headers = _security_headers()
    cors = _cors_headers(config, origin)
    if cors:
        headers.update(cors)
    return headers


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, default=str).encode()


def _request_fingerprint(body: dict[str, Any], keys: list[str]) -> str:
    subset = {key: body.get(key) for key in keys}
    blob = json.dumps(subset, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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


def _tool_calls_from_payload(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in payload.get("tool_calls", []) or []:
        if not isinstance(raw, dict):
            continue
        call_id = str(raw.get("id") or raw.get("call_id") or new_id("call"))
        if "function" in raw and isinstance(raw.get("function"), dict):
            func = raw["function"]
            name = str(func.get("name") or raw.get("name") or "")
            args = func.get("arguments", {})
        else:
            name = str(raw.get("name") or "")
            args = raw.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"__raw__": args}
        if not isinstance(args, dict):
            args = {"value": args}
        if name:
            calls.append(ToolCall(id=call_id, name=name, arguments=args))
    return calls


def _convert_message(m: dict[str, Any]) -> Message:
    role = str(m.get("role") or "user")
    text, images = _content(m.get("content", ""))
    if role in ("system", "developer"):
        return Message.user(f"<{role}_instructions>\n{text}\n</{role}_instructions>") if text else Message.user("")
    if role == "assistant":
        return Message.assistant(text, tool_calls=_tool_calls_from_payload(m))
    if role == "tool":
        return Message(
            role="tool",
            content=text,
            tool_call_id=m.get("tool_call_id") or m.get("call_id"),
            name=m.get("name"),
        )
    return Message.user(text, images=images)


def _convert(messages: list[dict]) -> tuple[list[Message], Message]:
    """Return (history_without_last_user, last_user_message)."""
    internal = [_convert_message(m) for m in messages]
    last_user = Message.user("")
    for i in range(len(internal) - 1, -1, -1):
        if internal[i].role == "user":
            last_user = internal.pop(i)
            break
    return internal, last_user


def _content_seed_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    try:
        return json.dumps(content, sort_keys=True, default=str)
    except TypeError:
        return str(content)


def _derive_chat_session_id(system_prompt: str | None, first_user_message: str) -> str:
    seed = f"{system_prompt or ''}\n{first_user_message}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"api-{digest}"


def _derive_chat_session_id_from_messages(messages: list[dict]) -> str:
    system_prompt = ""
    first_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "system" and not system_prompt:
            system_prompt = _content_seed_text(message.get("content"))
        elif role == "user" and not first_user:
            first_user = _content_seed_text(message.get("content"))
            break
    return _derive_chat_session_id(system_prompt, first_user)


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


def _response_output_items(result, text: str | None = None) -> list[dict[str, Any]]:
    final_text = text if text is not None else (getattr(result, "text", "") if result is not None else "")
    items: list[dict[str, Any]] = []
    pending_calls: set[str] = set()
    for event in getattr(result, "events", []) or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_start":
            call_id = str(event.get("id") or event.get("tool_call_id") or new_id("call"))
            args = event.get("args", event.get("arguments", {}))
            arguments = args if isinstance(args, str) else json.dumps(args if args is not None else {}, default=str)
            pending_calls.add(call_id)
            items.append({
                "type": "function_call",
                "name": str(event.get("name") or event.get("tool_name") or ""),
                "arguments": arguments,
                "call_id": call_id,
            })
            continue
        if event.get("type") == "tool_result":
            call_id = str(event.get("id") or event.get("tool_call_id") or "")
            if not call_id or call_id not in pending_calls:
                continue
            result_text = str(event.get("preview") or event.get("summary") or event.get("data") or "")
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": [{"type": "input_text", "text": result_text}],
            })
    return items + _response_output(final_text)


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
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": True,
        "output": _response_output_items(result, text),
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
    return _convert(messages)


def _parse_response_history(value: Any) -> tuple[list[Message], str]:
    if value in (None, ""):
        return [], ""
    if not isinstance(value, list):
        return [], "'conversation_history' must be an array of message objects"
    messages: list[Message] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or "role" not in item:
            return [], f"conversation_history[{index}] must have 'role' and 'content' fields"
        messages.append(_convert_message(item))
    return messages, ""


def _instruction_message(instructions: str | None) -> Message | None:
    text = str(instructions or "").strip()
    if not text:
        return None
    return Message.user(f"<system_instructions>\n{text}\n</system_instructions>")


def _is_instruction_wrapper(message: Message) -> bool:
    text = (message.content or "").lstrip()
    return message.role == "user" and (
        text.startswith("<system_instructions>")
        or text.startswith("<developer_instructions>")
    )


def _history_payload(messages: list[Message]) -> list[dict[str, Any]]:
    return [_message_payload(m) for m in messages if not _is_instruction_wrapper(m)]


def _history_from_state(state: dict[str, Any] | None) -> list[Message]:
    if not state:
        return []
    raw = state.get("conversation_history")
    if not isinstance(raw, list):
        return []
    messages: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            messages.append(Message.from_dict(item))
        except Exception:  # noqa: BLE001
            try:
                messages.append(_convert_message(item))
            except Exception:  # noqa: BLE001
                continue
    return messages


def _response_conversation_history(
    prior_history: list[Message],
    last_user: Message,
    result,
) -> list[dict[str, Any]]:
    session = getattr(result, "session", None)
    session_messages = getattr(session, "messages", None)
    if isinstance(session_messages, list) and session_messages:
        filtered = [
            m for m in session_messages
            if isinstance(m, Message) and m.role != "system" and not _is_instruction_wrapper(m)
        ]
        if filtered and len(filtered) >= len(prior_history) + 1:
            return _history_payload(filtered)
    history = list(prior_history)
    history.append(last_user)
    text = getattr(result, "text", "") if result is not None else ""
    message = getattr(result, "message", None)
    history.append(message if isinstance(message, Message) else Message.assistant(text))
    return _history_payload(history)


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
            "session_continuity_header": "X-Hermes-Session-Id",
            "session_key_header": "X-Hermes-Session-Key",
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


def _public_run_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in {"agent", "thread", "events"}}


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
    """SQLite-backed state store for the OpenAI-compatible Responses surface."""

    def __init__(self, config: Config):
        self.path = cfg_paths.sub("server_responses.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size = max(1, int(config.get("server.responses_store_max", _DEFAULT_MAX_STORED_RESPONSES)
                                   or _DEFAULT_MAX_STORED_RESPONSES))
        self._lock = threading.RLock()
        with self._connect() as db:
            self._configure_db(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, "
                "status TEXT NOT NULL, body TEXT NOT NULL, "
                "accessed_at REAL NOT NULL DEFAULT 0)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(responses)").fetchall()}
            if "accessed_at" not in columns:
                db.execute("ALTER TABLE responses ADD COLUMN accessed_at REAL NOT NULL DEFAULT 0")
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "name TEXT PRIMARY KEY, response_id TEXT NOT NULL)"
            )
        self._tighten_file_permissions()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=30)

    def _configure_db(self, db: sqlite3.Connection) -> None:
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            try:
                db.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.DatabaseError:
                pass
        try:
            db.execute("PRAGMA busy_timeout=30000")
        except sqlite3.DatabaseError:
            pass

    def _tighten_file_permissions(self) -> None:
        for candidate in (self.path, self.path.with_name(self.path.name + "-wal"),
                          self.path.with_name(self.path.name + "-shm")):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                pass

    def _state_payload(self, response: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(state or {})
        if isinstance(state.get("response"), dict):
            payload = state
            payload["response"] = response
            return payload
        return {
            "response": response,
            "conversation_history": list(state.get("conversation_history") or []),
            "instructions": state.get("instructions"),
            "session_id": state.get("session_id") or (response.get("metadata") or {}).get("session_id"),
            "conversation": state.get("conversation") or (response.get("metadata") or {}).get("conversation"),
        }

    @staticmethod
    def _normalize_state(body: dict[str, Any]) -> dict[str, Any]:
        if isinstance(body.get("response"), dict):
            body.setdefault("conversation_history", [])
            body.setdefault("instructions", None)
            body.setdefault("session_id", (body.get("response", {}).get("metadata") or {}).get("session_id"))
            return body
        return {
            "response": body,
            "conversation_history": body.get("_conversation_history", []),
            "instructions": body.get("instructions"),
            "session_id": (body.get("metadata") or {}).get("session_id"),
            "conversation": (body.get("metadata") or {}).get("conversation"),
        }

    def put(self, response: dict[str, Any], state: dict[str, Any] | None = None) -> None:
        payload = self._state_payload(response, state)
        body = json.dumps(payload, default=str)
        response_id = str(response.get("id") or "")
        now = time.time()
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute(
                "INSERT OR REPLACE INTO responses (id, created_at, status, body, accessed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    response_id,
                    int(response.get("created_at") or time.time()),
                    str(response.get("status") or ""),
                    body,
                    now,
                ),
            )
            count = int(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] or 0)
            if count > self.max_size:
                evict = [
                    row[0] for row in db.execute(
                        "SELECT id FROM responses ORDER BY accessed_at ASC LIMIT ?",
                        (count - self.max_size,),
                    ).fetchall()
                ]
                if evict:
                    placeholders = ",".join("?" for _ in evict)
                    db.execute(f"DELETE FROM conversations WHERE response_id IN ({placeholders})", evict)
                    db.execute(f"DELETE FROM responses WHERE id IN ({placeholders})", evict)
        self._tighten_file_permissions()

    def get_state(self, response_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            row = db.execute("SELECT body FROM responses WHERE id = ?", (response_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE responses SET accessed_at = ? WHERE id = ?", (time.time(), response_id))
        try:
            body = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            with self._lock, self._connect() as db:
                db.execute("DELETE FROM conversations WHERE response_id = ?", (response_id,))
                db.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            return None
        return self._normalize_state(body) if isinstance(body, dict) else None

    def get(self, response_id: str) -> dict[str, Any] | None:
        state = self.get_state(response_id)
        if state is None:
            return None
        response = state.get("response")
        return response if isinstance(response, dict) else None

    def delete(self, response_id: str) -> bool:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute("DELETE FROM conversations WHERE response_id = ?", (response_id,))
            cur = db.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            return cur.rowcount > 0

    def get_conversation(self, name: str) -> str | None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            row = db.execute("SELECT response_id FROM conversations WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else None

    def set_conversation(self, name: str, response_id: str) -> None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute(
                "INSERT OR REPLACE INTO conversations (name, response_id) VALUES (?, ?)",
                (name, response_id),
            )


class IdempotencyCache:
    """Small in-process LRU cache for OpenAI-style Idempotency-Key replays."""

    def __init__(self, *, max_items: int = 1000, ttl_seconds: float = 300) -> None:
        self.max_items = max(1, int(max_items or 1000))
        self.ttl_seconds = max(1.0, float(ttl_seconds or 300))
        self._lock = threading.RLock()
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._inflight: dict[tuple[str, str], dict[str, Any]] = {}

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [key for key, item in self._items.items()
                   if now - float(item.get("ts") or 0) > self.ttl_seconds]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def get(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            self._purge_locked()
            item = self._items.get(key)
            if not item or item.get("fingerprint") != fingerprint:
                return None
            self._items.move_to_end(key)
            response = item.get("response")
            return dict(response) if isinstance(response, dict) else None

    def put(self, key: str, fingerprint: str, response: dict[str, Any]) -> None:
        if not key:
            return
        with self._lock:
            self._items[key] = {
                "fingerprint": fingerprint,
                "response": dict(response),
                "ts": time.time(),
            }
            self._items.move_to_end(key)
            self._purge_locked()

    def get_or_compute(self, key: str, fingerprint: str, compute) -> dict[str, Any]:
        if not key:
            return compute()
        cached = self.get(key, fingerprint)
        if cached is not None:
            return cached
        flight_key = (key, fingerprint)
        with self._lock:
            cached = self.get(key, fingerprint)
            if cached is not None:
                return cached
            flight = self._inflight.get(flight_key)
            if flight is None:
                flight = {"event": threading.Event(), "response": None, "error": None}
                self._inflight[flight_key] = flight
                owner = True
            else:
                owner = False
        if not owner:
            flight["event"].wait()
            error = flight.get("error")
            if error is not None:
                raise error
            response = flight.get("response")
            return dict(response) if isinstance(response, dict) else {}
        try:
            response = compute()
            self.put(key, fingerprint, response)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                flight["error"] = exc
                flight["event"].set()
                self._inflight.pop(flight_key, None)
            raise
        with self._lock:
            flight["response"] = dict(response)
            flight["event"].set()
            self._inflight.pop(flight_key, None)
        return dict(response)


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")
    runner = SurfaceRunner(config, include_mcp=True)
    response_store = ResponseStore(config)
    idempotency_cache = IdempotencyCache(
        max_items=int(config.get("server.idempotency_cache_max", 1000) or 1000),
        ttl_seconds=float(config.get("server.idempotency_ttl_seconds", 300) or 300),
    )
    active_runs: dict[str, dict[str, Any]] = {}
    active_responses: dict[str, dict[str, Any]] = {}
    approvals: dict[str, dict[str, Any]] = {}
    state_lock = threading.RLock()
    max_concurrent_runs = max(1, int(config.get("server.max_concurrent_runs", 8) or 8))
    run_status_ttl = max(0.0, float(config.get("server.run_status_ttl_seconds", 3600) or 3600))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _authed(self) -> bool:
            if not api_key:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {api_key}"

        def _origin(self) -> str:
            return str(self.headers.get("Origin", "") or "")

        def _forbid_disallowed_origin(self) -> bool:
            if _origin_allowed(config, self._origin()):
                return False
            self._json(403, {"error": "cors origin not allowed"})
            return True

        def _json(self, code: int, obj: Any, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for name, value in _response_headers(config, self._origin()).items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                if value:
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(_json_bytes(obj))

        def _session_key(self) -> tuple[str | None, tuple[int, dict[str, Any]] | None]:
            raw = str(self.headers.get("X-Hermes-Session-Key") or "").strip()
            if not raw:
                return None, None
            if not api_key:
                return None, (
                    403,
                    {"error": "X-Hermes-Session-Key requires API key authentication"},
                )
            if any(ch in raw for ch in "\r\n\x00") or any(ord(ch) < 32 for ch in raw):
                return None, (400, {"error": "Invalid session key"})
            if len(raw) > _MAX_SESSION_KEY_CHARS:
                return None, (400, {"error": "Session key too long"})
            return raw, None

        def _session_headers(
            self,
            *,
            session_id: str | None = None,
            session_key: str | None = None,
        ) -> dict[str, str]:
            headers: dict[str, str] = {}
            if session_id:
                headers["X-Hermes-Session-Id"] = str(session_id)
            if session_key:
                headers["X-Hermes-Session-Key"] = str(session_key)
            return headers

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

        def _send_sse_headers(self, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            for name, value in _response_headers(config, self._origin()).items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                if value:
                    self.send_header(name, value)
            self.end_headers()

        def _sweep_runs_locked(self) -> None:
            if run_status_ttl <= 0:
                ttl = 0.0
            else:
                ttl = run_status_ttl
            now = time.time()
            expired: list[str] = []
            for run_id, rec in list(active_runs.items()):
                status = str(rec.get("status") or "")
                if status not in _TERMINAL_RUN_STATUSES:
                    continue
                updated = float(rec.get("updated_at_ts") or rec.get("created_at_ts") or now)
                if now - updated >= ttl:
                    expired.append(run_id)
            for run_id in expired:
                active_runs.pop(run_id, None)
                for approval_id, pending in list(approvals.items()):
                    if pending.get("run_id") == run_id:
                        approvals.pop(approval_id, None)

        def _active_run_count_locked(self) -> int:
            self._sweep_runs_locked()
            return sum(
                1 for rec in active_runs.values()
                if str(rec.get("status") or "") not in _TERMINAL_RUN_STATUSES
            )

        def _set_run_state_locked(self, run_id: str, **updates: Any) -> dict[str, Any] | None:
            rec = active_runs.get(run_id)
            if rec is None:
                return None
            rec.update(updates)
            now = time.time()
            rec["updated_at_ts"] = now
            rec["updated_at"] = int(now)
            return rec

        def _release_run_approvals_locked(self, run_id: str, reason: str) -> None:
            for pending in list(approvals.values()):
                if pending.get("run_id") != run_id or pending.get("answered"):
                    continue
                pending["approved"] = False
                pending["answered"] = True
                pending["cancelled"] = True
                pending["cancel_reason"] = reason
                event = pending.get("event")
                if event is not None:
                    event.set()

        def _request_stop_run_locked(self, run_id: str, reason: str = "stop requested") -> dict[str, Any] | None:
            rec = active_runs.get(run_id)
            if rec is None:
                return None
            rec["cancel_requested"] = True
            rec["cancel_reason"] = reason
            self._release_run_approvals_locked(run_id, reason)
            agent = rec.get("agent")
            if agent is not None:
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                elif getattr(agent, "cancel_event", None) is not None:
                    agent.cancel_event.set()
            return self._set_run_state_locked(run_id, status="cancelling", last_event="run.stopping")

        def _cancel_agent(self, agent: Any) -> None:
            if agent is None:
                return
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                cancel()
                return
            cancel_event = getattr(agent, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()

        def _mark_response_cancelled(self, response: dict[str, Any], reason: str) -> dict[str, Any]:
            cancelled = dict(response)
            cancelled["status"] = "cancelled"
            cancelled["error"] = None
            cancelled["incomplete_details"] = cancelled.get("incomplete_details") or {"reason": reason}
            metadata = cancelled.get("metadata") if isinstance(cancelled.get("metadata"), dict) else {}
            metadata = dict(metadata)
            metadata["cancel_reason"] = reason
            metadata["cancelled_at"] = int(time.time())
            cancelled["metadata"] = metadata
            return cancelled

        def _register_response_locked(
            self,
            response_id: str,
            *,
            response: dict[str, Any] | None = None,
            agent: Any = None,
            session: Any = None,
            store_response: bool = True,
        ) -> dict[str, Any]:
            rec = active_responses.setdefault(response_id, {
                "id": response_id,
                "cancel_requested": False,
                "cancel_reason": "",
                "status": "running",
                "created_at_ts": time.time(),
            })
            if response is not None:
                rec["response"] = dict(response)
            if agent is not None:
                rec["agent"] = agent
            if session is not None:
                rec["session"] = session
            rec["store_response"] = bool(store_response)
            rec["updated_at_ts"] = time.time()
            if rec.get("cancel_requested") and agent is not None:
                self._cancel_agent(agent)
            return rec

        def _request_cancel_response_locked(
            self,
            response_id: str,
            reason: str = "API cancel requested",
        ) -> dict[str, Any] | None:
            rec = active_responses.get(response_id)
            if rec is None:
                return None
            rec["cancel_requested"] = True
            rec["cancel_reason"] = reason
            rec["status"] = "cancelling"
            rec["updated_at_ts"] = time.time()
            self._cancel_agent(rec.get("agent"))
            return rec

        def _response_cancel_requested(self, response_id: str) -> tuple[bool, str]:
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is None:
                    return False, ""
                return bool(rec.get("cancel_requested")), str(rec.get("cancel_reason") or "API cancel requested")

        def _finish_response(self, response_id: str, response: dict[str, Any] | None = None) -> None:
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is not None and response is not None:
                    rec["response"] = dict(response)
                active_responses.pop(response_id, None)

        def _prepare_response_agent(
            self,
            response_id: str,
            *,
            session_id: str | None,
            title: str,
            history: list[Message],
            model: Any,
            provider_name: Any,
            cwd: Any,
            session_key: str | None,
        ) -> tuple[Any, Any]:
            load_session = getattr(runner, "load_or_create_session", None)
            make_agent = getattr(runner, "make_agent", None)
            if not callable(load_session) or not callable(make_agent):
                return None, None
            meta = {
                "request_id": response_id,
                "api": "responses",
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            session = load_session(
                session_id,
                title=title,
                history=history,
                surface="serve",
                meta=meta,
            )
            agent = make_agent(
                session=session,
                model=model,
                provider_name=provider_name,
                cwd=cwd,
            )
            return session, agent

        def _cancel_response(self, response_id: str) -> None:
            state = response_store.get_state(response_id)
            response = (state or {}).get("response") if state else None
            with state_lock:
                rec = self._request_cancel_response_locked(response_id, "API cancel requested")
                if not isinstance(response, dict) and rec is not None and isinstance(rec.get("response"), dict):
                    response = dict(rec["response"])
            if not isinstance(response, dict):
                return self._json(404, {"error": "response not found", "id": response_id})
            cancelled = self._mark_response_cancelled(response, "API cancel requested")
            if state is not None and _coerce_request_bool(cancelled.get("store"), True):
                response_store.put(cancelled, state)
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is not None:
                    rec["response"] = dict(cancelled)
                    rec["status"] = "cancelled"
            return self._json(200, cancelled)

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
                self._sweep_runs_locked()
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
                    self._sweep_runs_locked()
                    active = active_runs.get(run_id)
                    if active is None:
                        events = list(payload.get("events") or [])
                        status = "completed"
                        detail = {"id": run_id, "status": status}
                    else:
                        events = list(active.get("events") or [])
                        status = str(active.get("status") or "running")
                        detail = _public_run_record(active)
                for event in events[sent:]:
                    if not self._write_sse(event, event="event"):
                        with state_lock:
                            self._request_stop_run_locked(run_id, "SSE client disconnected")
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
            cors = _cors_headers(config, self._origin())
            if not cors:
                self.send_response(403)
                for name, value in _security_headers().items():
                    self.send_header(name, value)
                self.end_headers()
                return
            self.send_response(204)
            headers = _security_headers()
            headers.update(cors)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()

        def _run_detail(self, run_id: str) -> tuple[int, dict[str, Any]]:
            with state_lock:
                self._sweep_runs_locked()
                active = active_runs.get(run_id)
                if active is not None:
                    return 200, {"ok": True, "run": _public_run_record(active)}
            from .runs import RunStore

            run = RunStore().get(run_id)
            if run is None:
                return 404, {"ok": False, "error": "run not found", "id": run_id}
            return 200, {"ok": True, "run": run}

        def do_GET(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
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
                limit = max(1, min(limit, 500))
                with state_lock:
                    self._sweep_runs_locked()
                    active = [_public_run_record(rec) for rec in active_runs.values()]
                rows = active + RunStore().list(limit=max(1, limit - len(active)))
                return self._json(200, {"object": "list", "data": rows[:limit]})
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

                jobs = [_job_payload(job) for job in CronStore().list()]
                return self._json(200, {"ok": True, "object": "list",
                                        "jobs": jobs, "data": jobs})
            if path.startswith("/api/jobs/"):
                code, payload = self._job_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
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
            if self._forbid_disallowed_origin():
                return
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
            if self._forbid_disallowed_origin():
                return
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
                return self._cancel_response(path.split("/")[-2])
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

                store = SessionStore()
                requested_id, id_error = _api_session_id_from_body(body)
                if id_error is not None:
                    code, payload = id_error
                    return self._json(code, payload)
                if requested_id and store.load(requested_id) is not None:
                    return self._json(
                        409,
                        {
                            "ok": False,
                            "error": f"Session already exists: {requested_id}",
                            "code": "session_exists",
                        },
                    )
                title = str(body.get("title") or "api session")
                if requested_id:
                    session = Session(id=requested_id, title=title or requested_id, profile=cfg_paths.current_profile())
                else:
                    session = Session.create(title)
                if isinstance(body.get("meta"), dict):
                    session.meta.update(body["meta"])
                if isinstance(body.get("metadata"), dict):
                    session.meta.update(body["metadata"])
                session.meta["source"] = "api_server"
                if body.get("model"):
                    session.meta.update(runtime_controls_meta({"model": body.get("model")}))
                if body.get("provider"):
                    session.meta.update(runtime_controls_meta({"provider": body.get("provider")}))
                system_prompt = body.get("system_prompt")
                if system_prompt is not None:
                    if not isinstance(system_prompt, str):
                        return self._json(
                            400,
                            {
                                "ok": False,
                                "error": "system_prompt must be a string",
                                "code": "invalid_system_prompt",
                            },
                        )
                    session.meta["system_prompt"] = system_prompt
                    if system_prompt:
                        session.messages.append(Message.system(system_prompt))
                store.save(session)
                return self._json(201, {"ok": True, "object": "hermes.session", "session": _session_payload(session)})
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
                requested_id, id_error = _api_session_id_from_body(body)
                if id_error is not None:
                    code, payload = id_error
                    return self._json(code, payload)
                if requested_id and store.load(requested_id) is not None:
                    return self._json(
                        409,
                        {
                            "ok": False,
                            "error": f"Session already exists: {requested_id}",
                            "code": "session_exists",
                        },
                    )
                child = store.fork(parent, carry_summary=_coerce_request_bool(body.get("carry_summary"), True))
                if requested_id:
                    old_id = child.id
                    child.id = requested_id
                    child.parent_id = parent.id
                    store.delete(old_id)
                child.messages = [Message.from_dict(message.to_dict()) for message in parent.messages]
                if body.get("model"):
                    child.meta.update(runtime_controls_meta({"model": body.get("model")}))
                if body.get("provider"):
                    child.meta.update(runtime_controls_meta({"provider": body.get("provider")}))
                if body.get("title"):
                    child.title = str(body["title"])
                store.save(child)
                return self._json(201, {"ok": True, "object": "hermes.session", "session": _session_payload(child)})
            if path.startswith("/api/sessions/") and path.endswith(("/chat", "/chat/stream")):
                parts = path.split("/")
                session_id = parts[-3] if path.endswith("/chat/stream") else parts[-2]
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
            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            history, last_user = _convert(body.get("messages", []))
            model = body.get("model")
            stream = _coerce_request_bool(body.get("stream"), False)
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if session_key:
                metadata["session_key"] = session_key
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or self.headers.get("X-Hermes-Session-Id")
                or None
            )
            if not session_id:
                session_id = _derive_chat_session_id_from_messages(body.get("messages", []))
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
                idempotency_key = str(self.headers.get("Idempotency-Key", "") or "")
                idempotency_body = {
                    **body,
                    "_session_id_header": self.headers.get("X-Aegis-Session") or self.headers.get("X-Hermes-Session-Id"),
                    "_session_key_header": session_key,
                }
                idempotency_fp = _request_fingerprint(
                    idempotency_body,
                    ["model", "messages", "tools", "tool_choice", "stream", "_session_id_header", "_session_key_header"],
                )
                def compute_response() -> dict[str, Any]:
                    result = runner.run_prompt(
                        last_user,
                        session_id=session_id,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        stream=False,
                        surface="serve",
                        meta={
                            "request_id": cid,
                            **({"gateway_session_key": session_key} if session_key else {}),
                        },
                    )
                    response_metadata = {
                        "session_id": result.session.id,
                        "trace_id": result.trace_id,
                        "run_id": result.run_id,
                    }
                    if session_key:
                        response_metadata["session_key"] = session_key
                    return {
                        "id": cid, "object": "chat.completion", "created": int(time.time()),
                        "model": result.agent.provider.model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text},
                                     "finish_reason": "stop"}],
                        "usage": _usage(getattr(result, "usage", None) or result.agent),
                        "metadata": response_metadata,
                    }

                response = idempotency_cache.get_or_compute(idempotency_key, idempotency_fp, compute_response)
                response_metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
                response_session_id = response_metadata.get("session_id")
                return self._json(200, response, self._session_headers(
                    session_id=str(response_session_id or session_id or ""),
                    session_key=session_key,
                ))

            # streaming
            self._send_sse_headers(self._session_headers(session_id=session_id, session_key=session_key))

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
                meta={
                    "request_id": cid,
                    **({"gateway_session_key": session_key} if session_key else {}),
                },
                on_event=emit,
            )
            final_metadata = {
                "session_id": result.session.id,
                "trace_id": result.trace_id,
                "run_id": result.run_id,
            }
            if session_key:
                final_metadata["session_key"] = session_key
            final = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": result.agent.provider.model,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": _usage(getattr(result, "usage", None) or result.agent),
                     "metadata": final_metadata}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def _post_response(self, body: dict[str, Any]) -> None:
            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            response_id = new_id("resp")
            model = body.get("model")
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if session_key:
                metadata["session_key"] = session_key
            instructions = str(body.get("instructions") or "").strip() or None
            previous_id = str(body.get("previous_response_id") or "").strip()
            conversation = _conversation_id(body.get("conversation"))
            if previous_id and conversation:
                return self._json(400, {"error": "Cannot use both 'conversation' and 'previous_response_id'"})
            if conversation and not previous_id:
                previous_id = response_store.get_conversation(conversation) or ""

            explicit_history, history_error = _parse_response_history(body.get("conversation_history"))
            if history_error:
                return self._json(400, {"error": history_error})
            previous_state = None
            if previous_id and not explicit_history:
                previous_state = response_store.get_state(previous_id)
                if previous_state is None:
                    return self._json(404, {"error": f"Previous response not found: {previous_id}"})
                explicit_history = _history_from_state(previous_state)
                if instructions is None:
                    stored_instructions = previous_state.get("instructions")
                    instructions = str(stored_instructions or "").strip() or None

            input_history, last_user = _responses_messages(body)
            state_history = list(explicit_history) + list(input_history)
            history = list(state_history)
            instruction = _instruction_message(instructions)
            if instruction is not None:
                history.insert(0, instruction)

            if previous_state is not None:
                previous_response = previous_state.get("response") if isinstance(previous_state, dict) else {}
                prev_meta = previous_response.get("metadata") if isinstance(previous_response, dict) else {}
                if not metadata.get("session_id") and not body.get("session_id"):
                    inherited_session = previous_state.get("session_id") or prev_meta.get("session_id")
                    if inherited_session:
                        metadata["session_id"] = inherited_session
            if conversation:
                metadata["conversation"] = conversation
                if not metadata.get("session_id") and not body.get("session_id"):
                    metadata["session_id"] = f"conversation:{conversation}"
            if previous_id:
                metadata["previous_response_id"] = previous_id
            store_response = _coerce_request_bool(body.get("store"), True)
            stream = _coerce_request_bool(body.get("stream"), False)
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or self.headers.get("X-Hermes-Session-Id")
            )
            provider_name = metadata.get("provider") or body.get("provider")
            cwd = metadata.get("cwd") or body.get("cwd")
            response_title = last_user.content[:80] or response_id
            if stream:
                sequence = 0
                message_item_id = new_id("msg")
                message_opened = False
                message_output_index: int | None = None
                next_output_index = 0
                text_parts: list[str] = []
                pending_tool_calls: dict[str, dict[str, Any]] = {}
                streamed_output_items: list[dict[str, Any]] = []

                def send_event(event_name: str, payload: dict[str, Any]) -> bool:
                    nonlocal sequence
                    payload.setdefault("type", event_name)
                    payload.setdefault("sequence_number", sequence)
                    sequence += 1
                    return self._write_sse(payload, event=event_name)

                def open_message_item() -> bool:
                    nonlocal message_opened, message_output_index, next_output_index
                    if message_opened:
                        return True
                    message_opened = True
                    message_output_index = next_output_index
                    next_output_index += 1
                    return send_event("response.output_item.added", {
                        "output_index": message_output_index,
                        "item": {
                            "id": message_item_id,
                            "type": "message",
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    })

                def emit_tool_start(e: dict[str, Any]) -> None:
                    nonlocal next_output_index
                    call_id = str(e.get("id") or e.get("tool_call_id") or new_id("call"))
                    args = e.get("args", e.get("arguments", {}))
                    if isinstance(args, str):
                        arguments = args
                    else:
                        arguments = json.dumps(args if args is not None else {}, default=str)
                    item_id = new_id("fc")
                    output_index = next_output_index
                    next_output_index += 1
                    item = {
                        "id": item_id,
                        "type": "function_call",
                        "status": "in_progress",
                        "name": str(e.get("name") or e.get("tool_name") or ""),
                        "call_id": call_id,
                        "arguments": arguments,
                    }
                    pending_tool_calls[call_id] = {
                        "item_id": item_id,
                        "output_index": output_index,
                        "name": item["name"],
                        "arguments": arguments,
                        "call_id": call_id,
                    }
                    streamed_output_items.append({
                        "type": "function_call",
                        "name": item["name"],
                        "arguments": arguments,
                        "call_id": call_id,
                    })
                    send_event("response.output_item.added", {
                        "output_index": output_index,
                        "item": item,
                    })

                def emit_tool_result(e: dict[str, Any]) -> None:
                    nonlocal next_output_index
                    call_id = str(e.get("id") or e.get("tool_call_id") or "")
                    pending = pending_tool_calls.pop(call_id, None) if call_id else None
                    if pending is None:
                        return
                    done_item = {
                        "id": pending["item_id"],
                        "type": "function_call",
                        "status": "completed",
                        "name": pending["name"],
                        "call_id": pending["call_id"],
                        "arguments": pending["arguments"],
                    }
                    send_event("response.output_item.done", {
                        "output_index": pending["output_index"],
                        "item": done_item,
                    })
                    result_text = str(e.get("preview") or e.get("summary") or e.get("data") or "")
                    output_parts = [{"type": "input_text", "text": result_text}]
                    output_item = {
                        "id": new_id("fco"),
                        "type": "function_call_output",
                        "call_id": pending["call_id"],
                        "output": output_parts,
                        "status": "completed" if not e.get("is_error") else "failed",
                    }
                    output_index = next_output_index
                    next_output_index += 1
                    streamed_output_items.append({
                        "type": "function_call_output",
                        "call_id": pending["call_id"],
                        "output": output_parts,
                    })
                    send_event("response.output_item.added", {
                        "output_index": output_index,
                        "item": output_item,
                    })
                    send_event("response.output_item.done", {
                        "output_index": output_index,
                        "item": output_item,
                    })

                def persist_stream_response(response: dict[str, Any], result=None) -> None:
                    if not store_response:
                        return
                    full_history = _response_conversation_history(state_history, last_user, result)
                    response_store.put(response, {
                        "conversation_history": full_history,
                        "instructions": instructions,
                        "session_id": response.get("metadata", {}).get("session_id") or session_id,
                        "conversation": conversation,
                    })
                    if conversation:
                        response_store.set_conversation(conversation, response_id)

                self._send_sse_headers(self._session_headers(session_id=session_id, session_key=session_key))
                created_response = {
                    "id": response_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "model": model or config.get("model.default", ""),
                    "output": [],
                    "metadata": metadata,
                    "instructions": instructions,
                    "previous_response_id": previous_id or None,
                    "conversation": conversation or None,
                    "store": store_response,
                }
                send_event("response.created", {
                    "response": {
                        **created_response,
                    },
                })
                if store_response:
                    response_store.put(created_response, {
                        "conversation_history": _history_payload(state_history + [last_user]),
                        "instructions": instructions,
                        "session_id": session_id,
                        "conversation": conversation,
                    })
                with state_lock:
                    self._register_response_locked(
                        response_id,
                        response=created_response,
                        store_response=store_response,
                    )

                def emit(e: dict[str, Any]) -> None:
                    if e.get("type") == "assistant_delta":
                        delta = str(e.get("text") or "")
                        if not delta:
                            return
                        if not open_message_item():
                            return
                        text_parts.append(delta)
                        send_event("response.output_text.delta", {
                            "response_id": response_id,
                            "item_id": message_item_id,
                            "output_index": message_output_index if message_output_index is not None else 0,
                            "content_index": 0,
                            "delta": delta,
                            "logprobs": [],
                        })
                        return
                    if e.get("type") == "tool_start":
                        emit_tool_start(e)
                        return
                    if e.get("type") == "tool_result":
                        emit_tool_result(e)
                        return
                    meta = _event_metadata(e)
                    if meta:
                        send_event("aegis.event", {
                            "response_id": response_id,
                            "event": meta,
                        })

                try:
                    response_session, response_agent = self._prepare_response_agent(
                        response_id,
                        session_id=str(session_id) if session_id else None,
                        title=response_title,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        session_key=session_key,
                    )
                    with state_lock:
                        self._register_response_locked(
                            response_id,
                            agent=response_agent,
                            session=response_session,
                            store_response=store_response,
                        )
                    run_kwargs: dict[str, Any] = {
                        "model": model,
                        "provider_name": provider_name,
                        "cwd": cwd,
                        "stream": True,
                        "surface": "serve",
                        "meta": {
                            "request_id": response_id,
                            "api": "responses",
                            **({"gateway_session_key": session_key} if session_key else {}),
                        },
                        "on_event": emit,
                    }
                    if response_agent is not None:
                        run_kwargs.update({
                            "session": response_session,
                            "agent": response_agent,
                            "reuse_agent": False,
                        })
                    else:
                        run_kwargs.update({
                            "session_id": session_id,
                            "history": history,
                        })
                    result = runner.run_prompt(last_user, **run_kwargs)
                except Exception as exc:  # noqa: BLE001
                    text = "".join(text_parts)
                    cancelled, cancel_reason = self._response_cancel_requested(response_id)
                    failed = _response_object(
                        response_id,
                        None,
                        status="cancelled" if cancelled else "failed",
                        metadata_extra=metadata,
                    )
                    failed.update({
                        "model": model or config.get("model.default", ""),
                        "output": list(streamed_output_items) + (_response_output(text) if text else []),
                        "output_text": text,
                        "error": None if cancelled else {
                            "message": f"{type(exc).__name__}: {exc}",
                            "type": "server_error",
                        },
                        "instructions": instructions,
                        "previous_response_id": previous_id or None,
                        "conversation": conversation or None,
                        "store": store_response,
                    })
                    if cancelled:
                        failed = self._mark_response_cancelled(failed, cancel_reason)
                    if store_response:
                        history_snapshot = list(state_history)
                        history_snapshot.append(last_user)
                        if text:
                            history_snapshot.append(Message.assistant(text))
                        response_store.put(failed, {
                            "conversation_history": _history_payload(history_snapshot),
                            "instructions": instructions,
                            "session_id": session_id,
                            "conversation": conversation,
                        })
                        if conversation:
                            response_store.set_conversation(conversation, response_id)
                    if cancelled:
                        send_event("response.cancelled", {"response": failed})
                    else:
                        send_event("response.failed", {"response": failed, "error": failed["error"]})
                    self._finish_response(response_id, failed)
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return

                cancelled, cancel_reason = self._response_cancel_requested(response_id)
                response = _response_object(
                    response_id,
                    result,
                    status="cancelled" if cancelled else "completed",
                    metadata_extra=metadata,
                )
                response["instructions"] = instructions
                response["previous_response_id"] = previous_id or None
                response["conversation"] = conversation or None
                response["store"] = store_response
                final_text = response.get("output_text") or "".join(text_parts)
                if streamed_output_items:
                    response["output"] = list(streamed_output_items) + _response_output(final_text)
                if cancelled:
                    final_text = "".join(text_parts)
                    response["output_text"] = final_text
                    response["output"] = list(streamed_output_items) + (_response_output(final_text) if final_text else [])
                    response = self._mark_response_cancelled(response, cancel_reason)
                elif final_text or message_opened:
                    if not message_opened:
                        open_message_item()
                    out_index = message_output_index if message_output_index is not None else 0
                    send_event("response.output_text.done", {
                        "response_id": response_id,
                        "item_id": message_item_id,
                        "output_index": out_index,
                        "content_index": 0,
                        "text": final_text,
                        "logprobs": [],
                    })
                    send_event("response.output_item.done", {
                        "output_index": out_index,
                        "item": {
                            "id": message_item_id,
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": final_text}],
                        },
                    })
                persist_stream_response(response, result)
                if cancelled:
                    send_event("response.cancelled", {"response": response})
                else:
                    send_event("response.completed", {
                        "response": response,
                    })
                self._finish_response(response_id, response)
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            idempotency_key = str(self.headers.get("Idempotency-Key", "") or "")
            idempotency_body = {
                **body,
                "_session_id_header": self.headers.get("X-Aegis-Session") or self.headers.get("X-Hermes-Session-Id"),
                "_session_key_header": session_key,
            }
            idempotency_fp = _request_fingerprint(
                idempotency_body,
                [
                    "input",
                    "messages",
                    "instructions",
                    "previous_response_id",
                    "conversation",
                    "model",
                    "tools",
                    "_session_id_header",
                    "_session_key_header",
                ],
            )
            def compute_response() -> dict[str, Any]:
                response: dict[str, Any] | None = None
                with state_lock:
                    self._register_response_locked(response_id, store_response=store_response)
                try:
                    response_session, response_agent = self._prepare_response_agent(
                        response_id,
                        session_id=str(session_id) if session_id else None,
                        title=response_title,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        session_key=session_key,
                    )
                    with state_lock:
                        self._register_response_locked(
                            response_id,
                            agent=response_agent,
                            session=response_session,
                            store_response=store_response,
                        )
                    run_kwargs: dict[str, Any] = {
                        "model": model,
                        "provider_name": provider_name,
                        "cwd": cwd,
                        "stream": False,
                        "surface": "serve",
                        "meta": {
                            "request_id": response_id,
                            "api": "responses",
                            **({"gateway_session_key": session_key} if session_key else {}),
                        },
                    }
                    if response_agent is not None:
                        run_kwargs.update({
                            "session": response_session,
                            "agent": response_agent,
                            "reuse_agent": False,
                        })
                    else:
                        run_kwargs.update({
                            "session_id": session_id,
                            "history": history,
                        })
                    result = runner.run_prompt(last_user, **run_kwargs)
                    cancelled, cancel_reason = self._response_cancel_requested(response_id)
                    response = _response_object(
                        response_id,
                        result,
                        status="cancelled" if cancelled else "completed",
                        metadata_extra=metadata,
                    )
                    if cancelled:
                        response = self._mark_response_cancelled(response, cancel_reason)
                    response["instructions"] = instructions
                    response["previous_response_id"] = previous_id or None
                    response["conversation"] = conversation or None
                    response["store"] = store_response
                    if store_response:
                        full_history = _response_conversation_history(state_history, last_user, result)
                        response_store.put(response, {
                            "conversation_history": full_history,
                            "instructions": instructions,
                            "session_id": response.get("metadata", {}).get("session_id") or session_id,
                            "conversation": conversation,
                        })
                        if conversation:
                            response_store.set_conversation(conversation, response_id)
                    return response
                finally:
                    self._finish_response(response_id, response)

            response = idempotency_cache.get_or_compute(idempotency_key, idempotency_fp, compute_response)
            response_session_id = None
            response_metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            response_session_id = response_metadata.get("session_id")
            return self._json(200, response, self._session_headers(
                session_id=str(response_session_id or session_id or ""),
                session_key=session_key,
            ))

        def _post_session_chat(self, session_id: str, body: dict[str, Any], *, stream: bool = False) -> None:
            from .session import SessionStore

            store = SessionStore()
            session = store.load(session_id)
            if session is None:
                return self._json(404, {"ok": False, "error": "session not found", "id": session_id})
            prompt = body.get("prompt", body.get("input", body.get("message", "")))
            if stream:
                self._send_sse_headers()

                def emit(ev: dict[str, Any]) -> None:
                    self._write_sse(ev)

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

            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            with state_lock:
                active_count = self._active_run_count_locked()
                if active_count >= max_concurrent_runs:
                    return self._json(429, {
                        "error": f"too many concurrent runs (max {max_concurrent_runs})",
                        "code": "rate_limit_exceeded",
                    })
            run_id = new_id("run")
            prompt = str(body.get("prompt", body.get("input", "")) or "")
            if not prompt:
                return self._json(400, {"error": "missing input"})
            session_id = str(body.get("session_id") or "") or None
            title = str(body.get("title") or prompt[:80] or run_id)
            store = SessionStore()
            run_meta = {
                "server_run_id": run_id,
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            session = runner.load_or_create_session(session_id, title=title, surface="serve", meta=run_meta)
            now = time.time()
            record = {
                "id": run_id,
                "object": "run",
                "status": "queued",
                "created_at": int(now),
                "created_at_ts": now,
                "updated_at": int(now),
                "updated_at_ts": now,
                "session_id": session.id,
                "events": [],
                "result": "",
                "error": "",
                "trace_id": "",
                "surface_run_id": "",
                "cancel_requested": False,
                "cancel_reason": "",
                "last_event": "run.queued",
                "model": body.get("model") or "",
                "session_key": session_key or "",
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
                        rec = self._set_run_state_locked(run_id, status="running", agent=agent, last_event="run.running")
                        if rec is not None and rec.get("cancel_requested"):
                            self._request_stop_run_locked(run_id, str(rec.get("cancel_reason") or "stop requested"))

                    def emit(ev: dict[str, Any]) -> None:
                        with state_lock:
                            rec = active_runs.get(run_id)
                            if rec is not None:
                                rec.setdefault("events", []).append(dict(ev))
                                rec["last_event"] = str(ev.get("type") or ev.get("event") or "event")
                                rec["updated_at_ts"] = time.time()
                                rec["updated_at"] = int(rec["updated_at_ts"])

                    result = runner.run_prompt(
                        prompt,
                        session=session,
                        agent=agent,
                        surface="serve",
                        meta=run_meta,
                        stream=_coerce_request_bool(body.get("stream"), False),
                        on_event=emit,
                    )
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            status = "cancelled" if rec.get("cancel_requested") else "completed"
                            self._set_run_state_locked(run_id, **{
                                "status": status,
                                "result": result.text,
                                "trace_id": result.trace_id,
                                "surface_run_id": result.run_id,
                                "session_id": result.session.id,
                                "last_event": "run.cancelled" if status == "cancelled" else "run.completed",
                            })
                except Exception as exc:  # noqa: BLE001
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            status = "cancelled" if rec.get("cancel_requested") else "error"
                            self._set_run_state_locked(
                                run_id,
                                status=status,
                                error=f"{type(exc).__name__}: {exc}" if status == "error" else "",
                                last_event="run.cancelled" if status == "cancelled" else "run.error",
                            )

            thread = threading.Thread(target=worker, daemon=True, name=f"aegis-api-run-{run_id}")
            with state_lock:
                active_runs[run_id]["thread"] = thread
            thread.start()
            return self._json(202, _public_run_record(record), self._session_headers(
                session_id=session.id,
                session_key=session_key,
            ))

        def _stop_run(self, run_id: str) -> None:
            with state_lock:
                self._sweep_runs_locked()
                rec = self._request_stop_run_locked(run_id, "API stop requested")
                if rec is None:
                    return self._json(404, {"ok": False, "error": "active run not found", "id": run_id})
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
        origin = str(request.headers.get("Origin", "") or "")
        if request.method.upper() == "OPTIONS":
            cors = _cors_headers(config, origin)
            if not cors:
                return web.Response(status=403, headers=_security_headers())
            headers = _security_headers()
            headers.update(cors)
            return web.Response(status=204, headers=headers)
        if not _origin_allowed(config, origin):
            return web.json_response(
                {"error": "cors origin not allowed"},
                status=403,
                headers=_security_headers(),
            )
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
            return web.json_response(
                {"error": "method not allowed"},
                status=405,
                headers=_response_headers(config, origin),
            )

        task = asyncio.create_task(asyncio.to_thread(func))
        header_wait = asyncio.create_task(headers_ready.wait())
        done, _pending = await asyncio.wait({task, header_wait}, return_when=asyncio.FIRST_COMPLETED)
        if task in done and task.exception() is not None:
            header_wait.cancel()
            return web.json_response(
                {"error": f"{type(task.exception()).__name__}: {task.exception()}"},
                status=500,
                headers=_response_headers(config, origin),
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
                    headers=_response_headers(config, origin),
                )
            merged_headers = {**_response_headers(config, origin), **headers}
            return web.Response(status=adapter._aegis_status, headers=merged_headers, body=adapter.wfile.getvalue())

        response = web.StreamResponse(
            status=adapter._aegis_status,
            headers={**_response_headers(config, origin), **headers},
        )
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
