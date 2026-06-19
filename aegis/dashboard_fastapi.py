"""FastAPI dashboard backend for the AEGIS web UI."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import importlib
import importlib.util
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import subprocess
import tempfile
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .config import Config, DEFAULT_CONFIG, _deep_merge, config_type_errors

try:
    from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
except ImportError as exc:  # pragma: no cover - import check covers dependency presence
    raise RuntimeError(
        "AEGIS dashboard requires fastapi and uvicorn. Install with: "
        "python -m pip install 'fastapi' 'uvicorn[standard]'"
    ) from exc

from . import dashboard as dash

_RESIZE_RE = re.compile(rb"^\x1b\]1337;Resize=cols=(\d+);rows=(\d+)\x07$")
_WS_TICKET_TTL_SECONDS = 30
_WS_TICKETS: dict[str, float] = {}
_WS_TICKET_LOCK = threading.Lock()
_SESSION_COOKIE = "aegis_dashboard_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60
_BASIC_USER_ENV = "AEGIS_DASHBOARD_BASIC_AUTH_USERNAME"
_BASIC_PASS_ENV = "AEGIS_DASHBOARD_BASIC_AUTH_PASSWORD"
_BASIC_SECRET_ENV = "AEGIS_DASHBOARD_BASIC_AUTH_SECRET"
_DESKTOP_CRON_STARTED = False
_DESKTOP_CRON_LOCK = threading.Lock()
_DASHBOARD_READY_SENTINEL = "AEGIS_DASHBOARD_READY"
_DASHBOARD_PLUGIN_API_MOUNT_STATUS: dict[str, dict[str, Any]] = {}
_DASHBOARD_PLUGIN_API_MOUNT_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _coerce_dashboard_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in {"1", "true", "yes", "on"}:
            return True
        if stripped in {"0", "false", "no", "off", ""}:
            return False
    return default


def _start_desktop_cron_ticker(config: Config) -> bool:
    """Start the in-dashboard cron ticker for Electron desktop launches."""
    global _DESKTOP_CRON_STARTED
    if os.environ.get("AEGIS_DESKTOP") != "1":
        return False
    with _DESKTOP_CRON_LOCK:
        if _DESKTOP_CRON_STARTED:
            return False
        _DESKTOP_CRON_STARTED = True

    def loop() -> None:
        from . import cron
        from .surface import SurfaceRunner

        interval = int(config.get("gateway.cron_interval", 60) or 60)
        sink = cron.build_delivery_sink(config, verbose=False)
        runner = SurfaceRunner(config, include_mcp=True)
        while True:
            try:
                cron.tick(config, sink=sink, verbose=False, runner=runner)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(max(5, interval))

    threading.Thread(target=loop, daemon=True, name="aegis-desktop-cron").start()
    return True


def _dashboard_ready_probe_host(host: str) -> str:
    if host in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _dashboard_ready_probe_url(host: str, port: int) -> str:
    target = _dashboard_ready_probe_host(host)
    if ":" in target and not target.startswith("["):
        target = f"[{target}]"
    return f"http://{target}:{int(port)}/api/health"


def _announce_dashboard_ready_when_live(
    config: Config,
    host: str,
    port: int,
    *,
    attempts: int = 160,
    interval: float = 0.05,
    timeout: float = 1.0,
    urlopen: Any | None = None,
) -> threading.Thread:
    """Print an Electron-readable readiness line once /api/health really answers."""

    def loop() -> None:
        from urllib import request as urllib_request

        opener = urlopen or urllib_request.urlopen
        url = _dashboard_ready_probe_url(host, port)
        token = dash._dashboard_token(config) or ""
        headers = {"X-Aegis-Token": token} if token else {}
        last_error = ""
        for _ in range(max(1, int(attempts))):
            try:
                req = urllib_request.Request(url, headers=headers)
                with opener(req, timeout=timeout) as response:
                    status = int(getattr(response, "status", getattr(response, "code", 0)) or 0)
                    raw = response.read(512)
                if 200 <= status < 300:
                    payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "{}"))
                    if payload.get("ok") is True:
                        print(f"{_DASHBOARD_READY_SENTINEL} port={int(port)}", flush=True)
                        return
            except Exception as exc:  # noqa: BLE001 - readiness is best-effort diagnostics
                last_error = str(exc)
            time.sleep(max(0.01, float(interval)))
        if last_error:
            logger.debug("dashboard readiness announcement skipped: %s", last_error)

    thread = threading.Thread(target=loop, daemon=True, name="aegis-dashboard-ready")
    thread.start()
    return thread


def _query_dict(request: Request) -> dict[str, list[str]]:
    return {key: request.query_params.getlist(key) for key in request.query_params.keys()}


def _authorized_token(config: Config, *, query: str = "", header: str = "",
                      auth: str = "", cookie: str = "") -> bool:
    token = dash._dashboard_token(config)
    if not token:
        return not _basic_auth_configured() and not _remote_bind_requires_auth(config)
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    # Constant-time compare against each candidate so a timing side-channel can't
    # be used to recover the token byte-by-byte (matches the basic-auth/session path).
    return any(hmac.compare_digest(token, candidate) for candidate in (query, header, bearer, cookie))


def _basic_auth_credentials() -> tuple[str, str]:
    return os.environ.get(_BASIC_USER_ENV, ""), os.environ.get(_BASIC_PASS_ENV, "")


def _basic_auth_configured() -> bool:
    user, password = _basic_auth_credentials()
    return bool(user and password)


def _auth_configured(config: Config) -> bool:
    return bool(dash._dashboard_token(config) or _basic_auth_configured())


def _auth_providers_payload(config: Config) -> dict[str, Any]:
    token_configured = bool(dash._dashboard_token(config))
    basic_configured = _basic_auth_configured()
    providers: list[dict[str, Any]] = []
    if token_configured:
        providers.append({
            "id": "token",
            "name": "Dashboard token",
            "type": "token",
            "enabled": True,
            "available": True,
            "header": "X-Aegis-Token",
            "query_param": "token",
            "cookie": "aegis_dashboard_token",
        })
    if basic_configured:
        providers.append({
            "id": "basic",
            "name": "Username and password",
            "type": "password",
            "enabled": True,
            "available": True,
            "login_url": "/login",
            "session_cookie": _SESSION_COOKIE,
            "ttl_seconds": _SESSION_TTL_SECONDS,
        })
    if not providers:
        providers.append({
            "id": "loopback",
            "name": "Loopback local access",
            "type": "loopback",
            "enabled": not _remote_bind_requires_auth(config),
            "available": not _remote_bind_requires_auth(config),
        })
    return {
        "ok": True,
        "auth_required": _auth_configured(config) or _remote_bind_requires_auth(config),
        "token_configured": token_configured,
        "basic_configured": basic_configured,
        "session_cookie": _SESSION_COOKIE,
        "ttl_seconds": _SESSION_TTL_SECONDS,
        "default_provider": providers[0]["id"] if providers else "",
        "login_url": "/login" if basic_configured else "",
        "providers": providers,
    }


def _session_secret(config: Config) -> str:
    return (
        os.environ.get(_BASIC_SECRET_ENV)
        or dash._dashboard_token(config)
        or os.environ.get(_BASIC_PASS_ENV)
        or "aegis-dashboard-dev-session-secret"
    )


def _sign_session(payload: str, config: Config) -> str:
    return hmac.new(_session_secret(config).encode(), payload.encode(), hashlib.sha256).hexdigest()


def _make_session_cookie(username: str, config: Config) -> str:
    expiry = int(time.time() + _SESSION_TTL_SECONDS)
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": username, "exp": expiry}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"{payload}.{_sign_session(payload, config)}"


def _session_cookie_authorized(cookie: str, config: Config) -> bool:
    if not cookie or "." not in cookie:
        return False
    payload, _, sig = cookie.partition(".")
    if not hmac.compare_digest(sig, _sign_session(payload, config)):
        return False
    try:
        padded = payload + ("=" * (-len(payload) % 4))
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return int(data.get("exp", 0)) > time.time()
    except Exception:  # noqa: BLE001
        return False


def _basic_auth_authorized(auth: str) -> bool:
    if not auth.startswith("Basic "):
        return False
    expected_user, expected_password = _basic_auth_credentials()
    if not expected_user or not expected_password:
        return False
    try:
        decoded = base64.b64decode(auth.removeprefix("Basic ").strip()).decode()
    except Exception:  # noqa: BLE001
        return False
    username, _, password = decoded.partition(":")
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)


def _is_loopback_host(host: str) -> bool:
    host = (host or "").split(":", 1)[0].strip("[]").lower()
    return host in {"", "127.0.0.1", "localhost", "::1"} or host.startswith("127.")


def _bind_host(config: Config) -> str:
    return str(config.get("server.dashboard_host", "127.0.0.1") or "127.0.0.1")


def _remote_bind_requires_auth(config: Config) -> bool:
    host = _bind_host(config)
    return not _is_loopback_host(host)


def _peer_allowed(client_host: str, host_header: str, config: Config) -> bool:
    if _is_loopback_host(_bind_host(config)):
        return _is_loopback_host(client_host)
    if not _auth_configured(config):
        return False
    bind = _bind_host(config)
    if bind in {"0.0.0.0", "::"}:
        return True
    host_header = (host_header or "").split(":", 1)[0]
    return _is_loopback_host(host_header) or host_header == bind


def _request_peer_allowed(request: Request, config: Config) -> bool:
    client_host = getattr(getattr(request, "client", None), "host", "") or ""
    return _peer_allowed(client_host, request.headers.get("host", ""), config)


def _websocket_peer_allowed(ws: WebSocket, config: Config) -> bool:
    client_host = getattr(getattr(ws, "client", None), "host", "") or ""
    return _peer_allowed(client_host, ws.headers.get("host", ""), config)


def _request_authorized(request: Request, config: Config) -> bool:
    if not _request_peer_allowed(request, config):
        return False
    return _authorized_token(
        config,
        query=request.query_params.get("token", ""),
        header=request.headers.get("X-Aegis-Token", ""),
        auth=request.headers.get("Authorization", ""),
        cookie=request.cookies.get("aegis_dashboard_token", ""),
    ) or _basic_auth_authorized(
        request.headers.get("Authorization", "")
    ) or _session_cookie_authorized(
        request.cookies.get(_SESSION_COOKIE, ""),
        config,
    )


def _require_request(request: Request, config: Config) -> None:
    if not _request_authorized(request, config):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _issue_ws_ticket(ttl_seconds: int = _WS_TICKET_TTL_SECONDS) -> dict:
    now = time.time()
    expires = now + max(1, int(ttl_seconds))
    ticket = secrets.token_urlsafe(32)
    with _WS_TICKET_LOCK:
        for key, expiry in list(_WS_TICKETS.items()):
            if expiry <= now:
                _WS_TICKETS.pop(key, None)
        _WS_TICKETS[ticket] = expires
    return {
        "ticket": ticket,
        "ttl_seconds": int(expires - now),
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds"),
    }


def _consume_ws_ticket(ticket: str) -> bool:
    if not ticket:
        return False
    now = time.time()
    with _WS_TICKET_LOCK:
        expiry = _WS_TICKETS.pop(ticket, None)
        for key, candidate in list(_WS_TICKETS.items()):
            if candidate <= now:
                _WS_TICKETS.pop(key, None)
    return bool(expiry and expiry > now)


def _websocket_authorized(ws: WebSocket, config: Config) -> bool:
    if not _websocket_peer_allowed(ws, config):
        return False
    if _consume_ws_ticket(ws.query_params.get("ticket", "")):
        return True
    return _authorized_token(
        config,
        query=ws.query_params.get("token", ""),
        header=ws.headers.get("X-Aegis-Token", ""),
        auth=ws.headers.get("Authorization", ""),
        cookie=ws.cookies.get("aegis_dashboard_token", ""),
    ) or _basic_auth_authorized(
        ws.headers.get("Authorization", "")
    ) or _session_cookie_authorized(
        ws.cookies.get(_SESSION_COOKIE, ""),
        config,
    )


def _login_page(error: str = "") -> HTMLResponse:
    msg = f"<p class='err'>{error}</p>" if error else ""
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>AEGIS · Sign in</title><style>"
        ":root{--bg:#08090c;--panel:#0e1014;--panel2:#15181f;--line:#1d212a;--line2:#2c313c;"
        "--text:#e9ebf0;--mut:#8d94a4;--faint:#586071;--accent:#6e8bff;--err:#ff5f56;"
        "--grad:linear-gradient(135deg,#8b5cff,#5b8cff 55%,#22d3ee)}"
        "*{box-sizing:border-box}html,body{height:100%}"
        "body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "background:radial-gradient(120% 80% at 50% -10%,#141a2e 0%,var(--bg) 60%);color:var(--text);"
        "display:grid;place-items:center;padding:24px}"
        ".card{width:100%;max-width:360px;background:var(--panel);border:1px solid var(--line);"
        "border-radius:14px;padding:28px 26px;box-shadow:0 24px 60px -20px rgba(0,0,0,.7)}"
        ".brand{display:flex;align-items:center;gap:11px;margin-bottom:22px}"
        ".mark{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;background:var(--grad);"
        "color:#06080f;font-weight:800;font-size:17px}"
        ".brand b{font-size:16px;letter-spacing:-.01em;display:block}.brand span{font-size:12px;color:var(--mut)}"
        "label{display:block;font-size:12px;color:var(--mut);font-weight:500;margin:14px 0 6px}"
        "input{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--text);"
        "border-radius:8px;padding:10px 11px;outline:0;font-size:14px}"
        "input:focus{border-color:color-mix(in srgb,var(--accent) 55%,var(--line));"
        "box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 16%,transparent)}"
        "button{width:100%;margin-top:20px;padding:11px;border:0;border-radius:8px;background:var(--accent);"
        "color:#070a14;font-weight:650;font-size:14px;cursor:pointer;transition:filter .12s}"
        "button:hover{filter:brightness(1.08)}"
        ".err{background:color-mix(in srgb,var(--err) 12%,transparent);border:1px solid "
        "color-mix(in srgb,var(--err) 45%,var(--line));color:var(--err);font-size:12.5px;"
        "padding:9px 11px;border-radius:8px;margin:0 0 4px}"
        ".foot{margin-top:18px;text-align:center;font-size:11px;color:var(--faint)}"
        "</style></head><body><div class='card'>"
        "<div class='brand'><span class='mark'>A</span><div><b>AEGIS</b>"
        "<span>Operator workspace</span></div></div>"
        f"{msg}<form method='post' action='/auth/login'>"
        "<label for='u'>Username</label>"
        "<input id='u' name='username' autocomplete='username' autofocus>"
        "<label for='p'>Password</label>"
        "<input id='p' name='password' type='password' autocomplete='current-password'>"
        "<button type='submit'>Sign in</button></form>"
        "<div class='foot'>Secured dashboard · session expires automatically</div>"
        "</div></body></html>"
    )


def _html_response(config: Config, request: Request | None = None) -> HTMLResponse:
    if request is not None and _basic_auth_configured() and not _request_authorized(request, config):
        return _login_page()
    response = HTMLResponse(dash._page_with_bootstrap(config))
    token = dash._dashboard_token(config)
    client_host = getattr(getattr(request, "client", None), "host", "") if request is not None else "127.0.0.1"
    if token and _is_loopback_host(client_host):
        response.set_cookie(
            "aegis_dashboard_token",
            token,
            httponly=True,
            samesite="lax",
        )
    return response


_CONFIG_FIELD_META: dict[str, dict[str, Any]] = {
    "model.provider": {
        "label": "Provider",
        "description": "Primary model provider used by CLI, dashboard, gateway, and scheduled jobs.",
        "group": "Model",
    },
    "model.default": {
        "label": "Default model",
        "description": "Model id for the active provider.",
        "group": "Model",
    },
    "model.base_url": {
        "label": "Base URL override",
        "description": "OpenAI-compatible endpoint override for custom/local providers.",
        "group": "Model",
    },
    "model.api_mode": {
        "label": "API mode override",
        "description": "Force a transport when auto-detection is not enough.",
        "enum": ["", "chat_completions", "responses", "anthropic", "codex_app_server"],
        "group": "Model",
    },
    "model.context_length": {
        "label": "Context length override",
        "description": "Override detected context length only when the provider catalog is wrong.",
        "group": "Model",
    },
    "tools.exec_mode": {
        "label": "Tool permissions",
        "description": "How shell/file/network tools ask for or apply permissions.",
        "enum": ["auto", "ask", "smart", "allowlist", "deny", "full"],
        "group": "Tools & permissions",
        "restart": "new turns",
    },
    "display.reasoning": {
        "label": "Reasoning display",
        "description": "How much reasoning telemetry the terminal and dashboard show.",
        "enum": ["summary", "live", "off"],
        "group": "Display",
    },
    "display.tool_progress": {
        "label": "Tool progress",
        "description": "How much tool-call progress is shown in dashboard and gateway surfaces.",
        "enum": ["compact", "detailed"],
        "group": "Display",
    },
    "display.tool_progress_grouping": {
        "label": "Tool progress grouping",
        "description": "Group gateway tool progress into one editable bubble or send each tool as a separate message.",
        "enum": ["accumulate", "separate"],
        "group": "Display",
    },
    "display.memory_notifications": {
        "label": "Memory notifications",
        "description": "Chat notification detail for background memory updates.",
        "enum": ["off", "on", "verbose"],
        "group": "Display",
    },
    "agent.reasoning_effort": {
        "label": "Reasoning effort",
        "description": "Default reasoning budget for providers that support it.",
        "enum": ["medium", "high", "low", "minimal", "xhigh", "off"],
        "group": "Agent",
    },
    "agent.service_tier": {
        "label": "Fast mode",
        "description": "Default provider priority tier for models that support Hermes-style fast mode.",
        "enum": ["", "normal", "priority"],
        "group": "Agent",
    },
    "gateway.channels": {
        "label": "Gateway channels",
        "description": "Enabled inbound/outbound gateway platforms.",
        "group": "Gateway",
        "restart": "gateway service",
    },
    "gateway.busy_mode": {
        "label": "Busy mode",
        "description": "What happens when a channel message arrives while the agent is mid-turn.",
        "enum": ["queue", "steer", "interrupt"],
        "group": "Gateway",
    },
    "gateway.session_mode": {
        "label": "Session mode",
        "description": "How channel messages map to AEGIS sessions.",
        "enum": ["main", "per_channel", "per_channel_peer", "per_peer"],
        "group": "Gateway",
    },
    "gateway.require_mention": {
        "label": "Require mention",
        "description": "Only answer in group channels when the bot is mentioned.",
        "group": "Gateway",
    },
    "learn.background": {
        "label": "Background learning",
        "description": "Let AEGIS learn reusable memories/skills in the background.",
        "group": "Learning",
    },
    "learn.auto_apply": {
        "label": "Auto-write memories",
        "description": "Let background review write durable memory entries after substantial turns.",
        "group": "Learning",
    },
    "learn.auto_apply_skills": {
        "label": "Auto-write skills",
        "description": "Let background review create or update reusable skills after substantial turns.",
        "group": "Learning",
    },
    "skills.auto_load": {
        "label": "Auto-load skills",
        "description": "Attach relevant installed skill bodies before matching turns.",
        "group": "Skills",
    },
    "skills.auto_load_limit": {
        "label": "Skill load limit",
        "description": "Maximum number of relevant skills to attach to one turn.",
        "group": "Skills",
    },
    "skills.auto_load_min_score": {
        "label": "Skill match score",
        "description": "Minimum deterministic relevance score required before a skill auto-loads.",
        "group": "Skills",
    },
    "skills.auto_load_max_chars": {
        "label": "Skill load budget",
        "description": "Maximum characters of skill content attached to one turn.",
        "group": "Skills",
    },
    "skills.allowlist": {
        "label": "Skill allowlist",
        "description": "Optional strict list of skills allowed to load.",
        "group": "Skills",
    },
    "skills.bundles": {
        "label": "Skill bundles",
        "description": "Named stacks of skills that can be preloaded together.",
        "group": "Skills",
    },
    "skills.template_vars": {
        "label": "Skill template variables",
        "description": "Expand ${AEGIS_SKILL_DIR}/${AEGIS_SESSION_ID} placeholders when loading skills.",
        "group": "Skills",
    },
    "skills.inline_shell": {
        "label": "Skill inline shell",
        "description": "Opt-in expansion for !`cmd` snippets inside loaded skills.",
        "group": "Skills",
    },
    "skills.inline_shell_timeout": {
        "label": "Inline shell timeout",
        "description": "Maximum seconds for each skill inline shell snippet.",
        "group": "Skills",
    },
    "memory.enabled": {
        "label": "Memory",
        "description": "Enable local memory/profile retrieval.",
        "group": "Memory",
    },
}


def _config_schema(defaults: dict[str, Any] | None = None) -> dict:
    from .config import DEFAULT_CONFIG

    def flatten(node: Any, prefix: str = "") -> list[dict]:
        if isinstance(node, dict):
            rows: list[dict] = []
            for key, value in sorted(node.items()):
                path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, dict):
                    meta = _CONFIG_FIELD_META.get(path)
                    if meta:
                        rows.append({
                            "path": path,
                            "type": "dict",
                            "default": value,
                            **meta,
                        })
                    rows.extend(flatten(value, path))
                else:
                    meta = _CONFIG_FIELD_META.get(path, {})
                    rows.append({
                        "path": path,
                        "type": type(value).__name__ if value is not None else "null",
                        "default": value,
                        **meta,
                    })
            return rows
        return []

    base = copy.deepcopy(defaults or DEFAULT_CONFIG)
    sections = {
        key: {"type": "object", "fields": flatten(value, key)}
        for key, value in sorted(base.items())
        if isinstance(value, dict)
    }
    loose = [
        {"path": key, "type": type(value).__name__ if value is not None else "null",
         "default": value, **_CONFIG_FIELD_META.get(key, {})}
        for key, value in sorted(base.items())
        if not isinstance(value, dict)
    ]
    return {"sections": sections, "fields": flatten(base), "loose": loose}


_SAFE_RESOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _safe_resource_name(name: str, kind: str = "name") -> str:
    value = str(name or "").strip()
    if not value or not _SAFE_RESOURCE_NAME_RE.match(value) or ".." in value:
        raise ValueError(f"invalid {kind}")
    return value


def _get_config_value(config: Config, path: str) -> Any:
    return config.get(path)


def _config_field_map() -> dict[str, dict[str, Any]]:
    return {field["path"]: field for field in _config_schema()["fields"]}


def _validate_config_value(field: dict[str, Any], value: Any) -> str:
    enum = field.get("enum")
    if enum and value not in enum:
        return f"value must be one of: {', '.join(str(x) or '(empty)' for x in enum)}"
    expected = str(field.get("type") or "")
    if expected in {"bool", "boolean"} and not isinstance(value, bool):
        return "value must be a boolean"
    if expected == "int" and (not isinstance(value, int) or isinstance(value, bool)):
        return "value must be an integer"
    if expected == "float" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return "value must be a number"
    if expected == "str" and not isinstance(value, str):
        return "value must be a string"
    if expected == "list" and not isinstance(value, list):
        return "value must be a list"
    if expected == "dict" and not isinstance(value, dict):
        return "value must be an object"
    if expected == "null" and value is not None:
        return "value must be null"
    return ""


def _config_fields_patch(config: Config, body: dict) -> dict:
    updates = body.get("updates")
    if isinstance(updates, dict):
        updates = [{"path": path, "value": value} for path, value in updates.items()]
    if not isinstance(updates, list) or not updates:
        return {"ok": False, "error": "updates must be a non-empty list or object", "results": [], "errors": {}}
    fields = _config_field_map()
    dry_run = bool(body.get("dry_run", False))
    results = []
    errors = []
    error_map: dict[str, str] = {}
    for item in updates:
        if not isinstance(item, dict):
            result = {"ok": False, "error": "update must be an object"}
            results.append(result)
            errors.append(result)
            continue
        path = str(item.get("path") or "").strip()
        value = item.get("value")
        field = fields.get(path)
        if field is None:
            result = {"ok": False, "path": path, "error": "unknown config field"}
            results.append(result)
            errors.append(result)
            error_map[path] = str(result["error"])
            continue
        error = _validate_config_value(field, value)
        if error:
            result = {"ok": False, "path": path, "error": error}
            results.append(result)
            errors.append(result)
            error_map[path] = str(error)
            continue
        result = {
            "ok": True,
            "path": path,
            "value": value,
            "previous": _get_config_value(config, path),
            "restart": field.get("restart", ""),
        }
        results.append(result)
    if errors:
        return {"ok": False, "dry_run": dry_run, "results": results, "errors": error_map}
    if not dry_run:
        for result in results:
            config.set(str(result["path"]), result.get("value"))
    return {
        "ok": True,
        "dry_run": dry_run,
        "results": results,
        "changed": {str(result["path"]): result.get("value") for result in results},
        "config": dash._redacted_config(config),
    }


def _replace_config_mapping(config: Config, raw: dict[str, Any]) -> tuple[dict[str, Any], int]:
    errors = config_type_errors(raw)
    if errors:
        return {
            "ok": False,
            "error": "config type validation failed",
            "errors": errors,
        }, 400
    config.data = _deep_merge(DEFAULT_CONFIG, copy.deepcopy(raw))
    config.save()
    return {"ok": True, "config": copy.deepcopy(config.data)}, 200


def _redacted_value(value: str) -> dict:
    if value == "":
        return {"set": True, "preview": "", "length": 0}
    return {"set": True, "preview": "****", "length": len(value)}


def _env_file_values() -> dict[str, str]:
    from . import config as cfg

    values: dict[str, str] = {}
    path = cfg.env_path()
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _env_list() -> dict:
    from . import config as cfg

    file_values = _env_file_values()
    names = list(dict.fromkeys(dash._COMMON_KEYS + sorted(file_values)))  # noqa: SLF001
    rows = []
    for key in names:
        in_file = key in file_values
        live = os.environ.get(key)
        value = file_values.get(key, live or "")
        row = {"key": key, "source": "file" if in_file else ("environment" if live else "missing")}
        row.update(_redacted_value(value) if in_file or live else {"set": False, "preview": "", "length": 0})
        rows.append(row)
    return {"env_path": str(cfg.env_path()), "keys": rows}


def _env_key_is_set(key: str) -> bool:
    return key in _env_file_values() or bool(os.environ.get(key))


def _validate_env_key(key: Any) -> tuple[str, dict[str, Any] | None]:
    from .secret_capture import validate_secret_key

    try:
        return validate_secret_key(str(key or "")), None
    except ValueError as exc:
        return "", {"ok": False, "error": str(exc)}


def _validate_env_write(body: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    key, error = _validate_env_key(body.get("key"))
    if error is not None:
        return "", "", error
    if "value" not in body:
        return "", "", {"ok": False, "error": "missing value", "key": key}
    value = str(body.get("value") or "")
    if not value.strip():
        return "", "", {"ok": False, "error": "value must not be empty", "key": key}
    if any(ch in value for ch in ("\x00", "\r", "\n")):
        return "", "", {"ok": False, "error": "value must fit on one .env line", "key": key}
    return key, value, None


def _delete_env_key(key: str) -> bool:
    from . import config as cfg
    from .util import atomic_write

    key = key.strip()
    if not key:
        return False
    path = cfg.env_path()
    if not path.exists():
        os.environ.pop(key, None)
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [
        line for line in lines
        if not (line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="))
    ]
    changed = len(kept) != len(lines)
    if changed:
        atomic_write(path, "\n".join(kept) + ("\n" if kept else ""))
    os.environ.pop(key, None)
    return changed


def _env_set_payload(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    key, value, error = _validate_env_write(body)
    if error is not None:
        return error, 400
    from .config import set_env_var

    set_env_var(key, value)
    return {"ok": True, "key": key}, 200


def _env_reveal_payload(key: str) -> tuple[dict[str, Any], int]:
    key, error = _validate_env_key(key)
    if error is not None:
        return error, 400
    values = _env_file_values()
    if key not in values and key not in os.environ:
        return {"ok": False, "error": "key not set", "key": key}, 404
    return {"ok": True, "key": key, "value": values.get(key, os.environ.get(key, ""))}, 200


def _env_delete_payload(key: str) -> tuple[dict[str, Any], int]:
    key, error = _validate_env_key(key)
    if error is not None:
        return error, 400
    return {"ok": _delete_env_key(key), "key": key}, 200


def _provider_auth_row(row: dict, config: Config) -> dict:
    env_vars = list(row.get("env_vars") or [])
    missing = [key for key in env_vars if not _env_key_is_set(str(key))]
    auth = row.get("auth") if isinstance(row.get("auth"), dict) else {}
    pool_status = None
    try:
        from .credentials import pool_for

        pool = pool_for(str(row.get("name") or ""), env_vars, config)
        pool_status = pool.status() if pool is not None else None
    except Exception:  # noqa: BLE001
        pool_status = None
    methods = list(row.get("auth_methods") or [])
    if missing:
        action = "set_api_key"
    elif "codex_cli" in methods:
        action = "codex_login"
    elif row.get("oauth") and row.get("oauth_status") not in {"configured", "not_applicable"}:
        action = "oauth_setup"
    else:
        action = "ready" if auth.get("available") else "inspect"
    return {
        "name": row.get("name", ""),
        "display_name": row.get("display_name") or row.get("name", ""),
        "provider": row.get("name", ""),
        "model": row.get("default_model") or row.get("model") or "",
        "api_mode": row.get("api_mode", ""),
        "auth": auth,
        "auth_methods": methods,
        "auth_scheme": row.get("auth_scheme", ""),
        "env_vars": env_vars,
        "missing_env_vars": missing,
        "oauth": bool(row.get("oauth", False)),
        "oauth_status": row.get("oauth_status", ""),
        "oauth_notes": row.get("oauth_notes", ""),
        "credential_pool": pool_status,
        "capabilities": row.get("capabilities", {}),
        "capability_summary": row.get("capability_summary", ""),
        "suggested_action": action,
        "ready": bool(auth.get("available")) and not missing,
    }


def _provider_auth_payload(config: Config, provider: str = "") -> dict:
    from .providers import registry

    report = registry.provider_report(config)
    rows = [_provider_auth_row(row, config) for row in (report.get("provider_catalog") or [])]
    active_name = str((report.get("active") or {}).get("name") or config.get("model.provider") or "")
    active = next((row for row in rows if row.get("name") == active_name), None)
    if provider:
        match = next((row for row in rows if row.get("name") == provider), None)
        return {"ok": bool(match), "provider": provider, "auth": match, "active": active}
    return {"active": active, "providers": rows, "oauth_catalog": report.get("oauth_catalog", [])}


def _credential_pools_payload(config: Config, provider: str = "") -> dict[str, Any]:
    auth = _provider_auth_payload(config)
    pools: list[dict[str, Any]] = []
    for row in auth.get("providers", []) or []:
        status = row.get("credential_pool")
        if not isinstance(status, dict):
            continue
        item = {
            **status,
            "name": row.get("name", status.get("provider", "")),
            "display_name": row.get("display_name") or row.get("name", ""),
            "provider": row.get("provider") or status.get("provider", ""),
            "env_vars": row.get("env_vars", []),
            "auth_methods": row.get("auth_methods", []),
            "ready": bool(row.get("ready")),
        }
        pools.append(item)
    if provider:
        match = next((row for row in pools if row.get("name") == provider or row.get("provider") == provider), None)
        return {"ok": bool(match), "provider": provider, "pool": match}
    return {"ok": True, "pools": pools, "count": len(pools)}


def _dashboard_action_catalog() -> dict[str, Any]:
    actions = [
        {"id": "update_check", "label": "Check for updates", "destructive": False},
        {"id": "doctor", "label": "Run doctor", "destructive": False},
        {"id": "security_audit", "label": "Run security audit", "destructive": False},
        {"id": "backup", "label": "Create backup", "destructive": False},
        {"id": "curator_run", "label": "Run curator", "destructive": False},
        {"id": "curator_pause", "label": "Pause curator", "destructive": False},
        {"id": "curator_resume", "label": "Resume curator", "destructive": False},
        {"id": "gateway", "label": "Control gateway service", "destructive": True, "requires": ["op"]},
        {"id": "cron", "label": "Control cron service", "destructive": True, "requires": ["op"]},
        {"id": "memory_reset", "label": "Reset MEMORY.md", "destructive": True},
        {"id": "user_reset", "label": "Reset USER.md", "destructive": True},
    ]
    return {"ok": True, "actions": actions, "count": len(actions)}


def _portal_status_payload(config: Config) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "system": dash._system_info(),
        "ops": dash._ops_status(config),
        "update": dash._update_check(),
        "credentials": _credential_pools_payload(config),
        "actions": _dashboard_action_catalog()["actions"],
    }


def _admin_status_payload(config: Config) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "auth": _auth_providers_payload(config),
        "portal": _portal_status_payload(config),
        "plugins": _plugins_payload(config),
    }


def _observability_contract_payload(config: Config) -> dict[str, Any]:
    from . import hooks
    from .agent import events as agent_events

    configured = hooks.list_hooks(config)
    hook_rows = [
        {
            "event": event,
            "configured": event in configured,
            "commands": configured.get(event, []),
            "env_prefix": "AEGIS_HOOK_",
            "timeout_seconds": 10,
        }
        for event in hooks.EVENTS
    ]
    event_rows = [{"type": event_type, "known": True} for event_type in sorted(agent_events.ALL)]
    dashboard_plugins = _dashboard_plugins_payload(config, include_hidden=True)
    dashboard_api_mounts = {
        str(row.get("name") or ""): row.get("api_mount") or {}
        for row in dashboard_plugins
        if row.get("name")
    }
    dashboard_ui_assets = {
        str(row.get("name") or ""): row.get("ui_asset_status") or {}
        for row in dashboard_plugins
        if row.get("name")
    }
    mounted_plugin_api_count = sum(
        1 for mount in dashboard_api_mounts.values() if mount.get("mounted")
    )
    dashboard_plugin_api_route_count = sum(
        len(mount.get("routes") or [])
        for mount in dashboard_api_mounts.values()
        if mount.get("mounted")
    )
    dashboard_plugin_api_request_count = sum(
        int(mount.get("request_count") or 0)
        for mount in dashboard_api_mounts.values()
    )
    dashboard_plugin_api_error_count = sum(
        int(mount.get("mount_error_count") or 0)
        for mount in dashboard_api_mounts.values()
    )
    dashboard_plugin_api_request_error_count = sum(
        int(mount.get("error_count") or 0)
        for mount in dashboard_api_mounts.values()
    )
    dashboard_plugin_ui_asset_error_count = sum(
        len(status.get("errors") or [])
        for status in dashboard_ui_assets.values()
    )
    dashboard_plugin_ui_asset_error_plugin_count = sum(
        1 for status in dashboard_ui_assets.values()
        if str(status.get("status") or "") == "error"
    )
    return {
        "ok": True,
        "version": __version__,
        "agent_events": event_rows,
        "agent_event_types": [row["type"] for row in event_rows],
        "hooks": hook_rows,
        "configured_hooks": configured,
        "dashboard_plugins": {
            "count": len(dashboard_plugins),
            "api_mounts": dashboard_api_mounts,
            "ui_assets": dashboard_ui_assets,
            "api_mounted_count": mounted_plugin_api_count,
            "api_route_count": dashboard_plugin_api_route_count,
            "api_request_count": dashboard_plugin_api_request_count,
            "api_mount_error_count": dashboard_plugin_api_error_count,
            "api_request_error_count": dashboard_plugin_api_request_error_count,
            "api_error_count": dashboard_plugin_api_error_count + dashboard_plugin_api_request_error_count,
            "ui_asset_error_count": dashboard_plugin_ui_asset_error_count,
            "ui_asset_error_plugin_count": dashboard_plugin_ui_asset_error_plugin_count,
        },
        "dashboard_plugin_api_mounts": dashboard_api_mounts,
        "routes": {
            "traces": "/api/traces",
            "trace": "/api/trace",
            "runs": "/api/runs",
            "run": "/api/run",
            "events_sse": "/api/events",
            "events_ws": "/api/ws",
            "publish": "/api/pub",
            "plugins": "/api/plugins",
            "dashboard_plugins": "/api/dashboard/plugins",
            "dashboard_plugin_hub": "/api/dashboard/plugins/hub",
        },
        "semantics": {
            "hook_failure_policy": "best_effort",
            "hook_timeout_seconds": 10,
            "event_shape": "plain JSON object with a 'type' field",
        },
    }


def _hook_test_payload(config: Config, body: dict[str, Any]) -> dict[str, Any]:
    from . import hooks

    event = str(body.get("event") or "").strip()
    if event not in hooks.EVENTS:
        return {"ok": False, "error": "unknown hook event", "event": event, "known_events": list(hooks.EVENTS)}
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    results = hooks.run_hooks(config, event, context)
    return {
        "ok": all(result.ok for result in results),
        "event": event,
        "count": len(results),
        "results": [result.__dict__ for result in results],
    }


_CHANNEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "telegram",
        "label": "Telegram",
        "env": ["TELEGRAM_BOT_TOKEN"],
        "optional_env": [
            "TELEGRAM_ALLOWED_USERS",
            "TELEGRAM_ALLOWED_CHATS",
            "TELEGRAM_IGNORED_CHATS",
            "TELEGRAM_ALLOWED_CHAT_TYPES",
            "TELEGRAM_GROUP_TRIGGER_MODE",
            "TELEGRAM_BOT_USERNAME",
            "TELEGRAM_BOT_ID",
            "TELEGRAM_AUTO_DISCOVER_BOT",
            "TELEGRAM_REGISTER_COMMANDS",
            "TELEGRAM_COMMAND_SCOPE_CHAT_ID",
            "TELEGRAM_COMMAND_LANGUAGE_CODE",
            "TELEGRAM_IDEMPOTENCY_TTL_SECONDS",
            "TELEGRAM_IDEMPOTENCY_CACHE_MAX",
        ],
        "setup": "Create a bot with BotFather, set TELEGRAM_BOT_TOKEN, start the gateway, then approve the pairing code.",
        "pairing": True,
        "adapter_class": "aegis.gateway.channels.TelegramAdapter",
        "auth_type": "bot_token",
        "transport": "long_poll",
        "capabilities": [
            "text",
            "media",
            "typing",
            "status_edit",
            "reply_context",
            "threads",
            "slash_commands",
            "callbacks",
            "reactions",
            "idempotency",
        ],
        "delivery_modes": ["direct", "group", "thread"],
        "security": {
            "allowed_users_env": "TELEGRAM_ALLOWED_USERS",
            "pairing": True,
            "command_cap": 30,
            "command_registration_env": "TELEGRAM_REGISTER_COMMANDS",
            "idempotency_env": [
                "TELEGRAM_IDEMPOTENCY_TTL_SECONDS",
                "TELEGRAM_IDEMPOTENCY_CACHE_MAX",
            ],
        },
    },
    {
        "id": "discord",
        "label": "Discord",
        "env": ["DISCORD_BOT_TOKEN"],
        "optional_env": [
            "DISCORD_ALLOWED_USERS",
            "DISCORD_ALLOWED_ROLES",
            "DISCORD_ALLOW_BOTS",
            "DISCORD_ALLOWED_CHANNELS",
            "DISCORD_IGNORED_CHANNELS",
            "DISCORD_ALLOWED_GUILDS",
            "DISCORD_IGNORED_GUILDS",
            "DISCORD_TRIGGER_MODE",
        ],
        "setup": "Create a Discord bot token and install the discord extra when using this adapter.",
        "pairing": True,
        "adapter_class": "aegis.gateway.discord_channel.DiscordAdapter",
        "auth_type": "bot_token",
        "transport": "gateway",
        "capabilities": ["text", "status_edit", "mentions", "slash_commands"],
        "delivery_modes": ["direct", "guild_channel"],
        "security": {"pairing": True, "command_cap": 100},
    },
    {
        "id": "slack",
        "label": "Slack",
        "env": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
        "optional_env": [
            "SLACK_ALLOW_BOTS",
            "SLACK_ALLOWED_USERS",
            "SLACK_ALLOWED_CHANNELS",
            "SLACK_IGNORED_CHANNELS",
            "SLACK_ALLOWED_TEAMS",
            "SLACK_BOT_USER_ID",
            "SLACK_BOT_ID",
            "SLACK_TRIGGER_MODE",
            "SLACK_REPLY_IN_THREAD",
            "SLACK_IDEMPOTENCY_TTL_SECONDS",
            "SLACK_IDEMPOTENCY_CACHE_MAX",
        ],
        "setup": "Use Socket Mode with a bot token and app-level token.",
        "pairing": True,
        "adapter_class": "aegis.gateway.slack_channel.SlackAdapter",
        "auth_type": "bot_and_app_tokens",
        "transport": "socket_mode",
        "capabilities": [
            "text",
            "threads",
            "status_edit",
            "mentions",
            "slash_commands",
            "interactive_prompts",
            "reactions",
        ],
        "delivery_modes": ["direct", "channel", "thread"],
        "security": {"pairing": True, "command_cap": 30},
    },
    {
        "id": "signal",
        "label": "Signal",
        "env": ["SIGNAL_CLI_ACCOUNT"],
        "optional_env": ["SIGNAL_ALLOWED_USERS", "SIGNAL_CLI_BIN"],
        "setup": "Requires signal-cli and a registered account.",
        "pairing": False,
        "adapter_class": "aegis.gateway.signal_channel.SignalAdapter",
        "auth_type": "local_account",
        "transport": "signal_cli",
        "capabilities": ["text", "groups", "attachments"],
        "delivery_modes": ["direct", "group"],
        "security": {"local_binary": "signal-cli", "allowed_users_env": "SIGNAL_ALLOWED_USERS"},
    },
    {
        "id": "matrix",
        "label": "Matrix",
        "env": ["MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD"],
        "setup": "Requires matrix-nio plus a Matrix homeserver, user, and password.",
        "pairing": True,
        "adapter_class": "aegis.gateway.matrix_channel.MatrixAdapter",
        "auth_type": "password",
        "transport": "matrix_sync",
        "capabilities": ["text", "rooms", "threads"],
        "delivery_modes": ["direct", "room", "thread"],
        "security": {"pairing": True},
    },
    {
        "id": "email",
        "label": "Email",
        "env": ["EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_ADDRESS", "EMAIL_PASSWORD"],
        "optional_env": ["EMAIL_IMAP_PORT", "EMAIL_SMTP_PORT", "EMAIL_POLL", "EMAIL_ALLOWED_SENDERS"],
        "setup": "Configure IMAP and SMTP so AEGIS can read and send mail.",
        "pairing": False,
        "adapter_class": "aegis.gateway.email_channel.EmailAdapter",
        "auth_type": "mailbox_password",
        "transport": "imap_smtp",
        "capabilities": ["text", "attachments", "reply_headers", "sender_allowlist"],
        "delivery_modes": ["mailbox", "thread"],
        "security": {"secrets": ["EMAIL_PASSWORD"], "allowed_senders_env": "EMAIL_ALLOWED_SENDERS"},
    },
    {
        "id": "mattermost",
        "label": "Mattermost",
        "env": ["MATTERMOST_URL", "MATTERMOST_BOT_TOKEN"],
        "optional_env": [
            "MATTERMOST_WEBHOOK_SECRET",
            "MATTERMOST_OUTGOING_TOKEN",
            "MATTERMOST_CHANNEL_PORT",
            "MATTERMOST_BOT_USER_ID",
            "MATTERMOST_ALLOW_UNSIGNED_LOOPBACK",
            "MATTERMOST_INSECURE_NO_AUTH",
            "MATTERMOST_RATE_LIMIT_PER_MINUTE",
            "MATTERMOST_IDEMPOTENCY_TTL_SECONDS",
            "MATTERMOST_IDEMPOTENCY_CACHE_MAX",
        ],
        "setup": "Configure a Mattermost bot token plus an outgoing webhook or slash-command endpoint.",
        "pairing": False,
        "adapter_class": "aegis.gateway.mattermost_channel.MattermostAdapter",
        "auth_type": "bearer_and_webhook_secret",
        "transport": "http_webhook",
        "capabilities": ["text", "threads", "webhook_events", "slash_commands", "reactions", "idempotency", "rate_limit"],
        "delivery_modes": ["channel", "thread", "webhook"],
        "security": {
            "auth_type": "bearer",
            "webhook_secret_env": "MATTERMOST_WEBHOOK_SECRET",
            "outgoing_token_env": "MATTERMOST_OUTGOING_TOKEN",
            "loopback_unsigned_env": "MATTERMOST_ALLOW_UNSIGNED_LOOPBACK",
            "rate_limit_env": "MATTERMOST_RATE_LIMIT_PER_MINUTE",
            "idempotency_env": [
                "MATTERMOST_IDEMPOTENCY_TTL_SECONDS",
                "MATTERMOST_IDEMPOTENCY_CACHE_MAX",
            ],
        },
    },
    {
        "id": "webhook",
        "label": "Webhook",
        "env": [],
        "setup": "POST bridge events to the local webhook endpoint.",
        "optional_env": [
            "WEBHOOK_CHANNEL_SECRET",
            "WEBHOOK_CHANNEL_PORT",
            "WEBHOOK_CHANNEL_MAX_BYTES",
            "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE",
            "WEBHOOK_CHANNEL_IDEMPOTENCY_TTL_SECONDS",
            "WEBHOOK_CHANNEL_IDEMPOTENCY_CACHE_MAX",
            "WEBHOOK_CHANNEL_INSECURE_NO_AUTH",
            "WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
            "WEBHOOK_CHANNEL_OUTBOUND_URL",
            "WEBHOOK_CHANNEL_OUTBOUND_SECRET",
            "WEBHOOK_CHANNEL_OUTBOUND_MAX_CHARS",
        ],
        "pairing": False,
        "adapter_class": "aegis.gateway.webhook_channel.WebhookChannel",
        "auth_type": "local_http",
        "transport": "http",
        "capabilities": [
            "text",
            "threads",
            "webhook_events",
            "interactive_prompts",
            "idempotency",
            "rate_limit",
            "signature_verification",
        ],
        "delivery_modes": ["webhook", "thread"],
        "security": {
            "local_only_recommended": True,
            "loopback_unsigned_allowed": True,
            "secret_env": "WEBHOOK_CHANNEL_SECRET",
            "max_body_bytes_env": "WEBHOOK_CHANNEL_MAX_BYTES",
            "rate_limit_env": "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE",
            "idempotency_env": [
                "WEBHOOK_CHANNEL_IDEMPOTENCY_TTL_SECONDS",
                "WEBHOOK_CHANNEL_IDEMPOTENCY_CACHE_MAX",
            ],
            "signature_schemes": [
                "X-Secret",
                "X-Hub-Signature-256",
                "X-Webhook-Signature",
                "svix-signature",
                "X-Gitlab-Token",
            ],
        },
    },
    {
        "id": "whatsapp",
        "label": "WhatsApp",
        "env": [],
        "setup": "Connect Baileys, whatsapp-web.js, or another bridge to the local WhatsApp webhook endpoint.",
        "optional_env": [
            "WHATSAPP_CHANNEL_SECRET",
            "WHATSAPP_CHANNEL_PORT",
            "WHATSAPP_CHANNEL_MAX_BYTES",
            "WHATSAPP_CHANNEL_RATE_LIMIT_PER_MINUTE",
            "WHATSAPP_CHANNEL_IDEMPOTENCY_TTL_SECONDS",
            "WHATSAPP_CHANNEL_IDEMPOTENCY_CACHE_MAX",
            "WHATSAPP_CHANNEL_INSECURE_NO_AUTH",
            "WHATSAPP_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
            "WHATSAPP_CHANNEL_OUTBOUND_URL",
            "WHATSAPP_CHANNEL_OUTBOUND_SECRET",
            "WHATSAPP_CHANNEL_OUTBOUND_MAX_CHARS",
        ],
        "pairing": False,
        "adapter_class": "aegis.gateway.webhook_channel.WebhookChannel",
        "auth_type": "local_http_bridge",
        "transport": "http_bridge",
        "capabilities": [
            "text",
            "threads",
            "reply_context",
            "whatsapp_bridge_aliases",
            "whatsapp_nested_media",
            "interactive_prompts",
            "idempotency",
            "rate_limit",
            "signature_verification",
        ],
        "bridge_capabilities": ["whatsapp_bridge_aliases", "whatsapp_nested_media", "interactive_prompts"],
        "delivery_modes": ["chat", "group", "thread", "webhook"],
        "security": {
            "local_only_recommended": True,
            "loopback_unsigned_allowed": True,
            "secret_env": "WHATSAPP_CHANNEL_SECRET",
            "bridge": "webhook",
            "max_body_bytes_env": "WHATSAPP_CHANNEL_MAX_BYTES",
            "rate_limit_env": "WHATSAPP_CHANNEL_RATE_LIMIT_PER_MINUTE",
            "idempotency_env": [
                "WHATSAPP_CHANNEL_IDEMPOTENCY_TTL_SECONDS",
                "WHATSAPP_CHANNEL_IDEMPOTENCY_CACHE_MAX",
            ],
            "signature_schemes": [
                "X-Secret",
                "X-Hub-Signature-256",
                "X-Webhook-Signature",
                "svix-signature",
                "X-Gitlab-Token",
            ],
        },
    },
    {
        "id": "ntfy",
        "label": "ntfy",
        "env": ["NTFY_TOPIC"],
        "optional_env": ["NTFY_SERVER", "NTFY_TOKEN"],
        "setup": "Use ntfy for lightweight push notifications and replies.",
        "pairing": False,
        "adapter_class": "aegis.gateway.ntfy_channel.NtfyAdapter",
        "auth_type": "topic_token",
        "transport": "ntfy_stream",
        "capabilities": ["text", "push", "title_tags_priority", "attachments"],
        "delivery_modes": ["topic"],
        "security": {"optional_token": "NTFY_TOKEN"},
    },
]


def _channel_catalog_map() -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in _CHANNEL_CATALOG}


def _platform_required_env(item: dict[str, Any]) -> list[str]:
    return [str(key) for key in (item.get("env") or item.get("env_vars") or [])]


def _platform_optional_env(item: dict[str, Any]) -> list[str]:
    return [str(key) for key in (item.get("optional_env") or item.get("optional_env_vars") or [])]


def _platform_configurable_env(item: dict[str, Any]) -> set[str]:
    return set(_platform_required_env(item)) | set(_platform_optional_env(item))


def _gateway_channel_payload(config: Config, channel: str | None = None) -> dict:
    from .doctor import CHANNEL_PROBES

    enabled = set(config.get("gateway.channels", []) or [])
    profiles = config.get("gateway.profiles", {}) or {}
    rows = []
    for item in _CHANNEL_CATALOG:
        row = dict(item)
        env_vars = _platform_required_env(row)
        optional_env_vars = _platform_optional_env(row)
        missing = [key for key in env_vars if not _env_key_is_set(str(key))]
        channel_id = str(row["id"])
        row.update({
            "enabled": channel_id in enabled,
            "configured": channel_id in enabled and not missing,
            "env_vars": env_vars,
            "optional_env_vars": optional_env_vars,
            "missing_env_vars": missing,
            "probe_available": channel_id in CHANNEL_PROBES,
            "profile": profiles.get(channel_id, {}) if isinstance(profiles, dict) else {},
        })
        rows.append(row)
    if channel:
        match = next((row for row in rows if row["id"] == channel), None)
        return {"ok": bool(match), "channel": match}
    return {
        "channels": rows,
        "enabled": sorted(enabled),
        "gateway": _gateway_status(config),
    }


def _set_gateway_channel(config: Config, channel: str, body: dict) -> dict:
    channel = _safe_resource_name(channel, "channel").lower()
    if channel not in _channel_catalog_map():
        return {"ok": False, "error": "unknown channel", "channel": channel}
    channels = set(config.get("gateway.channels", []) or [])
    if "enabled" in body:
        if bool(body.get("enabled")):
            channels.add(channel)
        else:
            channels.discard(channel)
        config.data.setdefault("gateway", {})["channels"] = sorted(channels)
    profile_keys = {"personality", "profile", "provider", "model", "reasoning_effort", "service_tier", "busy_mode"}
    overlay = {k: body[k] for k in profile_keys if k in body and body[k] not in ("", None)}
    if overlay:
        profiles = dict(config.get("gateway.profiles", {}) or {})
        existing = dict(profiles.get(channel, {}) or {})
        if "profile" in overlay and "personality" not in overlay:
            overlay["personality"] = overlay.pop("profile")
        existing.update(overlay)
        profiles[channel] = existing
        config.data.setdefault("gateway", {})["profiles"] = profiles
    config.save()
    return {"ok": True, **_gateway_channel_payload(config, channel)}


def _messaging_platform_env_fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for key in _platform_required_env(item):
        key_text = str(key)
        fields.append({
            "key": key_text,
            "required": True,
            "set": _env_key_is_set(key_text),
            "description": f"Required for {item.get('label') or item.get('id')}",
        })
    for key in _platform_optional_env(item):
        key_text = str(key)
        fields.append({
            "key": key_text,
            "required": False,
            "set": _env_key_is_set(key_text),
            "description": f"Optional control for {item.get('label') or item.get('id')}",
        })
    return fields


def _messaging_platform_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter_class": item.get("adapter_class", ""),
        "auth_type": item.get("auth_type", ""),
        "transport": item.get("transport", ""),
        "capabilities": list(item.get("capabilities", []) or []),
        "bridge_capabilities": list(item.get("bridge_capabilities", []) or []),
        "delivery_modes": list(item.get("delivery_modes", []) or []),
        "security": copy.deepcopy(item.get("security", {}) or {}),
        "required_env": _platform_required_env(item),
        "optional_env": _platform_optional_env(item),
        "pairing": bool(item.get("pairing", False)),
        "source": item.get("source", "builtin"),
    }


def _messaging_platform_row(config: Config, item: dict[str, Any]) -> dict[str, Any]:
    gateway_payload = _gateway_channel_payload(config, str(item["id"]))
    channel = gateway_payload.get("channel") or {}
    env_fields = _messaging_platform_env_fields(item)
    missing = [field["key"] for field in env_fields if field["required"] and not field["set"]]
    enabled = bool(channel.get("enabled", False))
    configured = not missing
    if not enabled:
        state = "disabled"
    elif configured:
        state = "ready"
    else:
        state = "not_configured"
    return {
        "id": item["id"],
        "name": item.get("label") or item["id"],
        "label": item.get("label") or item["id"],
        "description": item.get("setup", ""),
        "setup": item.get("setup", ""),
        "docs_url": item.get("docs_url", ""),
        "enabled": enabled,
        "configured": configured,
        "state": state,
        "env_vars": env_fields,
        "required_env_vars": _platform_required_env(item),
        "optional_env_vars": _platform_optional_env(item),
        "missing_env_vars": missing,
        "pairing": bool(item.get("pairing", False)),
        "profile": channel.get("profile", {}),
        "probe_available": bool(channel.get("probe_available", False)),
        "source": item.get("source", "builtin"),
        "adapter_class": item.get("adapter_class", ""),
        "auth_type": item.get("auth_type", ""),
        "transport": item.get("transport", ""),
        "capabilities": list(item.get("capabilities", []) or []),
        "delivery_modes": list(item.get("delivery_modes", []) or []),
        "security": copy.deepcopy(item.get("security", {}) or {}),
        "metadata": _messaging_platform_metadata(item),
    }


def _messaging_platforms_payload(config: Config, platform_id: str = "") -> dict[str, Any]:
    catalog = _channel_catalog_map()
    if platform_id:
        safe = _safe_resource_name(platform_id, "platform").lower()
        item = catalog.get(safe)
        if not item:
            return {"ok": False, "error": "unknown messaging platform", "platform": safe}
        return {"ok": True, "platform": _messaging_platform_row(config, item)}
    return {"platforms": [_messaging_platform_row(config, item) for item in _CHANNEL_CATALOG]}


def _platform_registry_payload(config: Config, platform_id: str = "") -> dict[str, Any]:
    payload = _messaging_platforms_payload(config, platform_id)
    if platform_id:
        platform = payload.get("platform")
        return {
            "ok": bool(payload.get("ok")),
            "platform": platform,
            "registry": platform,
            "error": payload.get("error", ""),
        }
    rows = payload.get("platforms", [])
    return {
        "ok": True,
        "platforms": rows,
        "registry": rows,
        "count": len(rows),
        "enabled": [row["id"] for row in rows if row.get("enabled")],
    }


def _messaging_platform_update(config: Config, platform_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from .config import set_env_var

    safe = _safe_resource_name(platform_id, "platform").lower()
    item = _channel_catalog_map().get(safe)
    if not item:
        return {"ok": False, "error": "unknown messaging platform", "platform": safe}
    allowed_env = _platform_configurable_env(item)
    clear_env = body.get("clear_env") or []
    if isinstance(clear_env, str):
        clear_env = [clear_env]
    if not isinstance(clear_env, list):
        return {"ok": False, "error": "clear_env must be a list", "platform": safe}
    for key in clear_env:
        key_text = str(key).strip()
        if key_text not in allowed_env:
            return {"ok": False, "error": f"{key_text} is not configurable for {item.get('label')}", "platform": safe}
        _delete_env_key(key_text)
    env_updates = body.get("env") or {}
    if not isinstance(env_updates, dict):
        return {"ok": False, "error": "env must be an object", "platform": safe}
    for key, value in env_updates.items():
        key_text = str(key).strip()
        if key_text not in allowed_env:
            return {"ok": False, "error": f"{key_text} is not configurable for {item.get('label')}", "platform": safe}
        value_text = str(value or "").strip()
        if value_text:
            set_env_var(key_text, value_text)
        else:
            _delete_env_key(key_text)
    channel_body = {key: body[key] for key in (
        "enabled", "personality", "profile", "provider", "model", "reasoning_effort", "service_tier", "busy_mode",
    ) if key in body}
    if channel_body:
        result = _set_gateway_channel(config, safe, channel_body)
        if not result.get("ok"):
            return result
    return {"ok": True, "platform": _messaging_platform_row(config, item)}


def _messaging_platform_test(config: Config, platform_id: str) -> dict[str, Any]:
    safe = _safe_resource_name(platform_id, "platform").lower()
    item = _channel_catalog_map().get(safe)
    if not item:
        return {"ok": False, "error": "unknown messaging platform", "platform": safe}
    platform = _messaging_platform_row(config, item)
    if not platform["enabled"]:
        return {
            "ok": False,
            "platform": safe,
            "state": platform["state"],
            "message": f"{platform['name']} is disabled. Enable it, then restart the gateway.",
        }
    if not platform["configured"]:
        missing = ", ".join(platform["missing_env_vars"])
        return {
            "ok": False,
            "platform": safe,
            "state": platform["state"],
            "message": f"Missing required setup for {platform['name']}: {missing}",
        }
    probe = _gateway_probe({"channel": safe})
    probe["platform"] = safe
    probe["state"] = "ready" if probe.get("ok") else "error"
    if "detail" in probe and "message" not in probe:
        probe["message"] = probe["detail"]
    return probe


def _profile_dir() -> Path:
    from . import config as cfg

    return cfg.workspace_dir() / "personalities"


def _profile_path(name: str) -> Path:
    safe = _safe_resource_name(name, "profile")
    path = (_profile_dir() / f"{safe}.md").resolve()
    if path.parent != _profile_dir().resolve():
        raise ValueError("invalid profile")
    return path


def _profile_detail(config: Config, name: str) -> dict:
    try:
        path = _profile_path(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "name": name}
    if not path.exists():
        return {"ok": False, "error": "profile not found", "name": name}
    text = path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "name": path.stem,
        "active": (config.get("agent.personality") or "") == path.stem,
        "path": str(path),
        "content": text,
        "preview": text[:500],
        "bytes": len(text.encode("utf-8")),
    }


def _profiles_payload(config: Config) -> dict:
    active = str(config.get("agent.personality") or "")
    directory = _profile_dir()
    directory.mkdir(parents=True, exist_ok=True)
    names = sorted(path.stem for path in directory.glob("*.md"))
    return {
        "active": active,
        "available": names,
        "path": str(directory),
        "profiles": [
            {
                "name": name,
                "active": name == active,
                "path": str(_profile_path(name)),
            }
            for name in names
        ],
    }


def _write_profile(config: Config, name: str, content: str) -> dict:
    path = _profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "profile": _profile_detail(config, path.stem)}


def _runtime_profiles_payload() -> dict:
    from . import config as cfg
    from . import profiles

    rows = [info.as_dict() for info in profiles.list_profiles()]
    return {
        "active": profiles.label(cfg.current_profile()),
        "profiles": rows,
    }


def _skill_writable_roots() -> list[Path]:
    from . import config as cfg

    roots = [
        Path.cwd() / ".aegis" / "skills",
        Path.cwd() / "skills",
        cfg.skills_dir(),
    ]
    seen = set()
    resolved = []
    for root in roots:
        candidate = root.expanduser().resolve()
        if candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)
    return resolved


def _skill_path_editable(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in _skill_writable_roots())


def _path_has_symlink(raw_path: Path, root: Path) -> bool:
    try:
        rel = raw_path.relative_to(root)
    except ValueError:
        return True
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    return False


def _validate_skill_delete_target(skill_path: Path) -> tuple[Path | None, str]:
    if skill_path.name != "SKILL.md":
        return None, "skill path must point to SKILL.md"
    roots = _skill_writable_roots()
    raw_target = skill_path.expanduser().parent.absolute()
    try:
        resolved_target = raw_target.resolve(strict=True)
    except OSError as exc:
        return None, f"skill path cannot be resolved: {exc}"
    root = next((candidate for candidate in roots if resolved_target.is_relative_to(candidate)), None)
    if root is None:
        return None, "only workspace or personal skills can be deleted"
    if resolved_target == root:
        return None, "refusing to delete a skills root"
    if _path_has_symlink(raw_target, root):
        return None, "refusing to delete a symlinked skill path"
    try:
        (resolved_target / "SKILL.md").resolve(strict=True)
    except OSError:
        return None, "skill directory does not contain SKILL.md"
    return resolved_target, ""


def _title_category(value: str) -> str:
    return str(value or "General").replace("_", " ").replace("-", " ").title()


def _skill_category(skill) -> str:
    meta = skill.metadata if isinstance(skill.metadata, dict) else {}
    category = meta.get("category")
    if isinstance(category, str) and category.strip():
        return _title_category(category)
    try:
        rel = skill.path.parent.relative_to(Path(__file__).parent / "builtin_skills")
        if len(rel.parts) > 1:
            return _title_category(rel.parts[0])
    except ValueError:
        pass
    try:
        rel = skill.path.parent.relative_to(Path.cwd() / ".aegis" / "skills")
        if len(rel.parts) > 1:
            return _title_category(rel.parts[0])
    except ValueError:
        pass
    try:
        rel = skill.path.parent.relative_to(Path.cwd() / "skills")
        if len(rel.parts) > 1:
            return _title_category(rel.parts[0])
    except ValueError:
        pass
    return "General"


def _skill_entry(skill, usage: dict, installed_lock: dict, loader) -> dict:
    reason = loader.unavailable_reason(skill)
    ok = not reason
    lock = installed_lock.get(skill.name, {}) if isinstance(installed_lock, dict) else {}
    return {
        "name": skill.name,
        "description": skill.description,
        "category": _skill_category(skill),
        "path": str(skill.path),
        "tier": skill.tier,
        "platforms": list(getattr(skill, "platforms", []) or []),
        "environments": list(getattr(skill, "environments", []) or []),
        "toolsets": list(getattr(skill, "toolsets", []) or []),
        "available": ok,
        "unavailable_reason": reason,
        "enabled": reason != "disabled",
        "installed": bool(lock),
        "source": lock.get("source", ""),
        "installed_at": lock.get("installed_at", ""),
        "editable": _skill_path_editable(skill.path),
        "usage": usage.get(skill.name, {}) if isinstance(usage, dict) else {},
    }


def _skills_payload(config: Config) -> dict:
    from . import marketplace
    from .skills import SkillsLoader

    loader = SkillsLoader(config)
    usage = loader.usage()
    lock = marketplace.installed()
    rows = [_skill_entry(skill, usage, lock, loader)
            for skill in sorted(loader.discover().values(), key=lambda s: s.name)]
    categories: dict[str, int] = {}
    for row in rows:
        categories[row["category"]] = categories.get(row["category"], 0) + 1
    return {
        "skills": rows,
        "count": len(rows),
        "enabled_count": sum(1 for r in rows if r["enabled"]),
        "categories": categories,
        "installed": lock,
        "taps": marketplace.list_taps(config),
        "registries": marketplace.list_registries(config),
    }


def _skill_detail(config: Config, name: str) -> dict:
    from .skills import SkillsLoader

    try:
        name = _safe_resource_name(name, "skill")
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "name": name}
    loader = SkillsLoader(config)
    skill = loader.discover().get(name)
    if not skill:
        return {"ok": False, "error": "skill not found", "name": name}
    usage = loader.usage()
    entry = _skill_entry(skill, usage, {}, loader)
    return {
        "ok": True,
        "skill": entry,
        "body": skill.full_body(),
        "content": skill.path.read_text(encoding="utf-8"),
        "support_dirs": [
            p.name for p in skill.dir.iterdir()
            if p.is_dir() and p.name in {"assets", "references", "scripts", "templates"}
        ],
    }


def _plugin_detail(config: Config, name: str) -> dict:
    payload = _plugins_payload(config)
    manifests = payload.get("plugins") or []
    match = next((row for row in manifests if _plugin_row_matches(row, name)), None)
    return {"ok": bool(match), "plugin": match, **payload}


def _validate_plugin_source(source: str) -> dict:
    path = Path(str(source or "")).expanduser()
    if not source:
        return {"ok": False, "error": "source is required"}
    if not path.exists():
        try:
            from . import plugins as plugin_runtime

            git_url, subdir = plugin_runtime._resolve_git_url(str(source or ""))
            return {
                "ok": True,
                "source": source,
                "kind": "git",
                "git_url": git_url,
                "subdir": subdir or "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "source": source, "error": str(exc) or "source does not exist"}
    if path.is_file() and path.suffix != ".py":
        return {"ok": False, "source": str(path), "error": "plugin file must be a .py file"}
    if path.is_dir() and not any((path / name).exists() for name in ("plugin.yaml", "plugin.yml", "plugin.json", "aegis-plugin.json")):
        py_files = [p for p in path.glob("*.py") if not p.name.startswith("_")]
        if not py_files:
            return {"ok": False, "source": str(path), "error": "directory needs a plugin manifest or .py file"}
    return {"ok": True, "source": str(path), "kind": "directory" if path.is_dir() else "file"}


def _mcp_servers(config: Config) -> dict[str, dict]:
    return dict(config.get("mcp.servers", {}) or {})


def _mcp_spec_from_body(body: dict, existing: dict | None = None) -> dict:
    spec = dict(existing or {})
    if "enabled" in body:
        spec["enabled"] = bool(body.get("enabled"))
    if "url" in body:
        spec.pop("command", None)
        spec.pop("args", None)
        spec["url"] = str(body.get("url") or "").strip()
    if "command" in body:
        raw = body.get("command")
        if isinstance(raw, list):
            parts = [str(x) for x in raw if str(x).strip()]
        else:
            parts = str(raw or "").split()
        if parts:
            spec.pop("url", None)
            spec["command"] = parts[0]
            spec["args"] = parts[1:]
        else:
            spec["command"] = ""
            spec["args"] = []
    if "args" in body and isinstance(body.get("args"), list):
        spec["args"] = [str(x) for x in body["args"]]
    for key in ("env", "headers", "cwd", "tool_filter"):
        if key in body:
            value = body.get(key)
            if value in ("", None):
                spec.pop(key, None)
            else:
                spec[key] = copy.deepcopy(value)
    if not spec.get("command") and not spec.get("url"):
        raise ValueError("server needs command or url")
    return spec


def _save_mcp_servers(config: Config, servers: dict[str, dict]) -> None:
    config.data.setdefault("mcp", {})["servers"] = servers
    config.save()


def _mcp_catalog_install_response(config: Config, name: str) -> JSONResponse:
    try:
        from .mcp.client import install_from_catalog

        safe = _safe_resource_name(name, "mcp catalog entry")
        spec = install_from_catalog(config, safe)
        target = spec.get("url") or " ".join([spec.get("command", ""), *(spec.get("args") or [])])
        return JSONResponse({"ok": True, "name": safe, "target": target.strip(),
                             **dash._dashboard_mcp_catalog(config)})
    except KeyError:
        return JSONResponse({"ok": False, "error": "catalog entry not found"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _session_export(session) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_id": session.parent_id,
        "messages": [m.to_dict() for m in session.messages],
        "todos": session.todos,
        "meta": session.meta,
    }


def _load_session(session_id: str):
    from .session import SessionStore

    store = SessionStore()
    return store, store.load(session_id)


def _message_payload(message, index: int) -> dict:
    row = message.to_dict()
    row["id"] = index
    row["index"] = index
    return row


def _message_from_payload(body: dict):
    from .types import Message

    if not isinstance(body, dict):
        raise ValueError("message object required")
    role = str(body.get("role") or "").strip()
    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError("role must be system, user, assistant, or tool")
    payload = {
        key: copy.deepcopy(body[key])
        for key in ("role", "content", "tool_calls", "tool_call_id", "name",
                    "reasoning", "thinking_blocks", "images")
        if key in body
    }
    payload.setdefault("content", "")
    return Message.from_dict(payload)


def _patched_message(message, body: dict):
    payload = message.to_dict()
    for key in ("role", "content", "tool_calls", "tool_call_id", "name",
                "reasoning", "thinking_blocks", "images"):
        if key in body:
            payload[key] = copy.deepcopy(body[key])
    return _message_from_payload(payload)


def _plugins_payload(config: Config) -> dict:
    from .plugins import list_manifests, load_plugins, plugin_status, safe_mode_enabled

    api = load_plugins(quiet=True, config=config)
    status_rows = plugin_status(config, api)
    dashboard_plugins = _dashboard_plugins_payload(config, include_hidden=True)
    dashboard_mounts = {
        str(row.get("name") or ""): row.get("api_mount") or {}
        for row in dashboard_plugins
        if row.get("name")
    }
    return {
        "plugins": status_rows,
        "manifests": [m.to_dict() for m in list_manifests(config)],
        "plugin_status": status_rows,
        "dashboard_plugins": dashboard_plugins,
        "dashboard_plugin_count": len(dashboard_plugins),
        "dashboard_api_mounts": dashboard_mounts,
        "dashboard_api_route_count": sum(
            len(mount.get("routes") or [])
            for mount in dashboard_mounts.values()
            if mount.get("mounted")
        ),
        "loaded": [str(p) for p in api.files if p not in {e[0] for e in api.errors}],
        "tools": [getattr(t, "name", "") for t in api.tools],
        "tool_names": [getattr(t, "name", "") for t in api.tools],
        "channels": sorted(api.channels.keys()),
        "providers": list(api.providers),
        "errors": [{"path": str(path), "error": error} for path, error in api.errors],
        "enabled": config.get("plugins.enabled", []) or [],
        "disabled": config.get("plugins.disabled", []) or [],
        "allowlist": config.get("plugins.allowlist", []) or [],
        "safe_mode": safe_mode_enabled(),
    }


def _webhooks_status_payload(config: Config) -> dict[str, Any]:
    from .webhook import MAX_WEBHOOK_BYTES, WEBHOOK_REPLAY_WINDOW_SECONDS, WebhookStore, webhook_runtime_status

    hooks = []
    for hook in WebhookStore().list():
        deliver = [item.strip() for item in str(hook.deliver or "").split(",") if item.strip()]
        hooks.append({
            "name": hook.name,
            "prompt_preview": str(hook.prompt or "")[:160],
            "secret_configured": bool(hook.secret),
            "deliver": deliver,
            "delivery_count": len(deliver),
            "events": list(hook.events or []),
            "skills": list(hook.skills or []),
        })
    insecure_env = any(
        os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("AEGIS_WEBHOOK_INSECURE_NO_AUTH", "WEBHOOK_INSECURE_NO_AUTH")
    )
    return {
        "ok": True,
        "count": len(hooks),
        "hooks": hooks,
        "runtime": webhook_runtime_status(config),
        "security": {
            "allow_unsigned_loopback": bool(config.get("webhook.allow_unsigned_loopback", True)),
            "unsigned_auth_env_override": insecure_env,
            "rate_limit_per_minute": int(config.get("webhook.rate_limit_per_minute", 60) or 60),
            "idempotency_ttl_seconds": int(config.get("webhook.idempotency_ttl_seconds", 3600) or 3600),
            "idempotency_cache_max": int(config.get("webhook.idempotency_cache_max", 10000) or 10000),
            "max_body_bytes": MAX_WEBHOOK_BYTES,
            "replay_window_seconds": WEBHOOK_REPLAY_WINDOW_SECONDS,
            "signature_schemes": [
                "X-Hub-Signature-256",
                "X-Webhook-Signature",
                "svix-signature",
                "X-Gitlab-Token",
            ],
        },
    }


def _safe_plugin_route_name(name: str) -> str:
    value = str(name or "").strip().strip("/")
    if (
        not value
        or len(value) > 240
        or "\x00" in value
        or "\\" in value
        or any(ord(ch) < 32 for ch in value)
    ):
        raise HTTPException(status_code=400, detail="invalid plugin name")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="invalid plugin name")
    return value


def _plugin_row_matches(row: dict[str, Any], name: str) -> bool:
    safe = str(name or "")
    return safe in {str(row.get("name") or ""), str(row.get("key") or "")}


def _plugin_path_under(path: str, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _plugin_manifest_dir(row: dict[str, Any]) -> Path | None:
    raw = str(row.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.name in {"plugin.yaml", "plugin.yml", "plugin.json", "aegis-plugin.json"} or path.suffix == ".py":
        return path.parent
    return path


def _plugin_runtime_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").lower()
    if status == "disabled" or row.get("enabled") is False:
        return "disabled"
    if status == "loaded":
        return "enabled"
    if status == "error":
        return "error"
    return "inactive"


def _dashboard_plugin_hub(config: Config) -> dict[str, Any]:
    from . import config as config_paths
    from .agent.context_engine import _ENGINES
    from .memory_providers import memory_provider_catalog

    payload = _plugins_payload(config)
    dashboard_plugins = _dashboard_plugins_payload(config, include_hidden=True)
    plugin_root = config_paths.sub("plugins").resolve()
    hidden = {str(item) for item in (config.get("dashboard.hidden_plugins", []) or [])}

    dashboard_by_alias: dict[str, dict[str, Any]] = {}
    for record in dashboard_plugins:
        for alias in (record.get("plugin"), record.get("key"), record.get("name")):
            if alias:
                dashboard_by_alias.setdefault(str(alias), record)

    agent_aliases: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in payload.get("plugin_status") or payload.get("plugins") or []:
        name = str(row.get("name") or "")
        key = str(row.get("key") or name)
        aliases = {alias for alias in (name, key) if alias}
        agent_aliases.update(aliases)
        dashboard_manifest = next(
            (dashboard_by_alias[alias] for alias in aliases if alias in dashboard_by_alias),
            None,
        )
        if dashboard_manifest:
            aliases.update(
                str(alias)
                for alias in (
                    dashboard_manifest.get("name"),
                    dashboard_manifest.get("plugin"),
                    dashboard_manifest.get("key"),
                )
                if alias
            )
        manifest_dir = _plugin_manifest_dir(row)
        source = str(row.get("source") or "")
        can_remove = source in {"user", "git", "local"} and bool(
            row.get("path") and _plugin_path_under(str(row.get("path")), plugin_root)
        )
        can_update_git = bool(
            can_remove
            and manifest_dir
            and ((manifest_dir / ".git").exists() or (manifest_dir.parent / ".git").exists())
        )
        has_dashboard_manifest = bool(
            dashboard_manifest or (manifest_dir and (manifest_dir / "dashboard" / "manifest.json").exists())
        )
        dashboard_aliases = set(aliases)
        if not dashboard_manifest and manifest_dir:
            data, _manifest_error = _read_dashboard_manifest_with_error(manifest_dir / "dashboard" / "manifest.json")
            if isinstance(data, dict):
                for alias in (data.get("name"), data.get("plugin"), data.get("key")):
                    if alias:
                        dashboard_aliases.add(str(alias))
        dashboard_disabled = _dashboard_plugin_disabled_by_config(config, dashboard_aliases)
        enriched = dict(row)
        enriched.update({
            "runtime_status": _plugin_runtime_status(row),
            "has_dashboard_manifest": has_dashboard_manifest,
            "dashboard_manifest": dashboard_manifest,
            "dashboard_enabled": not dashboard_disabled,
            "dashboard_route": dashboard_manifest.get("route") if dashboard_manifest else None,
            "api_mount": dashboard_manifest.get("api_mount") if dashboard_manifest else None,
            "ui_asset_status": dashboard_manifest.get("ui_asset_status") if dashboard_manifest else None,
            "asset_errors": dashboard_manifest.get("asset_errors") if dashboard_manifest else [],
            "can_remove": can_remove,
            "can_update_git": can_update_git,
            "auth_required": bool(row.get("auth_required")),
            "auth_command": str(row.get("auth_command") or ""),
            "missing_env": row.get("missing_env") or [],
            "user_hidden": bool(aliases & hidden),
        })
        rows.append(enriched)

    orphan_dashboard_plugins = [
        record for record in dashboard_plugins
        if not ({
            str(record.get("plugin") or ""),
            str(record.get("key") or ""),
            str(record.get("name") or ""),
        } & agent_aliases)
    ]
    for record in orphan_dashboard_plugins:
        aliases = {
            str(record.get("plugin") or ""),
            str(record.get("key") or ""),
            str(record.get("name") or ""),
        }
        runtime_status = str(record.get("status") or "dashboard")
        dashboard_disabled = _dashboard_plugin_disabled_by_config(config, aliases)
        rows.append({
            "name": record.get("plugin") or record.get("name") or "",
            "key": record.get("key") or record.get("plugin") or record.get("name") or "",
            "kind": record.get("kind") or "dashboard",
            "category": record.get("category") or "",
            "source": record.get("source") or "user",
            "version": record.get("version") or "",
            "description": record.get("description") or "",
            "status": runtime_status,
            "enabled": True,
            "runtime_status": runtime_status,
            "dashboard_enabled": not dashboard_disabled,
            "error": record.get("error") or "",
            "errors": record.get("errors") or [],
            "has_dashboard_manifest": True,
            "dashboard_manifest": record,
            "dashboard_route": record.get("route"),
            "api_mount": record.get("api_mount"),
            "ui_asset_status": record.get("ui_asset_status"),
            "asset_errors": record.get("asset_errors") or [],
            "can_remove": False,
            "can_update_git": False,
            "auth_required": False,
            "auth_command": "",
            "user_hidden": bool(aliases & hidden),
        })
    memory_options = [
        str(row.get("name") or "") for row in memory_provider_catalog(config) if row.get("name")
    ]
    return {
        "ok": True,
        "plugins": rows,
        "plugin_status": rows,
        "manifests": payload.get("manifests", []),
        "orphan_dashboard_plugins": orphan_dashboard_plugins,
        "providers": {
            "memory_provider": str(config.get("memory.provider", "") or ""),
            "memory_options": memory_options,
            "context_engine": str(config.get("agent.context_engine", "default") or "default"),
            "context_options": sorted(_ENGINES.keys()),
        },
        "safe_mode": payload.get("safe_mode", False),
        "loaded": payload.get("loaded", []),
        "errors": payload.get("errors", []),
        "enabled": payload.get("enabled", []),
        "disabled": payload.get("disabled", []),
        "allowlist": payload.get("allowlist", []),
    }


def _set_dashboard_plugin_providers(config: Config, body: dict[str, Any]) -> dict[str, Any]:
    if "memory_provider" in body:
        config.set("memory.provider", str(body.get("memory_provider") or "").strip())
    if "context_engine" in body:
        config.set("agent.context_engine", str(body.get("context_engine") or "default").strip() or "default")
    return {"ok": True, **_dashboard_plugin_hub(config)}


def _set_dashboard_plugin_visibility(config: Config, name: str, hidden: bool) -> dict[str, Any]:
    safe = _safe_plugin_route_name(name)
    dashboard = config.data.setdefault("dashboard", {})
    current = dashboard.get("hidden_plugins", [])
    hidden_list = [str(item) for item in current] if isinstance(current, list) else []
    hidden_set = set(hidden_list)
    if hidden:
        hidden_set.add(safe)
    else:
        hidden_set.discard(safe)
    dashboard["hidden_plugins"] = sorted(hidden_set)
    config.save()
    return {"ok": True, "name": safe, "hidden": hidden, **_dashboard_plugin_hub(config)}


def _dashboard_agent_plugin_update(config: Config, name: str) -> dict[str, Any]:
    safe = _safe_plugin_route_name(name)
    hub = _dashboard_plugin_hub(config)
    row = next((item for item in hub.get("plugins", []) if _plugin_row_matches(item, safe)), None)
    if not row:
        return {"ok": False, "name": safe, "error": "plugin not found"}
    if not row.get("can_update_git"):
        return {"ok": False, "name": safe, "error": "plugin is not an updateable git checkout"}
    manifest_dir = _plugin_manifest_dir(row)
    if manifest_dir is None:
        return {"ok": False, "name": safe, "error": "plugin path not found"}
    repo_dir = manifest_dir
    if not (repo_dir / ".git").exists() and (repo_dir.parent / ".git").exists():
        repo_dir = repo_dir.parent
    if not (repo_dir / ".git").exists():
        return {"ok": False, "name": safe, "error": "plugin is not a git checkout"}
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "name": safe, "error": str(exc)}
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        return {"ok": False, "name": safe, "error": output or "git pull failed", "output": output}
    from . import plugins as plugin_runtime

    plugin_runtime.clear_runtime_cache()
    return {
        "ok": True,
        "name": safe,
        "output": output,
        "unchanged": "already up to date" in output.lower(),
    }


_DASHBOARD_STATIC_TYPES = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".map": "application/json",
}


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_plugin_relpath(value: str, *, suffix: str = "") -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or "\x00" in text:
        return ""
    parts = Path(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    if suffix and not text.endswith(suffix):
        return ""
    return text


def _dashboard_plugin_key(plugin_root: Path, base: Path, fallback: str) -> tuple[str, str]:
    try:
        rel = plugin_root.resolve().relative_to(base.resolve())
    except ValueError:
        return fallback, ""
    parts = [part for part in rel.parts if part not in {"", "."}]
    if len(parts) >= 2:
        return "/".join(parts), parts[0]
    return fallback, ""


def _dashboard_plugin_enabled(config: Config, name: str, key: str, dashboard_name: str = "") -> bool:
    disabled = set((config.get("plugins.disabled", []) or []))
    allowlist = set((config.get("plugins.allowlist", []) or []))
    aliases = {name, key or name, dashboard_name}
    aliases.discard("")
    return (
        not _dashboard_plugin_disabled_by_config(config, aliases)
        and not (aliases & disabled)
        and (not allowlist or bool(aliases & allowlist))
    )


def _dashboard_plugin_disabled_by_config(config: Config, aliases: set[str]) -> bool:
    dashboard_plugins = config.get("dashboard.plugins", {}) or {}
    if not isinstance(dashboard_plugins, dict):
        return False
    for alias in aliases:
        entry = dashboard_plugins.get(alias)
        if isinstance(entry, dict) and entry.get("enabled") is False:
            return True
        if entry is False:
            return True
    return False


def _dashboard_plugin_hidden(config: Config, row: dict[str, Any]) -> bool:
    hidden = {str(item) for item in (config.get("dashboard.hidden_plugins", []) or [])}
    aliases = {
        str(row.get("name") or ""),
        str(row.get("plugin") or ""),
        str(row.get("key") or ""),
    }
    aliases.discard("")
    return bool(aliases & hidden)


def _dashboard_plugin_route_metadata(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "")
    tab = row.get("tab") if isinstance(row.get("tab"), dict) else {}
    raw_path = str(tab.get("override") or tab.get("path") or f"/plugins/{name}")
    path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
    route = {
        "path": path,
        "label": str(tab.get("label") or row.get("label") or row.get("title") or name),
        "plugin": name,
        "hidden": bool(tab.get("hidden")),
        "position": str(tab.get("position") or "end"),
    }
    if isinstance(tab.get("override"), str) and str(tab.get("override")).startswith("/"):
        route["override"] = str(tab["override"])
    return route


def _dashboard_plugin_api_observability(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_count": int(info.get("request_count") or 0),
        "success_count": int(info.get("success_count") or 0),
        "error_count": int(info.get("error_count") or 0),
        "last_request_at": str(info.get("last_request_at") or ""),
        "last_request_path": str(info.get("last_request_path") or ""),
        "last_request_method": str(info.get("last_request_method") or ""),
        "last_success_at": str(info.get("last_success_at") or ""),
        "last_error_at": str(info.get("last_error_at") or ""),
        "last_error_path": str(info.get("last_error_path") or ""),
        "last_error_method": str(info.get("last_error_method") or ""),
        "last_error_type": str(info.get("last_error_type") or ""),
        "last_error": str(info.get("last_error") or ""),
        "mount_count": int(info.get("mount_count") or 0),
        "mount_error_count": int(info.get("mount_error_count") or 0),
        "mounted_at": str(info.get("mounted_at") or ""),
        "mount_error_at": str(info.get("mount_error_at") or ""),
        "mount_duration_ms": float(info.get("mount_duration_ms") or 0),
        "fingerprint": str(info.get("fingerprint") or ""),
    }


def _set_dashboard_plugin_api_mount_status(name: str, status: dict[str, Any]) -> None:
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        previous = _DASHBOARD_PLUGIN_API_MOUNT_STATUS.get(name) or {}
        for key, default in (
            ("request_count", 0),
            ("success_count", 0),
            ("error_count", 0),
            ("last_request_at", ""),
            ("last_request_path", ""),
            ("last_request_method", ""),
            ("last_success_at", ""),
            ("last_error_at", ""),
            ("last_error_path", ""),
            ("last_error_method", ""),
            ("last_error_type", ""),
            ("last_error", ""),
            ("mount_count", 0),
            ("mount_error_count", 0),
            ("mounted_at", ""),
            ("mount_error_at", ""),
            ("mount_duration_ms", 0),
            ("fingerprint", ""),
        ):
            if key not in status:
                status[key] = previous.get(key, default)
        _DASHBOARD_PLUGIN_API_MOUNT_STATUS[name] = status


def _dashboard_plugin_api_mount_attempt(
    name: str,
    started: float,
    *,
    ok: bool,
    fingerprint: str = "",
) -> dict[str, Any]:
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        previous = dict(_DASHBOARD_PLUGIN_API_MOUNT_STATUS.get(name) or {})
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "mount_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "fingerprint": fingerprint or str(previous.get("fingerprint") or ""),
    }
    if ok:
        payload.update({
            "mount_count": int(previous.get("mount_count") or 0) + 1,
            "mount_error_count": int(previous.get("mount_error_count") or 0),
            "mounted_at": now,
            "mount_error_at": str(previous.get("mount_error_at") or ""),
        })
    else:
        payload.update({
            "mount_count": int(previous.get("mount_count") or 0),
            "mount_error_count": int(previous.get("mount_error_count") or 0) + 1,
            "mounted_at": str(previous.get("mounted_at") or ""),
            "mount_error_at": now,
        })
    return payload


def _record_dashboard_plugin_api_request(name: str, request: Request) -> None:
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        info = _DASHBOARD_PLUGIN_API_MOUNT_STATUS.setdefault(name, {})
        info["request_count"] = int(info.get("request_count") or 0) + 1
        info["last_request_at"] = datetime.now(timezone.utc).isoformat()
        info["last_request_path"] = request.url.path
        info["last_request_method"] = request.method


def _record_dashboard_plugin_api_success(name: str) -> None:
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        info = _DASHBOARD_PLUGIN_API_MOUNT_STATUS.setdefault(name, {})
        info["success_count"] = int(info.get("success_count") or 0) + 1
        info["last_success_at"] = datetime.now(timezone.utc).isoformat()


def _dashboard_plugin_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        try:
            return json.dumps(detail, sort_keys=True)
        except TypeError:
            return str(detail)
    return str(exc)


def _record_dashboard_plugin_api_error(name: str, request: Request, exc: Exception) -> None:
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        info = _DASHBOARD_PLUGIN_API_MOUNT_STATUS.setdefault(name, {})
        info["error_count"] = int(info.get("error_count") or 0) + 1
        info["last_error_at"] = datetime.now(timezone.utc).isoformat()
        info["last_error_path"] = request.url.path
        info["last_error_method"] = request.method
        info["last_error_type"] = type(exc).__name__
        info["last_error"] = _dashboard_plugin_error_message(exc)[:500]


def _dashboard_plugin_mount_info(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "")
    api_path = str(row.get("_api") or "")
    if row.get("_manifest_error"):
        return {
            "status": "error",
            "mounted": False,
            "api": "",
            "routes": [],
            "error": str(row.get("error") or "invalid dashboard manifest"),
            **_dashboard_plugin_api_observability({}),
        }
    if row.get("_duplicate_name_conflict"):
        return {
            "status": "error",
            "mounted": False,
            "api": Path(api_path).name if api_path else "",
            "routes": [],
            "error": f"duplicate dashboard plugin name: {name}",
            **_dashboard_plugin_api_observability({}),
        }
    if not api_path:
        return {
            "status": "skipped",
            "mounted": False,
            "api": "",
            "routes": [],
            "error": "",
            **_dashboard_plugin_api_observability({}),
        }
    with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
        info = dict(_DASHBOARD_PLUGIN_API_MOUNT_STATUS.get(name) or {})
    if str(info.get("api_path") or "") != api_path:
        return {
            "status": "unmounted",
            "mounted": False,
            "api": Path(api_path).name,
            "routes": [],
            "error": "",
            **_dashboard_plugin_api_observability(info),
        }
    return {
        "status": str(info.get("status") or "unmounted"),
        "mounted": bool(info.get("mounted", False)),
        "api": str(info.get("api") or Path(api_path).name),
        "routes": list(info.get("routes") or []),
        "error": str(info.get("error") or ""),
        **_dashboard_plugin_api_observability(info),
    }


def _dashboard_plugin_asset_exists(asset_root: Path, dist_root: Path, rel: str) -> bool:
    if not rel:
        return False
    for root in (asset_root, dist_root):
        root = root.resolve()
        target = (root / rel).resolve()
        if _contained(root, target) and target.is_file():
            return True
    return False


def _dashboard_plugin_ui_asset_status(row: dict[str, Any]) -> dict[str, Any]:
    entry = str(row.get("entry") or "")
    css = [str(item) for item in (row.get("css") or []) if str(item)]
    errors = [str(item) for item in (row.get("errors") or []) if str(item)] if row.get("_manifest_error") else []
    missing: list[str] = []
    asset_root = Path(row.get("_asset_root") or row.get("_dist") or ".").resolve()
    dist_root = Path(row.get("_dist") or asset_root).resolve()

    if row.get("_duplicate_name_conflict"):
        errors.append(f"duplicate dashboard plugin name: {row.get('name') or ''}")
    if not row.get("_manifest_error") and not row.get("_duplicate_name_conflict"):
        if not entry:
            errors.append("dashboard entry is invalid or empty")
        elif not _dashboard_plugin_asset_exists(asset_root, dist_root, entry):
            missing.append(entry)
            errors.append(f"missing entry asset: {entry}")
        for item in css:
            if not _dashboard_plugin_asset_exists(asset_root, dist_root, item):
                missing.append(item)
                errors.append(f"missing stylesheet asset: {item}")

    return {
        "status": "error" if errors else "ok",
        "entry": entry,
        "entry_exists": bool(entry and entry not in missing),
        "css": css,
        "missing": missing,
        "errors": errors,
        "asset_count": (1 if entry else 0) + len(css),
        "checked": True,
    }


def _read_dashboard_manifest(manifest_path: Path) -> dict[str, Any] | None:
    loaded, _error = _read_dashboard_manifest_with_error(manifest_path)
    return loaded


def _read_dashboard_manifest_with_error(manifest_path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"{manifest_path.name}: {exc}"
    if not isinstance(loaded, dict):
        return None, f"{manifest_path.name}: expected a JSON object"
    return loaded, ""


def _dashboard_plugin_error_row(
    *,
    config: Config,
    plugin_root: Path,
    dash_root: Path,
    plugin_name: str,
    key: str,
    kind: str,
    category: str,
    source: str,
    error: str,
    description: str = "",
    version: str = "",
) -> dict[str, Any] | None:
    if not _dashboard_plugin_enabled(config, plugin_name, key):
        return None
    try:
        name = _safe_resource_name(plugin_name, "plugin")
    except ValueError:
        name = "plugin"
    return {
        "name": name,
        "plugin": plugin_name,
        "key": key or plugin_name,
        "kind": kind,
        "category": category,
        "source": source,
        "label": plugin_name,
        "icon": "TriangleAlert",
        "title": plugin_name,
        "description": description,
        "version": version,
        "status": "error",
        "error": error,
        "errors": [error],
        "manifest_error": True,
        "tab": {"path": f"/{name}", "position": "end", "hidden": True},
        "slots": [],
        "entry": "",
        "css": [],
        "base_path": f"/dashboard-plugins/{name}",
        "has_api": False,
        "api_compat_root": False,
        "_manifest_error": True,
        "_root": str(plugin_root.resolve()),
        "_asset_root": str(dash_root.resolve()),
        "_dist": str((dash_root / "dist").resolve()),
        "_api": "",
    }


def _dashboard_plugin_row(
    *,
    config: Config,
    plugin_root: Path,
    dash_root: Path,
    data: dict[str, Any],
    plugin_name: str,
    key: str,
    kind: str,
    category: str,
    source: str,
    description: str = "",
    version: str = "",
) -> dict[str, Any] | None:
    try:
        name = _safe_resource_name(str(data.get("name") or plugin_name), "plugin")
    except ValueError:
        return None
    if not _dashboard_plugin_enabled(config, plugin_name, key, name):
        return None
    asset_root = dash_root.resolve()
    entry = _safe_plugin_relpath(str(data.get("entry") or "dist/index.js"))
    integrity = str(data.get("integrity") or "").strip()
    css_raw = data.get("css") or []
    css = [_safe_plugin_relpath(str(item)) for item in (css_raw if isinstance(css_raw, list) else [css_raw])]
    css = [item for item in css if item]
    api_value = str(data.get("api") or "")
    if not api_value and (dash_root / "plugin_api.py").exists():
        api_value = "plugin_api.py"
    api_rel = _safe_plugin_relpath(api_value, suffix=".py")
    api_path = (dash_root / api_rel).resolve() if api_rel else None
    if api_path is not None and (not _contained(dash_root.resolve(), api_path) or not api_path.exists()):
        api_path = None
    raw_tab = data.get("tab", {}) if isinstance(data.get("tab"), dict) else {}
    tab = {
        "path": raw_tab.get("path", f"/{name}"),
        "position": raw_tab.get("position", "end"),
    }
    if raw_tab.get("label"):
        tab["label"] = raw_tab.get("label")
    override = raw_tab.get("override")
    if isinstance(override, str) and override.startswith("/"):
        tab["override"] = override
    if bool(raw_tab.get("hidden")):
        tab["hidden"] = True
    slots = [str(slot) for slot in (data.get("slots") or []) if isinstance(slot, str) and slot]
    return {
        "name": name,
        "plugin": plugin_name,
        "key": key or plugin_name,
        "kind": kind,
        "category": category,
        "source": source,
        "label": str(data.get("label") or data.get("title") or name),
        "icon": str(data.get("icon") or "Puzzle"),
        "title": str(data.get("title") or data.get("label") or name),
        "description": str(data.get("description") or description or ""),
        "version": str(data.get("version") or version or ""),
        "tab": tab,
        "slots": slots,
        "entry": entry,
        "integrity": integrity,
        "css": css,
        "base_path": f"/dashboard-plugins/{name}",
        "has_api": bool(api_path and api_path.exists()),
        "api_compat_root": False,
        "_root": str(plugin_root.resolve()),
        "_asset_root": str(asset_root),
        "_dist": str((dash_root / "dist").resolve()),
        "_api": str(api_path) if api_path and api_path.exists() else "",
    }


def _dashboard_plugin_records(config: Config) -> list[dict[str, Any]]:
    from . import config as config_paths
    from .plugins import list_manifests, safe_mode_enabled

    if safe_mode_enabled():
        return []
    rows: list[dict[str, Any]] = []
    base = config_paths.sub("plugins")
    manifest_roots: set[Path] = set()
    for manifest in list_manifests(config):
        plugin_root = manifest.path.parent if manifest.path.is_file() else manifest.path
        manifest_roots.add(plugin_root.resolve())
        if not manifest.enabled:
            continue
        dash_root = plugin_root / "dashboard"
        manifest_path = dash_root / "manifest.json"
        data: dict[str, Any] | None = None
        if manifest_path.exists():
            data, manifest_error = _read_dashboard_manifest_with_error(manifest_path)
            if manifest_error:
                row = _dashboard_plugin_error_row(
                    config=config,
                    plugin_root=plugin_root,
                    dash_root=dash_root,
                    plugin_name=manifest.name,
                    key=manifest.key or manifest.name,
                    kind=manifest.kind,
                    category=manifest.category,
                    source=manifest.source,
                    description=manifest.description,
                    version=manifest.version,
                    error=manifest_error,
                )
                if row:
                    rows.append(row)
                continue
        if data is None and isinstance(getattr(manifest, "raw", None), dict):
            raw_dashboard = manifest.raw.get("dashboard") or manifest.raw.get("dashboard_manifest")
            if isinstance(raw_dashboard, dict):
                data = raw_dashboard
            elif raw_dashboard is not None:
                row = _dashboard_plugin_error_row(
                    config=config,
                    plugin_root=plugin_root,
                    dash_root=dash_root,
                    plugin_name=manifest.name,
                    key=manifest.key or manifest.name,
                    kind=manifest.kind,
                    category=manifest.category,
                    source=manifest.source,
                    description=manifest.description,
                    version=manifest.version,
                    error="embedded dashboard manifest must be an object",
                )
                if row:
                    rows.append(row)
                continue
        if data is None:
            continue
        if not isinstance(data, dict):
            continue
        row = _dashboard_plugin_row(
            config=config,
            plugin_root=plugin_root,
            dash_root=dash_root,
            data=data,
            plugin_name=manifest.name,
            key=manifest.key or manifest.name,
            kind=manifest.kind,
            category=manifest.category,
            source=manifest.source,
            description=manifest.description,
            version=manifest.version,
        )
        if row:
            rows.append(row)
    for manifest_path in sorted(base.rglob("dashboard/manifest.json")) if base.exists() else []:
        plugin_root = manifest_path.parent.parent
        if plugin_root.resolve() in manifest_roots:
            continue
        data, manifest_error = _read_dashboard_manifest_with_error(manifest_path)
        plugin_name = plugin_root.name
        key, category = _dashboard_plugin_key(plugin_root, base, plugin_name)
        if manifest_error:
            row = _dashboard_plugin_error_row(
                config=config,
                plugin_root=plugin_root,
                dash_root=manifest_path.parent,
                plugin_name=plugin_name,
                key=key,
                kind="dashboard",
                category=category,
                source="user",
                error=manifest_error,
            )
            if row:
                rows.append(row)
            continue
        if not data:
            continue
        row = _dashboard_plugin_row(
            config=config,
            plugin_root=plugin_root,
            dash_root=manifest_path.parent,
            data=data,
            plugin_name=plugin_name,
            key=key,
            kind="dashboard",
            category=category,
            source="user",
        )
        if row:
            rows.append(row)
    name_counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("name") or "")
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
    for row in rows:
        name = str(row.get("name") or "")
        if name and name_counts.get(name, 0) > 1:
            row["_duplicate_name_conflict"] = True
            row["name_conflict"] = True
            row["errors"] = [f"duplicate dashboard plugin name: {name}"]
    return rows


def _dashboard_plugins_payload(config: Config, *, include_hidden: bool = False) -> list[dict[str, Any]]:
    public = []
    for row in _dashboard_plugin_records(config):
        if not include_hidden and _dashboard_plugin_hidden(config, row):
            continue
        item = {key: value for key, value in row.items() if not key.startswith("_")}
        mount = _dashboard_plugin_mount_info(row)
        ui_assets = _dashboard_plugin_ui_asset_status(row)
        item["route"] = _dashboard_plugin_route_metadata(row)
        item["api_mount"] = mount
        item["api_mounted"] = mount["mounted"]
        item["api_routes"] = mount["routes"]
        item["ui_asset_status"] = ui_assets
        item["asset_errors"] = ui_assets["errors"]
        item["user_hidden"] = _dashboard_plugin_hidden(config, row)
        public.append(item)
    return public


def _dashboard_plugin_record(config: Config, name: str) -> dict[str, Any] | None:
    safe = _safe_resource_name(name, "plugin")
    rows = [row for row in _dashboard_plugin_records(config) if row.get("name") == safe]
    return rows[0] if len(rows) == 1 and not rows[0].get("_duplicate_name_conflict") else None


def _dashboard_plugin_static(config: Config, name: str, file_path: str) -> Response:
    record = _dashboard_plugin_record(config, name)
    if not record:
        raise HTTPException(status_code=404, detail="dashboard plugin not found")
    rel = _safe_plugin_relpath(file_path)
    if not rel:
        raise HTTPException(status_code=404, detail="asset not found")
    asset_root = Path(record.get("_asset_root") or record["_dist"]).resolve()
    target = (asset_root / rel).resolve()
    if not _contained(asset_root, target) or not target.is_file():
        dist_root = Path(record["_dist"]).resolve()
        fallback = (dist_root / rel).resolve()
        if not _contained(dist_root, fallback) or not fallback.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        target = fallback
    media_type = _DASHBOARD_STATIC_TYPES.get(target.suffix.lower())
    if not media_type:
        raise HTTPException(status_code=404, detail="asset not found")
    data = target.read_bytes()
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


def _dashboard_plugin_api_insert_at(app: FastAPI) -> int:
    fallback_paths = {"/api/plugins/{plugin_name}/{plugin_path:path}", "/api/{path:path}"}
    for index, route in enumerate(app.router.routes):
        if getattr(route, "path", "") in fallback_paths:
            return index
    return len(app.router.routes)


def _dashboard_plugin_api_fingerprint(api_path: str | Path) -> str:
    try:
        stat = Path(api_path).stat()
    except OSError:
        return ""
    return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"


def _dashboard_plugin_api_mount_allowed(record: dict[str, Any]) -> bool:
    return str(record.get("source") or "user").strip().lower() != "project"


def _clear_dashboard_plugin_api_bytecode(path: Path) -> None:
    try:
        cached = importlib.util.cache_from_source(str(path))
    except (NotImplementedError, ValueError):
        cached = ""
    candidates: list[Path] = []
    if cached:
        candidates.append(Path(cached))
    candidates.extend((path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"))
    for candidate in candidates:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _mount_dashboard_plugin_api_routes(app: FastAPI, config: Config) -> None:
    def dashboard_plugin_auth(record_name: str, expected_api: str):
        def auth(request: Request):
            _require_request(request, config)
            live = _dashboard_plugin_record(config, record_name)
            if not live or str(live.get("_api") or "") != expected_api:
                raise HTTPException(status_code=404, detail="dashboard plugin API not mounted")
            _record_dashboard_plugin_api_request(record_name, request)
            try:
                yield
            except Exception as exc:
                _record_dashboard_plugin_api_error(record_name, request, exc)
                raise
            else:
                _record_dashboard_plugin_api_success(record_name)

        return auth

    mounted: dict[str, dict[str, Any]] = getattr(app.state, "dashboard_plugin_api_routes", None) or {}
    app.state.dashboard_plugin_api_routes = mounted
    all_records = _dashboard_plugin_records(config)
    records = {
        str(record["name"]): record
        for record in all_records
        if (
            record.get("_api")
            and not record.get("_duplicate_name_conflict")
            and _dashboard_plugin_api_mount_allowed(record)
        )
    }
    for name, row in list(mounted.items()):
        live = records.get(name)
        live_api = str(live.get("_api") or "") if live else ""
        live_fingerprint = _dashboard_plugin_api_fingerprint(live_api) if live_api else ""
        if (
            live
            and live_api == str(row.get("api") or "")
            and live_fingerprint == str(row.get("fingerprint") or "")
        ):
            continue
        for route in row.get("routes", []):
            try:
                app.router.routes.remove(route)
            except ValueError:
                pass
        mounted.pop(name, None)
        with _DASHBOARD_PLUGIN_API_MOUNT_LOCK:
            _DASHBOARD_PLUGIN_API_MOUNT_STATUS.pop(name, None)

    for record in all_records:
        api_path = record.get("_api")
        record_name = str(record["name"])
        if record.get("_duplicate_name_conflict"):
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "error",
                "mounted": False,
                "api_path": str(api_path or ""),
                "api": Path(str(api_path)).name if api_path else "",
                "routes": [],
                "error": f"duplicate dashboard plugin name: {record_name}",
            })
            continue
        if not api_path:
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "skipped",
                "mounted": False,
                "api_path": "",
                "api": "",
                "routes": [],
                "error": "",
            })
            continue
        if not _dashboard_plugin_api_mount_allowed(record):
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "skipped",
                "mounted": False,
                "api_path": str(api_path),
                "api": Path(str(api_path)).name,
                "routes": [],
                "error": "project dashboard plugin API routes are not auto-mounted",
            })
            continue
        fingerprint = _dashboard_plugin_api_fingerprint(str(api_path))
        if (
            record_name in mounted
            and mounted[record_name].get("api") == str(api_path)
            and mounted[record_name].get("fingerprint") == fingerprint
        ):
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "mounted",
                "mounted": True,
                "api_path": str(api_path),
                "api": Path(str(api_path)).name,
                "routes": list(mounted[record_name].get("route_paths") or []),
                "fingerprint": fingerprint,
                "error": "",
            })
            continue
        path = Path(api_path)
        module_name = "aegis_dashboard_plugin_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
        started = time.perf_counter()
        try:
            importlib.invalidate_caches()
            _clear_dashboard_plugin_api_bytecode(path)
            before = {id(route) for route in app.router.routes}
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                _set_dashboard_plugin_api_mount_status(record_name, {
                    "status": "error",
                    "mounted": False,
                    "api_path": str(api_path),
                    "api": path.name,
                    "routes": [],
                    "error": "could not load api module",
                    **_dashboard_plugin_api_mount_attempt(record_name, started, ok=False, fingerprint=fingerprint),
                })
                continue
            module = importlib.util.module_from_spec(spec)
            import sys

            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            router = getattr(module, "router", None)
            get_router = getattr(module, "get_router", None)
            if router is None and callable(get_router):
                router = get_router()
            if router is None:
                _set_dashboard_plugin_api_mount_status(record_name, {
                    "status": "error",
                    "mounted": False,
                    "api_path": str(api_path),
                    "api": path.name,
                    "routes": [],
                    "error": "api module has no router",
                    **_dashboard_plugin_api_mount_attempt(record_name, started, ok=False, fingerprint=fingerprint),
                })
                continue
            declared_route_paths = []
            for plugin_route in getattr(router, "routes", []) or []:
                plugin_path = str(getattr(plugin_route, "path", "") or "")
                if plugin_path:
                    declared_route_paths.append(
                        f"/api/plugins/{record_name}{plugin_path if plugin_path.startswith('/') else '/' + plugin_path}"
                    )
            app.include_router(
                router,
                prefix=f"/api/plugins/{record_name}",
                dependencies=[Depends(dashboard_plugin_auth(record_name, str(api_path)))],
            )
            new_routes = [route for route in app.router.routes if id(route) not in before]
            if new_routes:
                new_ids = {id(route) for route in new_routes}
                app.router.routes[:] = [route for route in app.router.routes if id(route) not in new_ids]
                insert_at = _dashboard_plugin_api_insert_at(app)
                for offset, route in enumerate(new_routes):
                    app.router.routes.insert(insert_at + offset, route)
            route_paths = sorted({
                str(getattr(route, "path", ""))
                for route in new_routes
                if str(getattr(route, "path", ""))
            } or set(declared_route_paths))
            mounted[record_name] = {
                "api": str(api_path),
                "fingerprint": fingerprint,
                "routes": new_routes,
                "route_paths": route_paths,
            }
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "mounted",
                "mounted": True,
                "api_path": str(api_path),
                "api": path.name,
                "routes": route_paths,
                "error": "",
                **_dashboard_plugin_api_mount_attempt(record_name, started, ok=True, fingerprint=fingerprint),
            })
        except Exception as exc:  # noqa: BLE001
            _set_dashboard_plugin_api_mount_status(record_name, {
                "status": "error",
                "mounted": False,
                "api_path": str(api_path),
                "api": path.name,
                "routes": [],
                "error": str(exc),
                **_dashboard_plugin_api_mount_attempt(record_name, started, ok=False, fingerprint=fingerprint),
            })
            continue


def _normalise_dashboard_choice(value: Any, choices: set[str], default: str) -> str:
    if isinstance(value, bool):
        value = "on" if value else "off"
    raw = str(value or "").strip().lower()
    return raw if raw in choices else default


def _tool_progress_grouping(config: Config) -> str:
    from .display_config import normalize_display_setting

    return str(normalize_display_setting(
        "tool_progress_grouping",
        config.get("display.tool_progress_grouping", "accumulate"),
    ))


def _memory_notifications(config: Config) -> str:
    from .display_config import normalize_display_setting

    return str(normalize_display_setting(
        "memory_notifications",
        config.get("display.memory_notifications", "on"),
    ))


def _display_platforms(config: Config) -> dict[str, dict[str, Any]]:
    from .display_config import normalize_platform_display_overrides

    return normalize_platform_display_overrides(config.get("display.platforms", {}) or {})


def _dashboard_preferences(config: Config) -> dict:
    grouping = _tool_progress_grouping(config)
    return {
        "theme": config.get("display.theme", "system"),
        "reasoning": config.get("display.reasoning", "summary"),
        "status_footer": bool(config.get("display.status_footer", True)),
        "tool_progress": config.get("display.tool_progress", "compact"),
        "tool_progress_grouping": grouping,
        "tool_progress_style": grouping,
        "memory_notifications": _memory_notifications(config),
        "platforms": _display_platforms(config),
        "frontend": config.get("dashboard.frontend", "static"),
        "cockpit": bool(config.get("dashboard.cockpit", True)),
    }


def _set_dashboard_preferences(config: Config, body: dict) -> dict:
    mapping = {
        "theme": "display.theme",
        "reasoning": "display.reasoning",
        "status_footer": "display.status_footer",
        "tool_progress": "display.tool_progress",
        "frontend": "dashboard.frontend",
        "cockpit": "dashboard.cockpit",
    }
    for key, config_key in mapping.items():
        if key in body:
            config.set(config_key, body[key])
    if "tool_progress_grouping" in body:
        config.set("display.tool_progress_grouping", _normalise_dashboard_choice(
            body["tool_progress_grouping"],
            {"accumulate", "separate"},
            "accumulate",
        ))
    elif "tool_progress_style" in body:
        config.set("display.tool_progress_grouping", _normalise_dashboard_choice(
            body["tool_progress_style"],
            {"accumulate", "separate"},
            "accumulate",
        ))
    if "memory_notifications" in body:
        config.set("display.memory_notifications", _normalise_dashboard_choice(
            body["memory_notifications"],
            {"off", "on", "verbose"},
            "on",
        ))
    if "platforms" in body:
        from .display_config import normalize_platform_display_overrides

        config.set("display.platforms", normalize_platform_display_overrides(body.get("platforms")))
    return _dashboard_preferences(config)


def _voice_tool_context(config: Config):
    from .tools.base import ToolContext

    return ToolContext(cwd=Path.cwd(), config=config)


def _session_stats() -> dict:
    from .session import SessionStore

    store = SessionStore()
    rows = store.list(10000)
    empty_sessions = store.prune_empty(dry_run=True)
    total_messages = 0
    role_counts: dict[str, int] = {}
    roots = 0
    children = 0
    for row in rows:
        sess = store.load(row["id"])
        if not sess:
            continue
        if sess.parent_id:
            children += 1
        else:
            roots += 1
        for message in sess.messages:
            if message.role == "system":
                continue
            total_messages += 1
            role_counts[message.role] = role_counts.get(message.role, 0) + 1
    return {
        "session_count": len(rows),
        "root_sessions": roots,
        "child_sessions": children,
        "empty_sessions": len(empty_sessions),
        "message_count": total_messages,
        "role_counts": role_counts,
    }


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _prune_sessions(older_than_days: int) -> dict:
    from .session import SessionStore

    older_than_days = max(0, int(older_than_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    store = SessionStore()
    removed: list[str] = []
    for row in store.list(10000):
        updated = _parse_iso(row.get("updated_at", ""))
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated < cutoff and store.delete(row["id"]):
            removed.append(row["id"])
    return {"ok": True, "removed": removed, "count": len(removed), "cutoff": cutoff.isoformat(timespec="seconds")}


def _empty_sessions(older_than_days: float = 0.0, *, dry_run: bool = True) -> dict:
    from .session import SessionStore

    store = SessionStore()
    removed = store.prune_empty(older_than_days=max(0.0, float(older_than_days)), dry_run=dry_run)
    return {"ok": True, "ids": removed, "count": len(removed), "dry_run": dry_run}


def _empty_session_count(older_than_days: float = 0.0) -> dict:
    result = _empty_sessions(older_than_days, dry_run=True)
    return {**result, "empty_sessions": result["count"]}


def _delete_sessions(ids: Any) -> dict:
    from .session import SessionStore

    if not isinstance(ids, list):
        return {"ok": False, "error": "ids must be a list", "removed": [], "count": 0}
    store = SessionStore()
    removed: list[str] = []
    missing: list[str] = []
    for raw in ids:
        sid = str(raw or "").strip()
        if not sid:
            continue
        if store.delete(sid):
            removed.append(sid)
        else:
            missing.append(sid)
    return {"ok": True, "removed": removed, "missing": missing, "count": len(removed)}


def _cron_job_detail(job_id: str) -> dict:
    for row in dash._dashboard_cron_jobs():
        if row["id"] == job_id or row["id"].startswith(job_id):
            return {"found": True, "job": row}
    return {"found": False, "id": job_id, "error": "cron job not found"}


def _cron_job_invalid_id_response(job_id: str, request: Request | None = None) -> JSONResponse | None:
    from .cron import _SAFE_JOB_ID_RE

    text = str(job_id or "").strip()
    valid = (
        bool(text)
        and text not in {".", ".."}
        and "/" not in text
        and "\\" not in text
        and "\x00" not in text
        and _SAFE_JOB_ID_RE.fullmatch(text) is not None
    )
    if valid:
        return None
    method = ""
    path = ""
    forwarded_for = ""
    user_agent = ""
    if request is not None:
        method = request.method
        path = request.url.path
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        user_agent = request.headers.get("User-Agent", "")
    logger.warning(
        "Cron jobs API rejected invalid job_id %r method=%s path=%s forwarded_for=%s user_agent=%s",
        text,
        method,
        path,
        forwarded_for,
        user_agent,
    )
    return JSONResponse(
        {"ok": False, "error": "Invalid job ID", "code": "invalid_job_id", "id": text},
        status_code=400,
    )


def _cron_context_refs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = raw.split(",") if "," in raw else [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    refs: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in refs:
            refs.append(text)
    return refs


def _validate_cron_context_refs(store: Any, refs: list[str]) -> str:
    for ref in refs:
        if store.resolve(ref) is None:
            return f"context_from job not found: {ref}"
    return ""


def _cron_jobs_response() -> JSONResponse:
    jobs = dash._dashboard_cron_jobs()
    return JSONResponse({"jobs": jobs, "data": jobs})


def _cron_job_create_response(config: Config, body: dict[str, Any]) -> JSONResponse:
    if not body.get("schedule") or not body.get("prompt"):
        return JSONResponse({"ok": False, "error": "schedule and prompt are required"}, status_code=400)
    from .cron import CronStore, _scan_cron_prompt

    store = CronStore()
    skills = body.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    context_from = _cron_context_refs(body.get("context_from"))
    context_error = _validate_cron_context_refs(store, context_from)
    if context_error:
        return JSONResponse({"ok": False, "error": context_error}, status_code=400)
    prompt_error = _scan_cron_prompt(str(body.get("prompt") or ""))
    if prompt_error:
        return JSONResponse({"ok": False, "error": prompt_error}, status_code=400)
    try:
        job = store.add(
            str(body["schedule"]),
            str(body["prompt"]),
            name=str(body.get("name") or ""),
            channel=str(body.get("channel") or ""),
            script=str(body.get("script") or ""),
            skills=list(skills),
            context_from=context_from,
            deliver=str(body.get("deliver") or ""),
            no_agent=_coerce_dashboard_bool(body.get("no_agent"), False),
            model=str(body.get("model") or ""),
            enabled_toolsets=_cron_context_refs(body.get("enabled_toolsets") or body.get("toolsets")),
            workdir=str(body.get("workdir") or ""),
            max_runs=int(body.get("max_runs") or 0),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "id": job.id, "job": _cron_job_detail(job.id)["job"]})


def _cron_job_patch_response(job_id: str, body: dict[str, Any], request: Request | None = None) -> JSONResponse:
    from .cron import CronStore, _scan_cron_prompt

    invalid = _cron_job_invalid_id_response(job_id, request)
    if invalid is not None:
        return invalid
    store = CronStore()
    updates = {key: body[key] for key in (
        "schedule", "prompt", "name", "channel", "enabled", "script", "skills", "context_from", "deliver",
        "no_agent", "max_runs", "model", "enabled_toolsets", "workdir",
    ) if key in body}
    if "toolsets" in body and "enabled_toolsets" not in updates:
        updates["enabled_toolsets"] = body["toolsets"]
    if "context_from" in updates:
        updates["context_from"] = _cron_context_refs(updates["context_from"])
        context_error = _validate_cron_context_refs(store, updates["context_from"])
        if context_error:
            return JSONResponse({"ok": False, "error": context_error}, status_code=400)
    if "enabled_toolsets" in updates:
        updates["enabled_toolsets"] = _cron_context_refs(updates["enabled_toolsets"])
    if "prompt" in updates:
        prompt_error = _scan_cron_prompt(str(updates.get("prompt") or ""))
        if prompt_error:
            return JSONResponse({"ok": False, "error": prompt_error}, status_code=400)
    try:
        job = store.update(job_id, **updates)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "id": job_id}, status_code=400)
    if job is None:
        return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
    return JSONResponse({"ok": True, "id": job.id, "job": _cron_job_detail(job.id)["job"]})


def _cron_job_put_response(job_id: str, body: dict[str, Any], request: Request | None = None) -> JSONResponse:
    updates = body.get("updates", body) if isinstance(body, dict) else {}
    if not isinstance(updates, dict):
        return JSONResponse({"ok": False, "error": "updates must be an object"}, status_code=400)
    return _cron_job_patch_response(job_id, updates, request)


def _cron_job_delete_response(job_id: str, request: Request | None = None) -> JSONResponse:
    from .cron import CronStore

    invalid = _cron_job_invalid_id_response(job_id, request)
    if invalid is not None:
        return invalid
    ok = CronStore().remove(job_id)
    return JSONResponse({"ok": ok, "id": job_id}, status_code=200 if ok else 404)


def _cron_job_enabled_response(job_id: str, enabled: bool, request: Request | None = None) -> JSONResponse:
    from .cron import CronStore

    invalid = _cron_job_invalid_id_response(job_id, request)
    if invalid is not None:
        return invalid
    ok = CronStore().set_enabled(job_id, enabled)
    if not ok:
        return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
    detail = _cron_job_detail(job_id)
    return JSONResponse({"ok": True, "id": job_id, "paused": not enabled, "job": detail["job"]})


def _cron_job_run_response(config: Config, job_id: str, request: Request | None = None) -> JSONResponse:
    from .cron import CronStore, build_delivery_sink, run_job

    invalid = _cron_job_invalid_id_response(job_id, request)
    if invalid is not None:
        return invalid
    store = CronStore()
    if store.get(job_id) is None:
        return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
    sink = build_delivery_sink(config, verbose=False)
    return JSONResponse(run_job(config, job_id, sink=sink, store=store, verbose=False))


def _cron_job_runs_response(job_id: str, query: dict[str, list[str]], request: Request | None = None) -> JSONResponse:
    invalid = _cron_job_invalid_id_response(job_id, request)
    if invalid is not None:
        return invalid
    try:
        limit = max(1, min(100, int(str(query.get("limit", ["20"])[0] or "20"))))
    except (TypeError, ValueError):
        limit = 20
    detail = _cron_job_detail(job_id)
    if not detail.get("found"):
        return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
    history = list((detail.get("job") or {}).get("history") or [])[:limit]
    return JSONResponse({"ok": True, "id": (detail.get("job") or {}).get("id", job_id), "limit": limit, "runs": history})


def _cron_delivery_targets(config: Config) -> dict[str, Any]:
    enabled_channels = set(config.get("gateway.channels", []) or [])
    catalog = _channel_catalog_map()
    targets: list[dict[str, Any]] = [
        {"id": "local", "label": "Local dashboard", "kind": "local", "enabled": True},
    ]
    for channel in sorted(enabled_channels):
        entry = catalog.get(str(channel))
        label = str((entry or {}).get("label") or channel)
        targets.append({
            "id": str(channel),
            "label": label,
            "kind": "gateway",
            "enabled": True,
            "syntax": f"{channel}:<recipient>",
        })
    return {"targets": targets, "channels": sorted(enabled_channels)}


def _service_result(result) -> dict:
    return {"ok": bool(getattr(result, "ok", False)), "message": str(getattr(result, "message", ""))}


def _gateway_service_control(action: str) -> dict:
    from .daemon import control_gateway_service

    return _service_result(control_gateway_service(action))


def _gateway_status(config: Config) -> dict:
    from .daemon import gateway_service_status
    from .gateway.queue import DeliveryQueue

    try:
        pending = DeliveryQueue().pending_count()
    except Exception:  # noqa: BLE001
        pending = 0
    channels = list(config.get("gateway.channels", []) or [])
    provider = str(config.get("model.provider") or "")
    model = str(config.get("model.default") or "")
    provider_error = ""
    capabilities: dict[str, Any] = {}
    try:
        context_length = int(config.get("model.context_length", 0) or 0)
    except (TypeError, ValueError):
        context_length = 0
    try:
        from .providers.registry import provider_report

        report = provider_report(config)
        active_provider = report.get("active") if isinstance(report.get("active"), dict) else {}
        provider = str(active_provider.get("name") or active_provider.get("provider") or provider)
        model = str(active_provider.get("model") or model)
        provider_error = str(active_provider.get("error") or "")
        capabilities = dash._jsonable(active_provider.get("capabilities", {}))
        try:
            context_length = int(active_provider.get("context_length") or context_length or 0)
        except (TypeError, ValueError):
            context_length = 0
    except Exception as exc:  # noqa: BLE001
        provider_error = f"{type(exc).__name__}: {exc}"
    return {
        "channels": channels,
        "configured": bool(channels),
        "provider": provider,
        "model": model,
        "context_length": context_length,
        "provider_error": provider_error,
        "capabilities": capabilities,
        "reasoning_effort": config.get("agent.reasoning_effort"),
        "service_tier": config.get("agent.service_tier"),
        "busy_mode": config.get("gateway.busy_mode", "queue"),
        "session_mode": config.get("gateway.session_mode", "per_channel_peer"),
        "require_mention": bool(config.get("gateway.require_mention", False)),
        "mention_triggers": list(config.get("gateway.mention_triggers", []) or []),
        "admins": list(config.get("gateway.admins", []) or []),
        "queue_pending": pending,
        "service": gateway_service_status(),
    }


def _provider_probe(config: Config, body: dict[str, Any]) -> dict:
    from .doctor import probe_provider

    probe_config = Config(copy.deepcopy(config.data))
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if provider:
        probe_config.data.setdefault("model", {})["provider"] = provider
    if model:
        probe_config.data.setdefault("model", {})["default"] = model
    if base_url:
        probe_config.data.setdefault("model", {})["base_url"] = base_url
    ok, detail = probe_provider(probe_config)
    return {
        "ok": bool(ok),
        "provider": probe_config.get("model.provider"),
        "model": probe_config.get("model.default"),
        "detail": detail,
    }


def _set_main_model_payload(config: Config, body: dict[str, Any]) -> dict[str, Any]:
    from .providers import registry

    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    base_url_present = "base_url" in body
    base_url = str(body.get("base_url") or "").strip()
    target_provider = provider or config.get("model.provider")
    target_model = model or config.get("model.default")

    validation_config = Config(copy.deepcopy(config.data))
    validation_model = validation_config.data.setdefault("model", {})
    if provider:
        validation_model["provider"] = provider
    if model:
        validation_model["default"] = model
    if base_url_present:
        validation_model["base_url"] = base_url

    validation = registry.validate_model_choice(target_provider, target_model, validation_config)
    if not validation.get("ok", True):
        return {
            "ok": False,
            "error": registry.model_validation_message(validation),
            "validation": validation,
        }

    if provider:
        config.set("model.provider", provider)
    if model:
        config.set("model.default", model)
    if base_url_present:
        config.set("model.base_url", base_url)

    validation = registry.validate_model_choice(
        config.get("model.provider"),
        config.get("model.default"),
        config,
    )
    return {
        "ok": True,
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "base_url": config.get("model.base_url", "") or "",
        "warning": registry.model_validation_message(validation),
        "validation": validation,
    }


def _model_info_payload(config: Config) -> dict[str, Any]:
    models = dash._dashboard_models(config)
    active = models.get("active") or {}
    validation = active.get("model_validation") or {}
    provider = str(models.get("provider") or config.get("model.provider") or "")
    model = str(models.get("model") or config.get("model.default") or "")
    context_length = active.get("effective_context_length") or active.get("context_length", 0)
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "active": active,
        "validation": validation,
        "warning": active.get("warning", ""),
        "context_length": context_length,
        "effective_context_length": context_length,
        "capabilities": active.get("capabilities", {}),
        "capability_summary": active.get("capability_summary", ""),
        "providers": models.get("providers", []),
    }


def _model_options_payload(config: Config) -> dict[str, Any]:
    models = dash._dashboard_models(config)
    providers: list[dict[str, Any]] = []
    presets = models.get("presets", {}) or {}
    catalog_by_name = {
        str(row.get("name") or ""): row
        for row in models.get("provider_catalog", []) or []
        if row.get("name")
    }
    for name in models.get("providers", []) or []:
        name = str(name)
        catalog = catalog_by_name.get(name, {})
        provider_models = list(presets.get(name) or [])
        providers.append({
            "slug": name,
            "name": catalog.get("display_name") or name,
            "models": provider_models,
            "total_models": len(provider_models),
            "is_current": name == models.get("provider"),
            "authenticated": bool((catalog.get("auth") or {}).get("available", False)),
            "auth_type": (catalog.get("auth_methods") or [""])[0],
            "key_env": (catalog.get("env_vars") or [""])[0],
            "warning": catalog.get("warning", ""),
            "is_user_defined": catalog.get("origin") == "custom",
        })
    return {
        "ok": True,
        "provider": models.get("provider"),
        "model": models.get("model"),
        "providers": providers,
        "provider_names": models.get("providers", []),
        "presets": models.get("presets", {}),
        "preset_rows": models.get("preset_rows", {}),
        "model_inventory": models.get("model_inventory", []),
        "provider_catalog": models.get("provider_catalog", []),
    }


def _recommended_default_payload(config: Config, query: dict[str, list[str]] | None = None) -> dict[str, Any]:
    models = dash._dashboard_models(config)
    query = query or {}
    provider = str(query.get("provider", [""])[0] or config.get("model.provider") or models.get("provider") or "")
    current = str(config.get("model.default") or models.get("model") or "")
    presets = list((models.get("presets") or {}).get(provider) or [])
    recommended = presets[0] if presets else current
    return {
        "provider": provider,
        "model": recommended,
        "free_tier": None,
        "ok": True,
        "current": current,
        "recommended": {"provider": provider, "model": recommended},
    }


def _auxiliary_model_payload(config: Config) -> dict[str, Any]:
    raw = config.get("auxiliary", {}) or {}
    slots = {key: value for key, value in raw.items() if isinstance(value, dict)} if isinstance(raw, dict) else {}
    tasks = [
        {
            "task": key,
            "provider": str((value or {}).get("provider") or "auto"),
            "model": str((value or {}).get("model") or ""),
            "base_url": str((value or {}).get("base_url") or ""),
        }
        for key, value in sorted(slots.items())
    ]
    return {
        "ok": True,
        "provider": config.get("auxiliary.provider", "") or "",
        "model": config.get("auxiliary.model", "") or "",
        "slots": copy.deepcopy(slots),
        "tasks": tasks,
        "main": {
            "provider": config.get("model.provider") or "",
            "model": config.get("model.default") or "",
        },
    }


def _model_set_payload(config: Config, body: dict[str, Any]) -> dict[str, Any]:
    scope = str(body.get("scope") or "main").strip().lower()
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    if scope == "main":
        result = _api_post(
            "/api/models",
            {
                "provider": provider,
                "model": model,
                **({"base_url": body.get("base_url")} if "base_url" in body else {}),
            },
            config,
            chat_runner=None,
        )
        info = _model_info_payload(config)
        stale_aux = []
        aux = config.get("auxiliary", {}) or {}
        if isinstance(aux, dict):
            for task, slot in aux.items():
                if not isinstance(slot, dict):
                    continue
                slot_provider = str(slot.get("provider") or "").strip()
                slot_model = str(slot.get("model") or "").strip()
                if slot_provider and slot_provider not in {"auto", provider}:
                    stale_aux.append({"task": task, "provider": slot_provider, "model": slot_model})
        return {**result, "scope": "main", "info": info, "stale_aux": stale_aux}
    if scope == "auxiliary":
        task = str(body.get("task") or "").strip().lower()
        aux = config.data.setdefault("auxiliary", {})
        slots = [key for key, value in aux.items() if isinstance(value, dict)]
        if task == "__reset__":
            for slot in slots:
                aux[slot] = {"provider": "auto", "model": "", "base_url": ""}
            config.save()
            return {"ok": True, "scope": "auxiliary", "reset": True, "tasks": slots}
        targets = slots if not task else [task]
        if not targets:
            return {"ok": False, "error": "no auxiliary task slots configured", "scope": "auxiliary"}
        for slot in targets:
            current = dict(aux.get(slot) or {})
            current["provider"] = provider or "auto"
            current["model"] = model
            if "base_url" in body:
                current["base_url"] = str(body.get("base_url") or "").strip()
            aux[slot] = current
        config.save()
        return {"ok": True, "scope": "auxiliary", "provider": provider or "auto", "model": model, "tasks": targets}
    return {"ok": False, "error": "scope must be 'main' or 'auxiliary'", "scope": scope}


def _gateway_probe(body: dict[str, Any]) -> dict:
    from .doctor import CHANNEL_PROBES

    channel = str(body.get("channel") or "").strip().lower()
    if not channel:
        return {"ok": False, "error": "channel is required"}
    probe = CHANNEL_PROBES.get(channel)
    if probe is None:
        return {"ok": False, "channel": channel, "detail": "no live probe for this channel yet"}
    try:
        ok, detail = probe()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    return {"ok": bool(ok), "channel": channel, "detail": detail}


def _fs_git_root(query: dict[str, list[str]]) -> dict:
    import subprocess

    raw = (query.get("path", [""])[0] or "").strip()
    base = Path(raw).expanduser() if raw else Path.cwd()
    try:
        base = base.resolve()
        cwd = base if base.is_dir() else base.parent
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "path": str(base), "root": "", "error": str(exc)}
    if result.returncode != 0:
        return {"ok": False, "path": str(cwd), "root": "", "error": (result.stderr or "not a git worktree").strip()}
    return {"ok": True, "path": str(cwd), "root": result.stdout.strip()}


def _fs_read_data_url(query: dict[str, list[str]]) -> dict:
    import mimetypes

    raw = (query.get("path", [""])[0] or "").strip()
    if not raw:
        return {"ok": False, "error": "no path"}
    try:
        path = Path(raw).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "bad path"}
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "not a file"}
    if dash._is_sensitive_path(path):
        return {"ok": False, "path": str(path), "error": "blocked: refusing to read a credential/key path"}
    try:
        size = path.stat().st_size
        if size > 2 * 1024 * 1024:
            return {"ok": False, "path": str(path), "size": size, "error": "file too large to encode (>2MB)"}
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{data}"
        return {
            "ok": True,
            "path": str(path),
            "size": size,
            "mime": mime,
            "data_url": data_url,
            "dataUrl": data_url,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "path": str(path), "error": str(exc)}


def _fs_media(query: dict[str, list[str]]) -> dict:
    result = _fs_read_data_url(query)
    if not result.get("ok"):
        return result
    mime = str(result.get("mime") or "")
    if not mime.startswith("image/"):
        return {"ok": False, "path": result.get("path", ""), "mime": mime, "error": "unsupported media type"}
    return result


def _fs_default_cwd() -> dict:
    import subprocess

    cwd = str(Path.cwd().resolve())
    branch = ""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except Exception:  # noqa: BLE001
        branch = ""
    return {"ok": True, "path": cwd, "cwd": cwd, "branch": branch}


def _delete_managed_file(body: dict[str, Any]) -> dict:
    import shutil

    raw = str(body.get("path") or "").strip()
    if not raw:
        return {"ok": False, "error": "missing path"}
    try:
        target = Path(raw).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "bad path"}
    protected = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if target in protected:
        return {"ok": False, "error": "refusing to delete a protected root path", "path": str(target)}
    if dash._is_sensitive_path(target):
        return {"ok": False, "error": "blocked: refusing to delete a credential/key path", "path": str(target)}
    if not target.exists():
        return {"ok": False, "error": "path does not exist", "path": str(target)}
    try:
        if target.is_dir():
            if not bool(body.get("recursive", False)):
                return {"ok": False, "error": "directory delete requires recursive=true", "path": str(target)}
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": str(target)}
    return {"ok": True, "path": str(target)}


def _ws_query(params: Any) -> dict[str, list[str]]:
    if not isinstance(params, dict):
        return {}
    raw = params.get("query") or params.get("params") or {}
    if isinstance(raw, str):
        return {key: [str(item) for item in value] for key, value in parse_qs(raw.lstrip("?")).items()}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            out[str(key)] = [str(item) for item in value]
        elif value is None:
            out[str(key)] = [""]
        else:
            out[str(key)] = [str(value)]
    return out


def _dashboard_ws_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "transport": {
            "events": True,
            "keepalive": True,
            "jsonrpc": "2.0",
            "legacy_ping": True,
        },
        "methods": [
            "ping",
            "dashboard.capabilities",
            "dashboard.status",
            "dashboard.get",
            "api.get",
        ],
        "routes": {
            "events": "/api/ws",
            "sse": "/api/events",
            "publish": "/api/pub",
            "pty": "/api/pty",
            "ws_ticket": "/api/auth/ws-ticket",
        },
    }


_EVENT_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "password",
    "secret",
    "token",
}


def _redact_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in _EVENT_SECRET_KEYS:
                out[str(key)] = "[redacted]"
            else:
                out[str(key)] = _redact_event_value(child)
        return out
    if isinstance(value, list):
        return [_redact_event_value(item) for item in value]
    return value


def _dashboard_event_payload(body: Any) -> dict[str, Any]:
    raw = body if isinstance(body, dict) else {"value": body}
    event = _redact_event_value(dash._jsonable(copy.deepcopy(raw)))
    event_type = str(event.get("type") or event.get("event") or "dashboard_event").strip() or "dashboard_event"
    event["type"] = event_type
    event.setdefault("source", "dashboard")
    event.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    return event


def _publish_dashboard_event(body: Any) -> dict[str, Any]:
    from .eventbus import BUS

    event = _dashboard_event_payload(body)
    BUS.publish(event)
    return {"ok": True, "event": event, "subscribers": BUS.subscriber_count()}


def _dashboard_events_response(config: Config, request: Request) -> StreamingResponse:
    _require_request(request, config)

    def stream():
        from .eventbus import BUS

        sub = BUS.subscribe()
        try:
            while True:
                try:
                    ev = sub.get(timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n".encode()
                except queue.Empty:
                    yield b": keepalive\n\n"
        finally:
            BUS.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _cancel_dashboard_stream_agent(agent: Any) -> None:
    if agent is None:
        return
    try:
        cancel = getattr(agent, "cancel", None)
        if callable(cancel):
            cancel()
            return
        cancel_event = getattr(agent, "cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
    except Exception:  # noqa: BLE001
        pass


def _dashboard_chat_streaming_response(body: dict, chat_runner, request: Request) -> StreamingResponse:
    events_q: queue.Queue[dict | object] = queue.Queue()
    sentinel = object()
    cancelled = threading.Event()
    active: dict[str, Any] = {}
    active_lock = threading.Lock()

    def start_cancel_watchdog() -> None:
        with active_lock:
            if active.get("cancel_watchdog_started"):
                return
            active["cancel_watchdog_started"] = True

        def reapply_cancel() -> None:
            deadline = time.monotonic() + 1.5
            while cancelled.is_set() and time.monotonic() < deadline:
                _cancel_dashboard_stream_agent(active.get("agent"))
                worker_thread = active.get("worker")
                if worker_thread is not None and not worker_thread.is_alive():
                    break
                time.sleep(0.025)

        threading.Thread(target=reapply_cancel, daemon=True, name="aegis-dashboard-stream-cancel").start()

    def on_agent(agent: Any) -> None:
        active["agent"] = agent
        if cancelled.is_set():
            _cancel_dashboard_stream_agent(agent)
            start_cancel_watchdog()

    def cancel_active_agent() -> None:
        if cancelled.is_set():
            return
        cancelled.set()
        _cancel_dashboard_stream_agent(active.get("agent"))
        start_cancel_watchdog()

    def worker() -> None:
        try:
            dash._dashboard_chat_stream(
                body,
                chat_runner,
                events_q.put,
                on_agent=on_agent,
                cancel_event=cancelled,
            )
        finally:
            events_q.put(sentinel)

    thread = threading.Thread(target=worker, daemon=True)
    active["worker"] = thread
    thread.start()

    async def stream():
        saw_sentinel = False
        try:
            while True:
                if await request.is_disconnected():
                    cancel_active_agent()
                    break
                try:
                    item = events_q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                if item is sentinel:
                    saw_sentinel = True
                    break
                yield f"data: {json.dumps(item)}\n\n".encode()
        except asyncio.CancelledError:
            cancel_active_agent()
            raise
        finally:
            if not saw_sentinel:
                cancel_active_agent()

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _dashboard_chat_json_response(body: dict, chat_runner, request: Request) -> JSONResponse:
    result: dict[str, Any] = {}
    done = threading.Event()
    cancelled = threading.Event()
    active: dict[str, Any] = {}
    active_lock = threading.Lock()

    def start_cancel_watchdog() -> None:
        with active_lock:
            if active.get("cancel_watchdog_started"):
                return
            active["cancel_watchdog_started"] = True

        def reapply_cancel() -> None:
            deadline = time.monotonic() + 1.5
            while cancelled.is_set() and time.monotonic() < deadline:
                _cancel_dashboard_stream_agent(active.get("agent"))
                worker_thread = active.get("worker")
                if worker_thread is not None and not worker_thread.is_alive():
                    break
                time.sleep(0.025)

        threading.Thread(target=reapply_cancel, daemon=True, name="aegis-dashboard-chat-cancel").start()

    def on_agent(agent: Any) -> None:
        active["agent"] = agent
        if cancelled.is_set():
            _cancel_dashboard_stream_agent(agent)
            start_cancel_watchdog()

    def cancel_active_agent() -> None:
        if cancelled.is_set():
            return
        cancelled.set()
        _cancel_dashboard_stream_agent(active.get("agent"))
        start_cancel_watchdog()

    def worker() -> None:
        try:
            payload = dash._dashboard_chat_stream(
                body,
                chat_runner,
                lambda _item: None,
                on_agent=on_agent,
                cancel_event=cancelled,
                meta_route="/api/chat",
            )
            result.update(payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001
            result.update({
                "reply": f"error: {exc}",
                "session_id": body.get("session_id") or "",
                "trace_id": "",
                "turn_id": "",
                "run_id": "",
                "cwd": dash._dashboard_chat_cwd(body),
                "events": [],
            })
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    active["worker"] = thread
    thread.start()
    try:
        while not done.is_set():
            if await request.is_disconnected():
                cancel_active_agent()
                return JSONResponse({"error": "client disconnected", "cancelled": True}, status_code=499)
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        cancel_active_agent()
        raise
    payload = dict(result)
    payload.pop("type", None)
    return JSONResponse(payload)


def _dashboard_ws_rpc_response(text: str | None, config: Config) -> dict[str, Any] | None:
    if text is None:
        return None
    if text == "ping":
        return {"type": "pong"}
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict):
        return None

    method = str(message.get("method") or message.get("type") or "").strip()
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    is_jsonrpc = message.get("jsonrpc") == "2.0" or "method" in message

    if method == "ping":
        result: dict[str, Any] = {"ok": True, "type": "pong"}
    elif method in {"dashboard.capabilities", "capabilities"}:
        result = _dashboard_ws_capabilities()
    elif method in {"dashboard.status", "status"}:
        result = dash._dashboard_status(config)
    elif method in {"dashboard.get", "api.get"}:
        path = str(params.get("path") or "").strip()
        if not path:
            return _ws_rpc_error(request_id, -32602, "params.path is required", is_jsonrpc=is_jsonrpc)
        split = urlsplit(path)
        route = split.path
        if not route.startswith("/api/"):
            return _ws_rpc_error(request_id, -32602, "dashboard.get only supports /api/* paths", is_jsonrpc=is_jsonrpc)
        query = _ws_query(params)
        for key, values in parse_qs(split.query).items():
            query.setdefault(key, [str(item) for item in values])
        result = _api_get(route, query, config)
    elif method:
        return _ws_rpc_error(request_id, -32601, f"unknown dashboard websocket method: {method}", is_jsonrpc=is_jsonrpc)
    else:
        return None

    if is_jsonrpc:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {"type": "rpc.result", "id": request_id, "method": method, "result": result}


def _ws_rpc_error(request_id: Any, code: int, message: str, *, is_jsonrpc: bool) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if is_jsonrpc:
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    return {"type": "rpc.error", "id": request_id, "error": error}


def _api_get(path: str, query: dict[str, list[str]], config: Config) -> dict:
    if path == "/api/status":
        return dash._dashboard_status(config)
    if path in {"/api/session-checks", "/api/cross-session/checks", "/api/harness/cross-session"}:
        from .session_checks import cross_session_integrity_report

        try:
            session_limit = int((query.get("session_limit") or query.get("sessions") or ["500"])[0] or 500)
        except (TypeError, ValueError):
            session_limit = 500
        try:
            run_limit = int((query.get("run_limit") or query.get("runs") or ["500"])[0] or 500)
        except (TypeError, ValueError):
            run_limit = 500
        try:
            stale_seconds = float((query.get("stale_running_seconds") or ["21600"])[0] or 21600)
        except (TypeError, ValueError):
            stale_seconds = 21600.0
        try:
            stale_resume_seconds = float((query.get("stale_resume_pending_seconds") or ["86400"])[0] or 86400)
        except (TypeError, ValueError):
            stale_resume_seconds = 86400.0
        return cross_session_integrity_report(
            session_limit=session_limit,
            run_limit=run_limit,
            stale_running_seconds=stale_seconds,
            stale_resume_pending_seconds=stale_resume_seconds,
        )
    if path == "/api/auth/providers":
        return _auth_providers_payload(config)
    if path == "/api/cockpit":
        return dash._dashboard_cockpit(config)
    if path == "/api/kanban":
        return dash._dashboard_kanban(include_archived=bool(query.get("archived")))
    if path == "/api/cron":
        return dash._dashboard_cron_jobs()
    if path == "/api/config":
        return dash._redacted_config(config)
    if path == "/api/models":
        return dash._dashboard_models(config)
    if path == "/api/providers":
        return dash._dashboard_models(config)
    if path == "/api/model/info":
        return _model_info_payload(config)
    if path == "/api/model/options":
        return _model_options_payload(config)
    if path == "/api/model/recommended-default":
        return _recommended_default_payload(config, query)
    if path == "/api/model/auxiliary":
        return _auxiliary_model_payload(config)
    if path in {"/api/credentials/pools", "/api/credential-pools"}:
        return _credential_pools_payload(config)
    if path.startswith("/api/credentials/pools/"):
        return _credential_pools_payload(config, path.removeprefix("/api/credentials/pools/"))
    if path == "/api/credential-pools/status":
        return _credential_pools_payload(config)
    if path == "/api/provider-auth":
        return _provider_auth_payload(config)
    if path in {"/api/update/check", "/api/portal/update/check", "/api/check/update"}:
        return dash._update_check()
    if path in {"/api/portal", "/api/portal/status"}:
        return _portal_status_payload(config)
    if path in {"/api/actions/status", "/api/admin/actions/status"}:
        return _dashboard_action_catalog()
    if path == "/api/admin/status":
        return _admin_status_payload(config)
    if path in {
        "/api/hooks",
        "/api/hooks/contract",
        "/api/observability",
        "/api/observability/contract",
        "/api/observability/events",
        "/api/observability/hooks",
    }:
        return _observability_contract_payload(config)
    if path in {"/api/analytics", "/api/analytics/usage"}:
        from . import ratelimit
        from .usage_log import cost_report, daily_series

        days = int((query.get("days", ["30"])[0]) or 30)
        rep = cost_report(days, config)
        rep["series"] = daily_series(days, config)
        rep["balance"] = ratelimit.balance()
        return rep
    if path == "/api/keys":
        return dash._env_keys()
    if path == "/api/pairing":
        from .gateway.pairing import PairingStore

        return PairingStore().list()
    if path in {"/api/platforms", "/api/platforms/registry", "/api/messaging/platforms/registry"}:
        return _platform_registry_payload(config)
    if path.startswith("/api/platforms/"):
        return _platform_registry_payload(config, path.removeprefix("/api/platforms/"))
    if path == "/api/mcp":
        servers = config.get("mcp.servers", {}) or {}
        return [{"name": n, "command": (s or {}).get("command", ""),
                 "args": (s or {}).get("args", [])} for n, s in servers.items()]
    if path == "/api/mcp/catalog":
        return dash._dashboard_mcp_catalog(
            config,
            live=(query.get("live", ["0"])[0] in {"1", "true", "yes"}),
        )
    if path == "/api/mcp/servers":
        return dash._dashboard_mcp_catalog(
            config,
            live=(query.get("live", ["0"])[0] in {"1", "true", "yes"}),
        )
    if path == "/api/webhooks":
        from .webhook import WebhookStore

        return [{"name": w.name, "prompt": w.prompt} for w in WebhookStore().list()]
    if path in {"/api/webhooks/status", "/api/automation/webhooks/status"}:
        return _webhooks_status_payload(config)
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=True)
    if path == "/api/plugins":
        return _plugins_payload(config)
    if path == "/api/profiles":
        return _profiles_payload(config)
    if path == "/api/system":
        return dash._system_info()
    if path == "/api/system/stats":
        return dash._system_stats()
    if path == "/api/ops":
        return dash._ops_status(config)
    if path == "/api/traces":
        return dash._dashboard_traces(query, config)
    if path == "/api/trace":
        return dash._dashboard_trace_detail(query, config)
    if path == "/api/runs":
        return dash._dashboard_runs(query)
    if path == "/api/run":
        return dash._dashboard_run_detail(query, config)
    if path == "/api/agents":
        return dash._dashboard_agents(config)
    if path == "/api/agent":
        return dash._dashboard_agent_detail(query, config)
    if path == "/api/projects":
        return dash._dashboard_projects()
    if path == "/api/worktrees":
        return dash._dashboard_worktrees()
    if path == "/api/files":
        return dash._dashboard_files(query)
    if path == "/api/files/read":
        return dash._dashboard_file_read(query)
    if path == "/api/fs/list":
        return dash._dashboard_files(query)
    if path == "/api/fs/read-text":
        result = dash._dashboard_file_read(query)
        return {"ok": not bool(result.get("error")), **result}
    if path == "/api/fs/read-data-url":
        return _fs_read_data_url(query)
    if path == "/api/media":
        return _fs_media(query)
    if path == "/api/fs/git-root":
        return _fs_git_root(query)
    if path == "/api/fs/default-cwd":
        return _fs_default_cwd()
    if path == "/api/review":
        return dash._dashboard_review()
    if path == "/api/evals":
        return dash._dashboard_evals(config)
    if path == "/api/eval":
        return dash._dashboard_eval_detail(query, config)
    if path == "/api/logs":
        from . import config as cfg

        name = str(query.get("name", ["agent"])[0] or "agent")
        try:
            limit = max(1, min(1000, int(str(query.get("limit", ["200"])[0] or "200"))))
        except (TypeError, ValueError):
            limit = 200
        allowed = {
            "agent": "agent.log",
            "desktop": "desktop.log",
            "errors": "errors.log",
            "gateway": "gateway.log",
            "gui": "gui.log",
            "legacy": "aegis.log",
        }
        lp = cfg.logs_dir() / allowed.get(name, "agent.log")
        if not lp.exists() and name == "agent":
            lp = cfg.logs_dir() / "aegis.log"
        lines = lp.read_text(errors="replace").splitlines()[-limit:] if lp.exists() else []
        return {"path": str(lp), "lines": lines}
    if path == "/api/sessions":
        from .session import SessionStore

        return SessionStore().list(100)
    if path == "/api/sessions/empty/count":
        older_than_days = float(query.get("older_than_days", ["0"])[0] or 0)
        return _empty_session_count(older_than_days)
    if path == "/api/sessions/empty":
        older_than_days = float(query.get("older_than_days", ["0"])[0] or 0)
        return _empty_sessions(older_than_days, dry_run=True)
    if path == "/api/session":
        from .session import SessionStore

        sid = query.get("id", [""])[0]
        session = SessionStore().load(sid)
        detail = dash._dashboard_session_detail(sid, config) if sid else {"found": False}
        return {
            "messages": [{"role": m.role, "content": m.content}
                         for m in (session.messages if session else []) if m.content],
            "detail": detail,
            "runs": detail.get("runs", []),
            "traces": detail.get("traces", []),
            "links": detail.get("links", {}),
            "lineage": {
                "parent": detail.get("parent"),
                "children": detail.get("children", []),
            } if detail.get("found") else {"parent": None, "children": []},
        }
    if path == "/api/memory":
        from .memory import MemoryStore

        ms = MemoryStore()
        return {"memory": ms.raw("memory"), "user": ms.raw("user")}
    if path == "/api/skills":
        from .skills import SkillsLoader

        return [{"name": s.name, "description": s.description}
                for s in sorted(SkillsLoader(config).available(), key=lambda s: s.name)]
    if path == "/api/skills/manage":
        return _skills_payload(config)
    if path == "/api/tools":
        return dash._dashboard_tools(config)["tools"]
    if path == "/api/tools/toolsets":
        return dash._dashboard_toolsets(config)
    if path == "/api/skills/bundles":
        from .skill_bundles import list_bundles

        return {"bundles": list_bundles()}
    return {"error": "not found"}


_CHAT_FALLBACK = object()


def _api_post(
    path: str,
    body: dict,
    config: Config,
    chat_runner: Any,
    *,
    chat_fallback: bool = True,
) -> dict | object:
    if path in {
        "/api/session-checks",
        "/api/session-checks/repair",
        "/api/cross-session/checks",
        "/api/cross-session/checks/repair",
        "/api/harness/cross-session",
        "/api/harness/cross-session/repair",
    }:
        from .session_checks import cross_session_integrity_report, repair_cross_session_integrity

        def as_int(name: str, default: int) -> int:
            raw = body.get(name) if name in body else default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def as_float(name: str, default: float) -> float:
            raw = body.get(name) if name in body else default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        limits = {
            "session_limit": as_int("session_limit", as_int("sessions", 500)),
            "run_limit": as_int("run_limit", as_int("runs", 500)),
            "stale_running_seconds": as_float("stale_running_seconds", as_float("stale_seconds", 21600.0)),
            "stale_resume_pending_seconds": as_float(
                "stale_resume_pending_seconds",
                as_float("stale_resume_seconds", 86400.0),
            ),
        }
        wants_repair = (
            path.endswith("/repair")
            or str(body.get("action") or "").lower() == "repair"
            or _coerce_dashboard_bool(body.get("repair"), False)
        )
        if not wants_repair:
            return cross_session_integrity_report(**limits)
        reason = str(body.get("resume_reason") or body.get("reason") or "dashboard_session_check_repair")
        repair = repair_cross_session_integrity(
            session_limit=limits["session_limit"],
            run_limit=limits["run_limit"],
            stale_running_seconds=limits["stale_running_seconds"],
            stale_resume_pending_seconds=limits["stale_resume_pending_seconds"],
            resume_reason=reason,
        )
        report = cross_session_integrity_report(**limits)
        return {
            "object": "hermes.cross_session_integrity_repair_result",
            "ok": bool(repair.get("ok", False)) and str(report.get("status") or "") != "error",
            "repair": repair,
            "report": report,
        }
    if path == "/api/kanban":
        from .kanban import STATUSES, KanbanStore

        ks = KanbanStore()
        act = body.get("action")
        if act == "create":
            parents = body.get("parents", body.get("parent", []))
            if isinstance(parents, str):
                parents = [p.strip() for p in parents.split(",") if p.strip()]
            t = ks.create(
                (body.get("title") or "untitled").strip(), body.get("body", ""),
                priority=int(body.get("priority") or 0),
                assignee=str(body.get("assignee") or ""),
                tenant=str(body.get("tenant") or ""),
                parents=list(parents or []),
                workspace=str(body.get("workspace_kind") or body.get("workspace") or "scratch"),
                skills=str(body.get("skills") or ""),
            )
            status = str(body.get("status") or "").strip()
            if status in STATUSES and status != t.status:
                ks._set_status(t.id, status)
            return {"id": t.id, "status": status if status in STATUSES else t.status}
        if act == "move" and body.get("id") and body.get("status") in STATUSES:
            ks._set_status(body["id"], body["status"])
            return {"ok": True}
        if act == "archive" and body.get("id"):
            return {"ok": ks.archive(str(body["id"]))}
        if act == "decompose" and body.get("goal"):
            from .kanban_auto import decompose

            cards = decompose(body["goal"], config, store=ks)
            return {"ok": True, "created": len(cards)}
        if act == "run":
            from .kanban_auto import run_board

            threading.Thread(target=run_board, args=(config,), kwargs={"store": ks},
                             daemon=True).start()
            return {"ok": True, "started": True}
        return {"error": "bad kanban request"}
    if path == "/api/cron":
        from .cron import CronStore, _scan_cron_prompt, build_delivery_sink, run_job

        cs = CronStore()
        act = body.get("action")
        if act == "add" and body.get("schedule") and body.get("prompt"):
            prompt_error = _scan_cron_prompt(str(body.get("prompt") or ""))
            if prompt_error:
                return {"ok": False, "error": prompt_error}
            context_from = _cron_context_refs(body.get("context_from"))
            context_error = _validate_cron_context_refs(cs, context_from)
            if context_error:
                return {"ok": False, "error": context_error}
            skills = body.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            try:
                j = cs.add(
                    body["schedule"],
                    body["prompt"],
                    body.get("channel", ""),
                    script=str(body.get("script") or ""),
                    skills=list(skills),
                    deliver=str(body.get("deliver") or ""),
                    no_agent=_coerce_dashboard_bool(body.get("no_agent"), False),
                    context_from=context_from,
                    model=str(body.get("model") or ""),
                    enabled_toolsets=_cron_context_refs(body.get("enabled_toolsets")),
                    workdir=str(body.get("workdir") or ""),
                    max_runs=int(body.get("max_runs") or 0),
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"id": j.id}
        if act == "remove" and body.get("id"):
            return {"ok": cs.remove(body["id"])}
        if act == "toggle" and body.get("id"):
            return {"ok": cs.set_enabled(body["id"], _coerce_dashboard_bool(body.get("enabled"), True))}
        if act in {"run", "run_now"} and body.get("id"):
            sink = build_delivery_sink(config, verbose=False)
            return run_job(config, str(body["id"]), sink=sink, store=cs, verbose=False)
        return {"error": "bad cron request"}
    if path == "/api/config":
        key, val = body.get("key"), body.get("value")
        if key:
            config.set(key, val)
            return {"ok": True}
        return {"error": "missing key"}
    if path == "/api/config/backup":
        return dash._config_backup_now()
    if path == "/api/config/reset":
        return dash._config_reset_section(str(body.get("section") or ""), config)
    if path == "/api/models":
        return _set_main_model_payload(config, body)
    if path in {"/api/update/check", "/api/portal/update/check", "/api/check/update"}:
        return dash._update_check()
    if path == "/api/model/set":
        return _model_set_payload(config, body)
    if path == "/api/providers/test":
        return _provider_probe(config, body)
    if path == "/api/keys":
        payload, _status = _env_set_payload(body)
        return payload
    if path == "/api/pairing":
        from .gateway.pairing import PairingStore

        ps = PairingStore()
        act, plat = body.get("action"), body.get("platform", "")
        if act == "approve" and body.get("code"):
            return {"ok": ps.approve(plat, body["code"])}
        if act == "revoke" and body.get("user_id"):
            return {"ok": ps.revoke(plat, body["user_id"])}
        return {"error": "bad pairing request"}
    if path == "/api/gateway/probe":
        return _gateway_probe(body)
    if path == "/api/system":
        if body.get("action") == "backup":
            from .backup import create_backup

            return {"ok": True, "path": str(create_backup())}
        return {"error": "unknown system action"}
    if path == "/api/ops":
        return dash._ops_action(str(body.get("action") or ""), body, config)
    if path == "/api/tools":
        if body.get("toolset") is not None:
            return dash._dashboard_toolset_toggle(body, config)
        return dash._dashboard_tool_toggle(body, config)
    if path == "/api/skills/bundles":
        from .skill_bundles import save_bundle

        bundle = save_bundle(
            str(body.get("name") or ""),
            body.get("skills") or body.get("members") or [],
            description=str(body.get("description") or ""),
            instruction=str(body.get("instruction") or ""),
        )
        return {"ok": True, "bundle": bundle}
    if path == "/api/session":
        act = body.get("action")
        sid = (body.get("id") or body.get("session_id") or "").strip()
        if act == "branch" and sid:
            return dash._dashboard_branch_session(
                sid,
                title=str(body.get("title") or ""),
                reason=str(body.get("reason") or "dashboard"),
            )
        return {"error": "bad session request"}
    if path == "/api/pub":
        return _publish_dashboard_event(body)
    if path in {"/api/actions/run", "/api/admin/actions/run"}:
        action = str(body.get("action") or body.get("id") or body.get("name") or "")
        return dash._ops_action(action, body, config)
    if path in {"/api/hooks/test", "/api/observability/hooks/test"}:
        return _hook_test_payload(config, body)
    if path == "/api/sessions/bulk-delete":
        ids = body.get("ids") if isinstance(body, dict) else None
        if not ids and isinstance(body, dict):
            ids = body.get("session_ids")
        return _delete_sessions(ids)
    if path == "/api/eval":
        if body.get("action") in {"run", "run_suite"}:
            return dash._dashboard_run_eval(body, config)
        return {"error": "bad eval request"}
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=False)
    if path == "/api/profiles":
        name = str(body.get("name") or "").strip()
        if not name:
            config.set("agent.personality", "")
        else:
            config.set("agent.personality", _safe_resource_name(name, "profile"))
        return {"ok": True, "active": config.get("agent.personality"), "profiles": _profiles_payload(config)}
    if path == "/api/mcp":
        servers = dict(config.get("mcp.servers", {}) or {})
        act = body.get("action")
        if act == "install" and body.get("name"):
            try:
                from .mcp.client import install_from_catalog

                spec = install_from_catalog(config, str(body["name"]))
                target = spec.get("url") or " ".join([spec.get("command", ""), *(spec.get("args") or [])])
                return {"ok": True, "name": body["name"], "target": target.strip()}
            except KeyError:
                return {"ok": False, "error": "catalog entry not found"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        if act == "add" and body.get("name") and body.get("command"):
            servers[_safe_resource_name(str(body["name"]), "mcp server")] = _mcp_spec_from_body(body)
            _save_mcp_servers(config, servers)
            return {"ok": True}
        if act == "remove" and body.get("name") in servers:
            servers.pop(body["name"])
            _save_mcp_servers(config, servers)
            return {"ok": True}
        if act == "probe" and body.get("name"):
            from .mcp.client import probe_server

            return probe_server(config, _safe_resource_name(str(body["name"]), "mcp server"))
        if act == "tools" and body.get("name"):
            from .mcp.client import save_tool_checklist, tool_checklist

            name = _safe_resource_name(str(body["name"]), "mcp server")
            if "include" in body:
                include = body.get("include") or []
                if not isinstance(include, list):
                    return {"ok": False, "error": "include must be a list"}
                save_tool_checklist(config, name, [str(x) for x in include])
            return tool_checklist(config, name)
        return {"error": "bad mcp request"}
    if path == "/api/plugins":
        act = body.get("action")
        name = str(body.get("name") or "").strip()
        try:
            from . import plugins as plugin_runtime

            if act == "reload":
                plugin_runtime.clear_runtime_cache()
                return {"ok": True, **_plugins_payload(config)}
            if act == "validate":
                return _validate_plugin_source(str(body.get("source") or ""))
            if act == "install" and body.get("source"):
                installed = plugin_runtime.install_details(
                    str(body["source"]),
                    config,
                    force=_coerce_dashboard_bool(body.get("force"), False),
                    enable_now=_coerce_dashboard_bool(body.get("enable"), True),
                )
                return installed
            if act == "enable" and name:
                return {"ok": plugin_runtime.enable(name, config)}
            if act == "disable" and name:
                return {"ok": plugin_runtime.disable(name, config)}
            if act == "remove" and name:
                return {"ok": plugin_runtime.remove(name, config)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"error": "bad plugins request"}
    if path == "/api/webhooks":
        from .webhook import WebhookStore

        ws = WebhookStore()
        act = body.get("action")
        if act == "add" and body.get("name") and body.get("prompt"):
            ws.add(body["name"], body["prompt"])
            return {"ok": True}
        if act == "remove" and body.get("name"):
            return {"ok": ws.remove(body["name"])}
        return {"error": "bad webhook request"}
    if path == "/api/memory":
        from .memory import MemoryStore

        ms = MemoryStore()
        act = body.get("action")
        target = body.get("target", "memory")
        if target not in ("memory", "user"):
            return {"error": "target must be 'memory' or 'user'"}
        if act == "add" and body.get("content"):
            return {"result": ms.add(target, body["content"])}
        if act == "remove" and body.get("match"):
            return {"result": ms.remove(target, body["match"])}
        return {"error": "bad memory request"}
    if path in {"/api/files/mkdir", "/api/fs/mkdir"}:
        raw_path = str(body.get("path") or "").strip()
        name = str(body.get("name") or "").strip()
        if name:
            parent = Path(raw_path or Path.home()).expanduser().resolve()
            target = parent / Path(name).name
        elif raw_path:
            target = Path(raw_path).expanduser().resolve()
        else:
            return {"ok": False, "error": "missing path"}
        if dash._is_sensitive_path(target):
            return {"ok": False, "error": "blocked: refusing to create a credential/key path", "path": str(target)}
        try:
            target.mkdir(parents=bool(body.get("parents", False)), exist_ok=bool(body.get("exist_ok", False)))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(target)}
    if path in {"/api/files/delete", "/api/fs/delete"}:
        return _delete_managed_file(body)
    if not chat_fallback:
        return _CHAT_FALLBACK
    return dash._dashboard_chat_response(body, chat_runner)


def create_app(config: Config) -> FastAPI:
    from .session import SessionStore
    from .surface import SurfaceRunner

    if _remote_bind_requires_auth(config) and not _auth_configured(config):
        raise RuntimeError(
            "dashboard bound to a non-loopback host without auth; set "
            "AEGIS_DASHBOARD_TOKEN or AEGIS_DASHBOARD_BASIC_AUTH_USERNAME/PASSWORD"
        )
    app = FastAPI(title="AEGIS", version=__version__)
    chat_runner = SurfaceRunner(config, store=SessionStore(), include_mcp=True)
    _start_desktop_cron_ticker(config)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _html_response(config, request)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        return _login_page()

    @app.post("/auth/login")
    async def login_form(username: Annotated[str, Form()] = "",
                         password: Annotated[str, Form()] = "") -> Response:
        if not _basic_auth_configured():
            return _login_page("Username/password login is not configured.")
        expected_user, expected_password = _basic_auth_credentials()
        if not (hmac.compare_digest(username, expected_user)
                and hmac.compare_digest(password, expected_password)):
            return _login_page("Invalid username or password.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            _SESSION_COOKIE,
            _make_session_cookie(username, config),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/auth/logout")
    async def logout_form() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie("aegis_dashboard_token")
        return response

    @app.get("/assets/{name:path}")
    async def asset(name: str) -> Response:
        found = dash._asset(f"/assets/{name}")
        if found is None:
            raise HTTPException(status_code=404, detail="asset not found")
        data, ctype = found
        return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/favicon.ico")
    @app.get("/fonts/{name:path}")
    @app.get("/fonts-terminal/{name:path}")
    async def dist_file(request: Request, name: str = "") -> Response:  # noqa: ARG001
        found = dash._dist_file(request.url.path)
        if found is None:
            raise HTTPException(status_code=404, detail="asset not found")
        data, ctype = found
        return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        return _dashboard_events_response(config, request)

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        return _dashboard_events_response(config, request)

    @app.post("/api/pub")
    async def api_pub(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return JSONResponse({"ok": False, "error": "request body must be JSON"}, status_code=400)
        return JSONResponse(_publish_dashboard_event(body))

    @app.websocket("/api/ws")
    async def event_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        from .eventbus import BUS

        sub = BUS.subscribe()
        await ws.accept()
        loop = asyncio.get_running_loop()
        send_lock = asyncio.Lock()

        async def send_json(payload: dict[str, Any]) -> None:
            async with send_lock:
                await ws.send_json(payload)

        async def pump_events() -> None:
            idle_ticks = 0
            while True:
                try:
                    event = await loop.run_in_executor(None, lambda: sub.get(timeout=0.2))
                    idle_ticks = 0
                    await send_json(event)
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= 75:
                        idle_ticks = 0
                        await send_json({"type": "keepalive"})
                except Exception:
                    return

        writer = asyncio.create_task(pump_events())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                reply = _dashboard_ws_rpc_response(msg.get("text"), config)
                if reply is not None:
                    await send_json(reply)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            BUS.unsubscribe(sub)

    @app.get("/api/health")
    async def api_health(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/api/browser/manage")
    async def api_browser_manage_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .browser_connect import manage_browser

        return JSONResponse(manage_browser("status", config=config))

    @app.post("/api/browser/manage")
    async def api_browser_manage(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        from .browser_connect import manage_browser

        try:
            result = manage_browser(
                str(body.get("action") or "status"),
                url=body.get("url"),
                config=config,
            )
        except ValueError as exc:
            return JSONResponse({"connected": False, "url": "", "error": str(exc)}, status_code=400)
        return JSONResponse(result)

    @app.get("/api/auth/providers")
    async def api_auth_providers(request: Request) -> JSONResponse:
        if not _request_peer_allowed(request, config):
            return JSONResponse({"ok": False, "error": "request rejected by dashboard host guard"}, status_code=403)
        return JSONResponse(_auth_providers_payload(config))

    @app.get("/api/auth/me")
    async def api_auth_me(request: Request) -> JSONResponse:
        _require_request(request, config)
        providers = []
        if dash._dashboard_token(config):
            providers.append("token")
        if _basic_auth_configured():
            providers.append("basic")
        if not providers:
            providers.append("loopback")
        return JSONResponse({
            "authenticated": True,
            "auth_required": _auth_configured(config) or _remote_bind_requires_auth(config),
            "providers": providers,
            "user": "local",
        })

    @app.post("/api/auth/login")
    async def api_auth_login(request: Request) -> JSONResponse:
        if not _request_peer_allowed(request, config):
            return JSONResponse({"ok": False, "error": "request rejected by dashboard host guard"}, status_code=403)
        body = await request.json()
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not _basic_auth_configured():
            return JSONResponse({"ok": False, "error": "username/password login is not configured"}, status_code=400)
        expected_user, expected_password = _basic_auth_credentials()
        if not (hmac.compare_digest(username, expected_user)
                and hmac.compare_digest(password, expected_password)):
            return JSONResponse({"ok": False, "error": "invalid username or password"}, status_code=401)
        response = JSONResponse({"ok": True, "user": username})
        response.set_cookie(
            _SESSION_COOKIE,
            _make_session_cookie(username, config),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/logout")
    async def api_auth_logout(request: Request) -> JSONResponse:  # noqa: ARG001
        response = JSONResponse({"ok": True})
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie("aegis_dashboard_token")
        return response

    @app.post("/api/auth/ws-ticket")
    async def api_auth_ws_ticket(request: Request) -> JSONResponse:
        _require_request(request, config)
        ticket = _issue_ws_ticket()
        return JSONResponse({"ok": True, **ticket})

    @app.get("/api/config")
    async def api_config_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._redacted_config(config))

    @app.post("/api/config")
    async def api_config_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_api_post("/api/config", body, config, chat_runner))

    @app.patch("/api/config/fields")
    async def api_config_fields_patch(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _config_fields_patch(config, body if isinstance(body, dict) else {})
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/config/defaults")
    async def api_config_defaults(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .config import DEFAULT_CONFIG

        return JSONResponse(copy.deepcopy(DEFAULT_CONFIG))

    @app.get("/api/config/schema")
    async def api_config_schema(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_config_schema())

    @app.get("/api/config/raw")
    async def api_config_raw(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"config": copy.deepcopy(config.data)})

    @app.get("/api/config/yaml")
    async def api_config_yaml(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._config_raw(config))

    @app.post("/api/config/yaml")
    async def api_config_yaml_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = dash._config_write_raw(str(body.get("raw") or "") if isinstance(body, dict) else "", config)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/config/export")
    async def api_config_export(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import config as cfg

        payload = {
            "ok": True,
            "config": copy.deepcopy(config.data),
            "redacted_config": dash._redacted_config(config),
            "env": _env_list(),
            "paths": {
                "home": str(cfg.get_home()),
                "config": str(cfg.config_path()),
                "env": str(cfg.env_path()),
            },
        }
        return JSONResponse(
            payload,
            headers={"Content-Disposition": 'attachment; filename="aegis-config-export.json"'},
        )

    @app.put("/api/config/raw")
    async def api_config_raw_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw = body.get("config", body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return JSONResponse({"ok": False, "error": "config object required"}, status_code=400)
        payload, status = _replace_config_mapping(config, raw)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/config/import")
    async def api_config_import(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw = body.get("config", body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return JSONResponse({"ok": False, "error": "config object required"}, status_code=400)
        payload, status = _replace_config_mapping(config, raw)
        if status == 200:
            payload = {"ok": True, "config": dash._redacted_config(config)}
        return JSONResponse(payload, status_code=status)

    @app.get("/api/env")
    async def api_env_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_env_list())

    @app.post("/api/env")
    async def api_env_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload, status = _env_set_payload(body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=status)

    @app.put("/api/env")
    async def api_env_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload, status = _env_set_payload(body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=status)

    @app.get("/api/env/{key}/reveal")
    async def api_env_reveal(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _env_reveal_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/env/reveal")
    async def api_env_reveal_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        key = str((body if isinstance(body, dict) else {}).get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "missing key"}, status_code=400)
        payload, status = _env_reveal_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.delete("/api/env/{key}")
    async def api_env_delete(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _env_delete_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.delete("/api/env")
    async def api_env_delete_body(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        key = str((body if isinstance(body, dict) else {}).get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "missing key"}, status_code=400)
        payload, status = _env_delete_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.get("/api/providers")
    async def api_providers_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_models(config))

    @app.post("/api/providers/probe")
    async def api_providers_probe(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_provider_probe(config, body if isinstance(body, dict) else {}))

    @app.get("/api/provider-auth")
    async def api_provider_auth_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_provider_auth_payload(config))

    @app.get("/api/provider-auth/{provider}")
    async def api_provider_auth_detail(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _provider_auth_payload(config, name)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.delete("/api/provider-auth/{provider}")
    async def api_provider_auth_delete(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _provider_auth_payload(config, name)
        row = payload.get("auth") or {}
        removed = []
        for key in row.get("env_vars", []) or []:
            if _delete_env_key(str(key)):
                removed.append(str(key))
        try:
            from .providers.auth import AuthStore

            AuthStore().delete(name)
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse({"ok": True, "provider": name, "removed_env": removed})

    @app.post("/api/provider-auth/anthropic/import-claude")
    async def api_provider_auth_import_claude(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .providers.auth import AuthStore, import_claude_cli_login

        ok, detail = import_claude_cli_login(AuthStore())
        return JSONResponse({"ok": bool(ok), "detail": detail}, status_code=200 if ok else 400)

    @app.get("/api/dashboard/preferences")
    async def api_dashboard_preferences(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_preferences(config))

    @app.put("/api/dashboard/preferences")
    async def api_dashboard_preferences_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "preferences object required"}, status_code=400)
        return JSONResponse({"ok": True, "preferences": _set_dashboard_preferences(config, body)})

    @app.get("/api/profiles")
    async def api_profiles_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_profiles_payload(config))

    @app.post("/api/profiles")
    async def api_profiles_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            raw_name = str((body or {}).get("name") or "").strip()
            if not raw_name:
                config.set("agent.personality", "")
                return JSONResponse({"ok": True, "active": "", "profiles": _profiles_payload(config)})
            name = _safe_resource_name(raw_name, "profile")
            content = str((body or {}).get("content") or "")
            if not content.strip() and _profile_path(name).exists():
                config.set("agent.personality", name)
                return JSONResponse({"ok": True, "active": name, "profiles": _profiles_payload(config)})
            if not content.strip():
                content = f"# {name}\n\n"
            result = _write_profile(config, name, content)
            if bool((body or {}).get("activate", False)):
                config.set("agent.personality", name)
            return JSONResponse({**result, "active": config.get("agent.personality") or "", "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/profiles/{name}")
    async def api_profile_get(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _profile_detail(config, name)
        return JSONResponse(result, status_code=200 if result.get("ok") else 404)

    @app.patch("/api/profiles/{name}")
    async def api_profile_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            existing = _profile_detail(config, name)
            if not existing.get("ok"):
                return JSONResponse(existing, status_code=404)
            content = str((body or {}).get("content", existing.get("content", "")))
            result = _write_profile(config, name, content)
            return JSONResponse({**result, "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/profiles/{name}")
    async def api_profile_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            path = _profile_path(name)
            if not path.exists():
                return JSONResponse({"ok": False, "error": "profile not found", "name": name}, status_code=404)
            path.unlink()
            if config.get("agent.personality") == path.stem:
                config.set("agent.personality", "")
            return JSONResponse({"ok": True, "name": path.stem, "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/profiles/{name}/activate")
    async def api_profile_activate(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        if name in {"default", "none", "_default"}:
            config.set("agent.personality", "")
            return JSONResponse({"ok": True, "active": "", "profiles": _profiles_payload(config)})
        result = _profile_detail(config, name)
        if not result.get("ok"):
            return JSONResponse(result, status_code=404)
        config.set("agent.personality", str(result["name"]))
        return JSONResponse({"ok": True, "active": result["name"], "profiles": _profiles_payload(config)})

    @app.get("/api/runtime-profiles")
    async def api_runtime_profiles_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_runtime_profiles_payload())

    @app.post("/api/runtime-profiles")
    async def api_runtime_profiles_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import profiles

        body = await request.json()
        try:
            name = str((body or {}).get("name") or "").strip()
            source = str((body or {}).get("clone_from") or "").strip() or None
            path = profiles.create_profile(
                name,
                clone_from=source,
                clone_config=bool((body or {}).get("clone", False) or source),
                clone_all=bool((body or {}).get("clone_all", False)),
            )
            if bool((body or {}).get("activate", False)):
                profiles.use_profile(name)
            return JSONResponse({"ok": True, "path": str(path), **_runtime_profiles_payload()})
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/runtime-profiles/{name}/activate")
    async def api_runtime_profile_activate(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import profiles

        try:
            profiles.use_profile(name)
            return JSONResponse({"ok": True, **_runtime_profiles_payload()})
        except (ValueError, FileNotFoundError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/runtime-profiles/{name}")
    async def api_runtime_profile_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import profiles

        try:
            ok = profiles.delete_profile(name)
            return JSONResponse({"ok": ok, **_runtime_profiles_payload()}, status_code=200 if ok else 404)
        except (ValueError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/skills/manage")
    async def api_skills_manage(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_skills_payload(config))

    @app.get("/api/skills/bundles")
    async def api_skill_bundles(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .skill_bundles import list_bundles

        return JSONResponse({"bundles": list_bundles()})

    @app.post("/api/skills/bundles")
    async def api_skill_bundle_save(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .skill_bundles import list_bundles, save_bundle

        body = await request.json()
        try:
            bundle = save_bundle(
                str(body.get("name") or ""),
                body.get("skills") or body.get("members") or [],
                description=str(body.get("description") or ""),
                instruction=str(body.get("instruction") or ""),
            )
            return JSONResponse({"ok": True, "bundle": bundle, "bundles": list_bundles()})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/skills/bundles/{name}")
    async def api_skill_bundle_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .skill_bundles import delete_bundle, list_bundles

        ok = delete_bundle(name)
        return JSONResponse({"ok": ok, "name": name, "bundles": list_bundles()}, status_code=200 if ok else 404)

    @app.get("/api/skills/marketplace/search")
    async def api_skills_marketplace_search(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import marketplace

        query = str(request.query_params.get("q") or request.query_params.get("query") or "")
        try:
            results = marketplace.search(query)
        except Exception as exc:  # noqa: BLE001
            results = []
            return JSONResponse({"ok": False, "error": str(exc), "results": results}, status_code=502)
        return JSONResponse({"ok": True, "query": query, "results": results})

    @app.post("/api/skills/marketplace/install")
    async def api_skills_marketplace_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import marketplace

        body = await request.json()
        try:
            if body.get("hub"):
                names = marketplace.install_hub(str(body["hub"]), config, force=bool(body.get("force", False)))
            else:
                source = str(body.get("source") or body.get("name") or "").strip()
                if not source:
                    return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
                names = marketplace.install(source, force=bool(body.get("force", False)))
            return JSONResponse({**_skills_payload(config), "ok": True, "installed": names})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/skills/marketplace/uninstall")
    async def api_skills_marketplace_uninstall(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import marketplace

        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        ok = marketplace.remove(name)
        return JSONResponse({"ok": ok, "name": name, **_skills_payload(config)},
                            status_code=200 if ok else 404)

    @app.post("/api/skills")
    async def api_skills_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            from .skills import SkillsLoader
            from .tools.skill_manage import _split_skill_content

            loader = SkillsLoader(config)
            content = body.get("content")
            if content is not None:
                fm, skill_body, err = _split_skill_content(str(content))
                if err:
                    return JSONResponse({"ok": False, "error": err}, status_code=400)
                name = str(fm.get("name") or "").strip()
                description = str(fm.get("description") or "").strip()
                extra = {k: v for k, v in fm.items() if k not in {"name", "description"}}
                path = loader.create(name, description, skill_body, extra_frontmatter=extra, origin="user")
            else:
                name = str(body.get("name") or "").strip()
                description = str(body.get("description") or "").strip()
                skill_body = str(body.get("body") or "").strip()
                if not name or not description or not skill_body:
                    return JSONResponse({"ok": False, "error": "name, description, and body are required"}, status_code=400)
                path = loader.create(name, description, skill_body, origin="user")
            return JSONResponse({"ok": True, "path": str(path), **_skills_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/skills/{name}")
    async def api_skill_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _skill_detail(config, name)
        return JSONResponse(result, status_code=200 if result.get("ok") else 404)

    @app.put("/api/skills/{name}/toggle")
    async def api_skill_toggle(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        safe = _safe_resource_name(name, "skill")
        disabled = [str(s) for s in (config.get("skills.disabled", []) or []) if str(s).strip()]
        enabled = bool(body.get("enabled"))
        if enabled:
            disabled = [item for item in disabled if item != safe]
        elif safe not in disabled:
            disabled.append(safe)
        config.set("skills.disabled", sorted(set(disabled)))
        return JSONResponse({"ok": True, "name": safe, "enabled": enabled, **_skills_payload(config)})

    @app.patch("/api/skills/{name}")
    async def api_skill_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        try:
            from .tools.skill_manage import _split_skill_content
            from .util import atomic_write

            skill_path = Path(detail["skill"]["path"]).resolve()
            if not _skill_path_editable(skill_path):
                return JSONResponse({"ok": False, "error": "only workspace or personal skills can be edited"}, status_code=403)
            content = str(body.get("content") or "")
            if not content:
                current = skill_path.read_text(encoding="utf-8")
                content = current
            fm, _skill_body, err = _split_skill_content(content)
            if err:
                return JSONResponse({"ok": False, "error": err}, status_code=400)
            if str(fm.get("name") or "").strip() != name:
                return JSONResponse({"ok": False, "error": "frontmatter name must match skill name"}, status_code=400)
            atomic_write(skill_path, content.rstrip() + "\n")
            return JSONResponse({"ok": True, **_skill_detail(config, name)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/skills/{name}")
    async def api_skill_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        import shutil

        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        skill_path = Path(detail["skill"]["path"])
        if not _skill_path_editable(skill_path):
            return JSONResponse({"ok": False, "error": "only workspace or personal skills can be deleted"}, status_code=403)
        target, err = _validate_skill_delete_target(skill_path)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=403)
        ok = bool(target and target.exists())
        if ok:
            shutil.rmtree(target)
        return JSONResponse({"ok": ok, "name": detail["skill"]["name"], **_skills_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/skills/{name}/pin")
    async def api_skill_pin(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "skill")
        from . import curator

        curator.pin(safe, True)
        return JSONResponse({"ok": True, "name": safe, "pinned": True})

    @app.post("/api/skills/{name}/unpin")
    async def api_skill_unpin(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "skill")
        from . import curator

        curator.pin(safe, False)
        return JSONResponse({"ok": True, "name": safe, "pinned": False})

    @app.get("/api/plugins")
    async def api_plugins_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_plugins_payload(config))

    @app.get("/api/dashboard/plugins")
    async def api_dashboard_plugins(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_plugins_payload(config))

    @app.get("/api/dashboard/plugins/hub")
    async def api_dashboard_plugins_hub(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_plugin_hub(config))

    @app.get("/api/dashboard/plugins/rescan")
    @app.post("/api/dashboard/plugins/rescan")
    async def api_dashboard_plugins_rescan(request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        plugin_runtime.clear_runtime_cache()
        _mount_dashboard_plugin_api_routes(app, config)
        hub = _dashboard_plugin_hub(config)
        return JSONResponse({"ok": True, "count": len(hub.get("plugins", [])), **hub})

    @app.get("/dashboard-plugins/{plugin_name}/{file_path:path}")
    async def dashboard_plugin_asset(plugin_name: str, file_path: str, request: Request) -> Response:
        _require_request(request, config)
        return _dashboard_plugin_static(config, plugin_name, file_path)

    @app.get("/dashboard-plugins/{bad_path:path}")
    async def dashboard_plugin_bad_path(bad_path: str) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"ok": False, "error": "dashboard plugin asset not found"}, status_code=404)

    _mount_dashboard_plugin_api_routes(app, config)

    @app.post("/api/plugins/reload")
    async def api_plugins_reload(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from . import plugins as plugin_runtime

            plugin_runtime.clear_runtime_cache()
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({"ok": True, **_plugins_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/plugins/validate")
    async def api_plugins_validate(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _validate_plugin_source(str((body or {}).get("source") or ""))
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/plugins/install")
    async def api_plugins_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        source = str(body.get("source") or "").strip()
        if not source:
            return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
        try:
            from . import plugins as plugin_runtime

            result = plugin_runtime.install_details(
                source,
                config,
                force=_coerce_dashboard_bool(body.get("force"), False),
                enable_now=_coerce_dashboard_bool(body.get("enable"), True),
            )
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_plugins_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/dashboard/agent-plugins/install")
    async def api_dashboard_agent_plugins_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        source = str(body.get("identifier") or body.get("source") or "").strip()
        if not source:
            return JSONResponse({"ok": False, "error": "identifier is required"}, status_code=400)
        try:
            from . import plugins as plugin_runtime

            result = plugin_runtime.install_details(
                source,
                config,
                force=_coerce_dashboard_bool(body.get("force"), False),
                enable_now=_coerce_dashboard_bool(body.get("enable"), True),
            )
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_dashboard_plugin_hub(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/dashboard/plugin-providers")
    async def api_dashboard_plugin_providers(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        return JSONResponse(_set_dashboard_plugin_providers(config, body))

    @app.post("/api/dashboard/plugins/{name:path}/visibility")
    async def api_dashboard_plugin_visibility(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        payload = _set_dashboard_plugin_visibility(config, name, bool(body.get("hidden", False)))
        return JSONResponse(payload)

    @app.get("/api/dashboard/agent-plugins/{name:path}")
    async def api_dashboard_agent_plugin_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_plugin_route_name(name)
        payload = _plugin_detail(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.get("/api/plugins/{name}")
    async def api_plugin_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_plugin_route_name(name)
        payload = _plugin_detail(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.post("/api/plugins/{name:path}/enable")
    async def api_plugin_enable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.enable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/enable")
    async def api_dashboard_agent_plugin_enable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.enable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

    @app.post("/api/plugins/{name:path}/disable")
    async def api_plugin_disable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.disable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/disable")
    async def api_dashboard_agent_plugin_disable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.disable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/update")
    async def api_dashboard_agent_plugin_update(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _dashboard_agent_plugin_update(config, name)
        if result.get("ok"):
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_dashboard_plugin_hub(config)})
        return JSONResponse(result, status_code=400 if result.get("error") else 404)

    @app.delete("/api/plugins/{name:path}")
    async def api_plugin_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.remove(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.delete("/api/dashboard/agent-plugins/{name:path}")
    async def api_dashboard_agent_plugin_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.remove(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

    @app.get("/api/tools/toolsets")
    async def api_toolsets(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_toolsets(config))

    @app.put("/api/tools/toolsets/{name}")
    async def api_toolset_toggle(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = dash._dashboard_toolset_toggle(
            {"toolset": name, "enabled": bool(body.get("enabled"))},
            config,
        )
        return JSONResponse(
            {**result, "toolsets_detail": dash._dashboard_toolsets(config)},
            status_code=200 if result.get("ok") else 400,
        )

    @app.get("/api/mcp/servers")
    async def api_mcp_servers(request: Request) -> JSONResponse:
        _require_request(request, config)
        live = str(request.query_params.get("live") or "").lower() in {"1", "true", "yes"}
        return JSONResponse(dash._dashboard_mcp_catalog(config, live=live))

    @app.post("/api/mcp/servers")
    async def api_mcp_server_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            name = _safe_resource_name(str((body or {}).get("name") or ""), "mcp server")
            servers = _mcp_servers(config)
            if name in servers and not bool((body or {}).get("force", False)):
                return JSONResponse({"ok": False, "error": "server already exists"}, status_code=409)
            servers[name] = _mcp_spec_from_body(body if isinstance(body, dict) else {})
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": name, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/mcp/catalog/{name}/install")
    async def api_mcp_catalog_install(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _mcp_catalog_install_response(config, name)

    @app.post("/api/mcp/catalog/install")
    async def api_mcp_catalog_install_body(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        name = str((body if isinstance(body, dict) else {}).get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "missing name"}, status_code=400)
        return _mcp_catalog_install_response(config, name)

    @app.get("/api/mcp/servers/{name}")
    async def api_mcp_server_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "mcp server")
        live = str(request.query_params.get("live") or "").lower() in {"1", "true", "yes"}
        payload = dash._dashboard_mcp_catalog(config, live=live)
        match = next((row for row in payload.get("servers", []) if row.get("name") == safe), None)
        return JSONResponse({"ok": bool(match), "server": match}, status_code=200 if match else 404)

    @app.patch("/api/mcp/servers/{name}")
    async def api_mcp_server_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe = _safe_resource_name(name, "mcp server")
            servers = _mcp_servers(config)
            if safe not in servers:
                return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)
            servers[safe] = _mcp_spec_from_body(body if isinstance(body, dict) else {}, servers[safe])
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": safe, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/mcp/servers/{name}/enabled")
    async def api_mcp_server_enabled(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe = _safe_resource_name(name, "mcp server")
            servers = _mcp_servers(config)
            if safe not in servers:
                return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)
            enabled = bool((body if isinstance(body, dict) else {}).get("enabled"))
            servers[safe] = _mcp_spec_from_body({"enabled": enabled}, servers[safe])
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": safe, "enabled": enabled, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/mcp/servers/{name}")
    async def api_mcp_server_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "mcp server")
        servers = _mcp_servers(config)
        ok = safe in servers
        if ok:
            servers.pop(safe, None)
            _save_mcp_servers(config, servers)
        return JSONResponse({"ok": ok, "name": safe, **dash._dashboard_mcp_catalog(config)}, status_code=200 if ok else 404)

    @app.post("/api/mcp/servers/{name}/probe")
    async def api_mcp_server_probe(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from .mcp.client import probe_server

            safe = _safe_resource_name(name, "mcp server")
            result = probe_server(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.post("/api/mcp/servers/{name}/test")
    async def api_mcp_server_test(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from .mcp.client import probe_server

            safe = _safe_resource_name(name, "mcp server")
            result = probe_server(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.get("/api/mcp/servers/{name}/tools")
    async def api_mcp_server_tools(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from .mcp.client import tool_checklist

            safe = _safe_resource_name(name, "mcp server")
            result = tool_checklist(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.post("/api/mcp/servers/{name}/tools")
    async def api_mcp_server_tools_post(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            from .mcp.client import save_tool_checklist, tool_checklist

            safe = _safe_resource_name(name, "mcp server")
            include = body.get("include", []) if isinstance(body, dict) else []
            if not isinstance(include, list):
                return JSONResponse({"ok": False, "error": "include must be a list"}, status_code=400)
            save_tool_checklist(config, safe, [str(x) for x in include])
            return JSONResponse({"ok": True, **tool_checklist(config, safe)})
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.get("/api/memory/providers")
    async def api_memory_providers(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .memory_providers import memory_provider_report

        return JSONResponse(memory_provider_report(config))

    @app.get("/api/memory/provider")
    async def api_memory_provider_active(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .memory_providers import memory_provider_report

        return JSONResponse(memory_provider_report(config)["active"])

    @app.get("/api/memory/providers/{name}")
    async def api_memory_provider_status(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .memory_providers import memory_provider_status

        status = memory_provider_status(name, config)
        return JSONResponse(status, status_code=200 if status.get("known") else 404)

    @app.get("/api/memory/providers/{name}/setup")
    async def api_memory_provider_setup(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .memory_providers import memory_provider_setup

        setup = memory_provider_setup(name)
        return JSONResponse(setup, status_code=200 if setup.get("known") else 404)

    @app.get("/api/memory/providers/{name}/schema")
    async def api_memory_provider_schema(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .memory_providers import memory_provider_config_schema

        schema = memory_provider_config_schema(name)
        return JSONResponse(schema, status_code=200 if schema.get("known") else 404)

    @app.get("/api/audio/voices")
    async def api_audio_voices(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({
            "voices": ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
            "transcription_models": ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"],
            "tts_models": ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"],
            "provider": config.get("model.provider"),
        })

    @app.post("/api/audio/tts")
    async def api_audio_tts(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not str(body.get("text") or "").strip():
            return JSONResponse({"ok": False, "error": "text is required"}, status_code=400)
        from .tools.voice import SpeakTool

        result = SpeakTool().run(body, _voice_tool_context(config))
        return JSONResponse(
            {"ok": not result.is_error, "content": result.content, "display": result.display, "data": result.data},
            status_code=502 if result.is_error else 200,
        )

    @app.post("/api/audio/transcribe")
    async def api_audio_transcribe(request: Request,
                                   file: Annotated[UploadFile, File()],
                                   model: Annotated[str, Form()] = "whisper-1") -> JSONResponse:
        _require_request(request, config)
        suffix = Path(file.filename or "audio").suffix or ".audio"
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="aegis-audio-", suffix=suffix, delete=False) as tmp:
                temp_path = tmp.name
                tmp.write(await file.read())
            from .tools.voice import TranscribeTool

            result = TranscribeTool().run({"path": temp_path, "model": model}, _voice_tool_context(config))
            return JSONResponse(
                {"ok": not result.is_error, "text": result.content, "display": result.display},
                status_code=502 if result.is_error else 200,
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass

    @app.get("/api/sessions")
    async def api_sessions_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        limit = int(request.query_params.get("limit") or 100)
        from .session import SessionStore

        return JSONResponse(SessionStore().list(max(1, min(limit, 1000))))

    @app.get("/api/sessions/stats")
    async def api_sessions_stats(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_session_stats())

    @app.get("/api/sessions/search")
    async def api_sessions_search(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .session import SessionStore

        store = SessionStore()
        query = str(request.query_params.get("query") or request.query_params.get("q") or "").strip()
        limit = int(request.query_params.get("limit") or (3 if query else 10))
        current_session_id = request.query_params.get("current_session_id")
        if not query:
            return JSONResponse(store.browse_sessions(limit=limit, current_session_id=current_session_id))
        role_filter = request.query_params.getlist("role")
        if not role_filter and request.query_params.get("role_filter"):
            role_filter = [r.strip() for r in request.query_params["role_filter"].split(",") if r.strip()]
        return JSONResponse(store.discover_sessions(
            query,
            limit=limit,
            role_filter=role_filter or None,
            sort=request.query_params.get("sort"),
            current_session_id=current_session_id,
        ))

    @app.post("/api/sessions/prune")
    async def api_sessions_prune(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_prune_sessions(int(body.get("older_than_days", 30))))

    @app.get("/api/sessions/empty")
    async def api_sessions_empty(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_sessions(older_than_days, dry_run=True))

    @app.get("/api/sessions/empty/count")
    async def api_sessions_empty_count(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_session_count(older_than_days))

    @app.delete("/api/sessions/empty")
    async def api_sessions_empty_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_sessions(older_than_days, dry_run=False))

    @app.post("/api/sessions/prune-empty")
    async def api_sessions_prune_empty(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_empty_sessions(
            float(body.get("older_than_days", 0)),
            dry_run=bool(body.get("dry_run", False)),
        ))

    @app.post("/api/sessions/delete")
    async def api_sessions_delete_many(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _delete_sessions(body.get("ids") if isinstance(body, dict) else None)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/sessions/bulk-delete")
    async def api_sessions_bulk_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        ids = body.get("ids") if isinstance(body, dict) else None
        if not ids and isinstance(body, dict):
            ids = body.get("session_ids")
        result = _delete_sessions(ids)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/sessions/{session_id}")
    async def api_session_detail(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_session_detail(session_id, config))

    @app.patch("/api/sessions/{session_id}")
    async def api_session_patch(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if "title" in body:
            title = str(body.get("title") or "").strip()
            if not title:
                return JSONResponse({"ok": False, "error": "title cannot be empty"}, status_code=400)
            session.title = title
        if "meta" in body:
            if not isinstance(body["meta"], dict):
                return JSONResponse({"ok": False, "error": "meta must be an object"}, status_code=400)
            session.meta.update(copy.deepcopy(body["meta"]))
        if "todos" in body:
            if not isinstance(body["todos"], list):
                return JSONResponse({"ok": False, "error": "todos must be a list"}, status_code=400)
            session.todos = copy.deepcopy(body["todos"])
        store.save(session)
        return JSONResponse({"ok": True, "session": _session_export(session)})

    @app.post("/api/sessions/{session_id}/rename")
    async def api_session_rename(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        title = str(body.get("title") or body.get("name") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title is required"}, status_code=400)
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        session.title = title
        store.save(session)
        return JSONResponse({"ok": True, "id": session.id, "title": session.title})

    @app.get("/api/sessions/{session_id}/messages")
    async def api_session_messages(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        _store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        return JSONResponse({
            "ok": True,
            "id": session.id,
            "count": len(session.messages),
            "messages": [_message_payload(message, i) for i, message in enumerate(session.messages)],
        })

    @app.post("/api/sessions/{session_id}/messages")
    async def api_session_message_add(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        try:
            message = _message_from_payload(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        session.messages.append(message)
        store.save(session)
        index = len(session.messages) - 1
        return JSONResponse({"ok": True, "id": session.id, "message": _message_payload(message, index)})

    @app.get("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_get(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        _store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        return JSONResponse({"ok": True, "message": _message_payload(session.messages[index], index)})

    @app.patch("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_patch(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        try:
            session.messages[index] = _patched_message(session.messages[index], body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        store.save(session)
        return JSONResponse({"ok": True, "message": _message_payload(session.messages[index], index)})

    @app.delete("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_delete(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        removed = session.messages.pop(index)
        store.save(session)
        return JSONResponse({"ok": True, "removed": _message_payload(removed, index), "count": len(session.messages)})

    @app.get("/api/sessions/{session_id}/export")
    async def api_session_export(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .session import SessionStore

        session = SessionStore().load(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        return JSONResponse(
            _session_export(session),
            headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'},
        )

    @app.delete("/api/sessions/{session_id}")
    async def api_session_delete(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .session import SessionStore

        ok = SessionStore().delete(session_id)
        return JSONResponse({"ok": ok, "id": session_id}, status_code=200 if ok else 404)

    @app.get("/api/cron/jobs")
    async def api_cron_jobs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_jobs_response()

    @app.post("/api/cron/jobs")
    async def api_cron_job_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_create_response(config, body)

    @app.get("/api/cron/delivery-targets")
    async def api_cron_delivery_targets(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_cron_delivery_targets(config))

    @app.get("/api/cron/jobs/{job_id}")
    async def api_cron_job_detail(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        invalid = _cron_job_invalid_id_response(job_id, request)
        if invalid is not None:
            return invalid
        detail = _cron_job_detail(job_id)
        return JSONResponse(detail, status_code=200 if detail.get("found") else 404)

    @app.patch("/api/cron/jobs/{job_id}")
    async def api_cron_job_patch(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_patch_response(job_id, body, request)

    @app.put("/api/cron/jobs/{job_id}")
    async def api_cron_job_put(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_put_response(job_id, body if isinstance(body, dict) else {}, request)

    @app.delete("/api/cron/jobs/{job_id}")
    async def api_cron_job_delete(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_delete_response(job_id, request)

    @app.get("/api/cron/jobs/{job_id}/runs")
    async def api_cron_job_runs(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_runs_response(job_id, _query_dict(request), request)

    @app.post("/api/cron/jobs/{job_id}/run")
    async def api_cron_job_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request)

    @app.post("/api/cron/jobs/{job_id}/trigger")
    async def api_cron_job_trigger(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request)

    @app.post("/api/cron/jobs/{job_id}/pause")
    async def api_cron_job_pause(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, False, request)

    @app.post("/api/cron/jobs/{job_id}/resume")
    async def api_cron_job_resume(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, True, request)

    @app.get("/api/jobs")
    async def api_jobs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_jobs_response()

    @app.post("/api/jobs")
    async def api_job_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_create_response(config, body)

    @app.get("/api/jobs/{job_id}")
    async def api_job_detail(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        invalid = _cron_job_invalid_id_response(job_id, request)
        if invalid is not None:
            return invalid
        detail = _cron_job_detail(job_id)
        return JSONResponse(detail, status_code=200 if detail.get("found") else 404)

    @app.patch("/api/jobs/{job_id}")
    async def api_job_patch(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_patch_response(job_id, body, request)

    @app.delete("/api/jobs/{job_id}")
    async def api_job_delete(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_delete_response(job_id, request)

    @app.post("/api/jobs/{job_id}/pause")
    async def api_job_pause(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, False, request)

    @app.post("/api/jobs/{job_id}/resume")
    async def api_job_resume(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, True, request)

    @app.post("/api/jobs/{job_id}/run")
    async def api_job_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request)

    @app.post("/api/jobs/{job_id}/trigger")
    async def api_job_trigger(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request)

    @app.get("/api/cron/service")
    async def api_cron_service_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .daemon import cron_service_status

        return JSONResponse({"service": "aegis-cron.service", "status": cron_service_status()})

    @app.post("/api/cron/service")
    async def api_cron_service_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        action = str(body.get("action") or "status")
        from .daemon import control_cron_service, cron_service_status, install_cron_service, remove_cron_service

        if action == "status":
            return JSONResponse({"ok": True, "service": "aegis-cron.service", "status": cron_service_status()})
        if action == "install":
            return JSONResponse(_service_result(install_cron_service(
                config,
                enable_now=not bool(body.get("no_start", False)),
            )))
        if action == "remove":
            return JSONResponse(_service_result(remove_cron_service()))
        if action in {"start", "stop", "restart"}:
            return JSONResponse(_service_result(control_cron_service(action)))
        return JSONResponse({"ok": False, "error": f"unknown cron service action: {action}"}, status_code=400)

    @app.get("/api/gateway/status")
    async def api_gateway_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_status(config))

    @app.get("/api/messaging/platforms")
    async def api_messaging_platforms(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_messaging_platforms_payload(config))

    @app.get("/api/platforms")
    @app.get("/api/platforms/registry")
    @app.get("/api/messaging/platforms/registry")
    async def api_platforms_registry(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_platform_registry_payload(config))

    @app.get("/api/platforms/{platform_id}")
    async def api_platform_registry_detail(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(platform_id, "platform").lower()
        payload = _platform_registry_payload(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.put("/api/messaging/platforms/{platform_id}")
    async def api_messaging_platform_update(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _messaging_platform_update(config, platform_id, body if isinstance(body, dict) else {})
        status = 200 if payload.get("ok") else (404 if "unknown" in str(payload.get("error", "")) else 400)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/messaging/platforms/{platform_id}/test")
    async def api_messaging_platform_test(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload = _messaging_platform_test(config, platform_id)
        status = 200 if payload.get("ok") or payload.get("state") in {"disabled", "not_configured", "error"} else 404
        return JSONResponse(payload, status_code=status)

    @app.get("/api/gateway/channels/catalog")
    async def api_gateway_channels_catalog(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_channel_payload(config))

    @app.get("/api/gateway/channels/{channel}")
    async def api_gateway_channel_get(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(channel, "channel").lower()
        payload = _gateway_channel_payload(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.patch("/api/gateway/channels/{channel}")
    async def api_gateway_channel_patch(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _set_gateway_channel(config, channel, body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

    @app.post("/api/gateway/channels/{channel}/probe")
    async def api_gateway_channel_probe(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(channel, "channel").lower()
        if safe not in _channel_catalog_map():
            return JSONResponse({"ok": False, "error": "unknown channel", "channel": safe}, status_code=404)
        return JSONResponse(_gateway_probe({"channel": safe}))

    @app.post("/api/gateway/channels")
    async def api_gateway_channels(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        channels = body.get("channels", [])
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        if not isinstance(channels, list):
            return JSONResponse({"ok": False, "error": "channels must be a list or comma string"}, status_code=400)
        config.data.setdefault("gateway", {})["channels"] = [str(c).strip() for c in channels if str(c).strip()]
        config.save()
        return JSONResponse({"ok": True, "gateway": _gateway_status(config)})

    @app.post("/api/gateway/start")
    async def api_gateway_start(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("start"))

    @app.post("/api/gateway/stop")
    async def api_gateway_stop(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("stop"))

    @app.post("/api/gateway/restart")
    async def api_gateway_restart(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("restart"))

    @app.post("/api/gateway/service")
    async def api_gateway_service(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        action = str(body.get("action") or "status")
        from .daemon import (
            gateway_service_status,
            install_gateway_service,
            remove_gateway_service,
        )

        if action == "status":
            return JSONResponse({"ok": True, "service": "aegis-gateway.service", "status": gateway_service_status()})
        if action == "install":
            channels = body.get("channels") or config.get("gateway.channels", []) or []
            if isinstance(channels, str):
                channels = [c.strip() for c in channels.split(",") if c.strip()]
            return JSONResponse(_service_result(install_gateway_service(
                config,
                [str(c).strip() for c in channels if str(c).strip()],
                enable_now=not bool(body.get("no_start", False)),
            )))
        if action == "remove":
            return JSONResponse(_service_result(remove_gateway_service()))
        if action in {"start", "stop", "restart"}:
            return JSONResponse(_gateway_service_control(action))
        return JSONResponse({"ok": False, "error": f"unknown gateway service action: {action}"}, status_code=400)

    @app.api_route(
        "/api/plugins/{plugin_name}/{plugin_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def api_plugin_api_missing(plugin_name: str, plugin_path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({
            "ok": False,
            "plugin": plugin_name,
            "path": plugin_path,
            "error": "dashboard plugin API not mounted",
        }, status_code=404)

    @app.get("/api/files/download")
    async def download_file(request: Request) -> FileResponse:
        _require_request(request, config)
        raw = (request.query_params.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="missing path")
        try:
            target = Path(raw).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="bad path") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        if dash._is_sensitive_path(target):
            raise HTTPException(
                status_code=403,
                detail="blocked: refusing to download a credential/key/SSH path through the dashboard.",
            )
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.delete("/api/files")
    async def api_files_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        return JSONResponse(_delete_managed_file(body if isinstance(body, dict) else {}))

    @app.get("/api/credentials/pools")
    async def api_credentials_pools(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_credential_pools_payload(config))

    @app.get("/api/credentials/pools/{provider}")
    async def api_credentials_pool_detail(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _credential_pools_payload(config, name)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.get("/api/credential-pools/status")
    async def api_credential_pools_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_credential_pools_payload(config))

    @app.get("/api/update/check")
    @app.get("/api/portal/update/check")
    @app.get("/api/check/update")
    async def api_update_check(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._update_check())

    @app.post("/api/update/check")
    @app.post("/api/portal/update/check")
    @app.post("/api/check/update")
    async def api_update_check_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._update_check())

    @app.get("/api/portal")
    @app.get("/api/portal/status")
    async def api_portal_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_portal_status_payload(config))

    @app.get("/api/actions/status")
    @app.get("/api/admin/actions/status")
    async def api_actions_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_action_catalog())

    @app.get("/api/admin/status")
    async def api_admin_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_admin_status_payload(config))

    @app.post("/api/actions/run")
    @app.post("/api/admin/actions/run")
    async def api_actions_run(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        action = str(body.get("action") or body.get("id") or body.get("name") or "")
        return JSONResponse(dash._ops_action(action, body, config))

    @app.get("/api/hooks")
    @app.get("/api/hooks/contract")
    @app.get("/api/observability")
    @app.get("/api/observability/contract")
    @app.get("/api/observability/events")
    @app.get("/api/observability/hooks")
    async def api_observability_contract(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_observability_contract_payload(config))

    @app.post("/api/hooks/test")
    @app.post("/api/observability/hooks/test")
    async def api_observability_hook_test(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return JSONResponse(_hook_test_payload(config, body if isinstance(body, dict) else {}))

    @app.get("/api/{path:path}")
    async def api_get(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get(f"/api/{path}", _query_dict(request), config))

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        _require_request(request, config)
        body = await request.json()
        return _dashboard_chat_streaming_response(body, chat_runner, request)

    @app.post("/api/files/upload")
    async def upload_file(request: Request) -> JSONResponse:
        _require_request(request, config)
        content_type = request.headers.get("content-type", "").lower()
        filename = ""
        target_path = ""
        data = b""
        if content_type.startswith("application/json"):
            body = await request.json()
            body = body if isinstance(body, dict) else {}
            target_path = str(body.get("path") or body.get("dir") or "")
            filename = Path(str(body.get("name") or body.get("filename") or "upload.bin")).name
            data_url = str(body.get("data_url") or body.get("dataUrl") or "")
            if data_url:
                header, sep, payload = data_url.partition(",")
                if not sep or ";base64" not in header:
                    return JSONResponse({"ok": False, "error": "data_url must be base64"})
                try:
                    data = base64.b64decode(payload, validate=True)
                except Exception:  # noqa: BLE001
                    return JSONResponse({"ok": False, "error": "invalid data_url"})
            elif "content" in body:
                data = str(body.get("content") or "").encode("utf-8")
            else:
                return JSONResponse({"ok": False, "error": "missing data_url or content"})
        else:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                return JSONResponse({"ok": False, "error": "missing file"})
            target_path = str(form.get("path") or "")
            filename = Path(str(getattr(upload, "filename", "") or "upload.bin")).name
            data = await upload.read()

        target_dir = Path(target_path or Path.home()).expanduser().resolve()
        if not target_dir.is_dir():
            return JSONResponse({"ok": False, "error": "target is not a directory"})
        target = target_dir / filename
        if dash._is_sensitive_path(target):
            return JSONResponse({"ok": False, "error": "blocked: refusing to write a "
                                 "credential/key/SSH path through the dashboard."})
        try:
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse({"ok": True, "path": str(target), "size": target.stat().st_size})

    @app.post("/api/{path:path}")
    async def api_post(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        # Tolerate an empty or malformed body (default to {}) — some POST endpoints
        # take no payload (e.g. /api/curator), and a 500 on missing JSON is hostile.
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if f"/api/{path}" == "/api/chat":
            return await _dashboard_chat_json_response(body, chat_runner, request)
        result = _api_post(f"/api/{path}", body, config, chat_runner, chat_fallback=False)
        if result is _CHAT_FALLBACK:
            return await _dashboard_chat_json_response(body, chat_runner, request)
        return JSONResponse(result)

    @app.websocket("/api/pty")
    async def pty_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        await ws.accept()
        try:
            from .dashboard_pty import PtyBridge, dashboard_terminal_argv

            bridge = PtyBridge.spawn(
                dashboard_terminal_argv(ws.query_params.get("resume") or None),
                cwd=os.getcwd(),
                cols=int(ws.query_params.get("cols") or 100),
                rows=int(ws.query_params.get("rows") or 30),
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"\r\nChat terminal unavailable: {exc}\r\n"
            await ws.send_text(msg)
            await ws.close(code=1011)
            return

        loop = asyncio.get_running_loop()

        async def pump_pty() -> None:
            while True:
                chunk = await loop.run_in_executor(None, bridge.read, 0.2)
                if chunk is None:
                    return
                if not chunk:
                    await asyncio.sleep(0)
                    continue
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    return

        reader = asyncio.create_task(pump_pty())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                raw = msg.get("bytes")
                if raw is None:
                    text = msg.get("text")
                    raw = text.encode() if isinstance(text, str) else b""
                match = _RESIZE_RE.match(raw or b"")
                if match:
                    bridge.resize(cols=int(match.group(1)), rows=int(match.group(2)))
                else:
                    bridge.write(raw or b"")
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            bridge.close()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return _html_response(config, request)

    return app


def run_dashboard(config: Config, host: str, port: int, *, open_browser: bool = False) -> None:
    import socket
    import uvicorn

    from ._log import setup_logging

    setup_logging(mode="gui")
    requested = port
    selected = None
    for candidate in range(port, port + 50):
        with socket.socket() as s:
            try:
                s.bind((host, candidate))
            except OSError:
                continue
            selected = candidate
            break
    if selected is None:
        raise OSError(f"no free port in {requested}-{requested + 49} on {host}")
    port = selected
    if port != requested:
        print(f"  (port {requested} busy - using {port})")
    url = dash._dashboard_url(config, host, port)
    print(f"AEGIS control panel -> {url}")
    print("  (leave this running; press Ctrl+C to stop)")
    if open_browser:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    _announce_dashboard_ready_when_live(config, host, port)
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
