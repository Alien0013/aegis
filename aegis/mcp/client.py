"""A from-scratch MCP client (JSON-RPC 2.0) over stdio and Streamable HTTP.

Implements the lifecycle: initialize -> notifications/initialized -> tools/list ->
tools/call. Each remote tool is wrapped as an AEGIS ``Tool`` (namespaced
``mcp__<server>__<tool>``) and registered like any built-in. Resource and prompt
capabilities are exposed as utility tools when a server advertises them.

Config (config.yaml ``mcp.servers`` or ``~/.aegis/mcp.json`` Claude-Desktop format):

    mcp:
      servers:
        filesystem: {command: npx, args: ["-y","@modelcontextprotocol/server-filesystem","/tmp"]}
        remote:     {url: "https://example.com/mcp", headers: {Authorization: "Bearer ..."}}
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextvars
import hashlib
import inspect
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .. import config as cfg
from ..providers.auth import AuthError, AuthStore
from ..redact import redact_secret_values, redact_secrets
from ..tools.base import Tool, ToolContext, ToolResult
from ..tools.thread_context import (
    await_pending_approval,
    get_current_approval_context,
    get_current_approver,
    has_approval_notifier,
)
from ..util import ensure_dir, read_text, truncate
from .oauth_manager import ManagedMCPOAuth, get_mcp_oauth_manager

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "aegis", "version": "0.1.0"}
_MCP_CIRCUIT_BREAKER_THRESHOLD = 3
_MCP_CIRCUIT_BREAKER_COOLDOWN_SEC = 60.0
_MCP_RECONNECT_MAX_ATTEMPTS = 5
_MCP_RECONNECT_INITIAL_BACKOFF_SEC = 1.0
_MCP_RECONNECT_MAX_BACKOFF_SEC = 60.0
_MCP_DEFAULT_KEEPALIVE_INTERVAL_SEC = 180.0
_MCP_MIN_KEEPALIVE_INTERVAL_SEC = 5.0
_MCP_HTTP_SSE_READ_TIMEOUT_SEC = 300.0
_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "SYSTEMROOT", "WINDIR",
}
_SAFE_ENV_KEYS_UPPER = {k.upper() for k in _SAFE_ENV_KEYS}
_NAME_PART_RE = re.compile(r"[^A-Za-z0-9_]")
_INLINE_AUTH_TOKEN_RE = re.compile(r"\b(Bearer|Basic|Bot)\s+\S+", re.IGNORECASE)

_MCP_SDK_AVAILABLE = False
_MCP_SDK_CLIENT_SESSION = None
_MCP_SDK_STDIO_SERVER_PARAMETERS = None
_MCP_SDK_STDIO_CLIENT = None
_MCP_SDK_STREAMABLE_HTTP_CLIENT = None
_MCP_SDK_SAMPLING_CAPABILITY = None
_MCP_SDK_SAMPLING_TOOLS_CAPABILITY = None
_MCP_SDK_CREATE_MESSAGE_RESULT = None
_MCP_SDK_CREATE_MESSAGE_RESULT_WITH_TOOLS = None
_MCP_SDK_TEXT_CONTENT = None
_MCP_SDK_TOOL_USE_CONTENT = None
_MCP_SDK_ERROR_DATA = None
_MCP_SDK_ELICIT_RESULT = None
_MCP_SDK_LIST_ROOTS_RESULT = None
_MCP_SDK_ROOT = None
try:  # pragma: no cover - optional dependency absent in the default test env.
    from mcp import ClientSession as _MCP_SDK_CLIENT_SESSION
    from mcp import StdioServerParameters as _MCP_SDK_STDIO_SERVER_PARAMETERS
    from mcp.client.stdio import stdio_client as _MCP_SDK_STDIO_CLIENT

    _MCP_SDK_AVAILABLE = True
    try:
        from mcp.client.streamable_http import streamable_http_client as _MCP_SDK_STREAMABLE_HTTP_CLIENT
    except ImportError:
        try:
            from mcp.client.streamable_http import streamablehttp_client as _MCP_SDK_STREAMABLE_HTTP_CLIENT
        except ImportError:
            _MCP_SDK_STREAMABLE_HTTP_CLIENT = None
    try:
        from mcp.types import (
            CreateMessageResult as _MCP_SDK_CREATE_MESSAGE_RESULT,
            CreateMessageResultWithTools as _MCP_SDK_CREATE_MESSAGE_RESULT_WITH_TOOLS,
            ElicitResult as _MCP_SDK_ELICIT_RESULT,
            ErrorData as _MCP_SDK_ERROR_DATA,
            ListRootsResult as _MCP_SDK_LIST_ROOTS_RESULT,
            Root as _MCP_SDK_ROOT,
            SamplingCapability as _MCP_SDK_SAMPLING_CAPABILITY,
            SamplingToolsCapability as _MCP_SDK_SAMPLING_TOOLS_CAPABILITY,
            TextContent as _MCP_SDK_TEXT_CONTENT,
            ToolUseContent as _MCP_SDK_TOOL_USE_CONTENT,
        )
    except ImportError:
        pass
except ImportError:
    pass


class MCPError(RuntimeError):
    pass


class InvalidMCPUrlError(ValueError):
    pass


class MCPClient:
    def __init__(self, name: str, *, command: str | None = None, args: list[str] | None = None,
                 env: dict | None = None, url: str | None = None, headers: dict | None = None,
                 cwd: str | None = None, tool_filter: dict | None = None,
                 oauth: ManagedMCPOAuth | None = None,
                 oauth_required: bool = False,
                 elicitation: dict | None = None,
                 sampling: dict | None = None,
                 roots: list | dict | str | None = None,
                 keepalive_interval: float | None = None,
                 transport: str | None = None,
                 sdk: bool | None = None,
                 supports_parallel_tool_calls: bool | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.headers = headers or {}
        self.transport = str(transport or ("http" if url else "stdio")).strip().lower()
        self.oauth = oauth
        self.oauth_required = bool(oauth_required or oauth is not None)
        self.cwd = cwd
        self.tool_filter = tool_filter or {}
        self.elicitation = elicitation or {}
        self.sampling = sampling or {}
        self.roots = _normalize_roots_config(roots, cwd)
        self.sdk = _boolish(sdk, default=False)
        self.supports_parallel_tool_calls = _boolish(
            supports_parallel_tool_calls,
            default=False,
        )
        interval = _safe_float(keepalive_interval, _MCP_DEFAULT_KEEPALIVE_INTERVAL_SEC)
        self.keepalive_interval = max(_MCP_MIN_KEEPALIVE_INTERVAL_SEC, interval)
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._session_id: str | None = None
        self._initialized = False
        self._tools_cache: list[dict] = []
        self._tools_stale = False
        self._state = "disconnected"
        self._last_error = ""
        self._auth_needed = False
        self._auth_refresh_needed = False
        self._disabled_reason = ""
        self._ping_unsupported = False
        self._failure_count = 0
        self._breaker_opened_at: float | None = None
        self._reconnect_requested = False
        self._stdio_cv = threading.Condition(threading.RLock())
        self._stdio_responses: dict[object, dict] = {}
        self._stdio_reader_error: str | None = None
        self._stdio_reader_thread: threading.Thread | None = None
        self._stdio_closing = False
        self._stdio_context_lock = threading.RLock()
        self._pending_stdio_context: contextvars.Context | None = None
        self._pending_tool_context: ToolContext | None = None
        self._sampling_context_snapshot: dict | None = None
        self._sse_cv = threading.Condition(threading.RLock())
        self._sse_responses: dict[object, dict] = {}
        self._sse_reader_error: str | None = None
        self._sse_reader_thread: threading.Thread | None = None
        self._sse_closing = False
        self._sse_endpoint_url: str | None = None
        self._sse_http_client: httpx.Client | None = None
        self._sse_stream_cm = None
        self._lifecycle_cv = threading.Condition(threading.RLock())
        self._lifecycle_thread: threading.Thread | None = None
        self._lifecycle_shutdown = False
        self._lifecycle_reconnect_requested = False
        self._lifecycle_reconnect_reason = ""
        self._sampling_rate_timestamps: list[float] = []
        self._sampling_metrics = {"requests": 0, "errors": 0, "tool_use_count": 0}
        self._notifications: list[dict] = []
        self._transport_generation = 0
        self._transport_owner: dict[str, object] = {}
        self.initialize_result: object | None = None
        self._sdk_runner: _MCPSDKSessionRunner | None = None

    @property
    def is_http(self) -> bool:
        return bool(self.url)

    @property
    def is_sse(self) -> bool:
        return self.is_http and self.transport == "sse"

    @property
    def tools_stale(self) -> bool:
        return self._tools_stale

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def auth_refresh_needed(self) -> bool:
        return self._auth_refresh_needed

    @property
    def auth_needed(self) -> bool:
        return self._auth_needed

    @property
    def disabled(self) -> bool:
        return bool(self._disabled_reason)

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def breaker_state(self) -> str:
        if self._failure_count < _MCP_CIRCUIT_BREAKER_THRESHOLD:
            return "closed"
        opened_at = self._breaker_opened_at
        if opened_at is not None and (
            time.monotonic() - opened_at
        ) >= _MCP_CIRCUIT_BREAKER_COOLDOWN_SEC:
            return "half_open"
        return "open"

    @property
    def reconnect_requested(self) -> bool:
        return self._reconnect_requested

    @property
    def recent_notifications(self) -> list[dict]:
        return list(self._notifications)

    @property
    def lifecycle_metadata(self) -> dict:
        """Return transport-owner metadata for MCP lifecycle audits."""
        with self._lifecycle_cv:
            owner = dict(self._transport_owner)
            generation = self._transport_generation
            lifecycle_thread = self._lifecycle_thread
        stdio_thread = self._stdio_reader_thread
        sse_thread = self._sse_reader_thread
        return {
            "server": self.name,
            "transport": self.transport,
            "state": self._state,
            "initialized": self._initialized,
            "generation": generation,
            "owner": owner or None,
            "stdio_reader": _thread_metadata(stdio_thread),
            "sse_reader": _thread_metadata(sse_thread),
            "lifecycle_thread": _thread_metadata(lifecycle_thread),
            "sampling": dict(self._sampling_metrics),
        }

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _mark_error_state(self, error: object) -> None:
        text = _nonempty_exc_text(error)
        self._last_error = redact_secrets(text)
        lowered = text.lower()
        if _is_auth_needed_error_text(lowered):
            self._mark_auth_needed(text)
        elif _is_auth_refresh_error_text(lowered):
            self._state = "auth_refresh_needed"
            self._auth_refresh_needed = True
        elif _is_session_expired_error_text(lowered):
            self._state = "reconnect_needed"
        else:
            self._state = "error"

    def _mark_reconnect_needed(self, error: object) -> None:
        text = _nonempty_exc_text(error)
        self._last_error = redact_secrets(text)
        if _is_auth_needed_error_text(text.lower()):
            self._mark_auth_needed(text)
        elif _is_auth_refresh_error_text(text.lower()):
            self._state = "auth_refresh_needed"
            self._auth_refresh_needed = True
        else:
            self._state = "reconnect_needed"

    def _mark_auth_needed(self, error: object | None = None) -> None:
        text = _nonempty_exc_text(error) if error else (
            f"MCP server '{self.name}' requires OAuth login. "
            f"Run `aegis mcp login {self.name}` before using this server."
        )
        self._last_error = redact_secrets(text)
        self._state = "auth_needed"
        self._auth_needed = True
        self._auth_refresh_needed = True
        self._disabled_reason = "auth_needed"

    def _record_remote_success(self) -> None:
        self._failure_count = 0
        self._breaker_opened_at = None
        self._reconnect_requested = False
        self._last_error = ""
        self._auth_needed = False
        self._auth_refresh_needed = False
        self._disabled_reason = ""
        if self._state in {"error", "parked", "reconnect_needed", "reconnecting"}:
            self._state = "connected"
        self._notify_lifecycle_waiters()

    def _record_remote_failure(self, error: object) -> None:
        self._failure_count += 1
        if self._failure_count >= _MCP_CIRCUIT_BREAKER_THRESHOLD:
            self._breaker_opened_at = time.monotonic()
            if self._state != "auth_refresh_needed":
                self._state = "parked"
        self._notify_lifecycle_waiters()

    def _breaker_block_message(self) -> str | None:
        if self._failure_count < _MCP_CIRCUIT_BREAKER_THRESHOLD:
            return None
        opened_at = self._breaker_opened_at
        elapsed = time.monotonic() - opened_at if opened_at is not None else 0.0
        if elapsed >= _MCP_CIRCUIT_BREAKER_COOLDOWN_SEC:
            return None
        remaining = max(1, int(_MCP_CIRCUIT_BREAKER_COOLDOWN_SEC - elapsed))
        return (
            f"MCP server '{self.name}' is unreachable after "
            f"{self._failure_count} consecutive failures. Auto-retry available "
            f"in ~{remaining}s. Do NOT retry this tool yet; use another approach "
            "or ask the user to check the MCP server."
        )

    def _transport_live(self) -> bool:
        if self.sdk and self._sdk_runner is not None:
            return self._initialized and self._sdk_runner.alive
        if not self._initialized:
            return False
        if self.is_sse:
            thread = self._sse_reader_thread
            return (
                self._sse_endpoint_url is not None
                and thread is not None
                and thread.is_alive()
                and not self._sse_reader_error
            )
        if self.is_http:
            return True
        return self._proc is not None and self._proc.poll() is None

    def _preflight_remote_call(self) -> None:
        blocked = self._breaker_block_message()
        if blocked:
            raise MCPError(blocked)
        if self.breaker_state == "half_open" and not self._transport_live():
            self.request_reconnect("breaker half-open probe")
            raise MCPError(
                f"MCP server '{self.name}' transport is down; reconnect requested. "
                "Do NOT retry this tool immediately; give it a few seconds to come back."
            )

    def parked_call_error(self) -> str | None:
        blocked = self._breaker_block_message()
        if blocked:
            return blocked
        if self.breaker_state == "half_open":
            return None
        if self._state not in {"reconnect_needed", "reconnecting", "parked"}:
            return None
        return (
            f"MCP server '{self.name}' transport is down; reconnect requested. "
            "Wait/backoff before retrying this tool so the MCP lifecycle can "
            "rebuild the connection."
        )

    def _handle_notification(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "notifications/tools/list_changed":
            self._tools_stale = True
        if method in {
            "notifications/progress",
            "notifications/message",
            "notifications/logging/message",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        }:
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                params = {"value": params}
            self._notifications.append({
                "method": method,
                "params": _redact_notification_payload(redact_secret_values(params)),
                "received_at": time.time(),
            })
            if len(self._notifications) > 100:
                del self._notifications[:-100]

    def _handle_server_request(self, msg: dict) -> bool:
        response = self._server_request_response(msg)
        if response is None:
            return False
        self._send_server_response(response)
        return True

    def _server_request_response(self, msg: dict) -> dict | None:
        method = msg.get("method")
        if not method or "id" not in msg:
            return None
        if method == "sampling/createMessage":
            if not self.sampling.get("enabled", True):
                return _jsonrpc_error(msg.get("id"), -32601, "Method not found: sampling/createMessage")
            try:
                result = self._handle_sampling_request(msg.get("params") or {})
            except Exception as exc:  # noqa: BLE001
                self._sampling_metrics["errors"] += 1
                return _jsonrpc_error(
                    msg.get("id"),
                    -32000,
                    f"sampling/createMessage failed: {redact_secrets(_nonempty_exc_text(exc))}",
                )
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": result,
            }
        if method == "elicitation/create":
            result = self._handle_elicitation_request(msg.get("params") or {})
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": result,
            }
        if method == "roots/list":
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {"roots": list(self.roots)},
            }
        return {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def _send_server_response(self, response: dict) -> None:
        if self.is_http:
            if self.is_sse and self._sse_endpoint_url:
                self._sse_send_message(response)
                return
            self._http_request(response, timeout=_safe_float(self.sampling.get("timeout"), 60.0))
            return
        self._send_stdio_message(response)

    def _sdk_session_kwargs(self) -> dict:
        """Return SDK-native ClientSession callbacks accepted by this SDK build."""
        kwargs: dict[str, object] = {}
        session_cls = _MCP_SDK_CLIENT_SESSION
        if session_cls is None:
            return kwargs
        if self.sampling.get("enabled", True):
            if _callable_accepts_keyword(session_cls, "sampling_callback"):
                kwargs["sampling_callback"] = self._sdk_sampling_callback
            if (
                _MCP_SDK_SAMPLING_CAPABILITY is not None
                and _callable_accepts_keyword(session_cls, "sampling_capabilities")
            ):
                tools_capability = (
                    _make_sdk_type(_MCP_SDK_SAMPLING_TOOLS_CAPABILITY)
                    if _MCP_SDK_SAMPLING_TOOLS_CAPABILITY is not None
                    else None
                )
                kwargs["sampling_capabilities"] = _make_sdk_type(
                    _MCP_SDK_SAMPLING_CAPABILITY,
                    tools=tools_capability,
                )
        if self.elicitation.get("enabled", True) and _callable_accepts_keyword(
            session_cls,
            "elicitation_callback",
        ):
            kwargs["elicitation_callback"] = self._sdk_elicitation_callback
        if self.roots:
            if _callable_accepts_keyword(session_cls, "list_roots_callback"):
                kwargs["list_roots_callback"] = self._sdk_list_roots_callback
            elif _callable_accepts_keyword(session_cls, "roots_callback"):
                kwargs["roots_callback"] = self._sdk_list_roots_callback
        if _callable_accepts_keyword(session_cls, "message_handler"):
            kwargs["message_handler"] = self._sdk_message_handler
        if _callable_accepts_keyword(session_cls, "logging_callback"):
            kwargs["logging_callback"] = self._sdk_logging_callback
        return kwargs

    async def _sdk_sampling_callback(self, _context, params):
        try:
            plain_params = _sdk_to_plain(params)
            if not isinstance(plain_params, dict):
                plain_params = {}
            result = await asyncio.to_thread(self._handle_sampling_request, plain_params)
        except Exception as exc:  # noqa: BLE001
            self._sampling_metrics["errors"] += 1
            return _sdk_error_data(
                f"sampling/createMessage failed: {redact_secrets(_nonempty_exc_text(exc))}",
                code=-32000,
            )
        return _sdk_sampling_result(result)

    async def _sdk_elicitation_callback(self, _context, params):
        try:
            plain_params = _sdk_to_plain(params)
            if not isinstance(plain_params, dict):
                plain_params = {}
            result = await asyncio.to_thread(self._handle_elicitation_request, plain_params)
        except Exception:  # noqa: BLE001
            result = {"action": "decline"}
        return _sdk_elicit_result(result)

    async def _sdk_list_roots_callback(self, *_args):
        return _sdk_list_roots_result(self.roots)

    async def _sdk_logging_callback(self, *args):
        params = _sdk_to_plain(args[-1] if args else {})
        if not isinstance(params, dict):
            params = {"value": params}
        self._handle_notification({
            "method": "notifications/logging/message",
            "params": params,
        })

    async def _sdk_message_handler(self, message):
        notification = _sdk_notification_to_jsonrpc(message)
        if notification is not None:
            self._handle_notification(notification)

    def _handle_sampling_request(self, params: dict) -> dict:
        sampling_context = self._current_sampling_context()
        if sampling_context is None:
            raise MCPError(
                f"MCP server '{self.name}' requested sampling before an agent context was available"
            )
        if not self._sampling_rate_allowed():
            raise MCPError(
                f"sampling rate limit exceeded for MCP server '{self.name}'"
            )
        messages = _sampling_messages_from_params(params)
        tools = _sampling_tools_from_params(params)
        max_tokens_cap = _safe_int(self.sampling.get("max_tokens_cap"), 4096)
        max_tokens = min(_safe_int(params.get("maxTokens"), max_tokens_cap), max_tokens_cap)
        model = self._sampling_model(params)

        provider = self._sampling_provider(sampling_context)
        if provider is None:
            raise MCPError(
                f"MCP server '{self.name}' requested sampling but no provider is active"
            )

        from ..agent.loop import _provider_complete

        reasoning = str(self.sampling.get("reasoning") or sampling_context.get("reasoning") or "off")

        def complete():
            return _provider_complete(
                provider,
                messages,
                tools=tools or None,
                stream=False,
                model=model,
                max_tokens=max_tokens,
                reasoning=reasoning,
                cwd=sampling_context.get("cwd"),
                metadata={"source": "mcp_sampling", "mcp_server": self.name},
            )

        captured = sampling_context.get("contextvars")
        response = captured.copy().run(complete) if captured is not None else complete()
        self._sampling_metrics["requests"] += 1
        return self._sampling_result_from_response(response, provider)

    def _current_tool_context(self) -> ToolContext | None:
        with self._stdio_context_lock:
            return self._pending_tool_context

    def _remember_sampling_context(self, ctx: ToolContext | None) -> None:
        snapshot = self._sampling_context_from_tool_context(ctx, capture_contextvars=True)
        if snapshot is None:
            return
        with self._stdio_context_lock:
            self._sampling_context_snapshot = snapshot

    def _current_sampling_context(self) -> dict | None:
        ctx = self._current_tool_context()
        if ctx is not None:
            return self._sampling_context_from_tool_context(ctx, capture_contextvars=False)
        with self._stdio_context_lock:
            snapshot = self._sampling_context_snapshot
        if snapshot is None:
            return None
        idle_ttl = _safe_float(self.sampling.get("idle_context_ttl"), 60.0)
        captured_at = snapshot.get("captured_at")
        if (
            idle_ttl <= 0
            or not isinstance(captured_at, (int, float))
            or time.monotonic() - captured_at > idle_ttl
        ):
            with self._stdio_context_lock:
                if self._sampling_context_snapshot is snapshot:
                    self._sampling_context_snapshot = None
            return None
        return dict(snapshot)

    def _sampling_context_from_tool_context(
        self,
        ctx: ToolContext | None,
        *,
        capture_contextvars: bool,
    ) -> dict | None:
        if ctx is None:
            return None
        agent = getattr(ctx, "agent", None)
        provider = getattr(agent, "provider", None)
        config = getattr(ctx, "config", None) or getattr(agent, "config", None)
        if provider is None and config is None:
            return None
        return {
            "provider": provider,
            "config": config,
            "cwd": getattr(ctx, "cwd", None),
            "reasoning": getattr(agent, "reasoning", "off"),
            "contextvars": contextvars.copy_context() if capture_contextvars else None,
            "captured_at": time.monotonic() if capture_contextvars else None,
        }

    @staticmethod
    def _sampling_provider(sampling_context: dict):
        provider = sampling_context.get("provider")
        config = sampling_context.get("config")
        if config is None:
            return provider
        try:
            from ..auxiliary import AuxRouter

            return AuxRouter(config, fallback_provider=provider).provider_for("mcp_sampling")
        except Exception:  # noqa: BLE001
            return provider

    def _sampling_rate_allowed(self) -> bool:
        max_rpm = _safe_int(self.sampling.get("max_rpm"), 10)
        now = time.monotonic()
        window_start = now - 60.0
        self._sampling_rate_timestamps = [
            timestamp for timestamp in self._sampling_rate_timestamps
            if timestamp > window_start
        ]
        if len(self._sampling_rate_timestamps) >= max_rpm:
            return False
        self._sampling_rate_timestamps.append(now)
        return True

    def _sampling_model(self, params: dict) -> str | None:
        configured = str(self.sampling.get("model") or "").strip()
        if configured:
            return configured
        preferences = params.get("modelPreferences") or params.get("model_preferences") or {}
        hints = preferences.get("hints") if isinstance(preferences, dict) else []
        if isinstance(hints, list):
            for hint in hints:
                if isinstance(hint, dict) and str(hint.get("name") or "").strip():
                    return str(hint["name"]).strip()
        return None

    def _sampling_result_from_response(self, response, provider) -> dict:
        model = (
            str(getattr(response, "model", "") or "")
            or str(getattr(provider, "model", "") or "")
            or str(self.sampling.get("model") or "")
            or "unknown"
        )
        tool_calls = list(getattr(response, "tool_calls", []) or [])
        if tool_calls:
            self._sampling_metrics["tool_use_count"] += len(tool_calls)
            return {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": str(getattr(call, "id", "")),
                        "name": str(getattr(call, "name", "")),
                        "input": getattr(call, "arguments", {}) or {},
                    }
                    for call in tool_calls
                ],
                "model": model,
                "stopReason": "toolUse",
            }
        text = str(getattr(response, "text", "") or getattr(response, "content", "") or "")
        finish_reason = str(getattr(response, "finish_reason", "") or "")
        stop_reason = {
            "length": "maxTokens",
            "max_tokens": "maxTokens",
            "tool_calls": "toolUse",
            "tool_use": "toolUse",
            "stop_sequence": "stopSequence",
        }.get(finish_reason, "endTurn")
        return {
            "role": "assistant",
            "content": {"type": "text", "text": redact_secrets(text)},
            "model": model,
            "stopReason": stop_reason,
        }

    def _handle_elicitation_request(self, params: dict) -> dict:
        if not self.elicitation.get("enabled", True):
            return {"action": "decline"}
        mode = str(params.get("mode") or "form")
        if mode == "url":
            return {"action": "decline"}
        message = str(params.get("message") or (
            f"MCP server '{self.name}' is requesting your approval"
        ))
        schema = params.get("requested_schema") or params.get("schema") or {}
        description = _format_elicitation_schema_summary(schema, self.name)
        timeout = _safe_float(self.elicitation.get("timeout"), 300.0)
        with self._stdio_context_lock:
            captured = self._pending_stdio_context

        def _route() -> str:
            approver = get_current_approver()
            prompt = f"{message}\n\n{description}"
            if approver is not None:
                try:
                    return "accept" if _choice_allows(approver(prompt)) else "decline"
                except Exception:  # noqa: BLE001
                    return "decline"
            approval_context = get_current_approval_context(default_session_key="default")
            if not has_approval_notifier(approval_context.session_key):
                return "decline"
            decision = await_pending_approval(
                {
                    "type": "mcp_elicitation",
                    "surface": f"mcp-elicitation/{self.name}",
                    "server": self.name,
                    "message": message,
                    "description": description,
                    "schema": schema if isinstance(schema, dict) else {},
                    "turn_id": approval_context.turn_id,
                    "tool_call_id": approval_context.tool_call_id,
                },
                session_key=approval_context.session_key,
                timeout_seconds=timeout,
            )
            if decision.outcome == "approved":
                return "accept"
            if decision.outcome in {"timeout", "interrupted"}:
                return "cancel"
            return "decline"

        action = captured.copy().run(_route) if captured is not None else _route()
        if action == "accept":
            return {"action": "accept", "content": {}}
        if action == "cancel":
            return {"action": "cancel"}
        return {"action": "decline"}

    def request_reconnect(self, reason: str = "") -> None:
        reason_text = reason or "reconnect requested"
        self._reconnect_requested = True
        self._mark_reconnect_needed(reason_text)
        thread = self._lifecycle_thread
        if thread is not None or self._initialized:
            self._ensure_lifecycle_worker()
            with self._lifecycle_cv:
                self._lifecycle_reconnect_requested = True
                self._lifecycle_reconnect_reason = reason_text
                self._lifecycle_cv.notify_all()

    def _notify_lifecycle_waiters(self) -> None:
        with self._lifecycle_cv:
            self._lifecycle_cv.notify_all()

    def _begin_transport_owner(self, transport: str) -> int:
        thread = threading.current_thread()
        with self._lifecycle_cv:
            self._transport_generation += 1
            generation = self._transport_generation
            self._transport_owner = {
                "generation": generation,
                "transport": transport,
                "owner_thread": thread.name,
                "owner_ident": thread.ident,
                "state": "starting",
                "started_at": time.time(),
            }
            self._lifecycle_cv.notify_all()
            return generation

    def _mark_transport_ready(self, generation: int) -> None:
        with self._lifecycle_cv:
            if generation != self._transport_generation:
                return
            owner = dict(self._transport_owner)
            owner["state"] = "ready"
            owner["ready_at"] = time.time()
            owner["session_id_present"] = bool(self._session_id)
            self._transport_owner = owner
            self._lifecycle_cv.notify_all()

    def _retire_transport_owner(self, reason: str) -> None:
        thread = threading.current_thread()
        with self._lifecycle_cv:
            self._transport_generation += 1
            self._transport_owner = {
                "generation": self._transport_generation,
                "transport": self.transport,
                "owner_thread": thread.name,
                "owner_ident": thread.ident,
                "state": reason,
                "closed_at": time.time(),
            }
            self._lifecycle_cv.notify_all()

    def _is_current_transport_generation(self, generation: int | None) -> bool:
        if generation is None:
            return True
        with self._lifecycle_cv:
            return generation == self._transport_generation

    def _ensure_lifecycle_worker(self) -> None:
        current = threading.current_thread()
        with self._lifecycle_cv:
            if current is self._lifecycle_thread:
                return
            if self._lifecycle_thread is not None and self._lifecycle_thread.is_alive():
                return
            self._lifecycle_shutdown = False
            thread = threading.Thread(
                target=self._lifecycle_loop,
                name=f"aegis-mcp-lifecycle-{_safe_name_part(self.name)}",
                daemon=True,
            )
            self._lifecycle_thread = thread
            thread.start()

    def _stop_lifecycle_worker(self) -> None:
        current = threading.current_thread()
        with self._lifecycle_cv:
            thread = self._lifecycle_thread
            self._lifecycle_shutdown = True
            self._lifecycle_reconnect_requested = False
            self._lifecycle_cv.notify_all()
        if thread is not None and thread is not current:
            thread.join(timeout=2.0)
        with self._lifecycle_cv:
            if self._lifecycle_thread is thread and (
                thread is None or not thread.is_alive()
            ):
                self._lifecycle_thread = None

    def _lifecycle_loop(self) -> None:
        next_keepalive = time.monotonic() + self.keepalive_interval
        while True:
            action = ""
            with self._lifecycle_cv:
                while not self._lifecycle_shutdown and not self._lifecycle_reconnect_requested:
                    remaining = next_keepalive - time.monotonic()
                    if remaining > 0:
                        self._lifecycle_cv.wait(remaining)
                        continue
                    next_keepalive = time.monotonic() + self.keepalive_interval
                    if self._lifecycle_shutdown or self._lifecycle_reconnect_requested:
                        break
                    if self._initialized and self._state == "connected":
                        action = "keepalive"
                        break
                if self._lifecycle_shutdown:
                    return
                if self._lifecycle_reconnect_requested:
                    self._lifecycle_reconnect_requested = False
                    action = "reconnect"
            if action == "keepalive":
                self.keepalive()
                next_keepalive = time.monotonic() + self.keepalive_interval
            elif action == "reconnect":
                self._run_lifecycle_reconnect()
                next_keepalive = time.monotonic() + self.keepalive_interval

    def _run_lifecycle_reconnect(self) -> None:
        attempts = 0
        backoff = _MCP_RECONNECT_INITIAL_BACKOFF_SEC
        while not self._lifecycle_shutdown:
            attempts += 1
            self._state = "reconnecting"
            self._notify_lifecycle_waiters()
            self._close_transport(mark_disconnected=False)
            try:
                self.connect()
            except Exception as exc:  # noqa: BLE001
                self._mark_reconnect_needed(exc)
                if self._state in {"auth_needed", "auth_refresh_needed"}:
                    self._notify_lifecycle_waiters()
                    return
                if attempts >= _MCP_RECONNECT_MAX_ATTEMPTS:
                    self._state = "parked"
                    self._last_error = redact_secrets(_nonempty_exc_text(exc))
                    self._notify_lifecycle_waiters()
                    return
                if self._sleep_or_shutdown(backoff):
                    return
                backoff = min(backoff * 2, _MCP_RECONNECT_MAX_BACKOFF_SEC)
                continue
            self._record_remote_success()
            return

    def _sleep_or_shutdown(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        with self._lifecycle_cv:
            while not self._lifecycle_shutdown:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lifecycle_cv.wait(remaining)
            return True

    def reconnect_for_retry(self, reason: str, *, timeout: float = 15.0) -> "MCPClient":
        thread = self._lifecycle_thread
        if thread is None or not thread.is_alive() or thread is threading.current_thread():
            return self.reconnect()
        self.request_reconnect(reason)
        if self._wait_until_connected(timeout):
            return self
        raise MCPError(
            f"MCP server '{self.name}' reconnect did not complete within {int(timeout)}s"
        )

    def _wait_until_connected(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lifecycle_cv:
            while time.monotonic() < deadline:
                if self._initialized and self._state == "connected":
                    return True
                if self._state in {"auth_needed", "auth_refresh_needed", "parked"}:
                    return False
                self._lifecycle_cv.wait(min(0.25, max(0.0, deadline - time.monotonic())))
            return self._initialized and self._state == "connected"

    def needs_oauth_login_before_startup(self) -> bool:
        """Return True when OAuth is configured but no cached login exists.

        Upstream clients treat noninteractive OAuth startup without cached tokens as a
        non-fatal auth-needed server, not a blocking browser/auth flow. AEGIS'
        OAuth provider will fail before the HTTP request in this case; detect it
        up front so agent startup can continue without touching the MCP server.
        """
        if not self.oauth_required:
            return False
        if self.oauth is None:
            return True
        try:
            if self.oauth.available():
                return False
        except Exception:  # noqa: BLE001
            pass
        return self._cached_oauth_credentials() is None

    def _cached_oauth_credentials(self) -> dict | None:
        if self.oauth is None:
            return None
        try:
            provider = self.oauth.oauth.provider
            creds = self.oauth.auth.store.load(provider)
        except Exception:  # noqa: BLE001
            return None
        return creds if isinstance(creds, dict) and creds else None

    def reconnect(self) -> "MCPClient":
        self.close()
        self._state = "reconnecting"
        try:
            return self.connect()
        except Exception as exc:
            self._mark_reconnect_needed(exc)
            raise

    # -- transport ----------------------------------------------------------
    def _require_sdk_runner(self) -> "_MCPSDKSessionRunner":
        runner = self._sdk_runner
        if runner is None or not runner.alive:
            raise MCPError(f"{self.name}: MCP SDK session is not connected")
        return runner

    def _connect_sdk(self) -> "MCPClient":
        if self._initialized:
            return self
        if not _MCP_SDK_AVAILABLE or _MCP_SDK_CLIENT_SESSION is None:
            raise MCPError(
                f"{self.name}: MCP SDK transport requested but the 'mcp' package is not installed"
            )
        if self.needs_oauth_login_before_startup():
            self._mark_auth_needed()
            raise MCPError(self.last_error)
        if not self.is_http and not self.command:
            raise MCPError(f"{self.name}: no command or url configured")
        runner = _MCPSDKSessionRunner(self)
        self._sdk_runner = runner
        try:
            initialize_result, generation = runner.start()
        except Exception as exc:  # noqa: BLE001
            self._sdk_runner = None
            self._mark_error_state(exc)
            raise
        self.initialize_result = initialize_result
        self._initialized = True
        self._state = "connected"
        self._last_error = ""
        self._auth_refresh_needed = False
        self._ping_unsupported = False
        self._mark_transport_ready(generation)
        self._record_remote_success()
        self._ensure_lifecycle_worker()
        return self

    def _sdk_request(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict | None:
        return self._require_sdk_runner().call("request", method, params or {}, notify)

    def _sdk_call_tool(
        self,
        name: str,
        arguments: dict,
        ctx: ToolContext | None = None,
    ) -> tuple[str, bool]:
        self._preflight_remote_call()
        try:
            with self._stdio_context_lock:
                previous_context = self._pending_tool_context
                previous_stdio_context = self._pending_stdio_context
                self._pending_tool_context = ctx
                self._pending_stdio_context = contextvars.copy_context()
                self._remember_sampling_context(ctx)
            try:
                text, is_error = self._require_sdk_runner().call("call_tool", name, arguments)
            finally:
                with self._stdio_context_lock:
                    self._pending_tool_context = previous_context
                    self._pending_stdio_context = previous_stdio_context
        except Exception as exc:
            self._record_remote_failure(exc)
            raise
        if is_error:
            self._record_remote_failure("MCP tool returned an error")
        else:
            self._record_remote_success()
        return text, is_error

    def _spawn(self) -> None:
        generation = self._begin_transport_owner("stdio")
        env = _safe_subprocess_env(self.env)
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=env, cwd=self.cwd,
        )
        with self._stdio_cv:
            self._stdio_responses.clear()
            self._stdio_reader_error = None
            self._stdio_closing = False
        self._start_stdio_reader(generation)

    def _start_stdio_reader(self, generation: int | None = None) -> None:
        if not self._proc or not self._proc.stdout:
            return
        stdout = self._proc.stdout
        if generation is None:
            with self._lifecycle_cv:
                generation = self._transport_generation
        thread = threading.Thread(
            target=self._stdio_reader_loop,
            args=(stdout, generation),
            name=f"aegis-mcp-stdio-{_safe_name_part(self.name)}",
            daemon=True,
        )
        self._stdio_reader_thread = thread
        thread.start()

    def _stdio_reader_loop(self, stdout=None, generation: int | None = None) -> None:
        while True:
            if stdout is None:
                return
            if not self._is_current_transport_generation(generation):
                return
            try:
                line = stdout.readline()
            except Exception as exc:  # noqa: BLE001
                self._record_stdio_reader_error(exc, generation=generation)
                return
            if not self._is_current_transport_generation(generation):
                return
            if not line:
                self._record_stdio_reader_error("server closed the connection", generation=generation)
                return
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in msg:
                self._handle_notification(msg)
                continue
            if self._handle_server_request(msg):
                continue
            with self._stdio_cv:
                self._stdio_responses[msg.get("id")] = msg
                self._stdio_cv.notify_all()

    def _send_stdio_message(self, payload: dict) -> None:
        proc = self._proc
        stdin = proc.stdin if proc is not None else None
        if stdin is None:
            return
        with self._stdio_cv:
            try:
                stdin.write(json.dumps(payload) + "\n")
                stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self._record_stdio_reader_error(exc)

    def _record_stdio_reader_error(
        self,
        error: object,
        *,
        generation: int | None = None,
    ) -> None:
        if not self._is_current_transport_generation(generation):
            with self._stdio_cv:
                self._stdio_cv.notify_all()
            return
        text = _nonempty_exc_text(error)
        with self._stdio_cv:
            if self._stdio_closing:
                self._stdio_cv.notify_all()
                return
            self._stdio_reader_error = f"{self.name}: {text}"
            self._stdio_cv.notify_all()
        self._mark_error_state(MCPError(self._stdio_reader_error))

    def _stdio_request(self, payload: dict, timeout: float = 30.0) -> dict | None:
        assert self._proc and self._proc.stdin and self._proc.stdout
        wanted = payload.get("id")
        deadline = time.monotonic() + timeout
        with self._stdio_context_lock:
            self._pending_stdio_context = contextvars.copy_context()
        try:
            with self._stdio_cv:
                if self._stdio_reader_error:
                    raise MCPError(self._stdio_reader_error)
                try:
                    self._proc.stdin.write(json.dumps(payload) + "\n")
                    self._proc.stdin.flush()
                except Exception as exc:  # noqa: BLE001
                    self._record_stdio_reader_error(exc)
                    raise MCPError(f"{self.name}: {_nonempty_exc_text(exc)}") from exc
                if wanted is None:  # notification, no response expected
                    return None
                while True:
                    if wanted in self._stdio_responses:
                        return self._stdio_responses.pop(wanted)
                    if self._stdio_reader_error:
                        raise MCPError(self._stdio_reader_error)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MCPError(
                            f"{self.name}: timed out waiting for response to {payload.get('method')}"
                        )
                    self._stdio_cv.wait(remaining)
        finally:
            with self._stdio_context_lock:
                self._pending_stdio_context = None

    def _http_headers(
        self,
        *,
        content_type: bool = True,
        session: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.headers,
        }
        if content_type:
            headers["Content-Type"] = "application/json"
        if self.oauth is not None:
            try:
                headers.update(self.oauth.headers())
            except AuthError as exc:
                self._auth_refresh_needed = True
                self._state = "auth_refresh_needed"
                raise MCPError(redact_secrets(_nonempty_exc_text(exc))) from exc
        if session and self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        return headers

    def _http_request(self, payload: dict, timeout: float = 60.0) -> dict | None:
        if self.is_sse:
            return self._sse_request(payload, timeout=timeout)
        headers = self._http_headers()
        timeout_obj = httpx.Timeout(timeout, read=_MCP_HTTP_SSE_READ_TIMEOUT_SEC)
        with httpx.Client(timeout=timeout_obj) as c:
            for attempt in range(2):
                with c.stream("POST", self.url, headers=headers, json=payload) as r:
                    if r.status_code in {401, 403} and self.oauth is not None and attempt == 0:
                        body = _response_text(r, limit=300)
                        if self.oauth.handle_401(_auth_failure_token(headers), {
                            "status_code": r.status_code,
                            "message": body,
                        }):
                            headers = self._http_headers()
                            continue
                    return self._consume_http_response(r, payload)
        raise MCPError(f"{self.name}: HTTP auth retry exhausted")

    def _consume_http_response(self, response: httpx.Response, payload: dict) -> dict | None:
        if response.status_code >= 400:
            body = _response_text(response, limit=200)
            if response.status_code in {401, 403}:
                self._auth_refresh_needed = True
                self._state = "auth_refresh_needed"
            raise MCPError(redact_secrets(f"{self.name}: HTTP {response.status_code}: {body}"))
        if "MCP-Session-Id" in response.headers:
            self._session_id = response.headers["MCP-Session-Id"]
        if "id" not in payload:
            return None
        ctype = response.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return self._read_sse_response_lines(response.iter_lines(), payload["id"])
        body = response.read()
        if not body:
            return None
        return json.loads(body.decode(response.encoding or "utf-8", errors="replace"))

    def _read_sse_response_lines(self, lines, wanted_id: object) -> dict:
        event_name = "message"
        data_parts: list[str] = []
        for raw_line in lines:
            line = _coerce_sse_line(raw_line)
            if not line:
                msg = self._handle_sse_event(event_name, "\n".join(data_parts))
                if isinstance(msg, dict) and msg.get("id") == wanted_id:
                    return msg
                event_name = "message"
                data_parts = []
                continue
            if line.startswith(":"):
                continue
            field, sep, value = line.partition(":")
            if not sep:
                continue
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value or "message"
            elif field == "data":
                data_parts.append(value)
        if data_parts:
            msg = self._handle_sse_event(event_name, "\n".join(data_parts))
            if isinstance(msg, dict) and msg.get("id") == wanted_id:
                return msg
        raise MCPError(f"{self.name}: no matching SSE response")

    def _handle_sse_event(self, event_name: str, data: str) -> dict | None:
        data = data.strip()
        if not data or data == "[DONE]":
            return None
        if event_name == "endpoint":
            endpoint = urljoin(str(self.url), data)
            with self._sse_cv:
                self._sse_endpoint_url = endpoint
                self._sse_cv.notify_all()
            return None
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return None
        if not isinstance(msg, dict):
            return None
        if "id" not in msg:
            self._handle_notification(msg)
            return None
        response = self._server_request_response(msg)
        if response is not None:
            try:
                self._send_server_response(response)
            except Exception as exc:  # noqa: BLE001
                self._mark_error_state(exc)
            return None
        return msg

    def _start_sse_transport(self, timeout: float = 60.0) -> None:
        with self._sse_cv:
            reader = self._sse_reader_thread
            if (
                self._sse_endpoint_url
                and reader is not None
                and reader.is_alive()
                and not self._sse_reader_error
            ):
                return
            self._sse_responses.clear()
            self._sse_reader_error = None
            self._sse_endpoint_url = None
            self._sse_closing = False

        generation = self._begin_transport_owner("sse")
        client = httpx.Client(
            timeout=httpx.Timeout(timeout, read=_MCP_HTTP_SSE_READ_TIMEOUT_SEC),
            follow_redirects=True,
        )
        stream_cm = client.stream(
            "GET",
            self.url,
            headers=self._http_headers(content_type=False, session=False),
        )
        try:
            response = stream_cm.__enter__()
            if response.status_code >= 400:
                body = _response_text(response, limit=200)
                raise MCPError(f"{self.name}: SSE HTTP {response.status_code}: {body}")
        except Exception:
            try:
                stream_cm.__exit__(None, None, None)
            finally:
                client.close()
            raise

        thread = threading.Thread(
            target=self._sse_reader_loop,
            args=(response, generation),
            name=f"aegis-mcp-sse-{_safe_name_part(self.name)}",
            daemon=True,
        )
        with self._sse_cv:
            self._sse_http_client = client
            self._sse_stream_cm = stream_cm
            self._sse_reader_thread = thread
        thread.start()

        deadline = time.monotonic() + timeout
        with self._sse_cv:
            while not self._sse_endpoint_url and not self._sse_reader_error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._sse_cv.wait(min(0.25, remaining))
            if self._sse_reader_error:
                raise MCPError(self._sse_reader_error)
            if not self._sse_endpoint_url:
                raise MCPError(f"{self.name}: timed out waiting for SSE endpoint")

    def _sse_reader_loop(
        self,
        response: httpx.Response,
        generation: int | None = None,
    ) -> None:
        event_name = "message"
        data_parts: list[str] = []
        try:
            for raw_line in response.iter_lines():
                if not self._is_current_transport_generation(generation):
                    return
                line = _coerce_sse_line(raw_line)
                if not line:
                    self._dispatch_background_sse_event(
                        event_name,
                        "\n".join(data_parts),
                        generation=generation,
                    )
                    event_name = "message"
                    data_parts = []
                    continue
                if line.startswith(":"):
                    continue
                field, sep, value = line.partition(":")
                if not sep:
                    continue
                if value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event_name = value or "message"
                elif field == "data":
                    data_parts.append(value)
            if data_parts:
                self._dispatch_background_sse_event(
                    event_name,
                    "\n".join(data_parts),
                    generation=generation,
                )
            with self._sse_cv:
                closing = self._sse_closing
            if not closing:
                self._record_sse_reader_error("connection closed", generation=generation)
        except Exception as exc:  # noqa: BLE001
            with self._sse_cv:
                closing = self._sse_closing
            if not closing:
                self._record_sse_reader_error(exc, generation=generation)

    def _dispatch_background_sse_event(
        self,
        event_name: str,
        data: str,
        *,
        generation: int | None = None,
    ) -> None:
        if not self._is_current_transport_generation(generation):
            return
        msg = self._handle_sse_event(event_name, data)
        if not isinstance(msg, dict):
            return
        with self._sse_cv:
            self._sse_responses[msg.get("id")] = msg
            self._sse_cv.notify_all()

    def _record_sse_reader_error(
        self,
        error: object,
        *,
        generation: int | None = None,
    ) -> None:
        if not self._is_current_transport_generation(generation):
            with self._sse_cv:
                self._sse_cv.notify_all()
            return
        text = _nonempty_exc_text(error)
        with self._sse_cv:
            if self._sse_closing:
                self._sse_cv.notify_all()
                return
            self._sse_reader_error = f"{self.name}: {text}"
            self._sse_cv.notify_all()
        self._mark_error_state(MCPError(self._sse_reader_error))

    def _sse_request(self, payload: dict, timeout: float = 60.0) -> dict | None:
        if not self._sse_endpoint_url:
            self._start_sse_transport(timeout=timeout)
        direct = self._sse_send_message(payload, timeout=timeout)
        wanted = payload.get("id")
        if wanted is None:
            return None
        if isinstance(direct, dict) and direct.get("id") == wanted:
            return direct
        deadline = time.monotonic() + timeout
        with self._sse_cv:
            while True:
                if wanted in self._sse_responses:
                    return self._sse_responses.pop(wanted)
                if self._sse_reader_error:
                    raise MCPError(self._sse_reader_error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPError(
                        f"{self.name}: timed out waiting for SSE response to {payload.get('method')}"
                    )
                self._sse_cv.wait(min(0.25, remaining))

    def _sse_send_message(self, payload: dict, timeout: float = 60.0) -> dict | None:
        endpoint = self._sse_endpoint_url
        if not endpoint:
            raise MCPError(f"{self.name}: SSE endpoint is not ready")
        headers = self._http_headers(session=False)
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            for attempt in range(2):
                r = c.post(endpoint, headers=headers, json=payload)
                if r.status_code in {401, 403} and self.oauth is not None and attempt == 0:
                    if self.oauth.handle_401(_auth_failure_token(headers), {
                        "status_code": r.status_code,
                        "message": r.text[:300],
                    }):
                        headers = self._http_headers(session=False)
                        continue
                if r.status_code >= 400:
                    if r.status_code in {401, 403}:
                        self._auth_refresh_needed = True
                        self._state = "auth_refresh_needed"
                    raise MCPError(redact_secrets(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}"))
                if "MCP-Session-Id" in r.headers:
                    self._session_id = r.headers["MCP-Session-Id"]
                ctype = r.headers.get("content-type", "")
                if "application/json" in ctype and r.content:
                    return r.json()
                if "text/event-stream" in ctype and r.text:
                    try:
                        return self._read_sse_response_lines(r.text.splitlines(), payload.get("id"))
                    except MCPError:
                        return None
                return None
        raise MCPError(f"{self.name}: SSE auth retry exhausted")

    def _close_sse_transport(self) -> None:
        with self._sse_cv:
            self._sse_closing = True
            self._sse_cv.notify_all()
            thread = self._sse_reader_thread
            stream_cm = self._sse_stream_cm
            client = self._sse_http_client
            self._sse_stream_cm = None
            self._sse_http_client = None
        if stream_cm is not None:
            try:
                stream_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        with self._sse_cv:
            self._sse_responses.clear()
            self._sse_reader_error = None
            self._sse_reader_thread = None
            self._sse_endpoint_url = None
            self._sse_closing = False

    def _request(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict | None:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_request(method, params, notify=notify)
        payload = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._next_id()
        if params is not None:
            payload["params"] = params
        try:
            resp = self._http_request(payload) if self.is_http else self._stdio_request(payload)
        except Exception as exc:
            self._mark_error_state(exc)
            raise
        if resp and "error" in resp:
            message = resp["error"].get("message", resp["error"])
            err = MCPError(redact_secrets(f"{self.name}: {_nonempty_exc_text(message)}"))
            self._mark_error_state(err)
            raise err
        return resp

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> "MCPClient":
        if self.sdk:
            return self._connect_sdk()
        if self._initialized:
            return self
        if self.needs_oauth_login_before_startup():
            self._mark_auth_needed()
            raise MCPError(self.last_error)
        generation: int | None = None
        try:
            if self.is_sse:
                self._start_sse_transport()
                with self._lifecycle_cv:
                    generation = self._transport_generation
            elif self.is_http:
                generation = self._begin_transport_owner("http")
            elif not self.is_http:
                if not self.command:
                    raise MCPError(f"{self.name}: no command or url configured")
                self._spawn()
                with self._lifecycle_cv:
                    generation = self._transport_generation
            initialize_response = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": self._client_capabilities(),
                "clientInfo": CLIENT_INFO,
            })
            self.initialize_result = (
                initialize_response.get("result")
                if isinstance(initialize_response, dict) and "result" in initialize_response
                else initialize_response
            )
            self._request("notifications/initialized", notify=True)
        except Exception as exc:
            self._mark_error_state(exc)
            self._close_transport(mark_disconnected=False)
            raise
        self._initialized = True
        self._state = "connected"
        self._last_error = ""
        self._auth_refresh_needed = False
        self._ping_unsupported = False
        if generation is not None:
            self._mark_transport_ready(generation)
        self._record_remote_success()
        self._ensure_lifecycle_worker()
        return self

    def list_tools(self, *, apply_filter: bool = True, force: bool = False) -> list[dict]:
        if self._tools_cache and not self._tools_stale and not force:
            tools = list(self._tools_cache)
            return _filter_tools(tools, self.tool_filter) if apply_filter else tools
        if self.sdk and self._sdk_runner is not None:
            tools = self._sdk_runner.call("list_tools")
            self._tools_cache = list(tools)
            self._tools_stale = False
            return _filter_tools(tools, self.tool_filter) if apply_filter else tools
        resp = self._request("tools/list", {})
        tools = (resp or {}).get("result", {}).get("tools", [])
        self._tools_cache = list(tools)
        self._tools_stale = False
        return _filter_tools(tools, self.tool_filter) if apply_filter else tools

    def keepalive(self) -> dict:
        """Probe MCP transport liveness.

        Prefer the optional MCP ``ping`` method. If the server reports JSON-RPC
        method-not-found, fall back to ``tools/list`` for the rest of this
        connection, mirroring the cheap-ping-then-list behavior.
        """
        method = "ping"
        try:
            if self.sdk and self._sdk_runner is not None:
                if not self._ping_unsupported:
                    try:
                        self._sdk_runner.call("ping")
                    except Exception as exc:  # noqa: BLE001
                        if not _is_method_not_found_error_text(_nonempty_exc_text(exc).lower()):
                            raise
                        self._ping_unsupported = True
                    else:
                        self._record_remote_success()
                        return {
                            "server": self.name,
                            "ok": True,
                            "method": method,
                            "state": self.state,
                        }
                method = "tools/list"
                self.list_tools(apply_filter=False, force=True)
                self._record_remote_success()
                return {
                    "server": self.name,
                    "ok": True,
                    "method": method,
                    "state": self.state,
                }
            if not self._ping_unsupported:
                try:
                    self._request("ping", {})
                except Exception as exc:  # noqa: BLE001
                    if not _is_method_not_found_error_text(_nonempty_exc_text(exc).lower()):
                        raise
                    self._ping_unsupported = True
                else:
                    self._record_remote_success()
                    return {
                        "server": self.name,
                        "ok": True,
                        "method": method,
                        "state": self.state,
                    }
            method = "tools/list"
            self.list_tools(apply_filter=False, force=True)
            self._record_remote_success()
            return {
                "server": self.name,
                "ok": True,
                "method": method,
                "state": self.state,
            }
        except Exception as exc:  # noqa: BLE001
            self.request_reconnect(_nonempty_exc_text(exc))
            return {
                "server": self.name,
                "ok": False,
                "method": method,
                "state": self.state,
                "error": self.last_error,
            }

    def list_resources(self) -> list[dict]:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_runner.call("list_resources")
        resp = self._request("resources/list", {})
        return (resp or {}).get("result", {}).get("resources", [])

    def read_resource(self, uri: str) -> str:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_runner.call("read_resource", uri)
        resp = self._request("resources/read", {"uri": uri})
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        for item in result.get("contents", []):
            label = item.get("uri") or uri
            mime = item.get("mimeType") or item.get("mime_type") or ""
            if "text" in item:
                header = f'<resource uri="{label}"' + (f' mime="{mime}"' if mime else "") + ">"
                parts.append(f"{header}\n{item.get('text') or ''}\n</resource>")
            elif item.get("blob"):
                size = len(str(item.get("blob") or ""))
                detail = f"base64 blob, {size} chars"
                if mime:
                    detail += f", {mime}"
                parts.append(f"[resource {label}: {detail}]")
        return "\n\n".join(parts) or "(empty resource)"

    def list_prompts(self) -> list[dict]:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_runner.call("list_prompts")
        resp = self._request("prompts/list", {})
        return (resp or {}).get("result", {}).get("prompts", [])

    def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_runner.call("get_prompt", name, arguments or {})
        resp = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        if result.get("description"):
            parts.append(f"# {result['description']}")
        for msg in result.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, dict):
                text = _render_prompt_content(content)
            elif isinstance(content, list):
                text = "\n".join(_render_prompt_content(block) for block in content)
            else:
                text = str(content)
            parts.append(f"<{role}>\n{text}\n</{role}>")
        return "\n\n".join(parts) or "(empty prompt)"

    def _client_capabilities(self) -> dict:
        capabilities: dict[str, dict] = {}
        if self.sampling.get("enabled", True):
            capabilities["sampling"] = {}
        if self.elicitation.get("enabled", True):
            capabilities["elicitation"] = {}
        if self.roots:
            capabilities["roots"] = {"listChanged": False}
        return capabilities

    def complete(
        self,
        ref: dict,
        argument: dict,
        *,
        context_arguments: dict | None = None,
    ) -> dict:
        """Ask a server for MCP completion candidates."""
        params: dict[str, object] = {"ref": ref, "argument": argument}
        if context_arguments:
            params["context"] = {"arguments": context_arguments}
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_runner.call("complete", params)
        resp = self._request("completion/complete", params)
        result = (resp or {}).get("result") or {}
        completion = result.get("completion") if isinstance(result, dict) else None
        return completion if isinstance(completion, dict) else {}

    def call_tool(self, name: str, arguments: dict, ctx: ToolContext | None = None) -> tuple[str, bool]:
        if self.sdk and self._sdk_runner is not None:
            return self._sdk_call_tool(name, arguments, ctx)
        self._preflight_remote_call()
        try:
            with self._stdio_context_lock:
                previous_context = self._pending_tool_context
                self._pending_tool_context = ctx
                self._remember_sampling_context(ctx)
            try:
                resp = self._request("tools/call", {"name": name, "arguments": arguments})
            finally:
                with self._stdio_context_lock:
                    self._pending_tool_context = previous_context
        except Exception as exc:
            self._record_remote_failure(exc)
            raise
        result = (resp or {}).get("result", {})
        text, is_error = _render_call_tool_result(result)
        if is_error:
            self._record_remote_failure("MCP tool returned an error")
        else:
            self._record_remote_success()
        return text, is_error

    async def _sdk_dispatch(self, session, operation: str, *args):
        if operation == "list_tools":
            result = await _maybe_await(session.list_tools())
            return _sdk_tools_from_result(result)
        if operation == "call_tool":
            tool_name, arguments = args
            result = await _sdk_call_tool_method(session, tool_name, arguments)
            return _render_call_tool_result(_sdk_to_plain(result))
        if operation == "list_resources":
            result = await _maybe_await(session.list_resources())
            plain = _sdk_to_plain(result)
            return plain.get("resources", []) if isinstance(plain, dict) else []
        if operation == "read_resource":
            (uri,) = args
            result = await _maybe_await(session.read_resource(uri))
            return _render_resource_result(_sdk_to_plain(result), uri)
        if operation == "list_prompts":
            result = await _maybe_await(session.list_prompts())
            plain = _sdk_to_plain(result)
            return plain.get("prompts", []) if isinstance(plain, dict) else []
        if operation == "get_prompt":
            name, arguments = args
            result = await _maybe_await(session.get_prompt(name, arguments or {}))
            return _render_prompt_result(_sdk_to_plain(result))
        if operation == "complete":
            (params,) = args
            complete_fn = getattr(session, "complete", None)
            if callable(complete_fn):
                result = await _maybe_await(
                    complete_fn(
                        params.get("ref"),
                        params.get("argument"),
                        context_arguments=(params.get("context") or {}).get("arguments"),
                    )
                )
            else:
                result = await _sdk_send_request(session, "completion/complete", params)
            plain = _sdk_to_plain(result)
            if isinstance(plain, dict):
                completion = plain.get("completion")
                return completion if isinstance(completion, dict) else plain
            return {}
        if operation == "ping":
            ping_fn = getattr(session, "send_ping", None) or getattr(session, "ping", None)
            if not callable(ping_fn):
                await _sdk_send_request(session, "ping", {})
            else:
                await _maybe_await(ping_fn())
            return None
        if operation == "request":
            method, params, notify = args
            if notify:
                await _sdk_send_notification(session, method, params)
                return None
            result = await _sdk_send_request(session, method, params)
            return {"result": _sdk_to_plain(result)}
        raise MCPError(f"{self.name}: unsupported MCP SDK operation {operation}")

    def _close_transport(self, *, mark_disconnected: bool) -> None:
        self._close_sdk_transport()
        self._retire_transport_owner("closed")
        self._close_sse_transport()
        with self._stdio_cv:
            self._stdio_closing = True
            self._stdio_cv.notify_all()
        reader = self._stdio_reader_thread
        if self._proc:
            try:
                self._proc.stdin and self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        if reader and reader is not threading.current_thread():
            reader.join(timeout=1)
        with self._stdio_cv:
            self._stdio_responses.clear()
            self._stdio_reader_error = None
            self._stdio_reader_thread = None
            self._stdio_closing = False
        self._initialized = False
        self._session_id = None
        if mark_disconnected and self._state not in {"auth_needed", "auth_refresh_needed"}:
            self._state = "disconnected"
        self._notify_lifecycle_waiters()

    def _close_sdk_transport(self) -> None:
        runner = self._sdk_runner
        self._sdk_runner = None
        if runner is not None:
            runner.close()

    def close(self) -> None:
        self._stop_lifecycle_worker()
        self._close_transport(mark_disconnected=True)


class _MCPSDKSessionRunner:
    """Own an MCP SDK ClientSession inside one async task.

    The Python MCP SDK uses anyio cancel scopes internally. Creating a
    ``ClientSession`` in one task and closing or driving it from another can
    violate anyio's task-local ownership rules, so this runner funnels every SDK
    operation through a single owner coroutine.
    """

    def __init__(self, client: MCPClient):
        self.client = client
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._startup: concurrent.futures.Future = concurrent.futures.Future()
        self._generation = 0

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._closed.is_set()

    def start(self, timeout: float = 30.0) -> tuple[object, int]:
        thread = threading.Thread(
            target=self._thread_main,
            name=f"aegis-mcp-sdk-{_safe_name_part(self.client.name)}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        if not self._ready.wait(timeout):
            self.close()
            raise MCPError(f"{self.client.name}: timed out starting MCP SDK session")
        return self._startup.result(timeout=0)

    def call(self, operation: str, *args, timeout: float = 60.0) -> object:
        loop = self._loop
        queue_obj = self._queue
        if loop is None or queue_obj is None or not self.alive:
            raise MCPError(f"{self.client.name}: MCP SDK session is not connected")
        future: concurrent.futures.Future = concurrent.futures.Future()

        def _enqueue() -> None:
            queue_obj.put_nowait((operation, args, future))

        loop.call_soon_threadsafe(_enqueue)
        return future.result(timeout=timeout)

    def close(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            return
        if self.alive:
            try:
                self.call("__close__", timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        thread.join(timeout=2.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._queue = asyncio.Queue()
        try:
            loop.run_until_complete(self._owner_main())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                self._closed.set()
                loop.close()

    async def _owner_main(self) -> None:
        generation = self.client._begin_transport_owner(self._transport_name())
        self._generation = generation
        try:
            async with self._transport_context() as streams:
                read_stream, write_stream = streams[0], streams[1]
                session_cls = _MCP_SDK_CLIENT_SESSION
                if session_cls is None:
                    raise MCPError("MCP SDK ClientSession is unavailable")
                async with session_cls(
                    read_stream,
                    write_stream,
                    **self.client._sdk_session_kwargs(),
                ) as session:
                    initialize_result = await _maybe_await(session.initialize())
                    if not self._startup.done():
                        self._startup.set_result((initialize_result, generation))
                    self._ready.set()
                    await self._serve_requests(session)
        except BaseException as exc:  # noqa: BLE001
            if not self._startup.done():
                self._startup.set_exception(exc)
                self._ready.set()
            self._fail_pending(exc)
        finally:
            if not self._startup.done():
                self._startup.set_exception(MCPError(f"{self.client.name}: MCP SDK session closed"))
                self._ready.set()

    def _transport_name(self) -> str:
        if self.client.is_http:
            return "sdk-http"
        return "sdk-stdio"

    def _transport_context(self):
        if self.client.is_http:
            if _MCP_SDK_STREAMABLE_HTTP_CLIENT is None:
                raise MCPError(f"{self.client.name}: MCP SDK streamable HTTP client is unavailable")
            headers = self.client._http_headers(content_type=False, session=False)
            return _call_with_compatible_kwargs(
                _MCP_SDK_STREAMABLE_HTTP_CLIENT,
                self.client.url,
                headers=headers,
            )
        if _MCP_SDK_STDIO_SERVER_PARAMETERS is None or _MCP_SDK_STDIO_CLIENT is None:
            raise MCPError(f"{self.client.name}: MCP SDK stdio client is unavailable")
        params_kwargs = {
            "command": self.client.command,
            "args": list(self.client.args),
            "env": _safe_subprocess_env(self.client.env),
        }
        if self.client.cwd and _callable_accepts_keyword(_MCP_SDK_STDIO_SERVER_PARAMETERS, "cwd"):
            params_kwargs["cwd"] = self.client.cwd
        params = _MCP_SDK_STDIO_SERVER_PARAMETERS(**params_kwargs)
        return _MCP_SDK_STDIO_CLIENT(params)

    async def _serve_requests(self, session) -> None:
        queue_obj = self._queue
        if queue_obj is None:
            return
        while True:
            operation, args, future = await queue_obj.get()
            if future.cancelled():
                continue
            if operation == "__close__":
                future.set_result(None)
                return
            try:
                result = await self.client._sdk_dispatch(session, operation, *args)
            except BaseException as exc:  # noqa: BLE001
                future.set_exception(exc)
            else:
                future.set_result(result)

    def _fail_pending(self, exc: BaseException) -> None:
        queue_obj = self._queue
        if queue_obj is None:
            return
        while True:
            try:
                _operation, _args, future = queue_obj.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not future.done():
                future.set_exception(exc)


class MCPTool(Tool):
    groups = ["network"]   # remote tools are gated like any side-effecting tool
    toolset = "mcp"
    allow_shadow = True

    def __init__(self, client: MCPClient, tool_def: dict):
        self._client = client
        self._remote = tool_def["name"]
        self.name = f"mcp__{_safe_name_part(client.name)}__{_safe_name_part(tool_def['name'])}"
        self.toolset = _mcp_toolset_name(client.name)
        self.toolset_alias = client.name
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/{self._remote}"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["oauth"] if client.oauth else (
            ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        )
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        self.description = tool_def.get("description", "") or f"MCP tool {self._remote}"
        self.parameters = _normalize_mcp_input_schema(tool_def.get("inputSchema"))

    def run(self, args, ctx: ToolContext) -> ToolResult:
        if parked := self._client.parked_call_error():
            return ToolResult.error(parked)
        try:
            content, is_err = _call_mcp_tool_with_context(self._client, self._remote, args, ctx)
        except Exception as e:  # noqa: BLE001
            if _is_session_expired_error_text(_nonempty_exc_text(e).lower()):
                try:
                    self._client.reconnect_for_retry(_nonempty_exc_text(e))
                    content, is_err = _call_mcp_tool_with_context(self._client, self._remote, args, ctx)
                    return ToolResult(content=truncate(content, 30_000), is_error=is_err,
                                      display=f"mcp:{self._client.name}/{self._remote}")
                except Exception as retry_exc:  # noqa: BLE001
                    return ToolResult.error(
                        "mcp call failed after session reconnect: "
                        f"{redact_secrets(_nonempty_exc_text(retry_exc))}"
                    )
            if self._client.auth_refresh_needed:
                return ToolResult.error(
                    "mcp auth refresh needed: "
                    f"{redact_secrets(_nonempty_exc_text(e))}"
                )
            return ToolResult.error(f"mcp call failed: {redact_secrets(_nonempty_exc_text(e))}")
        return ToolResult(content=truncate(content, 30_000), is_error=is_err,
                          display=f"mcp:{self._client.name}/{self._remote}")


class MCPReadResourceTool(Tool):
    groups = ["network"]
    toolset = "mcp"
    allow_shadow = True

    def __init__(self, client: MCPClient, resources: list[dict]):
        self._client = client
        self.name = f"mcp__{_safe_name_part(client.name)}__read_resource"
        self.toolset = _mcp_toolset_name(client.name)
        self.toolset_alias = client.name
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/resources"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["oauth"] if client.oauth else (
            ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        )
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        preview = _capability_preview(resources, "uri")
        self.description = (
            f"Read an MCP resource from server '{client.name}' by URI."
            + (f" Available resources include: {preview}." if preview else "")
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Resource URI from resources/list."},
            },
            "required": ["uri"],
        }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content = self._client.read_resource(str(args.get("uri", "")))
        except Exception as e:  # noqa: BLE001
            if _is_session_expired_error_text(_nonempty_exc_text(e).lower()):
                try:
                    self._client.reconnect_for_retry(_nonempty_exc_text(e))
                    content = self._client.read_resource(str(args.get("uri", "")))
                except Exception as retry_exc:  # noqa: BLE001
                    return ToolResult.error(
                        "mcp resource read failed after session reconnect: "
                        f"{redact_secrets(_nonempty_exc_text(retry_exc))}"
                    )
            else:
                return ToolResult.error(f"mcp resource read failed: {e}")
        return ToolResult.ok(
            truncate(content, 30_000),
            display=f"mcp:{self._client.name}/resource",
            data={"artifact_ref": str(args.get("uri", "")), "server": self._client.name},
        )


class MCPGetPromptTool(Tool):
    groups = ["network"]
    toolset = "mcp"
    allow_shadow = True

    def __init__(self, client: MCPClient, prompts: list[dict]):
        self._client = client
        self.name = f"mcp__{_safe_name_part(client.name)}__get_prompt"
        self.toolset = _mcp_toolset_name(client.name)
        self.toolset_alias = client.name
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/prompts"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["oauth"] if client.oauth else (
            ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        )
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        preview = _capability_preview(prompts, "name")
        self.description = (
            f"Render an MCP prompt template from server '{client.name}' by name."
            + (f" Available prompts include: {preview}." if preview else "")
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name from prompts/list."},
                "arguments": {
                    "type": "object",
                    "description": "Prompt arguments keyed by argument name.",
                    "additionalProperties": True,
                },
            },
            "required": ["name"],
        }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content = self._client.get_prompt(
                str(args.get("name", "")),
                args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            )
        except Exception as e:  # noqa: BLE001
            if _is_session_expired_error_text(_nonempty_exc_text(e).lower()):
                try:
                    self._client.reconnect_for_retry(_nonempty_exc_text(e))
                    content = self._client.get_prompt(
                        str(args.get("name", "")),
                        args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
                    )
                except Exception as retry_exc:  # noqa: BLE001
                    return ToolResult.error(
                        "mcp prompt render failed after session reconnect: "
                        f"{redact_secrets(_nonempty_exc_text(retry_exc))}"
                    )
            else:
                return ToolResult.error(f"mcp prompt render failed: {e}")
        return ToolResult.ok(
            truncate(content, 30_000),
            display=f"mcp:{self._client.name}/prompt",
            data={"server": self._client.name, "prompt": str(args.get("name", ""))},
        )


class MCPManager:
    def __init__(self):
        self.clients: list[MCPClient] = []
        self._registered_tool_names: dict[str, list[str]] = {}
        self._mcp_tool_server_names: dict[str, str] = {}
        self._parallel_safe_servers: set[str] = set()

    def add(self, client: MCPClient) -> None:
        self.clients.append(client)

    def mcp_tool_server_name(self, tool_name: str) -> str | None:
        """Return the exact MCP server that registered a namespaced tool."""
        return self._mcp_tool_server_names.get(tool_name)

    def mcp_tool_provenance(self, tool_name: str) -> dict:
        """Return exact MCP provenance and parallel-safety metadata for a tool."""
        server_name = self.mcp_tool_server_name(tool_name)
        if not server_name:
            return {}
        return {
            "server": server_name,
            "parallel_safe": self.is_mcp_tool_parallel_safe(tool_name),
        }

    def is_mcp_tool_parallel_safe(self, tool_name: str) -> bool:
        """Return True only for MCP tools from servers that opted into parallel calls."""
        if not tool_name.startswith("mcp__"):
            return False
        server_name = self._mcp_tool_server_names.get(tool_name)
        return bool(server_name and server_name in self._parallel_safe_servers)

    def _remember_registered_tool_names(self, client: MCPClient, tool_names: list[str]) -> None:
        old_names = set(self._registered_tool_names.get(client.name, []))
        new_names = list(tool_names)
        new_name_set = set(new_names)
        for old_name in old_names - new_name_set:
            self._mcp_tool_server_names.pop(old_name, None)
        for tool_name in new_names:
            self._mcp_tool_server_names[tool_name] = client.name
        self._registered_tool_names[client.name] = new_names
        if client.supports_parallel_tool_calls:
            self._parallel_safe_servers.add(client.name)
        else:
            self._parallel_safe_servers.discard(client.name)

    def connect_all(self) -> list[Tool]:
        tools: list[Tool] = []
        for client in self.clients:
            try:
                if client.needs_oauth_login_before_startup():
                    client._mark_auth_needed()
                    print(
                        f"  ! MCP server '{client.name}' auth needed: "
                        f"run `aegis mcp login {client.name}`"
                    )
                    continue
                client.connect()
                server_tool_names: list[str] = []
                for td in client.list_tools():
                    tool = MCPTool(client, td)
                    tools.append(tool)
                    server_tool_names.append(tool.name)
                for utility_tool in _mcp_utility_tools_for_client(client):
                    tools.append(utility_tool)
                    server_tool_names.append(utility_tool.name)
                self._remember_registered_tool_names(client, server_tool_names)
            except Exception as e:  # noqa: BLE001
                print(f"  ! MCP server '{client.name}' failed: {e}")
        return tools

    def refresh_changed_tools(self, registry=None) -> list[Tool]:
        """Refresh and optionally re-register tools for servers that signalled list changes."""
        refreshed: list[Tool] = []
        for client in self.clients:
            if not client.tools_stale:
                continue
            try:
                server_tools = [MCPTool(client, td) for td in client.list_tools(force=True)]
                server_tools.extend(_mcp_utility_tools_for_client(client))
            except Exception as e:  # noqa: BLE001
                client._mark_error_state(e)
                continue
            old_names = set(self._registered_tool_names.get(client.name, []))
            new_names = {tool.name for tool in server_tools}
            deregister = getattr(registry, "deregister", None)
            if callable(deregister):
                for old_name in sorted(old_names - new_names):
                    deregister(old_name)
            if registry is not None:
                for tool in server_tools:
                    registry.register(tool)
            self._remember_registered_tool_names(
                client,
                [tool.name for tool in server_tools],
            )
            refreshed.extend(server_tools)
        return refreshed

    def keepalive_all(self) -> list[dict]:
        """Probe every connected MCP client and return per-server liveness rows."""
        rows: list[dict] = []
        for client in self.clients:
            try:
                rows.append(client.keepalive())
            except Exception as exc:  # noqa: BLE001
                client._mark_reconnect_needed(exc)
                rows.append({
                    "server": client.name,
                    "ok": False,
                    "method": "keepalive",
                    "state": client.state,
                    "error": client.last_error,
                })
        return rows

    def close_all(self) -> None:
        for c in self.clients:
            c.close()


def _safe_subprocess_env(user_env: dict | None = None) -> dict[str, str]:
    """Return a minimal stdio-server env plus explicitly configured values."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.upper() in _SAFE_ENV_KEYS_UPPER or key.startswith("XDG_"):
            env[key] = value
    for key, value in (user_env or {}).items():
        env[str(key)] = str(value)
    return env


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int, minimum: int = 1) -> int:
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _thread_metadata(thread: threading.Thread | None) -> dict | None:
    if thread is None:
        return None
    return {
        "name": thread.name,
        "ident": thread.ident,
        "alive": thread.is_alive(),
    }


