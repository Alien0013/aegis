"""Local web dashboard — a self-contained single-page app (no build step).

`aegis dashboard` serves a control UI at http://127.0.0.1:9119: chat, sessions,
memory, skills, tools, and status. The frontend is a React+Vite app (web/, built to static/web_dist/);
the legacy single-file static/dashboard.html is the fallback
(shipped as package data — a plain file, no node toolchain) and talks to the
JSON API below. Binds loopback by default and can require the configured
dashboard token; do not expose it publicly without trusted network controls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from . import __version__
from .config import Config


def _page() -> bytes:
    """The SPA frontend, read from package data. Prefers the built React app
    (static/web_dist/index.html); falls back to the legacy single-file vanilla
    dashboard, then to a stub, so the API stays usable on a broken/partial install."""
    from importlib import resources
    for rel in ("static/web_dist/index.html", "static/dashboard.html"):
        try:
            return (resources.files("aegis") / rel).read_bytes()
        except Exception:  # noqa: BLE001
            continue
    return b"<h1>AEGIS</h1><p>dashboard frontend missing; API is at /api/*</p>"


_ASSET_TYPES = {".js": "text/javascript", ".css": "text/css", ".map": "application/json",
                ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml",
                ".png": "image/png", ".webp": "image/webp", ".ico": "image/x-icon",
                ".json": "application/json"}


def _asset(path: str) -> tuple[bytes, str] | None:
    """Serve a built React asset under /assets/<file> from the package, with a
    path-traversal guard. Returns (bytes, content_type) or None."""
    name = path.split("/assets/", 1)[-1]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    import os
    from importlib import resources
    try:
        data = (resources.files("aegis") / "static" / "web_dist" / "assets" / name).read_bytes()
    except Exception:  # noqa: BLE001
        return None
    ctype = _ASSET_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
    return data, ctype


def _dist_file(path: str) -> tuple[bytes, str] | None:
    """Serve public files copied by Vite into static/web_dist."""
    import os
    from importlib import resources
    rel = path.lstrip("/")
    if not rel or "\\" in rel or ".." in rel:
        return None
    try:
        data = (resources.files("aegis") / "static" / "web_dist" / rel).read_bytes()
    except Exception:  # noqa: BLE001
        return None
    ctype = _ASSET_TYPES.get(os.path.splitext(rel)[1], "application/octet-stream")
    return data, ctype


def _page_with_bootstrap(config: Config) -> bytes:
    page = _page()
    token = _dashboard_token(config) or ""
    bootstrap = (
        "<script>"
        f"window.__AEGIS_SESSION_TOKEN__={json.dumps(token)};"
        "</script>"
    ).encode()
    if b"</head>" in page:
        return page.replace(b"</head>", bootstrap + b"</head>", 1)
    return bootstrap + page





def _redacted_config(config: Config) -> dict:
    """Flattened config for the UI, with secret-looking values masked (never echo keys)."""
    import re as _re
    secret = _re.compile(
        r"(^|[._-])(api[_-]?key|token|secret|password|client[_-]?secret|"
        r"access[_-]?token|refresh[_-]?token)($|[._-])",
        _re.IGNORECASE,
    )
    out: dict[str, str] = {}

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            val = node
            if secret.search(prefix) and val:
                val = "••••••" + str(val)[-4:] if len(str(val)) > 4 else "••••••"
            out[prefix] = val

    walk("", getattr(config, "data", {}) or {})
    return out


_COMMON_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY",
    "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
    "NTFY_TOPIC", "TAVILY_API_KEY", "BRAVE_API_KEY",
]


def _env_keys() -> list:
    """Known + present env keys with set-status only — values are NEVER returned."""
    from . import config as cfg
    present = set()
    p = cfg.env_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                present.add(line.split("=", 1)[0].strip())
    names = list(dict.fromkeys(_COMMON_KEYS + sorted(present)))
    return [{"key": k, "set": k in present} for k in names]


def _profiles(config: Config) -> dict:
    """Available personality files (workspace/personalities/*.md) + the active one."""
    from . import config as cfg
    d = cfg.workspace_dir() / "personalities"
    names = sorted(p.stem for p in d.glob("*.md")) if d.exists() else []
    return {"active": config.get("agent.personality") or "", "available": names}


def _kanban_card(t, ks) -> dict:
    return {"id": t.id, "title": t.title, "body": t.body,
            "assignee": t.assignee, "priority": t.priority,
            "run_id": t.run_id, "session_id": t.session_id,
            "trace_id": t.trace_id, "tenant": t.tenant,
            "parents": ks.parents(t.id), "status": t.status,
            "created_at": getattr(t, "created_at", ""),
            "updated_at": getattr(t, "updated_at", "")}


def _dashboard_kanban(include_archived: bool = False) -> dict:
    """Full board payload. Each status maps to its cards (flat, e.g. ``["ready"]``);
    ``order`` lists the columns in board order and ``assignees`` / ``tenants`` /
    ``stats`` drive the dashboard's filters and lane grouping."""
    from .kanban import STATUSES, KanbanStore

    ks = KanbanStore()
    statuses = STATUSES if include_archived else tuple(s for s in STATUSES if s != "archived")
    out: dict = {}
    assignees: set[str] = set()
    tenants: set[str] = set()
    for s in statuses:
        cards = []
        for t in ks.list(status=s):
            cards.append(_kanban_card(t, ks))
            if t.assignee:
                assignees.add(t.assignee)
            if t.tenant:
                tenants.add(t.tenant)
        out[s] = cards
    out["order"] = list(statuses)
    out["assignees"] = sorted(assignees)
    out["tenants"] = sorted(tenants)
    out["stats"] = ks.stats()
    return out


def _dashboard_tools(config: Config) -> dict:
    from .tools.registry import default_registry

    reg = default_registry()
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    disabled = set(config.get("tools.disabled", []) or [])
    enabled = {t.name for t in reg.available(toolsets, only_usable=False, disabled=disabled)}
    rows = []
    for tool in reg.all():
        available, reason = tool.available()
        in_toolset = tool.toolset in toolsets or "all" in toolsets
        rows.append({
            "name": tool.name,
            "description": tool.description.splitlines()[0] if tool.description else "",
            "groups": list(tool.groups or []),
            "toolset": tool.toolset,
            "enabled": tool.name in enabled,
            "toolset_active": in_toolset,
            "off": tool.name in disabled,            # explicitly switched off (vs toolset inactive)
            "available": bool(available),
            "unavailable_reason": "" if available else str(reason),
            "schema": _jsonable(tool.schema()),
        })
    return {
        "toolsets": toolsets,
        "disabled": sorted(disabled),
        "deny_groups": list(config.get("tools.deny_groups", []) or []),
        "allowlist": list(config.get("tools.allowlist", []) or []),
        "tools": sorted(rows, key=lambda row: (row["toolset"], row["name"])),
    }


_TOOLSET_LABELS = {
    "browser": ("Browser Automation", "Browser interaction and UI verification tools."),
    "computer": ("Computer Control", "Desktop screen, mouse, and keyboard automation."),
    "core": ("Core Agent Tools", "Files, terminal, memory, skills, planning, messaging, and agent state."),
    "lsp": ("Language Server", "Code intelligence through language-server protocol tools."),
    "mcp": ("MCP Servers", "Tools exposed by configured Model Context Protocol servers."),
    "vision": ("Vision / Image Analysis", "Image understanding tools."),
    "voice": ("Voice", "Speech-to-text and text-to-speech tools."),
    "web": ("Web Extraction", "Web page extraction and summarization tools."),
}


def _toolset_label(name: str) -> str:
    return _TOOLSET_LABELS.get(name, (name.replace("_", " ").replace("-", " ").title(), ""))[0]


def _toolset_description(name: str, tools: list[dict]) -> str:
    if name in _TOOLSET_LABELS:
        return _TOOLSET_LABELS[name][1]
    if tools:
        return tools[0].get("description", "")
    return ""


def _dashboard_toolsets(config: Config) -> list[dict]:
    payload = _dashboard_tools(config)
    active = set(payload["toolsets"])
    grouped: dict[str, list[dict]] = {}
    for tool in payload["tools"]:
        grouped.setdefault(str(tool.get("toolset") or "core"), []).append(tool)
    for name in active:
        grouped.setdefault(name, [])
    rows = []
    for name, tools in sorted(grouped.items()):
        enabled_tools = [tool for tool in tools if tool.get("enabled")]
        available_tools = [tool for tool in tools if tool.get("available")]
        rows.append({
            "name": name,
            "label": _toolset_label(name),
            "description": _toolset_description(name, tools),
            "enabled": name in active or "all" in active,
            "available": bool(available_tools) or name in {"mcp"},
            "configured": name in active or "all" in active,
            "tools": sorted(tool["name"] for tool in tools),
            "enabled_tools": sorted(tool["name"] for tool in enabled_tools),
            "tool_count": len(tools),
            "enabled_count": len(enabled_tools),
        })
    return rows


def _dashboard_tool_toggle(body: dict, config: Config) -> dict:
    """Turn an individual tool on/off from the dashboard. Disabling adds the tool to the
    ``tools.disabled`` denylist; enabling removes it and, if the tool's toolset isn't active,
    activates that toolset so the switch actually makes the tool model-visible."""
    from .tools.registry import default_registry

    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "tool name required"}
    tool = default_registry().get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    enable = bool(body.get("enabled"))
    disabled = [t for t in (config.get("tools.disabled", []) or []) if t != name]
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    if enable:
        if tool.toolset not in toolsets and "all" not in toolsets:
            toolsets.append(tool.toolset)
            config.set("tools.toolsets", toolsets)
    else:
        disabled.append(name)
    config.set("tools.disabled", sorted(set(disabled)))
    return {"ok": True, "name": name, "enabled": enable, "toolsets": toolsets}


def _dashboard_toolset_toggle(body: dict, config: Config) -> dict:
    """Enable or disable a whole toolset (the bulk switch on each Tools group)."""
    name = str(body.get("toolset") or "").strip()
    if not name:
        return {"ok": False, "error": "toolset required"}
    enable = bool(body.get("enabled"))
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    if enable and name not in toolsets:
        toolsets.append(name)
    elif not enable:
        toolsets = [t for t in toolsets if t != name]
    config.set("tools.toolsets", toolsets)
    return {"ok": True, "toolset": name, "enabled": enable, "toolsets": toolsets}


def _dashboard_api_adapter_status(config: Config) -> dict:
    """Expose the Hermes-compatible API adapter's operational contract."""
    payload: dict[str, Any] = {
        "ok": True,
        "server": "aegis",
        "transport": "aiohttp",
        "stores": {},
        "errors": [],
    }
    try:
        from .server import _capabilities

        capabilities = _capabilities(config)
        payload.update({
            "object": capabilities.get("object"),
            "auth": capabilities.get("auth", {}),
            "limits": capabilities.get("limits", {}),
            "endpoints": capabilities.get("endpoints", {}),
            "features": capabilities.get("features", {}),
        })
    except Exception as exc:  # noqa: BLE001
        payload["ok"] = False
        payload["errors"].append(f"capabilities: {type(exc).__name__}: {exc}")
    try:
        from .server import ResponseStore

        payload["stores"]["responses"] = ResponseStore(config).stats()
    except Exception as exc:  # noqa: BLE001
        payload["ok"] = False
        payload["stores"]["responses"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .runs import RunStore

        rows = RunStore().list(limit=500)
        statuses: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
        payload["stores"]["runs"] = {"count": len(rows), "statuses": statuses}
    except Exception as exc:  # noqa: BLE001
        payload["ok"] = False
        payload["stores"]["runs"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .cron import CronStore

        jobs = CronStore().list()
        states: dict[str, int] = {}
        for job in jobs:
            state = str(getattr(job, "state", "") or "idle")
            states[state] = states.get(state, 0) + 1
        payload["stores"]["jobs"] = {"count": len(jobs), "states": states}
    except Exception as exc:  # noqa: BLE001
        payload["ok"] = False
        payload["stores"]["jobs"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return _jsonable(payload)


def _dashboard_status(config: Config) -> dict:
    from .session import SessionStore
    from .skills import SkillsLoader
    from .tools.registry import default_registry

    context_length = 0
    active_provider: dict = {}
    provider_error = ""
    try:
        from .providers.registry import provider_report

        report = provider_report(config)
        active_provider = report.get("active") if isinstance(report.get("active"), dict) else {}
        context_length = int(active_provider.get("context_length") or 0)
        provider_error = str(active_provider.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        provider_error = f"{type(exc).__name__}: {exc}"
    return {
        "version": __version__,
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "context_length": context_length,
        "provider_error": provider_error,
        "capabilities": _jsonable(active_provider.get("capabilities", {})),
        "api_adapter": _dashboard_api_adapter_status(config),
        "sessions": len(SessionStore().list(9999)),
        "skills": len(SkillsLoader(config).available()),
        "tools": len(default_registry().all()),
        "exec_mode": config.get("tools.exec_mode"),
        "toolsets": config.get("tools.toolsets", []),
        "reasoning_effort": config.get("agent.reasoning_effort"),
        "service_tier": config.get("agent.service_tier"),
        "reasoning_display": config.get("display.reasoning"),
        "busy_mode": config.get("gateway.busy_mode"),
        "learn": {
            "background": bool(config.get("learn.background", False)),
            "auto_apply": bool(config.get("learn.auto_apply", False)),
            "memory_every": int(config.get("learn.memory_every", 0) or 0),
            "flush_min_turns": int(config.get("learn.flush_min_turns", 0) or 0),
        },
    }


def _dashboard_review() -> dict:
    from .lsp.workspace import find_git_worktree

    cwd = Path.cwd().resolve()
    root = find_git_worktree(str(cwd))
    if not root:
        return {
            "available": False,
            "cwd": str(cwd),
            "root": "",
            "files": [],
            "diff_stat": "",
            "diff": "",
            "note": "Current directory is not a git worktree.",
        }
    root_path = Path(root)
    status = _git(root_path, "status", "--short") or ""
    name_status = _git(root_path, "diff", "--name-status") or ""
    staged_name_status = _git(root_path, "diff", "--cached", "--name-status") or ""
    files = []
    seen = set()
    for source, text in (("working", name_status), ("staged", staged_name_status)):
        for line in text.splitlines():
            if not line.strip():
                continue
            code, _, path = line.partition("\t")
            key = (path or code).strip()
            if not key or (source, key) in seen:
                continue
            seen.add((source, key))
            files.append({"path": key, "status": code.strip(), "source": source})
    return {
        "available": True,
        "cwd": str(cwd),
        "root": str(root_path),
        "branch": _git(root_path, "branch", "--show-current") or
        _git(root_path, "rev-parse", "--short", "HEAD") or "",
        "dirty": bool(status),
        "status": status.splitlines()[:200],
        "files": files[:200],
        "diff_stat": _git(root_path, "diff", "--stat") or "",
        "staged_diff_stat": _git(root_path, "diff", "--cached", "--stat") or "",
        "diff": (_git(root_path, "diff", "--no-ext-diff", "--unified=3") or "")[:40000],
    }


def _dashboard_recent_logs(limit: int = 80) -> dict:
    from . import config as _cfg

    log_dir = _cfg.logs_dir()
    lp = log_dir / "agent.log"
    if not lp.exists():
        lp = log_dir / "aegis.log"
    ep = log_dir / "errors.log"
    lines = lp.read_text(errors="replace").splitlines()[-limit:] if lp.exists() else []
    errors = ep.read_text(errors="replace").splitlines()[-20:] if ep.exists() else [
        line for line in lines if any(word in line.lower() for word in (
            "error", "exception", "traceback", "failed", "fatal",
        ))
    ][-20:]
    return {
        "path": str(lp),
        "lines": lines,
        "errors": errors,
        "files": {
            name: str(log_dir / name)
            for name in ("agent.log", "errors.log", "gateway.log", "gui.log", "aegis.log")
        },
    }


def _dashboard_memory_payload() -> dict:
    from .memory import MemoryStore

    ms = MemoryStore()

    def entries(raw: str) -> list[str]:
        return [part.strip() for part in (raw or "").split("§") if part.strip()]

    memory = ms.raw("memory")
    user = ms.raw("user")
    return {
        "memory": memory,
        "user": user,
        "memory_entries": entries(memory),
        "user_entries": entries(user),
    }


def _dashboard_cockpit(config: Config) -> dict:
    from . import ratelimit
    from .session import SessionStore
    from .usage_log import cost_report, daily_series

    analytics = cost_report(30, config)
    analytics["series"] = daily_series(30, config)
    analytics["balance"] = ratelimit.balance()
    sessions = SessionStore().list(40)
    latest_id = str((sessions[0] or {}).get("id", "")) if sessions else ""
    latest_session = _dashboard_session_detail(latest_id, config) if latest_id else {"found": False}
    return {
        "status": _dashboard_status(config),
        "analytics": analytics,
        "sessions": sessions,
        "latest_session": latest_session,
        "runs": _dashboard_runs({"limit": ["40"]}),
        "traces": _dashboard_traces({"limit": ["40"]}, config),
        "agents": _dashboard_agents(config),
        "kanban": _dashboard_kanban(),
        "tools": _dashboard_tools(config),
        "memory": _dashboard_memory_payload(),
        "projects": _dashboard_projects(),
        "worktrees": _dashboard_worktrees(),
        "review": _dashboard_review(),
        "system": _system_info(),
        "keys": _env_keys(),
        "plugins": _jsonable({
            "enabled": config.get("plugins.enabled", []) or [],
            "disabled": config.get("plugins.disabled", []) or [],
        }),
        "mcp": _jsonable(config.get("mcp.servers", {}) or {}),
        "profiles": _profiles(config),
        "logs": _dashboard_recent_logs(),
        "config": _redacted_config(config),
    }


def _system_info() -> dict:
    """Host + install facts and recent checkpoints for the System tab (no psutil dependency)."""
    import platform
    import shutil
    from . import __version__
    from . import config as cfg
    home = cfg.get_home()
    du = shutil.disk_usage(str(home))
    try:
        from .checkpoints import CheckpointStore
        cps = [{"id": c.id, "label": c.label, "at": getattr(c, "created_at", "")}
               for c in CheckpointStore().list()[:20]]
    except Exception:  # noqa: BLE001
        cps = []
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} ({platform.machine()})",
        "aegis_home": str(home),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "checkpoints": cps,
    }


def _ops_status(config: Config) -> dict:
    """Operational state for the System → Operations panel: curator schedule, memory-store
    sizes, and (when systemd user services are present) gateway/cron service status."""
    from .memory import MemoryStore
    curator_state: dict = {}
    try:
        from .curator import _load_state
        curator_state = _load_state() or {}
    except Exception:  # noqa: BLE001
        curator_state = {}
    store = MemoryStore()
    memory: dict[str, dict] = {}
    for target in ("memory", "user"):
        try:
            memory[target] = {"chars": len(store.raw(target)), "entries": len(store.entries(target))}
        except Exception:  # noqa: BLE001
            memory[target] = {"chars": 0, "entries": 0}
    services: dict = {"systemd": False}
    try:
        from . import daemon
        if daemon.systemd_available():
            services = {
                "systemd": True,
                "gateway": daemon.gateway_service_status(),
                "cron": daemon.cron_service_status(),
            }
    except Exception:  # noqa: BLE001
        services = {"systemd": False}
    return {
        "version": __version__,
        "curator": {
            "enabled": bool(config.get("curator.enabled", True)),
            "interval_hours": float(config.get("curator.interval_hours", 168) or 168),
            "last_run_at": curator_state.get("last_run_at", ""),
        },
        "memory": memory,
        "services": services,
    }


def _reset_memory_file(target: str) -> dict:
    """Back up MEMORY.md / USER.md to a timestamped sibling, then truncate it. Destructive —
    the dashboard gates this behind a typed confirmation."""
    from datetime import datetime, timezone

    from .memory import MemoryStore
    if target not in ("memory", "user"):
        return {"ok": False, "error": "target must be 'memory' or 'user'"}
    store = MemoryStore()
    path = store._path(target)
    prev = store.raw(target)
    if not prev:
        return {"ok": True, "target": target, "backup": "", "note": "already empty"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
    try:
        backup_path.write_text(prev, encoding="utf-8")
        path.write_text("", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "target": target, "backup": str(backup_path)}


def _update_check() -> dict:
    """Best-effort update status: installed version, package location, and git distance.

    Shallow checkouts compare fetched commit SHAs instead of asking git for a bogus
    rev-list count across the shallow boundary.
    """
    import subprocess
    pkg_dir = Path(__file__).resolve().parent.parent
    info: dict = {"version": __version__, "path": str(pkg_dir)}

    def _git(*args: str) -> str:
        try:
            r = subprocess.run(["git", "-C", str(pkg_dir), *args],
                               capture_output=True, text=True, timeout=2.0, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""

    if _git("rev-parse", "--is-inside-work-tree") == "true":
        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        shallow = _git("rev-parse", "--is-shallow-repository") == "true"
        behind = ahead = 0
        commit_count_available = True
        update_available = False
        if upstream and shallow:
            upstream_branch = upstream.split("/", 1)[1] if upstream.startswith("origin/") else branch
            if upstream_branch:
                _git("fetch", "--quiet", "--depth", "1", "origin", upstream_branch)
            head_sha = _git("rev-parse", "HEAD")
            upstream_sha = _git("rev-parse", upstream)
            if head_sha and upstream_sha and head_sha != upstream_sha:
                behind = None
                ahead = None
                update_available = True
            commit_count_available = False
        elif upstream:
            counts = _git("rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
            if len(counts) == 2:
                behind, ahead = int(counts[0]), int(counts[1])
                update_available = behind > 0
        info.update({"install": "git", "branch": branch, "upstream": upstream,
                     "behind": behind, "ahead": ahead,
                     "commit_count_available": commit_count_available,
                     "update_available": update_available,
                     "hint": f"git -C {pkg_dir} pull" if update_available else "up to date with fetched upstream"})
    else:
        info.update({"install": "package", "update_available": None,
                     "hint": "pip install -U aegis (or your package manager)"})
    return info


def _ops_action(action: str, body: dict, config: Config) -> dict:
    """Execute a System → Operations action. Destructive ones (memory/user reset) are gated by
    a typed confirmation on the client; service control needs systemd user units."""
    if action == "curator_run":
        from .curator import run
        res = run(config, dry_run=False)
        keep = {k: res[k] for k in ("promoted", "archived", "deleted", "report", "llm_review")
                if k in res}
        return {"ok": True, "result": keep}
    if action in ("curator_pause", "curator_resume"):
        enabled = action == "curator_resume"
        config.set("curator.enabled", enabled)
        return {"ok": True, "enabled": enabled}
    if action == "backup":
        from .backup import create_backup
        return {"ok": True, "path": str(create_backup())}
    if action == "memory_reset":
        return _reset_memory_file("memory")
    if action == "user_reset":
        return _reset_memory_file("user")
    if action in ("gateway", "cron"):
        op = str(body.get("op") or "").strip()
        if op not in {"start", "stop", "restart"}:
            return {"ok": False, "error": "op must be start, stop, or restart"}
        from . import daemon
        if not daemon.systemd_available():
            return {"ok": False, "error": "systemd user services are not available on this host"}
        ctl = daemon.control_gateway_service if action == "gateway" else daemon.control_cron_service
        r = ctl(op)
        return {"ok": bool(r.ok), "message": r.message}
    if action == "update_check":
        return _update_check()
    if action == "doctor":
        from .cli.main import cmd_doctor
        return _capture_cli(lambda: cmd_doctor(type("A", (), {"fix": False})(), config))
    if action == "security_audit":
        from .ops import cmd_security_audit
        return _capture_cli(lambda: cmd_security_audit(type("A", (), {"fail_on": None})(), config))
    return {"error": f"unknown operation: {action}"}


def _config_raw(config: Config) -> dict:
    """The effective config serialized to YAML text + path, for the YAML editor mode. Serializing
    config.data (not the on-disk file) means the editor shows the live config even before any
    overrides have been written to disk."""
    import yaml

    from . import config as cfg
    return {"path": str(cfg.config_path()), "raw": yaml.safe_dump(config.data, sort_keys=False)}


def _config_backup_now() -> dict:
    """Copy the current config.yaml to a timestamped .bak sibling. Returns the backup path."""
    from datetime import datetime, timezone

    from . import config as cfg
    from .util import atomic_write, read_text
    path = cfg.config_path()
    raw = read_text(path)
    if not raw.strip():
        return {"ok": True, "backup": "", "note": "config is empty"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.stem}.{stamp}.yaml.bak")
    atomic_write(bak, raw)
    return {"ok": True, "backup": str(bak)}


def _config_write_raw(text: str, config: Config) -> dict:
    """Validate YAML, back up the current file, then atomically write the new config. Keeps the
    in-memory config in sync so the next read reflects the save."""
    import yaml

    from . import config as cfg
    try:
        parsed = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return {"ok": False, "error": f"invalid YAML: {exc}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "top-level YAML must be a mapping"}
    type_errors = cfg.config_type_errors(parsed)
    if type_errors:
        return {"ok": False, "error": "config type validation failed: " + "; ".join(type_errors)}
    backup = _config_backup_now().get("backup", "")
    config.data = cfg._deep_merge(cfg.DEFAULT_CONFIG, parsed)
    from .util import atomic_write

    delta = cfg._config_delta(config.data, cfg.DEFAULT_CONFIG)
    atomic_write(cfg.config_path(), cfg._dump_config_delta(delta, comment_source=text))
    return {"ok": True, "backup": backup}


def _config_reset_section(section: str, config: Config) -> dict:
    """Reset one top-level config section to its default (or drop it when there's no default).
    Backs up first."""
    import copy

    from .config import DEFAULT_CONFIG
    section = (section or "").strip()
    if not section:
        return {"ok": False, "error": "section required"}
    backup = _config_backup_now().get("backup", "")
    default = DEFAULT_CONFIG.get(section)
    if default is None:
        config.data.pop(section, None)
    else:
        config.data[section] = copy.deepcopy(default)
    config.save()
    return {"ok": True, "section": section, "backup": backup}


def _system_stats() -> dict:
    """Live host stats for the System page — CPU/RAM/disk/uptime/load — read from os + /proc
    so there's no psutil dependency. Fields degrade to absent on non-Linux."""
    import os
    import platform
    import shutil

    from . import config as cfg
    home = cfg.get_home()
    du = shutil.disk_usage(str(home))
    out: dict = {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "host": platform.node(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
        "disk_used_gb": round((du.total - du.free) / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "disk_percent": round(100 * (du.total - du.free) / du.total, 1) if du.total else 0,
    }
    try:
        out["load_avg"] = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        pass
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, val = line.partition(":")
            mem[key.strip()] = int(val.strip().split()[0])      # kB
        total = mem.get("MemTotal", 0) / 1e6                     # GB
        avail = mem.get("MemAvailable", 0) / 1e6
        if total:
            out.update(mem_total_gb=round(total, 1), mem_used_gb=round(total - avail, 1),
                       mem_percent=round(100 * (total - avail) / total, 1))
    except (OSError, ValueError):
        pass
    try:
        secs = int(float(Path("/proc/uptime").read_text().split()[0]))
        days, rem = divmod(secs, 86400)
        hours, rem = divmod(rem, 3600)
        out["uptime"] = f"{days}d {hours}h {rem // 60}m" if days else f"{hours}h {rem // 60}m"
    except (OSError, ValueError):
        pass
    return out


def _capture_cli(fn) -> dict:
    """Run a CLI command function, capturing its stdout into an ``output`` string for the
    dashboard's action console."""
    import contextlib
    import io

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = fn()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "output": buf.getvalue()}
    return {"ok": rc in (None, 0), "output": buf.getvalue()}


def _int_param(query: dict, name: str, default: int, *, lo: int = 1, hi: int = 200) -> int:
    try:
        n = int((query.get(name, [str(default)])[0]) or default)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _str_param(query: dict, *names: str) -> str:
    for name in names:
        value = query.get(name, [""])
        raw = value[0] if isinstance(value, list) and value else value
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()
                if k != "agent" and not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _session_metrics(sess) -> dict:
    counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    for m in sess.messages:
        counts[m.role] = counts.get(m.role, 0) + 1
        for tc in getattr(m, "tool_calls", []) or []:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
    goal = sess.meta.get("goal") if isinstance(sess.meta, dict) else None
    comps = sess.meta.get("compactions", []) if isinstance(sess.meta, dict) else []
    return {
        "messages": len(sess.messages),
        "roles": counts,
        "tool_calls": sum(tool_counts.values()),
        "tools": [{"name": name, "calls": n} for name, n in sorted(tool_counts.items())],
        "compactions": len(comps) if isinstance(comps, list) else 0,
        "goal": {
            "text": goal.get("text", ""),
            "status": goal.get("status", ""),
            "turns_used": goal.get("turns_used", 0),
            "max_turns": goal.get("max_turns", 0),
        } if isinstance(goal, dict) and goal.get("text") else None,
    }


def _recent_sessions(limit: int = 50) -> list[dict]:
    from .session import SessionStore
    store = SessionStore()
    out = []
    for meta in store.list(limit):
        sess = store.load(meta["id"])
        if sess is None:
            continue
        out.append({**meta, **_session_metrics(sess),
                    "summary": sess.meta.get("summary", "") if isinstance(sess.meta, dict) else "",
                    "runtime": _jsonable(sess.meta.get("runtime", {})) if isinstance(sess.meta, dict) else {},
                    "usage": _jsonable(sess.meta.get("usage", {})) if isinstance(sess.meta, dict) else {}})
    return out


def _trace_db_path(config: Config | None = None) -> Path:
    from . import config as cfg
    raw = config.get("tracing.path", "traces.db") if config else "traces.db"
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else cfg.sub(str(raw))


def _eval_db_path(config: Config | None = None) -> Path:
    from . import config as cfg
    raw = str(config.get("evals.path", "evals") if config else "evals")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cfg.sub(raw)
    return path if path.suffix else path / "runs.db"


def _normalize_trace_row(row: dict) -> dict:
    if "trace_id" not in row:
        return row
    raw_spans = row.get("spans")
    raw_span_summary = raw_spans if isinstance(raw_spans, dict) else {}
    span_count = int(row.get("span_count") or raw_span_summary.get("span_count")
                     or raw_span_summary.get("messages") or (raw_spans if isinstance(raw_spans, int) else 0) or 0)
    kind_counts = row.get("kind_counts") if isinstance(row.get("kind_counts"), dict) else {}
    provider_calls = int(row.get("provider_calls") or raw_span_summary.get("provider_calls")
                         or kind_counts.get("provider_call") or kind_counts.get("model") or 0)
    tool_calls = int(row.get("tool_calls") or raw_span_summary.get("tool_calls") or kind_counts.get("tool") or 0)
    compactions = int(row.get("compactions") or raw_span_summary.get("compactions")
                      or kind_counts.get("compaction") or kind_counts.get("compact") or 0)
    errors = int(row.get("error_spans") or raw_span_summary.get("errors") or 0)
    return {
        **row,
        "id": row.get("trace_id"),
        "title": row.get("trace_id"),
        "source": "trace_store",
        "updated_at": row.get("ended_at") or row.get("started_at") or "",
        "spans": {
            "messages": span_count,
            "span_count": span_count,
            "provider_calls": provider_calls,
            "tool_calls": tool_calls,
            "compactions": compactions,
            "errors": errors,
        },
        "providers": row.get("providers") or [],
        "models": row.get("models") or [],
        "tools": row.get("tools") or [],
        "duration_ms": int(row.get("duration_ms") or row.get("latency_ms") or 0),
        "latency_ms": int(row.get("latency_ms") or row.get("duration_ms") or 0),
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "cache_read": int(row.get("cache_read") or 0),
        "cache_write": int(row.get("cache_write") or 0),
        "cost": float(row.get("cost") or 0),
    }


def _runtime_trace_rows(
    limit: int,
    config: Config | None = None,
    *,
    session_id: str = "",
) -> tuple[str, list[dict]] | None:
    """Use a future runtime trace module if it is installed; otherwise return None."""
    import importlib

    for modname in ("aegis.tracing", "aegis.runtime.traces", "aegis.runtime_trace", "aegis.traces"):
        try:
            mod = importlib.import_module(modname)
        except Exception:  # noqa: BLE001 - optional integration point
            continue
        store_cls = getattr(mod, "TraceStore", None)
        if store_cls is not None:
            path = _trace_db_path(config)
            if not path.exists():
                continue
            try:
                rows = store_cls(path).list_traces(session_id=session_id or None, limit=limit)
            except Exception:  # noqa: BLE001
                rows = []
            return modname, [_normalize_trace_row(_jsonable(r)) for r in list(rows or [])[:limit]]
        for name in ("list_traces", "recent_traces", "list"):
            fn = getattr(mod, name, None)
            if not callable(fn):
                continue
            try:
                rows = fn(limit=limit)
            except TypeError:
                rows = fn(limit)
            except Exception:  # noqa: BLE001
                rows = []
            if isinstance(rows, dict):
                rows = rows.get("traces") or rows.get("items") or rows.get("spans") or []
            return modname, [_jsonable(r) for r in list(rows or [])[:limit]]
    return None


def _dashboard_traces(query: dict, config: Config | None = None) -> dict:
    limit = _int_param(query, "limit", 50)
    session_id = _str_param(query, "session_id", "session")
    status = _str_param(query, "status")
    source_filter = _str_param(query, "source")
    runtime = _runtime_trace_rows(limit, config, session_id=session_id)
    if runtime is not None:
        source, rows = runtime
        rows = _filter_trace_rows(rows, status=status, source=source_filter)
        return {"available": True, "source": source, "traces": rows,
                "summary": {"total": len(rows), "source": source,
                            "filters": {"session_id": session_id, "status": status,
                                        "source": source_filter}}}
    rows = []
    for s in _recent_sessions(limit):
        if session_id and session_id not in str(s.get("id", "")):
            continue
        rows.append({
            "id": s["id"],
            "title": s.get("title", ""),
            "source": "session",
            "started_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "status": "goal_active" if (s.get("goal") or {}).get("status") == "active" else "recorded",
            "spans": {
                "messages": s.get("messages", 0),
                "span_count": s.get("messages", 0),
                "provider_calls": 0,
                "tool_calls": s.get("tool_calls", 0),
                "compactions": s.get("compactions", 0),
                "errors": 0,
            },
            "tools": s.get("tools", []),
            "providers": [s.get("runtime", {}).get("provider")] if s.get("runtime", {}).get("provider") else [],
            "models": [s.get("runtime", {}).get("model")] if s.get("runtime", {}).get("model") else [],
            "cache_read": int(s.get("usage", {}).get("cache_read", 0) or 0),
            "cache_write": int(s.get("usage", {}).get("cache_write", 0) or 0),
        })
    rows = _filter_trace_rows(rows, status=status, source=source_filter)
    try:
        from .trajectory import stats
        summary = stats()
    except Exception:  # noqa: BLE001
        summary = {"trajectories": len(rows)}
    summary["filters"] = {"session_id": session_id, "status": status, "source": source_filter}
    return {"available": bool(rows), "source": "sessions", "traces": rows, "summary": summary,
            "note": "Runtime trace store not present; showing session-derived traces."}


def _filter_trace_rows(rows: list[dict], *, status: str = "", source: str = "") -> list[dict]:
    status_l = status.lower()
    source_l = source.lower()
    filtered: list[dict] = []
    for row in rows:
        if status_l and status_l not in str(row.get("status", "")).lower():
            continue
        if source_l and source_l not in str(row.get("source", "")).lower():
            continue
        filtered.append(row)
    return filtered


def _dashboard_runs(query: dict) -> dict:
    limit = _int_param(query, "limit", 50)
    surface = _str_param(query, "surface")
    status = _str_param(query, "status")
    session_id = _str_param(query, "session_id", "session")
    try:
        from .runs import RunStore
        rows = RunStore().list(
            limit=limit,
            surface=surface or None,
            status=status or None,
            session_id=session_id or None,
        )
        if rows:
            return {"runs": [_dashboard_run_row(r) for r in rows],
                    "summary": {"total": len(rows), "source": "runs",
                                "filters": {"surface": surface, "status": status,
                                            "session_id": session_id}}}
    except Exception:  # noqa: BLE001
        pass
    runs = []
    for s in _recent_sessions(limit):
        goal = s.get("goal") or {}
        runs.append({
            "id": s["id"],
            "kind": "session",
            "title": s.get("title", ""),
            "status": "active_goal" if goal.get("status") == "active" else "completed",
            "started_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "turns": s.get("roles", {}).get("user", 0),
            "messages": s.get("messages", 0),
            "tool_calls": s.get("tool_calls", 0),
            "summary": s.get("summary", ""),
        })
    try:
        from .background import get_manager
        for t in get_manager().list():
            runs.append({"id": t["id"], "kind": "background", "title": t["prompt"],
                         "status": t["status"], "preview": t.get("result_preview", "")})
    except Exception:  # noqa: BLE001
        pass
    return {"runs": runs[:limit], "summary": {"total": len(runs)}}


def _dashboard_run_row(run: dict) -> dict:
    return {
        "id": run.get("id", ""),
        "kind": run.get("kind", ""),
        "surface": run.get("surface", ""),
        "title": run.get("title", "") or run.get("prompt_preview", "")[:60],
        "status": run.get("status", ""),
        "started_at": run.get("started_at", ""),
        "updated_at": run.get("ended_at") or run.get("started_at", ""),
        "ended_at": run.get("ended_at", ""),
        "session_id": run.get("session_id", ""),
        "trace_id": run.get("trace_id", ""),
        "summary": run.get("result_preview", ""),
        "preview": run.get("prompt_preview", ""),
        "error": run.get("error", ""),
        "data": run.get("data", {}),
    }


def _latest_trace_id_for_session(config: Config | None, session_id: str) -> str:
    if not session_id:
        return ""
    try:
        from .tracing import TraceStore
        store = TraceStore.from_config(config or Config.load())
        traces = store.list_traces(session_id=session_id, limit=1)
    except Exception:  # noqa: BLE001
        return ""
    if not traces:
        return ""
    return str(traces[0].get("trace_id") or traces[0].get("id") or "")


def _dashboard_agent_from_run(run: dict, config: Config | None = None) -> dict:
    row = _dashboard_run_row(run)
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    trace_id = row.get("trace_id") or _latest_trace_id_for_session(config, row.get("session_id", ""))
    return {
        "id": row["id"],
        "kind": "active_run" if row.get("status") == "running" else "run",
        "type": row.get("surface") or row.get("kind") or "agent",
        "status": row.get("status") or "recorded",
        "task": row.get("title") or row.get("preview") or "",
        "preview": row.get("summary") or row.get("preview") or "",
        "run_id": row["id"],
        "session_id": row.get("session_id", ""),
        "trace_id": trace_id,
        "surface": row.get("surface", ""),
        "model": data.get("model") or "",
        "provider": data.get("provider") or "",
        "started_at": row.get("started_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def _dashboard_active_run_agents(config: Config) -> list[dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(limit=50, status="running")
    except Exception:  # noqa: BLE001
        return []
    return [_dashboard_agent_from_run(row, config) for row in rows]


def _task_target(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("goal", "task", "prompt", "description"):
            val = value.get(key)
            if val:
                return str(val).strip()
    return ""


def _tool_target(args: dict) -> str:
    """Human-readable one-liner for what a tool call is about to do."""
    if not isinstance(args, dict):
        return ""
    tasks = args.get("tasks")
    if isinstance(tasks, list):
        goals = []
        for item in tasks:
            goal = _task_target(item)
            if goal:
                goals.append(goal[:40])
        if goals:
            return f"{len(tasks)} tasks: {' | '.join(goals)}"[:240]
        return f"{len(tasks)} parallel tasks"[:240]
    task = _task_target(args.get("task"))
    if task:
        return task[:240]
    for key in ("command", "path", "file_path", "url", "query", "pattern", "prompt", "name"):
        val = args.get(key)
        if val:
            return str(val)[:240]
    return ""


def _chat_event_row(event: dict) -> dict:
    """Project a raw loop event into the compact, JSON-safe row the dashboard
    Chat page renders. Carries the streaming text and thinking so the bubble can
    show the agent's words and reasoning as they arrive, and a stable ``id`` so
    tool_start/tool_result can be paired into a single live activity card."""
    etype = str(event.get("type") or "")
    row = {
        "type": etype,
        "id": str(event.get("id") or ""),
        "name": str(event.get("name") or event.get("tool_name") or ""),
        "summary": str(event.get("summary") or event.get("message") or event.get("reason") or ""),
        "status": "error" if event.get("is_error") else str(event.get("status") or ""),
    }
    if etype in ("assistant_delta", "reasoning_delta", "assistant_message"):
        row["text"] = str(event.get("text") or "")
    elif etype == "tool_start":
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        row["target"] = _tool_target(args)
    elif etype == "tool_result":
        row["target"] = str(event.get("preview") or event.get("summary") or "")[:240]
        if not row["status"]:
            row["status"] = "ok"
    elif etype == "iteration":
        row["n"] = event.get("n")
        row["max"] = event.get("max")
        row["summary"] = f"{event.get('n', '')}/{event.get('max', '')}".strip("/")
    elif etype in ("subagent_start", "subagent_done"):
        # Spawned subagents: surface the task + agent type so the UI can show each
        # one as its own live card (and so the Agents page can track them).
        row["task"] = str(event.get("task") or event.get("summary") or "")[:240]
        row["agent_type"] = str(event.get("agent_type") or event.get("type_name") or "")
        if not row["status"] and etype == "subagent_start":
            row["status"] = "running"
    elif etype in ("subagent_text", "subagent_reasoning"):
        row["text"] = str(event.get("text") or "")
        row["subagent_id"] = str(event.get("subagent_id") or event.get("id") or "")
        row["task"] = str(event.get("task") or event.get("summary") or "")[:240]
        row["agent_type"] = str(event.get("agent_type") or event.get("type_name") or "")
        if not row["status"]:
            row["status"] = "running"
    return row


def _dashboard_chat_cwd(body: dict) -> str:
    raw = body.get("cwd") or body.get("project") or body.get("worktree") or ""
    text = str(raw).strip()
    return str(Path(text).expanduser()) if text else ""


_REASONING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")


def _dashboard_chat_meta(body: dict, route: str) -> dict:
    cwd = _dashboard_chat_cwd(body)
    meta = {"surface_route": route}
    if cwd:
        meta["dashboard_cwd"] = cwd
    controls: dict[str, str] = {}

    # A reasoning choice from the Chat toggle becomes a session runtime control so
    # the agent streams (or stops streaming) live thinking for this turn onward.
    reasoning = str(body.get("reasoning") or "").strip().lower()
    if reasoning in _REASONING_LEVELS:
        controls["reasoning_effort"] = reasoning
        controls["reasoning_display"] = "live" if reasoning != "off" else "off"

    # Dashboard/desktop composer choices are session runtime controls, not
    # profile-default writes. Persisting them here keeps resume/reconnect truthful
    # after a live or new-chat model switch.
    model = str(body.get("model") or "").strip()
    provider = str(body.get("provider") or body.get("provider_name") or "").strip()
    if model:
        controls["model"] = model
    if provider:
        controls["provider"] = provider

    service_tier = str(body.get("service_tier") or "").strip()
    if not service_tier and "fast" in body:
        fast_value = body.get("fast")
        if isinstance(fast_value, bool):
            service_tier = "priority" if fast_value else "normal"
        else:
            service_tier = str(fast_value or "").strip()
    if service_tier:
        from .surface import normalize_service_tier
        normalized_tier = normalize_service_tier(service_tier)
        if normalized_tier:
            controls["service_tier"] = normalized_tier

    if controls:
        from .surface import runtime_controls_meta
        meta.update(runtime_controls_meta(controls))
    return meta


def _dashboard_chat_runtime(body: dict) -> dict:
    model = str(body.get("model") or "").strip()
    provider = str(body.get("provider") or body.get("provider_name") or "").strip()
    service_tier = str(body.get("service_tier") or "").strip()
    if not service_tier and "fast" in body:
        fast_value = body.get("fast")
        if isinstance(fast_value, bool):
            service_tier = "priority" if fast_value else "normal"
        else:
            service_tier = str(fast_value or "").strip()
    out = {}
    if model:
        out["model"] = model
    if provider:
        out["provider_name"] = provider
    if service_tier:
        from .surface import normalize_service_tier
        normalized_tier = normalize_service_tier(service_tier)
        if normalized_tier:
            out["service_tier"] = normalized_tier
    return out


def _publish_dashboard_chat_event(
    body: dict,
    etype: str,
    *,
    row: dict | None = None,
    result: Any | None = None,
    cwd: str = "",
) -> None:
    try:
        from .eventbus import BUS

        session_id = str(
            getattr(getattr(result, "session", None), "id", "")
            or body.get("session_id")
            or ""
        )
        event = {
            "platform": "dashboard",
            "chat_id": session_id,
            "session_id": session_id,
            "type": etype,
            "surface": "dashboard",
            "cwd": cwd,
        }
        if row:
            event.update({k: v for k, v in row.items() if v not in ("", None)})
            event["type"] = str(row.get("type") or etype)
        if result is not None:
            for attr, key in (("run_id", "run_id"), ("trace_id", "trace_id"), ("turn_id", "turn_id")):
                value = str(getattr(result, attr, "") or "")
                if value:
                    event[key] = value
        if etype == "chat_start":
            event["text"] = str(body.get("message", ""))[:240]
        elif etype == "chat_final":
            event["text"] = str(getattr(result, "text", "") or "")[:240]
        BUS.publish(event)
    except Exception:  # noqa: BLE001
        pass


def _dashboard_chat_response(body: dict, chat_runner) -> dict:
    events: list[dict] = []
    result = None
    cwd = _dashboard_chat_cwd(body)
    _publish_dashboard_chat_event(body, "chat_start", cwd=cwd)

    def on_event(event: dict) -> None:
        events.append(dict(event))
        _publish_dashboard_chat_event(body, str(event.get("type") or "event"),
                                      row=_chat_event_row(event), cwd=cwd)

    try:
        result = chat_runner.run_prompt(
            body.get("message", ""),
            session_id=body.get("session_id") or None,
            cwd=cwd or None,
            **_dashboard_chat_runtime(body),
            surface="dashboard",
            meta=_dashboard_chat_meta(body, "/api/chat"),
            on_event=on_event,
        )
        reply = result.text
        _publish_dashboard_chat_event(body, "chat_final", result=result, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        reply = f"error: {e}"
        _publish_dashboard_chat_event(body, "chat_error", cwd=cwd,
                                      row={"type": "chat_error", "summary": str(e)})
    return {
        "reply": reply or "(no response)",
        "session_id": result.session.id if result else (body.get("session_id") or ""),
        "trace_id": result.trace_id if result else "",
        "turn_id": result.turn_id if result else "",
        "run_id": result.run_id if result else "",
        "cwd": cwd,
        "events": [_chat_event_row(e) for e in events[-80:]],
    }


def _mark_dashboard_cancelled_run(result: Any, reason: str = "client disconnected") -> None:
    run_id = str(getattr(result, "run_id", "") or "")
    if not run_id:
        return
    try:
        from .runs import RunStore

        RunStore().finish(
            run_id,
            status="cancelled",
            trace_id=str(getattr(result, "trace_id", "") or ""),
            result="[cancelled]",
            error=reason,
            data={"cancelled": True, "cancel_reason": reason},
        )
    except Exception:  # noqa: BLE001
        pass


def _dashboard_chat_stream(
    body: dict,
    chat_runner,
    send,
    *,
    on_agent=None,
    cancel_event=None,
    meta_route: str = "/api/chat/stream",
) -> dict:
    events: list[dict] = []
    result = None
    cwd = _dashboard_chat_cwd(body)
    _publish_dashboard_chat_event(body, "chat_start", cwd=cwd)
    send({"type": "start", "session_id": body.get("session_id") or "", "cwd": cwd})

    def on_event(event: dict) -> None:
        events.append(dict(event))
        row = _chat_event_row(event)
        _publish_dashboard_chat_event(body, str(event.get("type") or "event"), row=row, cwd=cwd)
        send({"type": "event", "event": row})

    try:
        runtime = _dashboard_chat_runtime(body)
        meta = _dashboard_chat_meta(body, meta_route)
        run_kwargs = {
            "cwd": cwd or None,
            **runtime,
            "surface": "dashboard",
            "meta": meta,
            "on_event": on_event,
        }
        if (
            on_agent is not None
            and hasattr(chat_runner, "load_or_create_session")
            and hasattr(chat_runner, "make_agent")
        ):
            session = chat_runner.load_or_create_session(
                body.get("session_id") or None,
                surface="dashboard",
                meta=meta,
            )
            agent = chat_runner.make_agent(
                session=session,
                cwd=cwd or None,
                model=runtime.get("model"),
                provider_name=runtime.get("provider_name"),
            )
            on_agent(agent)
            if cancel_event is not None and cancel_event.is_set():
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                elif getattr(agent, "cancel_event", None) is not None:
                    agent.cancel_event.set()
            run_kwargs.update({"session": session, "agent": agent, "reuse_agent": False})
        else:
            run_kwargs["session_id"] = body.get("session_id") or None
        result = chat_runner.run_prompt(
            body.get("message", ""),
            **run_kwargs,
        )
        if cancel_event is not None and cancel_event.is_set():
            _mark_dashboard_cancelled_run(result)
            final = {
                "type": "cancelled",
                "reply": "cancelled",
                "cancelled": True,
                "session_id": result.session.id,
                "trace_id": result.trace_id,
                "turn_id": result.turn_id,
                "run_id": result.run_id,
                "cwd": cwd,
                "events": [_chat_event_row(e) for e in events[-80:]],
            }
            _publish_dashboard_chat_event(
                body,
                "chat_cancelled",
                cwd=cwd,
                row={"type": "chat_cancelled", "summary": "client disconnected"},
            )
            send(final)
            return final
        final = {
            "type": "final",
            "reply": result.text or "(no response)",
            "session_id": result.session.id,
            "trace_id": result.trace_id,
            "turn_id": result.turn_id,
            "run_id": result.run_id,
            "cwd": cwd,
            "events": [_chat_event_row(e) for e in events[-80:]],
        }
        _publish_dashboard_chat_event(body, "chat_final", result=result, cwd=cwd)
        send(final)
        return final
    except Exception as e:  # noqa: BLE001
        final = {
            "type": "error",
            "reply": f"error: {e}",
            "session_id": body.get("session_id") or "",
            "trace_id": "",
            "turn_id": "",
            "run_id": "",
            "cwd": cwd,
            "events": [_chat_event_row(ev) for ev in events[-80:]],
        }
        _publish_dashboard_chat_event(body, "chat_error", cwd=cwd,
                                      row={"type": "chat_error", "summary": str(e)})
        send(final)
        return final


def _dashboard_models(config: Config) -> dict:
    from .providers import registry
    from .providers.registry import provider_report

    report = provider_report(config)
    provider_names = sorted(({
        str(row.get("name")) for row in report.get("provider_catalog", [])
        if row.get("name")
    } | {str(config.get("model.provider") or "")}) - {""})
    report.update({
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "providers": provider_names,
        "presets": {p: registry.picker_models_for(p, config) for p in provider_names},
        "preset_rows": {p: registry.picker_model_entries_for(p, config) for p in provider_names},
        "model_inventory": registry.model_inventory(config, provider_names),
    })
    return report


def _message_detail(message, index: int) -> dict:
    row = {
        "index": index,
        "role": getattr(message, "role", ""),
        "content": getattr(message, "content", ""),
    }
    tool_calls = getattr(message, "tool_calls", []) or []
    if tool_calls:
        row["tool_calls"] = [_jsonable(tc.to_dict() if hasattr(tc, "to_dict") else tc)
                             for tc in tool_calls]
    for attr in ("tool_call_id", "name", "reasoning", "images"):
        value = getattr(message, attr, None)
        if value:
            row[attr] = _jsonable(value)
    return row


def _session_prompt_detail(sess) -> dict:
    meta = getattr(sess, "meta", {}) if hasattr(sess, "meta") else {}
    meta = meta if isinstance(meta, dict) else {}
    parts = [_jsonable(p) for p in (meta.get("prompt_parts") or []) if isinstance(p, dict)]
    by_tier: dict[str, list[dict]] = {}
    for part in parts:
        by_tier.setdefault(str(part.get("tier") or "other"), []).append(part)
    prompt_text = ""
    for message in getattr(sess, "messages", []) or []:
        if getattr(message, "role", "") == "system":
            prompt_text = getattr(message, "content", "") or ""
            break
    runtime = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
    controls = meta.get("runtime_controls") if isinstance(meta.get("runtime_controls"), dict) else {}
    last_refs = meta.get("last_context_references") if isinstance(meta.get("last_context_references"), dict) else {}
    ref_history = [r for r in (meta.get("context_references") or []) if isinstance(r, dict)]
    return {
        "hash": meta.get("system_prompt_hash", ""),
        "chars": int(meta.get("system_prompt_chars", 0) or 0),
        "tokens": int(meta.get("system_prompt_tokens", 0) or 0),
        "part_count": len(parts),
        "parts": parts,
        "tiers": by_tier,
        "runtime": _jsonable(runtime),
        "runtime_controls": _jsonable(controls),
        "context_references": _jsonable(last_refs),
        "context_reference_history": _jsonable(ref_history[-10:]),
        "preview": prompt_text[:1200],
    }


def _dashboard_session_links(sess, config: Config | None = None, *, limit: int = 10) -> dict:
    try:
        from .runs import RunStore
        runs = [_dashboard_run_row(r) for r in RunStore().list(session_id=sess.id, limit=limit)]
    except Exception:  # noqa: BLE001
        runs = []
    try:
        trace_payload = _dashboard_traces({"session_id": [sess.id], "limit": [str(limit)]}, config)
        traces = list(trace_payload.get("traces") or [])[:limit]
        trace_source = str(trace_payload.get("source") or "")
    except Exception:  # noqa: BLE001
        traces = []
        trace_source = ""

    meta = getattr(sess, "meta", {}) if hasattr(sess, "meta") else {}
    meta = meta if isinstance(meta, dict) else {}
    meta_trace_ids = [
        str(meta.get(key) or "") for key in ("last_trace_id", "trace_id")
        if meta.get(key)
    ]
    trace_ids = []
    for value in [r.get("trace_id", "") for r in runs] + \
            [t.get("trace_id") or t.get("id", "") for t in traces] + meta_trace_ids:
        value = str(value or "")
        if value and value not in trace_ids:
            trace_ids.append(value)
    run_ids = [str(r.get("id") or "") for r in runs if r.get("id")]
    return {
        "runs": runs,
        "traces": _jsonable(traces),
        "links": {
            "run_ids": run_ids,
            "trace_ids": trace_ids,
            "latest_run_id": run_ids[0] if run_ids else str(meta.get("last_run_id", "")),
            "latest_trace_id": trace_ids[0] if trace_ids else str(meta.get("last_trace_id", "")),
            "trace_source": trace_source,
        },
    }


def _dashboard_session_detail(session_id: str, config: Config | None = None) -> dict:
    from .session import SessionStore
    store = SessionStore()
    sess = store.load(session_id)
    if sess is None:
        return {"found": False, "id": session_id, "error": "session not found"}
    metrics = _session_metrics(sess)
    children = store.children(sess.id)
    parent = store.load(sess.parent_id) if sess.parent_id else None
    linked = _dashboard_session_links(sess, config)
    return {
        "found": True,
        "id": sess.id,
        "kind": "session",
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "parent_id": sess.parent_id,
        "parent": {
            "id": parent.id,
            "title": parent.title,
            "updated_at": parent.updated_at,
        } if parent is not None else None,
        "children": _jsonable(children),
        "metrics": metrics,
        "prompt": _session_prompt_detail(sess),
        "messages": [_message_detail(m, i) for i, m in enumerate(sess.messages)],
        "runs": linked["runs"],
        "traces": linked["traces"],
        "links": linked["links"],
        "todos": _jsonable(sess.todos),
        "meta": _jsonable(sess.meta),
    }


def _dashboard_branch_session(session_id: str, *, title: str = "", reason: str = "dashboard") -> dict:
    from .session import SessionStore
    store = SessionStore()
    parent = store.load(session_id)
    if parent is None:
        return {"ok": False, "found": False, "id": session_id, "error": "session not found"}
    child = store.fork(parent)
    if title.strip():
        child.title = title.strip()
    child.meta["creator_kind"] = "dashboard_branch"
    child.meta["reason"] = reason or "dashboard"
    child.meta["branch_root"] = parent.meta.get("lineage_root") or parent.parent_id or parent.id
    parent.meta.setdefault("child_sessions", [])
    if child.id not in parent.meta["child_sessions"]:
        parent.meta["child_sessions"].append(child.id)
    store.save(parent)
    store.save(child)
    return {
        "ok": True,
        "session_id": child.id,
        "parent_id": parent.id,
        "session": _dashboard_session_detail(child.id),
        "parent": _dashboard_session_detail(parent.id),
    }


def _session_span_rows(session_detail: dict) -> list[dict]:
    spans = []
    call_parents: dict[str, str] = {}
    for msg in session_detail.get("messages", []):
        idx = msg.get("index", len(spans))
        span_id = f"msg_{idx}"
        for call in msg.get("tool_calls", []) or []:
            if isinstance(call, dict) and call.get("id"):
                call_parents[str(call["id"])] = span_id
        tool_call_id = str(msg.get("tool_call_id") or "")
        spans.append({
            "span_id": span_id,
            "trace_id": session_detail.get("id", ""),
            "session_id": session_detail.get("id", ""),
            "turn_id": str(idx),
            "parent_span_id": call_parents.get(tool_call_id, ""),
            "kind": "tool" if msg.get("role") == "tool" else "message",
            "status": "ok",
            "started_at": "",
            "ended_at": "",
            "provider": "",
            "model": "",
            "tool_name": msg.get("name", ""),
            "role": msg.get("role", ""),
            "duration_ms": 0,
            "latency_ms": 0,
            "data": {
                "role": msg.get("role", ""),
                "content": msg.get("content", ""),
                "tool_calls": msg.get("tool_calls", []),
                "tool_call_id": tool_call_id,
                "tool_name": msg.get("name", ""),
            },
        })
    return spans


def _dashboard_trace_detail(query: dict, config: Config | None = None) -> dict:
    trace_id = ((query.get("id", [""])[0]) or "").strip()
    if not trace_id:
        return {"found": False, "error": "missing id"}

    trace_path = _trace_db_path(config)
    if trace_path.exists():
        try:
            from .tracing import TraceStore
            store = TraceStore(trace_path)
            trace = store.get_trace(trace_id)
        except Exception:  # noqa: BLE001
            trace = None
            store = None
        if trace:
            detail = _jsonable(trace)
            summary = _normalize_trace_row({k: v for k, v in detail.items() if k != "spans"})
            try:
                from . import evals as eval_mod
                evaluation = _jsonable(eval_mod.evaluate_trace(trace_id, store=store))
                replay = _jsonable(eval_mod.replay_trace(trace_id, store=store).to_dict())
            except Exception:  # noqa: BLE001
                evaluation = {}
                replay = {}
            return {
                "found": True,
                "id": trace_id,
                "source": "aegis.tracing",
                "trace": summary,
                "spans": detail.get("spans", []),
                "replay": replay,
                "grades": evaluation.get("grades", []),
                "evaluation": evaluation,
            }

    session = _dashboard_session_detail(trace_id, config)
    if session.get("found"):
        spans = _session_span_rows(session)
        try:
            from . import evals as eval_mod
            evaluation = _jsonable(eval_mod.evaluate_session(trace_id))
            replay = _jsonable(eval_mod.replay_session(trace_id).to_dict())
        except Exception:  # noqa: BLE001
            evaluation = {}
            replay = {}
        metrics = session.get("metrics", {})
        usage = session.get("meta", {}).get("usage", {}) if isinstance(session.get("meta"), dict) else {}
        runtime = session.get("meta", {}).get("runtime", {}) if isinstance(session.get("meta"), dict) else {}
        return {
            "found": True,
            "id": trace_id,
            "source": "session",
            "trace": {
                "id": trace_id,
                "title": session.get("title", trace_id),
                "status": "recorded",
                "started_at": session.get("created_at", ""),
                "updated_at": session.get("updated_at", ""),
                "spans": {
                    "messages": metrics.get("messages", 0),
                    "span_count": metrics.get("messages", 0),
                    "provider_calls": 0,
                    "tool_calls": metrics.get("tool_calls", 0),
                    "compactions": metrics.get("compactions", 0),
                    "errors": 0,
                },
                "providers": [runtime.get("provider")] if runtime.get("provider") else [],
                "models": [runtime.get("model")] if runtime.get("model") else [],
                "cache_read": int(usage.get("cache_read", 0) or 0),
                "cache_write": int(usage.get("cache_write", 0) or 0),
            },
            "spans": spans,
            "messages": session.get("messages", []),
            "replay": replay,
            "grades": evaluation.get("grades", []),
            "evaluation": evaluation,
        }
    return {"found": False, "id": trace_id, "error": "trace not found"}


def _dashboard_run_detail(query: dict, config: Config | None = None) -> dict:
    run_id = ((query.get("id", [""])[0]) or "").strip()
    if not run_id:
        return {"found": False, "error": "missing id"}

    try:
        from .runs import RunStore
        stored = RunStore().get(run_id)
    except Exception:  # noqa: BLE001
        stored = None
    if stored:
        session_id = stored.get("session_id", "")
        trace_id = stored.get("trace_id", "")
        session = _dashboard_session_detail(session_id, config) if session_id else None
        trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
        return {
            "found": True,
            "id": stored["id"],
            "run": _dashboard_run_row(stored),
            "session": session,
            "trace": trace,
            "messages": (session or {}).get("messages", []),
        }

    session = _dashboard_session_detail(run_id, config)
    if session.get("found"):
        goal = (session.get("metrics") or {}).get("goal") or {}
        run = {
            "id": run_id,
            "kind": "session",
            "title": session.get("title", ""),
            "status": "active_goal" if goal.get("status") == "active" else "completed",
            "started_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "turns": (session.get("metrics") or {}).get("roles", {}).get("user", 0),
            "messages": (session.get("metrics") or {}).get("messages", 0),
            "tool_calls": (session.get("metrics") or {}).get("tool_calls", 0),
            "summary": (session.get("meta") or {}).get("summary", ""),
        }
        return {"found": True, "id": run_id, "run": run, "session": session,
                "messages": session.get("messages", [])}

    try:
        from .background import get_manager
        for task in get_manager().list():
            if task.get("id") == run_id:
                return {"found": True, "id": run_id, "run": _jsonable(task),
                        "messages": [], "note": "background task details are limited to task metadata"}
    except Exception:  # noqa: BLE001
        pass
    return {"found": False, "id": run_id, "error": "run not found"}


def _dashboard_agents(config: Config) -> dict:
    active_runs = _dashboard_active_run_agents(config)
    agents = [{
        "id": "primary",
        "kind": "agent",
        "type": "general",
        "status": "running" if active_runs else "configured",
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "toolsets": config.get("tools.toolsets", []),
        "active_runs": len(active_runs),
    }]
    agents.extend(active_runs)
    types = []
    try:
        from .tools.agentic import AGENT_TYPES, _REGISTRY, _REG_LOCK
        for name, spec in AGENT_TYPES.items():
            tools = spec.get("tools")
            types.append({"name": name, "tools": "all" if tools is None else sorted(tools),
                          "readonly": tools is not None})
        with _REG_LOCK:
            for sid, entry in list(_REGISTRY.items())[-50:]:
                safe = _jsonable({k: v for k, v in entry.items() if k != "agent"})
                safe.update({"id": sid, "kind": "subagent", "continuable": "agent" in entry})
                agents.append(safe)
    except Exception:  # noqa: BLE001
        types = []
    try:
        from .background import get_manager
        for task in get_manager().list():
            run_id = task.get("run_id", "")
            trace_id = ""
            session_id = f"background:{task['id']}"
            if run_id:
                try:
                    from .runs import RunStore
                    run = RunStore().get(run_id)
                    if run:
                        trace_id = run.get("trace_id", "")
                        session_id = run.get("session_id", session_id) or session_id
                except Exception:  # noqa: BLE001
                    pass
            agents.append({"id": task["id"], "kind": "background", "type": "general",
                           "status": task["status"], "task": task["prompt"],
                           "preview": task.get("result_preview", ""),
                           "run_id": run_id, "session_id": session_id, "trace_id": trace_id})
    except Exception:  # noqa: BLE001
        pass
    return {"agents": agents, "active_runs": active_runs, "types": types}


def _dashboard_agent_detail(query: dict, config: Config) -> dict:
    agent_id = ((query.get("id", [""])[0]) or "").strip()
    if not agent_id:
        return {"found": False, "error": "missing id"}
    if agent_id == "primary":
        try:
            from .runs import RunStore
            recent = RunStore().list(limit=10)
        except Exception:  # noqa: BLE001
            recent = []
        active_runs = _dashboard_active_run_agents(config)
        return {
            "found": True,
            "id": "primary",
            "agent": {
                "id": "primary",
                "kind": "agent",
                "type": "general",
                "status": "running" if active_runs else "configured",
                "provider": config.get("model.provider"),
                "model": config.get("model.default"),
                "toolsets": config.get("tools.toolsets", []),
                "active_runs": len(active_runs),
            },
            "active_runs": active_runs,
            "runs": [_dashboard_run_row(r) for r in recent],
        }

    run_detail = None
    try:
        from .runs import RunStore
        run = RunStore().get(agent_id)
    except Exception:  # noqa: BLE001
        run = None
    if run is not None:
        agent = _dashboard_agent_from_run(run, config)
        run_detail = _dashboard_run_detail({"id": [run["id"]]}, config)
        session_id = str(agent.get("session_id") or "")
        trace_id = str(agent.get("trace_id") or "")
        session = _dashboard_session_detail(session_id, config) if session_id else None
        trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
        return {
            "found": True,
            "id": agent_id,
            "agent": agent,
            "run": (run_detail or {}).get("run") if run_detail else None,
            "session": session,
            "trace": trace,
            "messages": (session or {}).get("messages", []),
        }

    agent: dict | None = None
    try:
        from .tools.agentic import _REGISTRY, _REG_LOCK
        with _REG_LOCK:
            entry = _REGISTRY.get(agent_id)
            if entry is None:
                entry = next((v for k, v in _REGISTRY.items() if k.startswith(agent_id)), None)
                if entry is not None:
                    agent_id = next(k for k, v in _REGISTRY.items() if v is entry)
            if entry is not None:
                agent = _jsonable({k: v for k, v in entry.items() if k != "agent"})
                agent.update({"id": agent_id, "kind": "subagent", "continuable": "agent" in entry})
    except Exception:  # noqa: BLE001
        agent = None

    if agent is None:
        try:
            from .background import get_manager
            task = get_manager().get(agent_id)
            if task is not None:
                agent = {
                    "id": task.id,
                    "kind": "background",
                    "type": "general",
                    "status": task.status,
                    "task": task.prompt,
                    "preview": (task.result or task.error)[:500],
                    "run_id": task.run_id,
                    "session_id": f"background:{task.id}",
                }
        except Exception:  # noqa: BLE001
            agent = None

    if agent is None:
        return {"found": False, "id": agent_id, "error": "agent not found"}

    run_id = str(agent.get("run_id") or "")
    session_id = str(agent.get("session_id") or "")
    trace_id = str(agent.get("trace_id") or "")
    run_detail = _dashboard_run_detail({"id": [run_id]}, config) if run_id else None
    if run_detail and run_detail.get("found"):
        run = run_detail.get("run") or {}
        session_id = session_id or str(run.get("session_id") or "")
        trace_id = trace_id or str(run.get("trace_id") or "")
    session = _dashboard_session_detail(session_id, config) if session_id else None
    trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
    return {
        "found": True,
        "id": agent_id,
        "agent": agent,
        "run": (run_detail or {}).get("run") if run_detail else None,
        "session": session,
        "trace": trace,
        "messages": (session or {}).get("messages", []),
    }


def _git(cwd: str | Path, *args: str) -> str | None:
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           timeout=2)
    except Exception:  # noqa: BLE001
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _project_marker(path: Path) -> str:
    for name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml",
                 "requirements.txt"):
        if (path / name).exists():
            return name
    return ""


def _workspace_run_stats() -> dict[str, dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(limit=500)
    except Exception:  # noqa: BLE001
        return {}
    stats: dict[str, dict] = {}
    for row in rows:
        data = row.get("data") or {}
        key = str(data.get("project") or data.get("cwd") or "").strip()
        if not key:
            continue
        item = stats.setdefault(key, {
            "run_count": 0,
            "surfaces": set(),
            "last_run_at": "",
            "worktree": str(data.get("worktree") or ""),
            "branch": str(data.get("branch") or ""),
        })
        item["run_count"] += 1
        if row.get("surface"):
            item["surfaces"].add(str(row["surface"]))
        updated = str(row.get("ended_at") or row.get("started_at") or "")
        if updated > item["last_run_at"]:
            item["last_run_at"] = updated
    for item in stats.values():
        item["surfaces"] = sorted(item["surfaces"])
    return stats


def _dashboard_projects() -> dict:
    from . import config as cfg
    from .lsp.workspace import find_git_worktree

    cwd = Path.cwd().resolve()
    root = find_git_worktree(str(cwd))
    projects = []
    run_stats = _workspace_run_stats()
    if root:
        r = Path(root)
        projects.append({
            "id": str(r),
            "name": r.name or str(r),
            "path": str(r),
            "kind": "git",
            "current": str(cwd).startswith(str(r)),
            "branch": (_git(r, "branch", "--show-current") or
                       _git(r, "rev-parse", "--short", "HEAD") or ""),
            "marker": _project_marker(r),
            **run_stats.pop(str(r), {}),
        })
    else:
        projects.append({"id": str(cwd), "name": cwd.name or str(cwd), "path": str(cwd),
                         "kind": "directory", "current": True, "marker": _project_marker(cwd),
                         **run_stats.pop(str(cwd), {})})
    for path, stats in sorted(run_stats.items(), key=lambda item: item[1].get("last_run_at", ""),
                              reverse=True)[:20]:
        p = Path(path)
        projects.append({
            "id": path,
            "name": p.name or path,
            "path": path,
            "kind": "recent",
            "current": False,
            "branch": stats.get("branch", ""),
            "marker": _project_marker(p) if p.exists() else "",
            **stats,
        })
    workspace = cfg.sub("workspace")
    projects.append({"id": "aegis-workspace", "name": "AEGIS workspace",
                     "path": str(workspace), "kind": "aegis_workspace", "current": False})
    return {"projects": projects}


def _parse_worktree_porcelain(text: str) -> list[dict]:
    out, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur:
                out.append(cur)
                cur = {}
            continue
        key, _, value = line.partition(" ")
        cur[key] = value or True
    return out


def _dashboard_files(query: dict) -> dict:
    """Read-only directory listing for the dashboard file browser. Token-gated,
    localhost-only; no write/delete (those would need a real upload transport)."""
    import time
    raw = (query.get("path", [""])[0] or "").strip()
    base = Path(raw).expanduser() if raw else Path.home()
    try:
        base = base.resolve()
    except Exception:  # noqa: BLE001
        base = Path.home()
    if not base.is_dir():
        return {"path": str(base), "error": "not a directory", "entries": []}
    entries = []
    try:
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = p.stat()
                entries.append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": None if p.is_dir() else st.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                })
            except OSError:
                continue
    except PermissionError:
        return {"path": str(base), "error": "permission denied", "entries": []}
    return {"path": str(base), "parent": str(base.parent), "entries": entries[:2000]}


_SENSITIVE_NAMES = frozenset({
    ".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
    "secrets.json", "auth.json", ".netrc", ".pgpass", ".htpasswd",
})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")
_SENSITIVE_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".docker"})


def _is_sensitive_path(p: Path) -> bool:
    """Files that must never be served as content through the dashboard browser:
    credentials, private keys, .env secret stores, SSH/GPG/cloud config. The file
    browser is read-only and token-gated, but reading a secret file would still
    exfiltrate API keys to anyone with dashboard access (or a remote-bound host)."""
    name = p.name.lower()
    if name in _SENSITIVE_NAMES or name.startswith(".env"):
        return True
    if any(name.endswith(s) for s in _SENSITIVE_SUFFIXES):
        return True
    parts = {part.lower() for part in p.parts}
    return bool(parts & _SENSITIVE_DIRS)


def _dashboard_file_read(query: dict) -> dict:
    raw = (query.get("path", [""])[0] or "").strip()
    if not raw:
        return {"error": "no path"}
    try:
        p = Path(raw).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return {"error": "bad path"}
    if not p.is_file():
        return {"error": "not a file"}
    if _is_sensitive_path(p):
        return {"path": str(p), "error": "blocked: this looks like a secret/credential file "
                "and can't be previewed through the dashboard."}
    try:
        if p.stat().st_size > 512 * 1024:
            return {"path": str(p), "error": "file too large to preview (>512KB)"}
        return {"path": str(p), "content": p.read_text(errors="replace")}
    except Exception as e:  # noqa: BLE001
        return {"path": str(p), "error": str(e)}


def _dashboard_worktrees() -> dict:
    from .lsp.workspace import find_git_worktree
    root = find_git_worktree(str(Path.cwd()))
    if not root:
        return {"available": False, "worktrees": [], "note": "Current directory is not a git worktree."}
    run_stats = _workspace_run_stats()
    raw = _git(root, "worktree", "list", "--porcelain")
    rows = _parse_worktree_porcelain(raw or "")
    if not rows:
        rows = [{"worktree": root, "branch": _git(root, "branch", "--show-current") or ""}]
    worktrees = []
    for row in rows:
        path = str(row.get("worktree", ""))
        if not path:
            continue
        dirty = _git(path, "status", "--porcelain")
        branch = str(row.get("branch", "")).removeprefix("refs/heads/")
        stats = run_stats.get(path, {})
        worktrees.append({"path": path, "branch": branch, "head": row.get("HEAD", ""),
                          "detached": bool(row.get("detached")), "bare": bool(row.get("bare")),
                          "dirty": bool(dirty), "run_count": stats.get("run_count", 0),
                          "last_run_at": stats.get("last_run_at", ""),
                          "surfaces": stats.get("surfaces", [])})
    return {"available": True, "worktrees": worktrees}


def _cron_run_history(job_id: str, limit: int = 5) -> list[dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(surface="cron", limit=200)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in rows:
        data = row.get("data") or {}
        if data.get("cron_job_id") == job_id:
            out.append(_dashboard_run_row(row))
        if len(out) >= limit:
            break
    return out


def _dashboard_cron_jobs() -> list[dict]:
    from .cron import CronStore, _latest_job_output
    rows = []
    for job in CronStore().list():
        history = _cron_run_history(job.id)
        rows.append({
            "id": job.id,
            "name": getattr(job, "name", "") or "",
            "schedule": job.schedule,
            "prompt": job.prompt,
            "enabled": job.enabled,
            "one_shot": bool(job.run_at),
            "no_agent": bool(job.no_agent),
            "channel": job.channel,
            "context_from": list(getattr(job, "context_from", []) or []),
            "script": job.script,
            "skills": list(job.skills or []),
            "model": getattr(job, "model", "") or "",
            "enabled_toolsets": list(getattr(job, "enabled_toolsets", []) or []),
            "workdir": getattr(job, "workdir", "") or "",
            "deliver": job.deliver,
            "max_runs": job.max_runs,
            "state": job.state,
            "last_error": job.last_error,
            "next_run": job.next_run,
            "runs": list(job.runs or []),
            "last_run": job.last_run,
            "run_count": len(history),
            "last_run_id": history[0]["id"] if history else "",
            "last_status": history[0]["status"] if history else "",
            "history": history,
            "latest_output": _latest_job_output(job, limit=1200),
        })
    return rows


def _dashboard_evals(config: Config | None = None) -> dict:
    from . import config as cfg
    paths = [cfg.sub("evals.jsonl"), cfg.sub("evals", "runs.jsonl"), cfg.sub("evals", "results.jsonl")]
    eval_dir = cfg.sub("evals")
    if eval_dir.exists():
        paths.extend(sorted(eval_dir.glob("*.jsonl")))
    seen, rows = set(), []
    for p in paths:
        if p in seen or not p.exists():
            continue
        seen.add(p)
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[-50:]:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rec = _jsonable(rec)
                rec.setdefault("source", str(p))
                rows.append(rec)
    try:
        from .evals import EvalStore
        store = EvalStore.from_config(config) if config else EvalStore()
        for rec in store.list_runs(limit=50):
            rows.append({**_jsonable(rec), "name": rec.get("suite", ""), "status": "recorded",
                         "source": "eval_store"})
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import evals as eval_mod
        from .session import SessionStore
        ids = [s["id"] for s in SessionStore().list(20)]
        rows.extend(_jsonable(r) for r in eval_mod.evaluate_sessions(ids))
        trace_path = _trace_db_path(config)
        if trace_path.exists():
            from .tracing import TraceStore
            trace_store = TraceStore(trace_path)
            trace_ids = [t["trace_id"] for t in trace_store.list_traces(limit=20)]
            rows.extend(_jsonable(r) for r in eval_mod.evaluate_traces(trace_ids, store=trace_store))
    except Exception:  # noqa: BLE001
        pass
    try:
        from .trajectory import stats
        traj = stats()
    except Exception:  # noqa: BLE001
        traj = {}
    return {"available": bool(rows), "evals": rows[-100:], "summary": {"records": len(rows),
            "trajectory": traj}, "note": "" if rows else "No eval result store found yet."}


def _dashboard_eval_detail(query: dict, config: Config | None = None) -> dict:
    eval_id = ((query.get("id", [""])[0]) or "").strip()
    if not eval_id:
        return {"found": False, "error": "missing id"}

    db_path = _eval_db_path(config)
    if db_path.exists():
        try:
            from .evals import EvalStore
            run = EvalStore(db_path).get_run(eval_id)
        except Exception:  # noqa: BLE001
            run = None
        if run:
            run = _jsonable(run)
            summary = run.get("summary") or {
                "total": run.get("total", 0),
                "passed": run.get("passed", 0),
                "failed": max(0, int(run.get("total") or 0) - int(run.get("passed") or 0)),
                "score": run.get("score", 0),
            }
            return {
                "found": True,
                "id": eval_id,
                "source": "eval_store",
                "eval": {**run, "name": run.get("suite", run.get("name", "")),
                         "status": "recorded", "summary": summary},
                "summary": summary,
                "results": run.get("results", []),
            }

    for rec in _dashboard_evals(config).get("evals", []):
        keys = {str(rec.get(k, "")) for k in ("id", "run_id", "name", "suite", "case", "task")}
        if eval_id in keys:
            return {
                "found": True,
                "id": eval_id,
                "source": rec.get("source", "eval_record"),
                "eval": rec,
                "summary": rec.get("summary", {}),
                "results": rec.get("results", rec.get("grades", [])),
            }
    return {"found": False, "id": eval_id, "error": "eval run not found"}


def _dashboard_run_eval(body: dict, config: Config | None = None) -> dict:
    path = str(body.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "missing path"}
    try:
        from .evals import run_suite

        run = _jsonable(run_suite(path, config=config))
        return {"ok": True, "eval": run}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.query and any(k in parsed.query.lower()
                            for k in ("key", "token", "secret", "password", "auth")):
        return urlunparse(parsed._replace(query="redacted=1"))
    return value


def _dashboard_mcp_catalog(config: Config, *, live: bool = False) -> dict:
    try:
        from .mcp.client import _server_configs, catalog as _mcp_catalog
        raw = _server_configs(config)
    except Exception:  # noqa: BLE001
        raw = config.get("mcp.servers", {}) or {}
        _mcp_catalog = None
    servers, malformed = [], []
    for name, spec in (raw or {}).items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            malformed.append(str(name))
            continue
        transport = "http" if spec.get("url") else "stdio"
        servers.append({
            "name": str(name),
            "transport": transport,
            "enabled": bool(spec.get("enabled", True)),
            "command": spec.get("command", ""),
            "args": list(spec.get("args") or []),
            "url": _redact_url(str(spec.get("url", ""))) if spec.get("url") else "",
            "cwd": spec.get("cwd", ""),
            "env_keys": sorted((spec.get("env") or {}).keys()),
            "header_keys": sorted((spec.get("headers") or {}).keys()),
            "status": "configured" if bool(spec.get("enabled", True)) else "disabled",
            **(_mcp_live_inventory(str(name), spec) if live else {}),
        })
    recipes = []
    if _mcp_catalog is not None:
        try:
            for entry in _mcp_catalog(config):
                target = entry.get("url") or " ".join([entry.get("command", ""), *(entry.get("args") or [])])
                recipes.append({
                    "name": str(entry.get("name", "")),
                    "description": str(entry.get("description", "")),
                    "transport": "http" if entry.get("url") else "stdio",
                    "target": target.strip(),
                    "installed": str(entry.get("name", "")) in (raw or {}),
                })
        except Exception:  # noqa: BLE001
            recipes = []
    return {"enabled": bool(config.get("mcp.enabled", True)), "available": bool(servers),
            "servers": servers, "catalog": recipes, "malformed": malformed}


def _mcp_live_inventory(name: str, spec: dict) -> dict:
    try:
        from .mcp.client import MCPClient

        client = MCPClient(
            name,
            command=spec.get("command"),
            args=spec.get("args"),
            env=spec.get("env"),
            url=spec.get("url"),
            headers=spec.get("headers"),
            cwd=spec.get("cwd"),
            tool_filter=spec.get("tool_filter"),
        )
        try:
            client.connect()
            return {
                "status": "ok",
                "tools": client.list_tools(),
                "resources": client.list_resources(),
                "prompts": client.list_prompts(),
            }
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def _dashboard_token(config: Config) -> str | None:
    """Token gating the dashboard. AEGIS_DASHBOARD_TOKEN (env) wins so a launcher
    like the Electron desktop app can inject a fresh random token per run without
    writing config; otherwise the persisted server.dashboard_token is used."""
    import os
    return os.environ.get("AEGIS_DASHBOARD_TOKEN") or config.get("server.dashboard_token")


def _dashboard_url(config: Config, host: str, port: int) -> str:
    token = _dashboard_token(config)
    base = f"http://{host}:{port}"
    return f"{base}/?token={token}" if token else base


def serve_dashboard(config: Config, host: str = "127.0.0.1", port: int = 9119,
                    open_browser: bool = False) -> None:
    from .dashboard_fastapi import run_dashboard

    try:
        run_dashboard(config, host=host, port=port, open_browser=open_browser)
    except KeyboardInterrupt:
        print("\ndashboard stopped.")


def cmd_dashboard(args, config: Config) -> int:
    host = getattr(args, "host", None) or config.get("server.dashboard_host", "127.0.0.1")
    port = getattr(args, "port", None) or config.get("server.dashboard_port", 9119)
    # Beginner-friendly default: open the browser unless asked not to.
    open_browser = not getattr(args, "no_open", False)
    serve_dashboard(config, host=host, port=int(port), open_browser=open_browser)
    return 0
