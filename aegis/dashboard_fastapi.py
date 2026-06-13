"""FastAPI dashboard backend for the AEGIS web UI."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import tempfile
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from . import __version__
from .config import Config

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
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


def _query_dict(request: Request) -> dict[str, list[str]]:
    return {key: request.query_params.getlist(key) for key in request.query_params.keys()}


def _authorized_token(config: Config, *, query: str = "", header: str = "",
                      auth: str = "", cookie: str = "") -> bool:
    token = dash._dashboard_token(config)
    if not token:
        return not _basic_auth_configured() and not _remote_bind_requires_auth(config)
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    return token in (query, header, bearer, cookie)


def _basic_auth_credentials() -> tuple[str, str]:
    return os.environ.get(_BASIC_USER_ENV, ""), os.environ.get(_BASIC_PASS_ENV, "")


def _basic_auth_configured() -> bool:
    user, password = _basic_auth_credentials()
    return bool(user and password)


def _auth_configured(config: Config) -> bool:
    return bool(dash._dashboard_token(config) or _basic_auth_configured())


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


def _config_schema(defaults: dict[str, Any] | None = None) -> dict:
    from .config import DEFAULT_CONFIG

    def flatten(node: Any, prefix: str = "") -> list[dict]:
        if isinstance(node, dict):
            rows: list[dict] = []
            for key, value in sorted(node.items()):
                path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, dict):
                    rows.extend(flatten(value, path))
                else:
                    rows.append({
                        "path": path,
                        "type": type(value).__name__ if value is not None else "null",
                        "default": value,
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
        {"path": key, "type": type(value).__name__ if value is not None else "null", "default": value}
        for key, value in sorted(base.items())
        if not isinstance(value, dict)
    ]
    return {"sections": sections, "fields": flatten(base), "loose": loose}


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
    from .plugins import list_manifests, load_plugins

    api = load_plugins(quiet=True, config=config)
    return {
        "plugins": [m.to_dict() for m in list_manifests(config)],
        "tools": [getattr(t, "name", "") for t in api.tools],
        "channels": sorted(api.channels.keys()),
        "providers": list(api.providers),
        "errors": [{"path": str(path), "error": error} for path, error in api.errors],
        "enabled": config.get("plugins.enabled", []) or [],
        "disabled": config.get("plugins.disabled", []) or [],
        "allowlist": config.get("plugins.allowlist", []) or [],
    }


def _dashboard_preferences(config: Config) -> dict:
    return {
        "theme": config.get("display.theme", "system"),
        "reasoning": config.get("display.reasoning", "summary"),
        "status_footer": bool(config.get("display.status_footer", True)),
        "tool_progress": config.get("display.tool_progress", "compact"),
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
    return _dashboard_preferences(config)


def _voice_tool_context(config: Config):
    from .tools.base import ToolContext

    return ToolContext(cwd=Path.cwd(), config=config)


def _session_stats() -> dict:
    from .session import SessionStore

    store = SessionStore()
    rows = store.list(10000)
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


def _cron_job_detail(job_id: str) -> dict:
    for row in dash._dashboard_cron_jobs():
        if row["id"] == job_id or row["id"].startswith(job_id):
            return {"found": True, "job": row}
    return {"found": False, "id": job_id, "error": "cron job not found"}


def _service_result(result) -> dict:
    return {"ok": bool(getattr(result, "ok", False)), "message": str(getattr(result, "message", ""))}


def _gateway_status(config: Config) -> dict:
    from .daemon import gateway_service_status
    from .gateway.queue import DeliveryQueue

    try:
        pending = DeliveryQueue().pending_count()
    except Exception:  # noqa: BLE001
        pending = 0
    channels = list(config.get("gateway.channels", []) or [])
    return {
        "channels": channels,
        "configured": bool(channels),
        "busy_mode": config.get("gateway.busy_mode", "queue"),
        "session_mode": config.get("gateway.session_mode", "per_channel_peer"),
        "require_mention": bool(config.get("gateway.require_mention", False)),
        "mention_triggers": list(config.get("gateway.mention_triggers", []) or []),
        "admins": list(config.get("gateway.admins", []) or []),
        "queue_pending": pending,
        "service": gateway_service_status(),
    }


def _api_get(path: str, query: dict[str, list[str]], config: Config) -> dict:
    if path == "/api/status":
        return dash._dashboard_status(config)
    if path == "/api/cockpit":
        return dash._dashboard_cockpit(config)
    if path == "/api/kanban":
        return dash._dashboard_kanban()
    if path == "/api/cron":
        return dash._dashboard_cron_jobs()
    if path == "/api/config":
        return dash._redacted_config(config)
    if path == "/api/models":
        return dash._dashboard_models(config)
    if path == "/api/analytics":
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
    if path == "/api/mcp":
        servers = config.get("mcp.servers", {}) or {}
        return [{"name": n, "command": (s or {}).get("command", ""),
                 "args": (s or {}).get("args", [])} for n, s in servers.items()]
    if path == "/api/mcp/catalog":
        return dash._dashboard_mcp_catalog(
            config,
            live=(query.get("live", ["0"])[0] in {"1", "true", "yes"}),
        )
    if path == "/api/webhooks":
        from .webhook import WebhookStore

        return [{"name": w.name, "prompt": w.prompt} for w in WebhookStore().list()]
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=True)
    if path == "/api/plugins":
        from .plugins import list_manifests, load_plugins

        api = load_plugins(quiet=True, config=config)
        return {"loaded": [p.name for p in api.files],
                "errors": [{"file": f.name, "error": e} for f, e in api.errors],
                "tools": len(api.tools),
                "tool_names": sorted(getattr(t, "name", str(t)) for t in api.tools),
                "channels": sorted(api.channels),
                "providers": sorted(api.providers),
                "manifests": [m.to_dict() for m in list_manifests(config)]}
    if path == "/api/profiles":
        return dash._profiles(config)
    if path == "/api/system":
        return dash._system_info()
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
    if path == "/api/review":
        return dash._dashboard_review()
    if path == "/api/evals":
        return dash._dashboard_evals(config)
    if path == "/api/eval":
        return dash._dashboard_eval_detail(query, config)
    if path == "/api/logs":
        from . import config as cfg

        lp = cfg.logs_dir() / "aegis.log"
        lines = lp.read_text(errors="replace").splitlines()[-200:] if lp.exists() else []
        return {"path": str(lp), "lines": lines}
    if path == "/api/sessions":
        from .session import SessionStore

        return SessionStore().list(100)
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
    if path == "/api/tools":
        return dash._dashboard_tools(config)["tools"]
    return {"error": "not found"}


def _api_post(path: str, body: dict, config: Config, chat_runner: Any) -> dict:
    if path == "/api/kanban":
        from .kanban import KanbanStore

        ks = KanbanStore()
        act = body.get("action")
        if act == "create":
            t = ks.create((body.get("title") or "untitled").strip(), body.get("body", ""))
            return {"id": t.id}
        if act == "move" and body.get("id") and body.get("status") in (
            "ready", "in_progress", "done", "blocked"
        ):
            ks._set_status(body["id"], body["status"])
            return {"ok": True}
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
        from .cron import CronStore, build_delivery_sink, run_job

        cs = CronStore()
        act = body.get("action")
        if act == "add" and body.get("schedule") and body.get("prompt"):
            j = cs.add(body["schedule"], body["prompt"], body.get("channel", ""))
            return {"id": j.id}
        if act == "remove" and body.get("id"):
            return {"ok": cs.remove(body["id"])}
        if act == "toggle" and body.get("id"):
            return {"ok": cs.set_enabled(body["id"], bool(body.get("enabled", True)))}
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
    if path == "/api/models":
        from .providers import registry

        prov, model = body.get("provider"), body.get("model")
        target_provider = prov or config.get("model.provider")
        target_model = model or config.get("model.default")
        validation = registry.validate_model_choice(target_provider, target_model, config)
        if not validation.get("ok", True):
            return {"ok": False, "error": registry.model_validation_message(validation),
                    "validation": validation}
        if prov:
            config.set("model.provider", prov)
        if model:
            config.set("model.default", model)
        validation = registry.validate_model_choice(
            config.get("model.provider"), config.get("model.default"), config
        )
        return {"ok": True, "provider": config.get("model.provider"),
                "model": config.get("model.default"),
                "warning": registry.model_validation_message(validation),
                "validation": validation}
    if path == "/api/keys":
        from .config import set_env_var

        if body.get("key"):
            set_env_var(body["key"].strip(), body.get("value", ""))
            return {"ok": True}
        return {"error": "missing key"}
    if path == "/api/pairing":
        from .gateway.pairing import PairingStore

        ps = PairingStore()
        act, plat = body.get("action"), body.get("platform", "")
        if act == "approve" and body.get("code"):
            return {"ok": ps.approve(plat, body["code"])}
        if act == "revoke" and body.get("user_id"):
            return {"ok": ps.revoke(plat, body["user_id"])}
        return {"error": "bad pairing request"}
    if path == "/api/system":
        if body.get("action") == "backup":
            from .backup import create_backup

            return {"ok": True, "path": str(create_backup())}
        return {"error": "unknown system action"}
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
    if path == "/api/eval":
        if body.get("action") in {"run", "run_suite"}:
            return dash._dashboard_run_eval(body, config)
        return {"error": "bad eval request"}
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=False)
    if path == "/api/profiles":
        config.set("agent.personality", body.get("name") or "")
        return {"ok": True, "active": config.get("agent.personality")}
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
            parts = str(body["command"]).split()
            servers[body["name"]] = {"command": parts[0], "args": parts[1:]}
            config.data.setdefault("mcp", {})["servers"] = servers
            config.save()
            return {"ok": True}
        if act == "remove" and body.get("name") in servers:
            servers.pop(body["name"])
            config.data.setdefault("mcp", {})["servers"] = servers
            config.save()
            return {"ok": True}
        return {"error": "bad mcp request"}
    if path == "/api/plugins":
        act = body.get("action")
        name = str(body.get("name") or "").strip()
        try:
            from . import plugins as plugin_runtime

            if act == "install" and body.get("source"):
                installed = plugin_runtime.install(
                    str(body["source"]),
                    config,
                    force=bool(body.get("force", False)),
                )
                return {"ok": True, "name": installed}
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
    if path == "/api/files/mkdir":
        parent = Path(str(body.get("path") or Path.home())).expanduser().resolve()
        name = Path(str(body.get("name") or "")).name
        if not name:
            return {"ok": False, "error": "missing name"}
        target = parent / name
        try:
            target.mkdir(parents=bool(body.get("parents", False)), exist_ok=bool(body.get("exist_ok", False)))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(target)}
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

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.websocket("/api/ws")
    async def event_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        from .eventbus import BUS

        sub = BUS.subscribe()
        await ws.accept()
        loop = asyncio.get_running_loop()

        async def pump_events() -> None:
            idle_ticks = 0
            while True:
                try:
                    event = await loop.run_in_executor(None, lambda: sub.get(timeout=0.2))
                    idle_ticks = 0
                    await ws.send_json(event)
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= 75:
                        idle_ticks = 0
                        await ws.send_json({"type": "keepalive"})
                except Exception:
                    return

        writer = asyncio.create_task(pump_events())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("text") == "ping":
                    await ws.send_json({"type": "pong"})
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
        config.data = copy.deepcopy(raw)
        config.save()
        return JSONResponse({"ok": True, "config": copy.deepcopy(config.data)})

    @app.post("/api/config/import")
    async def api_config_import(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw = body.get("config", body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return JSONResponse({"ok": False, "error": "config object required"}, status_code=400)
        config.data = copy.deepcopy(raw)
        config.save()
        return JSONResponse({"ok": True, "config": dash._redacted_config(config)})

    @app.get("/api/env")
    async def api_env_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_env_list())

    @app.post("/api/env")
    async def api_env_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        key = str(body.get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "missing key"}, status_code=400)
        from .config import set_env_var

        set_env_var(key, str(body.get("value") or ""))
        return JSONResponse({"ok": True, "key": key})

    @app.get("/api/env/{key}/reveal")
    async def api_env_reveal(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        values = _env_file_values()
        if key not in values and key not in os.environ:
            return JSONResponse({"ok": False, "error": "key not set", "key": key}, status_code=404)
        return JSONResponse({"ok": True, "key": key, "value": values.get(key, os.environ.get(key, ""))})

    @app.delete("/api/env/{key}")
    async def api_env_delete(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"ok": _delete_env_key(key), "key": key})

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

    @app.get("/api/plugins")
    async def api_plugins_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_plugins_payload(config))

    @app.post("/api/plugins/install")
    async def api_plugins_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        source = str(body.get("source") or "").strip()
        if not source:
            return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
        try:
            from . import plugins as plugin_runtime

            name = plugin_runtime.install(source, config, force=bool(body.get("force", False)))
            return JSONResponse({"ok": True, "name": name, **_plugins_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/plugins/{name}/enable")
    async def api_plugin_enable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        ok = plugin_runtime.enable(name, config)
        return JSONResponse({"ok": ok, "name": name, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/plugins/{name}/disable")
    async def api_plugin_disable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        ok = plugin_runtime.disable(name, config)
        return JSONResponse({"ok": ok, "name": name, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.delete("/api/plugins/{name}")
    async def api_plugin_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from . import plugins as plugin_runtime

        ok = plugin_runtime.remove(name, config)
        return JSONResponse({"ok": ok, "name": name, **_plugins_payload(config)}, status_code=200 if ok else 404)

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
        return JSONResponse({"jobs": dash._dashboard_cron_jobs()})

    @app.post("/api/cron/jobs")
    async def api_cron_job_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not body.get("schedule") or not body.get("prompt"):
            return JSONResponse({"ok": False, "error": "schedule and prompt are required"}, status_code=400)
        from .cron import CronStore

        skills = body.get("skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        job = CronStore().add(
            str(body["schedule"]),
            str(body["prompt"]),
            name=str(body.get("name") or ""),
            channel=str(body.get("channel") or ""),
            script=str(body.get("script") or ""),
            skills=list(skills),
            deliver=str(body.get("deliver") or ""),
        )
        return JSONResponse({"ok": True, "id": job.id, "job": _cron_job_detail(job.id)["job"]})

    @app.get("/api/cron/jobs/{job_id}")
    async def api_cron_job_detail(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        detail = _cron_job_detail(job_id)
        return JSONResponse(detail, status_code=200 if detail.get("found") else 404)

    @app.patch("/api/cron/jobs/{job_id}")
    async def api_cron_job_patch(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        from .cron import CronStore

        updates = {key: body[key] for key in (
            "schedule", "prompt", "name", "channel", "enabled", "script", "skills", "deliver"
        ) if key in body}
        job = CronStore().update(job_id, **updates)
        if job is None:
            return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
        return JSONResponse({"ok": True, "id": job.id, "job": _cron_job_detail(job.id)["job"]})

    @app.delete("/api/cron/jobs/{job_id}")
    async def api_cron_job_delete(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .cron import CronStore

        ok = CronStore().remove(job_id)
        return JSONResponse({"ok": ok, "id": job_id}, status_code=200 if ok else 404)

    @app.post("/api/cron/jobs/{job_id}/run")
    async def api_cron_job_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .cron import CronStore, build_delivery_sink, run_job

        store = CronStore()
        if store.get(job_id) is None:
            return JSONResponse({"ok": False, "error": "cron job not found", "id": job_id}, status_code=404)
        sink = build_delivery_sink(config, verbose=False)
        return JSONResponse(run_job(config, job_id, sink=sink, store=store, verbose=False))

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

    @app.post("/api/gateway/service")
    async def api_gateway_service(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        action = str(body.get("action") or "status")
        from .daemon import (
            control_gateway_service,
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
            return JSONResponse(_service_result(control_gateway_service(action)))
        return JSONResponse({"ok": False, "error": f"unknown gateway service action: {action}"}, status_code=400)

    @app.get("/api/{path:path}")
    async def api_get(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get(f"/api/{path}", _query_dict(request), config))

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        _require_request(request, config)
        body = await request.json()
        events_q: queue.Queue[dict | object] = queue.Queue()
        sentinel = object()

        def worker() -> None:
            try:
                dash._dashboard_chat_stream(body, chat_runner, events_q.put)
            finally:
                events_q.put(sentinel)

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            while True:
                item = events_q.get()
                if item is sentinel:
                    break
                yield f"data: {json.dumps(item)}\n\n".encode()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/files/upload")
    async def upload_file(
        request: Request,
        file: Annotated[UploadFile, File()],
        path: Annotated[str, Form()] = "",
    ) -> JSONResponse:
        _require_request(request, config)
        target_dir = Path(path or Path.home()).expanduser().resolve()
        if not target_dir.is_dir():
            return JSONResponse({"ok": False, "error": "target is not a directory"})
        filename = Path(file.filename or "upload.bin").name
        target = target_dir / filename
        try:
            data = await file.read()
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse({"ok": True, "path": str(target), "size": target.stat().st_size})

    @app.post("/api/{path:path}")
    async def api_post(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_api_post(f"/api/{path}", body, config, chat_runner))

    @app.websocket("/api/pty")
    async def pty_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        await ws.accept()
        try:
            from .dashboard_pty import PtyBridge, dashboard_tui_argv

            bridge = PtyBridge.spawn(
                dashboard_tui_argv(ws.query_params.get("resume") or None),
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
    async def spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return _html_response(config)

    return app


def run_dashboard(config: Config, host: str, port: int, *, open_browser: bool = False) -> None:
    import socket
    import uvicorn

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
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