def _boolish(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _callable_accepts_keyword(target, keyword: str) -> bool:
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return False
    return keyword in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in params.values()
    )


def _call_with_compatible_kwargs(fn, *args, **kwargs):
    accepted = {
        key: value
        for key, value in kwargs.items()
        if _callable_accepts_keyword(fn, key)
    }
    return fn(*args, **accepted)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _make_sdk_type(cls, **kwargs):
    if cls is None:
        return dict(kwargs) if kwargs else None
    try:
        return cls(**kwargs)
    except TypeError:
        accepted = {
            key: value
            for key, value in kwargs.items()
            if _callable_accepts_keyword(cls, key)
        }
        return cls(**accepted)


def _sdk_to_plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: _sdk_to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sdk_to_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _sdk_to_plain(model_dump(by_alias=True, exclude_none=True))
        except TypeError:
            return _sdk_to_plain(model_dump())
    dict_fn = getattr(value, "dict", None)
    if callable(dict_fn):
        try:
            return _sdk_to_plain(dict_fn(by_alias=True, exclude_none=True))
        except TypeError:
            return _sdk_to_plain(dict_fn())
    if hasattr(value, "__dict__"):
        return {
            key: _sdk_to_plain(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _sdk_notification_to_jsonrpc(message) -> dict | None:
    plain = _sdk_to_plain(message)
    method = ""
    params = {}
    if isinstance(plain, dict):
        root = plain.get("root")
        if isinstance(root, dict):
            method = str(root.get("method") or plain.get("method") or "")
            params = root.get("params") or plain.get("params") or {}
        else:
            method = str(plain.get("method") or "")
            params = plain.get("params") or {}
    if not method:
        root_obj = getattr(message, "root", message)
        method = str(getattr(root_obj, "method", "") or getattr(message, "method", "") or "")
        params = _sdk_to_plain(getattr(root_obj, "params", None) or getattr(message, "params", {}) or {})
    if not method:
        class_name = type(getattr(message, "root", message)).__name__
        method = {
            "ToolListChangedNotification": "notifications/tools/list_changed",
            "PromptListChangedNotification": "notifications/prompts/list_changed",
            "ResourceListChangedNotification": "notifications/resources/list_changed",
        }.get(class_name, "")
    if not method:
        return None
    if not isinstance(params, dict):
        params = {"value": params}
    return {"jsonrpc": "2.0", "method": method, "params": params}


def _sdk_error_data(message: str, *, code: int = -32000):
    if _MCP_SDK_ERROR_DATA is None:
        return {"code": code, "message": message}
    return _make_sdk_type(_MCP_SDK_ERROR_DATA, code=code, message=message)


def _sdk_sampling_result(result):
    if not isinstance(result, dict):
        return result
    role = result.get("role", "assistant")
    model = result.get("model", "unknown")
    stop_reason = result.get("stopReason") or result.get("stop_reason") or "endTurn"
    content = result.get("content")
    if (
        isinstance(content, list)
        and _MCP_SDK_CREATE_MESSAGE_RESULT_WITH_TOOLS is not None
        and _MCP_SDK_TOOL_USE_CONTENT is not None
    ):
        blocks = [
            _make_sdk_type(
                _MCP_SDK_TOOL_USE_CONTENT,
                type=block.get("type", "tool_use"),
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input", {}),
            )
            for block in content
            if isinstance(block, dict)
        ]
        return _make_sdk_type(
            _MCP_SDK_CREATE_MESSAGE_RESULT_WITH_TOOLS,
            role=role,
            content=blocks,
            model=model,
            stopReason=stop_reason,
        )
    if (
        isinstance(content, dict)
        and _MCP_SDK_CREATE_MESSAGE_RESULT is not None
        and _MCP_SDK_TEXT_CONTENT is not None
    ):
        text_content = _make_sdk_type(
            _MCP_SDK_TEXT_CONTENT,
            type=content.get("type", "text"),
            text=content.get("text", ""),
        )
        return _make_sdk_type(
            _MCP_SDK_CREATE_MESSAGE_RESULT,
            role=role,
            content=text_content,
            model=model,
            stopReason=stop_reason,
        )
    return result


def _sdk_elicit_result(result):
    if not isinstance(result, dict) or _MCP_SDK_ELICIT_RESULT is None:
        return result
    kwargs = {"action": result.get("action", "decline")}
    if "content" in result:
        kwargs["content"] = result.get("content") or {}
    return _make_sdk_type(_MCP_SDK_ELICIT_RESULT, **kwargs)


def _sdk_list_roots_result(roots: list[dict]):
    if _MCP_SDK_LIST_ROOTS_RESULT is None or _MCP_SDK_ROOT is None:
        return {"roots": list(roots)}
    root_objects = [
        _make_sdk_type(_MCP_SDK_ROOT, uri=root.get("uri"), name=root.get("name"))
        for root in roots
    ]
    return _make_sdk_type(_MCP_SDK_LIST_ROOTS_RESULT, roots=root_objects)


def _jsonrpc_error(msg_id: object, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _sampling_messages_from_params(params: dict) -> list:
    from ..types import Message, ToolCall

    messages = []
    system_prompt = str(params.get("systemPrompt") or params.get("system_prompt") or "").strip()
    if system_prompt:
        messages.append(Message.system(system_prompt))

    for raw in params.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user")
        blocks = _sampling_content_blocks(raw.get("content"))
        text_parts: list[str] = []
        images: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in blocks:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            block_type = str(block.get("type") or "").lower()
            if block_type == "text" or "text" in block:
                text_parts.append(str(block.get("text") or ""))
            elif block_type == "image" or ("data" in block and "mimeType" in block):
                data_url = _sampling_image_data_url(block)
                if data_url:
                    images.append(data_url)
            elif block_type in {"tool_use", "tooluse"} or (
                "name" in block and "input" in block and "toolUseId" not in block
            ):
                tool_calls.append(ToolCall(
                    id=str(block.get("id") or f"call_{len(tool_calls)}"),
                    name=str(block.get("name") or ""),
                    arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
                ))
            elif block_type in {"tool_result", "toolresult"} or "toolUseId" in block:
                content = _sampling_tool_result_text(block)
                messages.append(Message.tool(
                    str(block.get("toolUseId") or block.get("tool_use_id") or ""),
                    str(block.get("name") or "tool"),
                    content,
                ))

        if text_parts or images or tool_calls:
            messages.append(Message(
                role=role,
                content="\n".join(part for part in text_parts if part),
                tool_calls=tool_calls,
                images=images,
            ))

    return messages


def _sampling_content_blocks(content) -> list:
    if content is None:
        return []
    if isinstance(content, list):
        return content
    return [content]


def _sampling_image_data_url(block: dict) -> str:
    data = str(block.get("data") or "")
    if not data:
        return ""
    mime = str(block.get("mimeType") or block.get("mime_type") or "image/*")
    if data.startswith("data:"):
        return data
    return f"data:{mime};base64,{data}"


def _sampling_tool_result_text(block: dict) -> str:
    content = block.get("content")
    parts = _sampling_content_blocks(content)
    text: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text.append(part)
        elif isinstance(part, dict) and "text" in part:
            text.append(str(part.get("text") or ""))
    return "\n".join(part for part in text if part)


def _sampling_tools_from_params(params: dict) -> list[dict]:
    tools = []
    for tool in params.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        tools.append({
            "name": str(tool.get("name")),
            "description": str(tool.get("description") or ""),
            "parameters": _normalize_mcp_input_schema(schema),
        })
    return tools


def _sdk_tool_dict(tool) -> dict:
    plain = _sdk_to_plain(tool)
    if not isinstance(plain, dict):
        return {"name": str(plain), "description": "", "inputSchema": {"type": "object"}}
    out = dict(plain)
    if "inputSchema" not in out and "input_schema" in out:
        out["inputSchema"] = out.pop("input_schema")
    out.setdefault("description", "")
    out.setdefault("inputSchema", {"type": "object"})
    return out


def _sdk_tools_from_result(result) -> list[dict]:
    plain = _sdk_to_plain(result)
    tools = plain.get("tools", []) if isinstance(plain, dict) else []
    return [_sdk_tool_dict(tool) for tool in tools]


async def _sdk_call_tool_method(session, tool_name: str, arguments: dict):
    call_tool = getattr(session, "call_tool")
    if _callable_accepts_keyword(call_tool, "arguments"):
        return await _maybe_await(call_tool(tool_name, arguments=arguments))
    return await _maybe_await(call_tool(tool_name, arguments))


async def _sdk_send_request(session, method: str, params: dict):
    send_request = getattr(session, "send_request", None)
    if not callable(send_request):
        raise MCPError(f"MCP SDK session does not expose {method}")
    try:
        return await _maybe_await(send_request(method, params))
    except TypeError:
        return await _maybe_await(send_request({"method": method, "params": params}))


async def _sdk_send_notification(session, method: str, params: dict):
    send_notification = getattr(session, "send_notification", None)
    if not callable(send_notification):
        return None
    try:
        return await _maybe_await(send_notification(method, params))
    except TypeError:
        return await _maybe_await(send_notification({"method": method, "params": params}))


def _render_call_tool_result(result: object) -> tuple[str, bool]:
    plain = _sdk_to_plain(result)
    if not isinstance(plain, dict):
        return str(plain or "(no content)"), False
    parts: list[str] = []
    for block in plain.get("content", []) or []:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "image":
            tag = _cache_mcp_image_block(block)
            if tag:
                parts.append(tag)
            else:
                mime = block.get("mimeType") or block.get("mime_type") or "image/*"
                data_len = len(str(block.get("data") or ""))
                parts.append(f"[image content: {mime}, {data_len} base64 chars]")
        elif block_type == "resource":
            res = block.get("resource", {})
            parts.append(res.get("text") or f"[resource {res.get('uri')}]")
        else:
            parts.append(f"[{block_type} content]")
    text = "\n".join(part for part in parts if part) or ""
    structured = plain.get("structuredContent")
    if structured is None:
        structured = plain.get("structured_content")
    if structured is not None:
        structured = redact_secret_values(structured)
        rendered = json.dumps(structured, ensure_ascii=False, indent=2)
        if text:
            text = f"{text}\n\n<structuredContent>\n{rendered}\n</structuredContent>"
        else:
            text = rendered
    is_error = bool(plain.get("isError") or plain.get("is_error"))
    return text or "(no content)", is_error


def _render_resource_result(result: object, uri: str) -> str:
    plain = _sdk_to_plain(result)
    if not isinstance(plain, dict):
        return str(plain or "(empty resource)")
    parts: list[str] = []
    for item in plain.get("contents", []) or []:
        if not isinstance(item, dict):
            continue
        label = item.get("uri") or uri
        mime = item.get("mimeType") or item.get("mime_type") or ""
        if "text" in item:
            header = f'<resource uri="{label}"' + (f' mime="{mime}"' if mime else "") + ">"
            parts.append(f"{header}\n{item.get('text') or ''}\n</resource>")
        elif item.get("blob"):
            size = len(str(item.get("blob") or ""))
            detail = f"base64 blob, {size} chars"
            if mime:
                detail += f", {mime}"
            parts.append(f"[resource {label}: {detail}]")
    return "\n\n".join(parts) or "(empty resource)"


def _render_prompt_result(result: object) -> str:
    plain = _sdk_to_plain(result)
    if not isinstance(plain, dict):
        return str(plain or "(empty prompt)")
    parts: list[str] = []
    if plain.get("description"):
        parts.append(f"# {plain['description']}")
    for msg in plain.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, dict):
            text = _render_prompt_content(content)
        elif isinstance(content, list):
            text = "\n".join(_render_prompt_content(block) for block in content)
        else:
            text = str(content)
        parts.append(f"<{role}>\n{text}\n</{role}>")
    return "\n\n".join(parts) or "(empty prompt)"


def _call_mcp_tool_with_context(
    client: MCPClient,
    remote_name: str,
    args: dict,
    ctx: ToolContext | None,
) -> tuple[str, bool]:
    import inspect

    try:
        params = inspect.signature(client.call_tool).parameters
    except (TypeError, ValueError):
        return client.call_tool(remote_name, args, ctx=ctx)
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    if "ctx" in params or accepts_kwargs:
        return client.call_tool(remote_name, args, ctx=ctx)
    return client.call_tool(remote_name, args)


def _response_text(response: httpx.Response, *, limit: int) -> str:
    try:
        data = response.read()
    except Exception:  # noqa: BLE001
        return ""
    try:
        return data.decode(response.encoding or "utf-8", errors="replace")[:limit]
    except (LookupError, AttributeError):
        return data.decode("utf-8", errors="replace")[:limit]


def _coerce_sse_line(line: object) -> str:
    if isinstance(line, bytes):
        return line.decode("utf-8", errors="replace").rstrip("\r\n")
    return str(line).rstrip("\r\n")


def _choice_allows(verdict: object) -> bool:
    if verdict is True:
        return True
    if verdict is False or verdict is None:
        return False
    text = str(verdict).strip().lower()
    return text in {
        "1",
        "true",
        "yes",
        "y",
        "approve",
        "approved",
        "allow",
        "allowed",
        "accept",
        "accepted",
        "once",
        "session",
        "always",
    }


def _format_elicitation_schema_summary(schema: object, server_name: str) -> str:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return f"Approval requested by MCP server '{server_name}'."
    lines = [f"Fields requested by MCP server '{server_name}':"]
    for field_name, field_spec in props.items():
        field_type = ""
        field_desc = ""
        if isinstance(field_spec, dict):
            field_type = str(field_spec.get("type") or "")
            field_desc = str(field_spec.get("description") or "")
        suffix = f" ({field_type})" if field_type else ""
        if field_desc:
            lines.append(f"  - {field_name}{suffix}: {field_desc}")
        else:
            lines.append(f"  - {field_name}{suffix}")
    return "\n".join(lines)


def _nonempty_exc_text(value) -> str:
    text = str(value).strip()
    return text if text else repr(value)


def _is_session_expired_error_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        needle in lowered
        for needle in (
            "invalid or expired session",
            "expired session",
            "session expired",
            "session has expired",
            "session not found",
            "unknown session",
            "session terminated",
            "invalid session",
            "mcp-session-id",
            "transport session",
            "closedresourceerror",
            "closed resource",
            "transport is closed",
            "connection closed",
            "server closed the connection",
            "broken pipe",
            "end of file",
        )
    )


def _is_auth_refresh_error_text(text: str) -> bool:
    lowered = str(text or "").lower()
    if _is_session_expired_error_text(lowered):
        return False
    return any(
        needle in lowered
        for needle in (
            "http 401",
            "http 403",
            "unauthorized",
            "forbidden",
            "invalid_token",
            "invalid token",
            "oauth",
            "bearer",
        )
    )


def _is_auth_needed_error_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "not logged in" in lowered
        and "oauth" in lowered
    ) or "run `aegis mcp login" in lowered


