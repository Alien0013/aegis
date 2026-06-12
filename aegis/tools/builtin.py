"""Built-in tools: filesystem, shell, web, todo, memory, skills."""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from ..util import truncate
from .base import Tool, ToolContext, ToolResult

MAX_OUTPUT = 30_000


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (ctx.cwd / p)


# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #
class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file. Returns content with 1-based line numbers. Use offset/limit for large files."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or relative to cwd)."},
            "offset": {"type": "integer", "description": "1-based line to start at."},
            "limit": {"type": "integer", "description": "Max lines to read (default 2000)."},
        },
        "required": ["path"],
    }

    def run(self, args, ctx) -> ToolResult:
        path = _resolve(ctx, args["path"])
        fs = getattr(ctx, "fs", None)
        if fs is None:                       # real filesystem (default)
            if not path.exists():
                return ToolResult.error(f"No such file: {path}")
            if path.is_dir():
                return ToolResult.error(f"{path} is a directory (use list_dir).")
        try:
            text = fs.read_text(str(path)) if fs else path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {e}")
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 2000))
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{offset + i:6d}\t{ln}" for i, ln in enumerate(chunk))
        if fs is None:
            from . import file_state
            file_state.note(path)            # freshness stamp for later stale-write checks
        return ToolResult.ok(truncate(body, MAX_OUTPUT), display=f"read {path.name} ({len(chunk)} lines)")


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content. Creates parent directories."
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def run(self, args, ctx) -> ToolResult:
        path = _resolve(ctx, args["path"])
        fs = getattr(ctx, "fs", None)
        stale = ""
        if fs is None:
            from . import file_safety, file_state
            denied = file_safety.authorize_write(path, ctx)
            if denied:
                return ToolResult.error(denied)
            stale = file_state.stale_warning(path)
            _lsp_snapshot(ctx, path)
        try:
            if fs:                           # delegate to the editor (ACP fs/write_text_file)
                fs.write_text(str(path), args["content"])
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"], encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {e}")
        if fs is None:
            from . import file_state
            file_state.note(path)
        n = args["content"].count("\n") + 1
        msg = f"Wrote {n} lines to {path}" + ("" if fs else _lsp_delta(ctx, path)) + stale
        return ToolResult.ok(msg, display=f"wrote {path.name}")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace an exact string in a file. old_string must be unique unless replace_all=true."
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, args, ctx) -> ToolResult:
        path = _resolve(ctx, args["path"])
        if not path.exists():
            return ToolResult.error(f"No such file: {path}")
        from . import file_safety, file_state
        denied = file_safety.authorize_write(path, ctx)
        if denied:
            return ToolResult.error(denied)
        stale = file_state.stale_warning(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        old, new = args["old_string"], args["new_string"]
        via = ""
        count = text.count(old)
        if count == 0:
            # Fuzzy recovery: whitespace/indentation drift is the usual cause. Only a
            # UNIQUE fuzzy match is trusted; ambiguity falls through to the error.
            from .fuzzy import find_fuzzy, reindent
            hit = find_fuzzy(text, old)
            if hit is None:
                return ToolResult.error("old_string not found in file." + _closest_hint(text, old))
            matched, strategy = hit
            old, new = matched, reindent(new, matched, old)
            count, via = 1, f" (matched via {strategy} — whitespace drift auto-recovered)"
        if count > 1 and not args.get("replace_all"):
            return ToolResult.error(f"old_string appears {count}× — pass replace_all=true or add context.")
        _lsp_snapshot(ctx, path)
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        file_state.note(path)
        return ToolResult.ok(f"Edited {path} ({count if args.get('replace_all') else 1} replacement(s))."
                             + via + _lsp_delta(ctx, path) + stale,
                             display=f"edited {path.name}")


def _lsp_enabled(ctx, path) -> bool:
    cfg = getattr(ctx, "config", None)
    if cfg is None or not cfg.get("lsp.on_edit", True):
        return False
    if "lsp" not in (cfg.get("tools.toolsets", []) or []):
        return False
    from ..lsp.servers import find_server
    return find_server(str(path), cfg) is not None


def _lsp_snapshot(ctx, path) -> None:
    """Pre-edit diagnostics baseline so only NEW problems get reported after the edit."""
    try:
        if path.exists() and _lsp_enabled(ctx, path):
            from ..lsp import get_service
            get_service(ctx.config).snapshot(str(path), str(ctx.cwd))
    except Exception:  # noqa: BLE001  (LSP must never break an edit)
        pass


def _lsp_delta(ctx, path) -> str:
    """Diagnostics the edit introduced, as a footer for the tool result ('' when clean)."""
    try:
        if not _lsp_enabled(ctx, path):
            return ""
        from ..lsp import get_service
        from ..lsp.service import format_diags
        new = get_service(ctx.config).delta(str(path), str(ctx.cwd))
        if new:
            return "\n\nNew diagnostics introduced by this edit:\n" + format_diags(new)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _closest_hint(text: str, old: str) -> str:
    """When old_string isn't found exactly, surface the closest block in the file so the model
    can self-correct in one step instead of guessing (whitespace/indent drift is the usual cause)."""
    import difflib
    old = old.strip("\n")
    if not old:
        return ""
    lines = text.splitlines()
    n = max(1, len(old.splitlines()))
    best, best_ratio = "", 0.0
    for i in range(max(1, len(lines) - n + 1)):
        window = "\n".join(lines[i:i + n])
        r = difflib.SequenceMatcher(None, old, window).ratio()
        if r > best_ratio:
            best, best_ratio = window, r
    if best_ratio < 0.6:
        return " (no close match — re-read the file and copy the exact text.)"
    return (f"\nClosest match in the file ({best_ratio:.0%} similar) — copy it EXACTLY "
            f"(whitespace matters):\n----\n{best[:600]}\n----")


class ListDirTool(Tool):
    name = "list_dir"
    description = "List the entries of a directory."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory (default: cwd)."}},
    }

    def run(self, args, ctx) -> ToolResult:
        path = _resolve(ctx, args.get("path", "."))
        if not path.exists():
            return ToolResult.error(f"No such directory: {path}")
        if not path.is_dir():
            return ToolResult.error(f"{path} is not a directory.")
        rows = []
        for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            mark = "/" if entry.is_dir() else ""
            rows.append(f"{entry.name}{mark}")
        return ToolResult.ok("\n".join(rows) or "(empty)", display=f"ls {path} ({len(rows)})")


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern (e.g. '**/*.py'). Returns matching paths."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Base directory (default cwd)."},
        },
        "required": ["pattern"],
    }

    def run(self, args, ctx) -> ToolResult:
        base = _resolve(ctx, args.get("path", "."))
        matches = sorted(str(p) for p in base.glob(args["pattern"]) if p.is_file())[:500]
        return ToolResult.ok("\n".join(matches) or "(no matches)", display=f"glob {len(matches)} files")


