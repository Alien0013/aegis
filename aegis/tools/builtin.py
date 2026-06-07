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
        if not path.exists():
            return ToolResult.error(f"No such file: {path}")
        if path.is_dir():
            return ToolResult.error(f"{path} is a directory (use list_dir).")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {e}")
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 2000))
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{offset + i:6d}\t{ln}" for i, ln in enumerate(chunk))
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
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(args["content"], encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {e}")
        n = args["content"].count("\n") + 1
        return ToolResult.ok(f"Wrote {n} lines to {path}", display=f"wrote {path.name}")


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
        text = path.read_text(encoding="utf-8", errors="replace")
        old, new = args["old_string"], args["new_string"]
        count = text.count(old)
        if count == 0:
            return ToolResult.error("old_string not found in file.")
        if count > 1 and not args.get("replace_all"):
            return ToolResult.error(f"old_string appears {count}× — pass replace_all=true or add context.")
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        return ToolResult.ok(f"Edited {path} ({count if args.get('replace_all') else 1} replacement(s)).",
                             display=f"edited {path.name}")


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
        },
        "required": ["command"],
    }

    def run(self, args, ctx) -> ToolResult:
        timeout = min(int(args.get("timeout", 120)), 600)
        try:
            proc = subprocess.run(
                args["command"], shell=True, cwd=str(ctx.cwd),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.error(f"command timed out after {timeout}s")
        out = proc.stdout
        if proc.stderr:
            out += ("\n[stderr]\n" + proc.stderr)
        out = out.strip() or "(no output)"
        tail = f"\n[exit {proc.returncode}]"
        return ToolResult(
            content=truncate(out, MAX_OUTPUT) + tail,
            is_error=proc.returncode != 0,
            display=f"$ {args['command'][:60]} (exit {proc.returncode})",
        )


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
    description = "Search the web (DuckDuckGo). Returns titles, URLs, and snippets."
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
        try:
            with httpx.Client(timeout=30, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (AEGIS)"}) as c:
                r = c.get("https://html.duckduckgo.com/html/", params={"q": args["query"]})
                r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"search failed: {e}")
        out: list[str] = []
        for m in self._RESULT.finditer(r.text):
            url = html.unescape(m.group(1))
            title = _html_to_text(m.group(2) or "")
            snippet = _html_to_text(m.group(3) or "")
            out.append(f"• {title}\n  {url}\n  {snippet}".rstrip())
            if len(out) >= 8:
                break
        return ToolResult.ok("\n\n".join(out) or "(no results)", display=f"search: {args['query'][:50]}")


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
        "Persist long-term facts across sessions. action: add|replace|remove. "
        "target: memory (project/world facts) or user (preferences about the user)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "replace", "remove"]},
            "target": {"type": "string", "enum": ["memory", "user"]},
            "content": {"type": "string", "description": "Fact to add, or new text for replace."},
            "match": {"type": "string", "description": "Substring to find for replace/remove."},
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
        "Work with skills. action: list (show all) | view (load a skill's full body) | "
        "create (SAVE a reusable skill after you solve a non-trivial, repeatable task — "
        "this is how you improve over time). create needs name, description, body."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "view", "create"]},
            "name": {"type": "string", "description": "skill name (lowercase-with-hyphens)"},
            "description": {"type": "string", "description": "what it does and WHEN to use it (for create)"},
            "body": {"type": "string", "description": "the SKILL.md markdown body (for create)"},
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
            return ToolResult.ok(f"saved skill '{args['name']}' to {path}", display=f"created skill {args['name']}")
        name = args.get("name")
        if not name:
            return ToolResult.error("name is required for view.")
        body = ctx.skills.activate(name)
        if body is None:
            return ToolResult.error(f"skill '{name}' not found.")
        return ToolResult.ok(body, display=f"loaded skill {name}")


def all_builtin_tools() -> list[Tool]:
    return [
        ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool(), GlobTool(), SearchTool(),
        BashTool(),
        WebFetchTool(), WebSearchTool(),
        TodoWriteTool(),
        MemoryTool(),
        SkillTool(),
    ]