def _is_method_not_found_error_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "-32601" in lowered
        or "method not found" in lowered
        or "unknown method" in lowered
        or "not found: ping" in lowered
    )


def _safe_name_part(value: str) -> str:
    text = _NAME_PART_RE.sub("_", str(value or "")).strip("_")
    return text or "unnamed"


def _mcp_toolset_name(server_name: str) -> str:
    return f"mcp-{_safe_name_part(server_name)}"


def _normalize_mcp_input_schema(schema) -> dict:
    """Repair common MCP JSON Schema shapes before exposing them to model APIs."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}

    def rewrite_refs(node):
        if isinstance(node, list):
            return [rewrite_refs(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            out_key = "$defs" if key == "definitions" else key
            out[out_key] = rewrite_refs(value)
        ref = out.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            out["$ref"] = "#/$defs/" + ref[len("#/definitions/"):]
        return out

    from ..providers.schema import sanitize as sanitize_schema

    normalized = sanitize_schema(rewrite_refs(schema))
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}}
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _mcp_image_extension_for_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    return mimetypes.guess_extension(normalized) or ".png"


def _looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"GIF87a")
        or data.startswith(b"GIF89a")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def _cache_mcp_image_block(block: dict) -> str:
    mime_type = str(block.get("mimeType") or block.get("mime_type") or "").split(";", 1)[0].lower()
    if not mime_type.startswith("image/") or not block.get("data"):
        return ""
    try:
        raw = base64.b64decode(str(block.get("data")), validate=True)
    except (TypeError, ValueError):
        return ""
    if not _looks_like_image(raw):
        return ""
    ext = _mcp_image_extension_for_mime_type(mime_type)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    out_dir = ensure_dir(cfg.sub("tool_outputs", "mcp_images"))
    out = out_dir / f"mcp_{digest}{ext}"
    if not out.exists():
        out.write_bytes(raw)
    return f"MEDIA:{out}"


def _validate_remote_mcp_url(server_name: str, url) -> str:
    if not isinstance(url, str):
        raise InvalidMCPUrlError(
            f"MCP server '{server_name}' expected a string url, got {type(url).__name__}"
        )
    stripped = url.strip()
    if not stripped:
        raise InvalidMCPUrlError(f"MCP server '{server_name}' has an empty url")
    parsed = urlparse(stripped)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidMCPUrlError(
            f"MCP server '{server_name}' url scheme must be http or https"
        )
    if not parsed.hostname:
        raise InvalidMCPUrlError(f"MCP server '{server_name}' url is missing host")
    return stripped


def _server_configs(config) -> dict:
    servers = dict(config.get("mcp.servers", {}) or {})
    # also merge ~/.aegis/mcp.json (Claude Desktop format: {"mcpServers": {...}})
    raw = read_text(cfg.sub("mcp.json"))
    if raw.strip():
        try:
            data = json.loads(raw)
            servers.update(_normalize_external_mcp_config(data))
        except json.JSONDecodeError:
            pass
    return servers


def catalog(config) -> list[dict]:
    """Configured MCP catalog entries.

    The catalog is intentionally local/config-backed: users and distributions can
    ship known server recipes without requiring a network marketplace.
    """
    out = []
    for entry in config.get("mcp.catalog", []) or []:
        if isinstance(entry, dict) and entry.get("name") and (entry.get("command") or entry.get("url")):
            out.append(dict(entry))
    return out


def install_from_catalog(config, name: str) -> dict:
    entries = {e["name"]: e for e in catalog(config)}
    entry = entries.get(name)
    if not entry:
        raise KeyError(name)
    servers = dict(config.get("mcp.servers", {}) or {})
    spec = {k: v for k, v in entry.items()
            if k in {
                "command", "args", "env", "url", "headers", "cwd", "tool_filter",
                "transport", "sampling", "elicitation", "roots", "keepalive_interval",
                "sdk", "use_sdk", "supports_parallel_tool_calls",
            }}
    servers[name] = spec
    config.data.setdefault("mcp", {})["servers"] = servers
    config.save()
    return spec


def probe_server(config, name: str) -> dict:
    """Connect to a configured MCP server and return a structured inventory."""
    spec = _server_configs(config).get(name)
    if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
        raise KeyError(name)
    client = _client_from_spec(name, spec)
    try:
        client.connect()
        all_tools = client.list_tools(apply_filter=False)
        tools = _filter_tools(all_tools, spec.get("tool_filter"))
        resources, resource_error = _optional_capability(client.list_resources)
        prompts, prompt_error = _optional_capability(client.list_prompts)
        return {
            "ok": True,
            "name": name,
            "transport": "http" if spec.get("url") else "stdio",
            "tools": tools,
            "all_tools": all_tools,
            "resources": resources,
            "prompts": prompts,
            "capability_errors": {
                k: v for k, v in {
                    "resources": resource_error,
                    "prompts": prompt_error,
                }.items() if v
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "name": name, "error": str(e), "tools": [],
                "all_tools": [], "resources": [], "prompts": []}
    finally:
        client.close()


def tool_checklist(config, name: str) -> dict:
    """Return discovered MCP tools with their current selected/surfaced state."""
    probe = probe_server(config, name)
    if not probe.get("ok"):
        return {**probe, "items": []}
    selected = {tool.get("name", "") for tool in probe.get("tools", [])}
    items = []
    for tool in probe.get("all_tools", []):
        tool_name = str(tool.get("name", ""))
        if not tool_name:
            continue
        items.append({
            "name": tool_name,
            "description": str(tool.get("description", "")),
            "selected": tool_name in selected,
        })
    return {**probe, "items": items}


def save_tool_checklist(config, name: str, include: list[str]) -> dict:
    """Persist a selected MCP tool checklist as ``tool_filter.include``."""
    servers = dict(config.get("mcp.servers", {}) or {})
    spec = servers.get(name)
    if not isinstance(spec, dict):
        raise KeyError(name)
    spec = dict(spec)
    tool_filter = dict(spec.get("tool_filter") or {})
    tool_filter["include"] = _dedupe_strings(include)
    spec["tool_filter"] = tool_filter
    servers[name] = spec
    config.data.setdefault("mcp", {})["servers"] = servers
    config.save()
    return spec


def _filter_tools(tools: list[dict], tool_filter: dict | None) -> list[dict]:
    filt = tool_filter or {}
    has_include = "include" in filt and filt.get("include") is not None
    include = set(filt.get("include") or [])
    exclude = set(filt.get("exclude") or [])
    if not has_include and not exclude:
        return tools
    out = []
    for tool in tools:
        name = tool.get("name", "")
        if has_include and name not in include:
            continue
        if exclude and name in exclude:
            continue
        out.append(tool)
    return out


def _optional_capability(fn) -> tuple[list[dict], str]:
    try:
        return fn(), ""
    except Exception as e:  # noqa: BLE001
        return [], str(e)


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _normalize_roots_config(raw: list | dict | str | None, cwd: str | None = None) -> list[dict]:
    roots: list[dict] = []

    def add_root(value: object, name: object = None) -> None:
        uri = ""
        root_name = str(name or "").strip()
        if isinstance(value, dict):
            root_name = str(value.get("name") or value.get("title") or root_name).strip()
            uri = str(value.get("uri") or value.get("path") or "").strip()
        else:
            uri = str(value or "").strip()
        if not uri:
            return
        parsed = urlparse(uri)
        if not parsed.scheme:
            try:
                path = Path(os.path.expandvars(uri)).expanduser().resolve()
                uri = path.as_uri()
                if not root_name:
                    root_name = path.name or str(path)
            except (OSError, ValueError):
                return
        elif parsed.scheme != "file":
            return
        elif not root_name:
            try:
                root_name = Path(parsed.path).name or parsed.path or uri
            except Exception:  # noqa: BLE001
                root_name = uri
        root = {"uri": uri}
        if root_name:
            root["name"] = root_name
        if root not in roots:
            roots.append(root)

    if isinstance(raw, dict):
        items = raw.get("roots") if isinstance(raw.get("roots"), list) else [raw]
    elif isinstance(raw, list):
        items = raw
    elif raw:
        items = [raw]
    else:
        items = []

    for item in items:
        add_root(item)
    if not roots and cwd:
        add_root(cwd)
    return roots


def _redact_notification_payload(value):
    if isinstance(value, str):
        return _INLINE_AUTH_TOKEN_RE.sub(r"\1 [REDACTED]", redact_secrets(value))
    if isinstance(value, dict):
        return {
            key: _redact_notification_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_notification_payload(item) for item in value]
    return value


def _capability_preview(items: list[dict], key: str, limit: int = 8) -> str:
    values = [str(item.get(key, "")).strip() for item in items if item.get(key)]
    shown = values[:limit]
    suffix = " ..." if len(values) > limit else ""
    return ", ".join(shown) + suffix


def _mcp_advertised_capabilities(client: MCPClient) -> object | None:
    result = getattr(client, "initialize_result", None)
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        result = result["result"]
    if isinstance(result, dict):
        return result.get("capabilities")
    return getattr(result, "capabilities", None) if result is not None else None


def _mcp_capability_advertised(client: MCPClient, capability: str) -> bool | None:
    caps = _mcp_advertised_capabilities(client)
    if caps is None:
        return None
    if isinstance(caps, dict):
        return caps.get(capability) is not None
    return getattr(caps, capability, None) is not None


def _mcp_should_probe_capability(client: MCPClient, capability: str, method_name: str) -> bool:
    advertised = _mcp_capability_advertised(client, capability)
    if advertised is not None:
        return advertised
    return hasattr(client, method_name)


def _mcp_utility_tools_for_client(client: MCPClient) -> list[Tool]:
    tools: list[Tool] = []
    if _mcp_should_probe_capability(client, "resources", "list_resources"):
        try:
            resources = client.list_resources()
        except Exception:  # noqa: BLE001
            resources = []
        if resources:
            tools.append(MCPReadResourceTool(client, resources))
    if _mcp_should_probe_capability(client, "prompts", "list_prompts"):
        try:
            prompts = client.list_prompts()
        except Exception:  # noqa: BLE001
            prompts = []
        if prompts:
            tools.append(MCPGetPromptTool(client, prompts))
    return tools


def _render_prompt_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return str(content)
    ctype = content.get("type", "")
    if ctype == "text":
        return str(content.get("text", ""))
    if ctype == "resource":
        res = content.get("resource") or {}
        return res.get("text") or f"[resource {res.get('uri', '')}]"
    if ctype == "image":
        return "[image content]"
    if ctype == "audio":
        return "[audio content]"
    return f"[{ctype or 'unknown'} content]"


def _looks_like_server_spec(value: object) -> bool:
    return isinstance(value, dict) and any(k in value for k in ("command", "url"))


def _normalize_external_mcp_config(data: object) -> dict:
    """Accept Claude, AEGIS, and common wrapper shapes for mcp.json."""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    if isinstance(data.get("mcp"), dict) and isinstance(data["mcp"].get("servers"), dict):
        return data["mcp"]["servers"]
    if isinstance(data.get("servers"), dict) and not _looks_like_server_spec(data["servers"]):
        return data["servers"]
    return data


def build_manager(config) -> MCPManager:
    mgr = MCPManager()
    for name, spec in _server_configs(config).items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            # skip malformed entries instead of spamming "no command or url configured"
            continue
        mgr.add(_client_from_spec(name, spec))
    return mgr


def _client_from_spec(name: str, spec: dict) -> MCPClient:
    url = spec.get("url")
    if url is not None:
        url = _validate_remote_mcp_url(name, url)
    oauth_required = bool(url and _oauth_required_from_spec(spec))
    oauth = None
    if url and (
        not oauth_required
        or _has_cached_oauth_tokens_for_spec(name, spec)
        or _oauth_spec_needs_bootstrap(spec)
    ):
        oauth = _oauth_from_spec(name, spec, url)
    return MCPClient(
        name, command=spec.get("command"), args=spec.get("args"),
        env=spec.get("env"), url=url, headers=spec.get("headers"),
        cwd=spec.get("cwd"), tool_filter=spec.get("tool_filter"),
        oauth=oauth, oauth_required=oauth_required, elicitation=spec.get("elicitation"),
        sampling=spec.get("sampling"), roots=spec.get("roots"),
        keepalive_interval=spec.get("keepalive_interval"),
        transport=spec.get("transport"),
        sdk=spec.get("sdk", spec.get("use_sdk")),
        supports_parallel_tool_calls=spec.get("supports_parallel_tool_calls"),
    )


def _oauth_from_spec(name: str, spec: dict, url: str) -> ManagedMCPOAuth | None:
    return get_mcp_oauth_manager().get_or_build_auth(name, url, spec)


def _oauth_required_from_spec(spec: dict) -> bool:
    return str(spec.get("auth") or "").lower() == "oauth" or spec.get("oauth") is not None


def _oauth_provider_from_spec(name: str, spec: dict) -> str:
    raw = spec.get("oauth")
    oauth_cfg = raw if isinstance(raw, dict) else {}
    return str(oauth_cfg.get("provider") or f"mcp:{name}")


def _oauth_spec_needs_bootstrap(spec: dict) -> bool:
    raw = spec.get("oauth")
    oauth_cfg = raw if isinstance(raw, dict) else {}
    client_id = oauth_cfg.get("client_id") or oauth_cfg.get("clientId")
    token_url = oauth_cfg.get("token_url") or oauth_cfg.get("tokenUrl")
    return not (client_id and token_url)


def _has_cached_oauth_tokens_for_spec(name: str, spec: dict) -> bool:
    provider = _oauth_provider_from_spec(name, spec)
    try:
        creds = AuthStore().load(provider)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(creds, dict):
        return False
    return bool(creds.get("access_token") or creds.get("refresh_token"))


def _auth_failure_token(headers: dict[str, str]) -> str | None:
    value = headers.get("Authorization") or headers.get("authorization")
    if not value:
        return None
    parts = value.split(None, 1)
    return parts[1] if len(parts) == 2 else value


def mcp_tools_from_config(config) -> tuple[list[Tool], MCPManager]:
    if not config.get("mcp.enabled", True):
        return [], MCPManager()       # respect the disable flag
    try:
        from .startup import claim_background_mcp_discovery

        discovered = claim_background_mcp_discovery(config)
        if discovered is not None:
            return discovered
    except Exception:  # noqa: BLE001
        pass
    mgr = build_manager(config)
    return mgr.connect_all(), mgr