class SearchTool(Tool):
    name = "search"
    description = "Search file contents for a regex (uses ripgrep if available). Returns matching lines."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Dir/file to search (default cwd)."},
            "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py'."},
        },
        "required": ["pattern"],
    }

    def run(self, args, ctx) -> ToolResult:
        base = _resolve(ctx, args.get("path", "."))
        pattern = args["pattern"]
        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-n", "--no-heading", "--color=never", pattern, str(base)]
            if args.get("glob"):
                cmd[1:1] = ["-g", args["glob"]]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                body = out.stdout or out.stderr or "(no matches)"
                return ToolResult.ok(truncate(body, MAX_OUTPUT), display="ripgrep search")
            except subprocess.TimeoutExpired:
                return ToolResult.error("search timed out")
        # python fallback
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult.error(f"bad regex: {e}")
        hits: list[str] = []
        files = [base] if base.is_file() else base.rglob(args.get("glob", "*"))
        for f in files:
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}:{line}")
                        if len(hits) >= 500:
                            break
            except Exception:  # noqa: BLE001
                continue
            if len(hits) >= 500:
                break
        return ToolResult.ok("\n".join(hits) or "(no matches)", display=f"search ({len(hits)} hits)")


# --------------------------------------------------------------------------- #
# Shell
# --------------------------------------------------------------------------- #
class BashTool(Tool):
    name = "bash"
    description = "Run a shell command in the working directory and return stdout/stderr."
    groups = ["runtime"]
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds (default 120, max 600)."},
            "background": {
                "type": "boolean",
                "description": "Start the command as a managed background process and return a session_id.",
            },
            "notify_on_complete": {
                "type": "boolean",
                "description": "Queue a wakeup when a background process exits (default true).",
            },
        },
        "required": ["command"],
    }

    def run(self, args, ctx) -> ToolResult:
        from .backends import run_command

        timeout = min(int(args.get("timeout", 120)), 600)
        backend = ctx.config.get("tools.terminal_backend", "local") if ctx.config else "local"
        if args.get("background"):
            if str(backend or "local").strip().lower() != "local":
                return ToolResult.error(
                    "background bash is currently supported for the local terminal backend only"
                )
            from .process_registry import process_registry

            agent = getattr(ctx, "agent", None)
            proc = process_registry.spawn_local(
                args["command"],
                cwd=ctx.cwd,
                task_id=getattr(ctx, "task_id", "") or "",
                notify_on_complete=bool(args.get("notify_on_complete", True)),
                watcher_platform=getattr(agent, "platform", "") or "",
                watcher_chat_id=getattr(agent, "chat_id", "") or "",
            )
            return ToolResult.ok(
                (
                    "Background process started\n"
                    f"session_id: {proc.id}\n"
                    f"pid: {proc.pid}\n"
                    "Use process(action='poll'|'log'|'wait'|'kill', session_id=...) "
                    "to inspect or control it."
                ),
                display=f"background {proc.id}",
                data={"session_id": proc.id, "pid": proc.pid},
            )
        out, code = run_command(
            args["command"],
            str(ctx.cwd),
            timeout,
            backend,
            ctx.config,
            task_id=getattr(ctx, "task_id", "") or None,
        )
        out = out.strip() or "(no output)"
        tail = f"\n[exit {code}]"
        return ToolResult(
            content=truncate(out, MAX_OUTPUT) + tail,
            is_error=code != 0,
            display=f"$ {args['command'][:60]} (exit {code})",
        )


# --------------------------------------------------------------------------- #
# System status
# --------------------------------------------------------------------------- #
class SystemStatusTool(Tool):
    name = "system_status"
    description = (
        "Inspect AEGIS runtime status: provider/auth, tools, skills, plugins, workspace, "
        "dashboard, and user services. Does not reveal tokens or secret values."
    )
    parameters = {"type": "object", "properties": {}}

    def run(self, args, ctx) -> ToolResult:
        from .. import __version__, config as cfg

        config = ctx.config
        agent = ctx.agent
        lines = [f"AEGIS v{__version__} system status"]
        lines.append(f"home: {cfg.get_home()}")
        lines.append(f"cwd: {ctx.cwd}")

        provider = getattr(agent, "provider", None)
        if provider is not None:
            lines.append("")
            lines.append("Model")
            lines.append(f"provider: {getattr(provider, 'name', 'unknown')}")
            lines.append(f"model: {getattr(provider, 'model', 'unknown')}")
            api_mode = getattr(getattr(provider, "api_mode", None), "value", "unknown")
            lines.append(f"transport: {api_mode}")
            auth = getattr(provider, "auth", None)
            if auth is None:
                lines.append("auth: unknown")
            else:
                try:
                    auth_desc = auth.describe()
                except Exception:  # noqa: BLE001
                    auth_desc = "unknown"
                try:
                    auth_ready = "ready" if auth.available() else "missing"
                except Exception:  # noqa: BLE001
                    auth_ready = "unknown"
                lines.append(f"auth: {auth_desc} ({auth_ready})")
        elif config is not None:
            lines.append("")
            lines.append("Model")
            lines.append(f"provider: {config.get('model.provider')}")
            lines.append(f"model: {config.get('model.default')}")

        if config is not None:
            try:
                from ..surface import plugin_inventory, skill_inventory, tool_inventory

                tools = tool_inventory(config)
                skills = skill_inventory(config, ctx.cwd)
                plugins = plugin_inventory()
                lines.append("")
                lines.append("Surface")
                lines.append(f"toolsets: {', '.join(tools.toolsets)}")
                lines.append(f"tools: {tools.enabled_count}/{tools.total_count} model-visible")
                lines.append(
                    f"skills: {skills.available_count} available "
                    f"({skills.bundled_count} bundled, {skills.personal_count} personal)"
                )
                lines.append(
                    f"plugins: {plugins.files_count} file(s), {len(plugins.tools)} tool(s), "
                    f"{len(plugins.errors)} error(s)"
                )
            except Exception as e:  # noqa: BLE001
                lines.append(f"surface: error: {type(e).__name__}: {e}")

            workspace = cfg.workspace_dir()
            workspace_files = []
            if workspace.exists():
                workspace_files = sorted(p.name for p in workspace.iterdir() if p.is_file())
            lines.append("")
            lines.append("Workspace")
            lines.append(f"path: {workspace}")
            lines.append("files: " + (", ".join(workspace_files) if workspace_files else "(empty)"))

            host = config.get("server.dashboard_host", "127.0.0.1")
            port = config.get("server.dashboard_port", 9119)
            dashboard_state = "configured" if config.get("server.dashboard_token") else "no token configured"
            lines.append("")
            lines.append("Dashboard")
            lines.append(f"url: http://{host}:{port}/")
            lines.append(f"access: {dashboard_state}")

            try:
                from ..daemon import status as daemon_status

                lines.append("")
                lines.append("Services")
                for unit, state in daemon_status().items():
                    lines.append(f"{unit}: {state}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"services: unavailable: {type(e).__name__}: {e}")

            channels = config.get("gateway.channels", []) or []
            mcp_servers = config.get("mcp.servers", {}) or {}
            lines.append("")
            lines.append("Integrations")
            lines.append(f"gateway channels: {', '.join(channels) or 'none'}")
            lines.append(f"mcp servers: {len(mcp_servers)}")

        return ToolResult.ok("\n".join(lines), display="system status")


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(raw: str) -> str:
    raw = _TAG_RE.sub(" ", raw)
    raw = _HTML_RE.sub(" ", raw)
    raw = html.unescape(raw)
    raw = _WS_RE.sub("\n\n", raw)
    return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its readable text content."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }

    def run(self, args, ctx) -> ToolResult:
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from ..net_safety import guard
        blocked = guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            with httpx.Client(timeout=30, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (AEGIS)"}) as c:
                r = c.get(url)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                body = r.text
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"fetch failed: {e}")
        text = _html_to_text(body) if "html" in ctype else body
        return ToolResult.ok(truncate(text, MAX_OUTPUT), display=f"fetched {url[:60]}")


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets. Backend configurable (web.search_backend)."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    _RESULT = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:class="result__snippet"[^>]*>(.*?)</a>)?',
        re.S,
    )

    def run(self, args, ctx) -> ToolResult:
        query = args["query"]
        backend = (ctx.config.get("web.search_backend", "auto") if ctx.config else "auto")
        # auto: pick a keyed backend if available, else DuckDuckGo (no key)
        if backend == "auto":
            if os.environ.get("BRAVE_API_KEY"):
                backend = "brave"
            elif os.environ.get("TAVILY_API_KEY"):
                backend = "tavily"
            elif os.environ.get("SERPER_API_KEY"):
                backend = "serper"
            else:
                backend = "duckduckgo"
        try:
            if backend == "brave":
                return self._brave(query, os.environ["BRAVE_API_KEY"])
            if backend == "tavily":
                return self._tavily(query, os.environ["TAVILY_API_KEY"])
            if backend == "serper":
                return self._serper(query, os.environ["SERPER_API_KEY"])
            return self._ddg(query)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"search ({backend}) failed: {e}")

    @staticmethod
    def _fmt(items: list[tuple[str, str, str]], query: str) -> ToolResult:
        out = [f"• {t}\n  {u}\n  {s}".rstrip() for t, u, s in items[:8]]
        return ToolResult.ok("\n\n".join(out) or "(no results)", display=f"search: {query[:50]}")

    def _ddg(self, query: str) -> ToolResult:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (AEGIS)"}) as c:
            r = c.get("https://html.duckduckgo.com/html/", params={"q": query})
            r.raise_for_status()
        items = [(_html_to_text(m.group(2) or ""), html.unescape(m.group(1)), _html_to_text(m.group(3) or ""))
                 for m in self._RESULT.finditer(r.text)]
        return self._fmt(items, query)

    def _brave(self, query: str, key: str) -> ToolResult:
        with httpx.Client(timeout=30) as c:
            r = c.get("https://api.search.brave.com/res/v1/web/search",
                      params={"q": query, "count": 8},
                      headers={"X-Subscription-Token": key, "Accept": "application/json"})
            r.raise_for_status()
        items = [(w.get("title", ""), w.get("url", ""), w.get("description", ""))
                 for w in r.json().get("web", {}).get("results", [])]
        return self._fmt(items, query)

    def _tavily(self, query: str, key: str) -> ToolResult:
        with httpx.Client(timeout=30) as c:
            r = c.post("https://api.tavily.com/search",
                       json={"api_key": key, "query": query, "max_results": 8})
            r.raise_for_status()
        items = [(w.get("title", ""), w.get("url", ""), w.get("content", ""))
                 for w in r.json().get("results", [])]
        return self._fmt(items, query)

    def _serper(self, query: str, key: str) -> ToolResult:
        with httpx.Client(timeout=30) as c:
            r = c.post("https://google.serper.dev/search", json={"q": query},
                       headers={"X-API-KEY": key, "Content-Type": "application/json"})
            r.raise_for_status()
        items = [(w.get("title", ""), w.get("link", ""), w.get("snippet", ""))
                 for w in r.json().get("organic", [])]
        return self._fmt(items, query)


# --------------------------------------------------------------------------- #
# Task management
# --------------------------------------------------------------------------- #
class TodoWriteTool(Tool):
    name = "todo_write"
    description = "Record/replace the working task list. Pass the full list each call."
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }

    def run(self, args, ctx) -> ToolResult:
        todos = args["todos"]
        if ctx.session is not None:
            ctx.session.todos = todos
        marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        rendered = "\n".join(f"{marks.get(t['status'], '[ ]')} {t['content']}" for t in todos)
        done = sum(1 for t in todos if t["status"] == "completed")
        return ToolResult.ok(rendered or "(empty)", display=f"todos {done}/{len(todos)} done")


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class MemoryTool(Tool):
    name = "memory"
    description = (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future sessions, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state; use session_search to recall those from past transcripts. If you've solved a "
        "repeatable problem, save it as a skill with the skill tool instead.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is — name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes — environment facts, project conventions, tool quirks, lessons learned\n\n"
        "ACTIONS: add (new entry), replace (update existing — old_text identifies it), "
        "remove (delete — old_text identifies it).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, temporary task state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "replace", "remove"]},
            "target": {"type": "string", "enum": ["memory", "user"],
                       "description": "'memory' for personal notes, 'user' for the user profile."},
            "content": {"type": "string",
                        "description": "The entry content. Required for add and replace."},
            "old_text": {"type": "string",
                         "description": "Short unique substring identifying the entry to "
                                        "replace or remove."},
        },
        "required": ["action", "target"],
    }

    def run(self, args, ctx) -> ToolResult:
        if ctx.memory is None:
            return ToolResult.error("memory is not enabled.")
        return ctx.memory.handle_tool(args)


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
class SkillTool(Tool):
    name = "skill"
    description = (
        "Work with skills (your procedural memory). action: list | view (load full body) | "
        "create (SAVE a reusable skill after solving a non-trivial, repeatable task) | "
        "improve (append a learned note to an existing skill) | stats (usage counts). "
        "Creating and improving skills is how you get better over time."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "view", "create", "improve", "stats"]},
            "name": {"type": "string", "description": "skill name (lowercase-with-hyphens)"},
            "description": {"type": "string", "description": "what it does and WHEN to use it (for create)"},
            "body": {"type": "string", "description": "SKILL.md body (create) or a learned note (improve)"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx) -> ToolResult:
        if ctx.skills is None:
            return ToolResult.error("skills are not available.")
        action = args["action"]
        if action == "list":
            return ToolResult.ok(ctx.skills.index_block() or "(no skills)", display="listed skills")
        if action == "create":
            if not all(args.get(k) for k in ("name", "description", "body")):
                return ToolResult.error("create needs name, description, and body.")
            try:
                path = ctx.skills.create(args["name"], args["description"], args["body"])
            except Exception as e:  # noqa: BLE001
                return ToolResult.error(f"could not create skill: {e}")
            _refresh_agent_prompt(ctx)
            return ToolResult.ok(f"saved skill '{args['name']}' to {path}", display=f"created skill {args['name']}")
        if action == "improve":
            if not args.get("name") or not args.get("body"):
                return ToolResult.error("improve needs name and body (the learned note).")
            path = ctx.skills.improve(args["name"], args["body"])
            if path is None:
                return ToolResult.error(f"skill '{args['name']}' not found.")
            _refresh_agent_prompt(ctx)
            return ToolResult.ok(f"recorded a learned note on '{args['name']}'", display=f"improved {args['name']}")
        if action == "stats":
            usage = ctx.skills.usage()
            if not usage:
                return ToolResult.ok("(no skill usage recorded yet)", display="skill stats")
            rows = sorted(usage.items(), key=lambda kv: -kv[1].get("count", 0))
            return ToolResult.ok("\n".join(f"{n}: used {u['count']}x (last {u.get('last_used','?')})"
                                           for n, u in rows), display="skill stats")
        name = args.get("name")
        if not name:
            return ToolResult.error("name is required for view.")
        body = ctx.skills.activate(name)
        if body is None:
            return ToolResult.error(f"skill '{name}' not found.")
        return ToolResult.ok(body, display=f"loaded skill {name}")


def _refresh_agent_prompt(ctx) -> None:
    agent = getattr(ctx, "agent", None)
    refresh = getattr(agent, "refresh_volatile", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001
            pass


def all_builtin_tools() -> list[Tool]:
    return [
        ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool(), GlobTool(), SearchTool(),
        BashTool(),
        SystemStatusTool(),
        WebFetchTool(), WebSearchTool(),
        TodoWriteTool(),
        MemoryTool(),
        SkillTool(),
    ]
