"""Built-in tools: filesystem, shell, web, todo, memory, skills."""

from __future__ import annotations

import html
import fnmatch
import json
import os
import re
import shutil
import subprocess
import stat
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx

from ..util import truncate
from .base import Tool, ToolContext, ToolResult

MAX_OUTPUT = 30_000
DEFAULT_FILE_READ_MAX_CHARS = 100_000
DEFAULT_FILE_READ_MAX_LINES = 2000
DEFAULT_FILE_READ_MAX_LINE_LENGTH = 2000
_UTF8_BOM = "\ufeff"
_READ_DISPLAY_PREFIX_RE = re.compile(r"^\s*(\d+)(?:\||\t)")
_TEXT_READ_BINARY_EXTENSIONS = frozenset({
    ".7z", ".a", ".aac", ".aiff", ".app", ".avi", ".bin", ".blend", ".bmp", ".bz2",
    ".class", ".dat", ".data", ".db", ".deb", ".dll", ".doc", ".docx", ".dylib",
    ".ear", ".eot", ".exe", ".fig", ".fla", ".flac", ".flv", ".gif", ".gz", ".ico",
    ".idx", ".iso", ".jar", ".jpeg", ".jpg", ".lockb", ".m4a", ".m4v", ".max",
    ".mdb", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".msi", ".node",
    ".o", ".obj", ".odp", ".ods", ".odt", ".ogg", ".opus", ".otf", ".ppt", ".pptx",
    ".psd", ".pyc", ".pyo", ".rar", ".rlib", ".rpm", ".sketch", ".so", ".sqlite",
    ".sqlite3", ".swf", ".tar", ".tgz", ".tif", ".tiff", ".ttf", ".war", ".wasm",
    ".wav", ".webm", ".webp", ".wmv", ".woff", ".woff2", ".xls", ".xlsx", ".xz",
    ".z", ".zip",
})


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (ctx.cwd / p)


def _cfg_int(ctx: ToolContext, dotted: str, default: int, *, aliases: tuple[str, ...] = ()) -> int:
    cfg = getattr(ctx, "config", None)
    keys = aliases + (dotted,)
    try:
        if cfg is None:
            return default
        for key in keys:
            value = cfg.get(key, None)
            if value is None:
                continue
            value = int(value)
            return value if value > 0 else default
    except (TypeError, ValueError):
        return default
    return default


def _max_output_chars(ctx: ToolContext) -> int:
    return _cfg_int(ctx, "tools.max_output_chars", MAX_OUTPUT, aliases=("tool_output.max_bytes",))


def _file_read_max_chars(ctx: ToolContext) -> int:
    return _cfg_int(ctx, "tools.file_read_max_chars", DEFAULT_FILE_READ_MAX_CHARS)


def _file_read_max_lines(ctx: ToolContext) -> int:
    return _cfg_int(ctx, "tools.file_read_max_lines", DEFAULT_FILE_READ_MAX_LINES, aliases=("tool_output.max_lines",))


def _file_read_max_line_length(ctx: ToolContext) -> int:
    return _cfg_int(
        ctx,
        "tools.file_read_max_line_length",
        DEFAULT_FILE_READ_MAX_LINE_LENGTH,
        aliases=("tool_output.max_line_length",),
    )


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _strip_bom(text: str) -> tuple[str, bool]:
    if text.startswith(_UTF8_BOM):
        return text[len(_UTF8_BOM):], True
    return text, False


def _restore_bom(text: str, had_bom: bool) -> str:
    if not had_bom:
        return text
    return text if text.startswith(_UTF8_BOM) else _UTF8_BOM + text


def _detect_line_ending(text: str) -> str | None:
    head = text[:4096]
    if "\r\n" in head:
        return "\r\n"
    if "\n" in head:
        return "\n"
    return None


def _normalize_line_endings(text: str, ending: str | None) -> str:
    if ending not in ("\n", "\r\n"):
        return text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if ending == "\n" else normalized.replace("\n", "\r\n")


def _read_text_for_edit(path: Path) -> tuple[str, bool, str | None]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read()
    text, had_bom = _strip_bom(text)
    return text, had_bom, _detect_line_ending(text)


def _atomic_write_local(path: Path, content: str) -> None:
    mode = None
    try:
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _line_for_read(line: str, max_len: int) -> str:
    if len(line) <= max_len:
        return line
    return line[:max_len] + " ... [line truncated]"


def _has_text_read_binary_extension(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_READ_BINARY_EXTENSIONS


def _normalize_for_write_verify(text: str) -> str:
    text, _had_bom = _strip_bom(text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _verify_local_write(path: Path, expected: str) -> str:
    try:
        actual = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Post-write verification failed for {path}: could not re-read file ({exc})."
    if _normalize_for_write_verify(actual) == _normalize_for_write_verify(expected):
        return ""
    return (
        f"Post-write verification failed for {path}: on-disk content differs "
        "from the intended write. The edit did not persist; re-read the file and try again."
    )


def _is_read_file_display_text(content: str) -> bool:
    """Detect content dominated by read_file line-number gutters."""
    if not isinstance(content, str):
        return False
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    numbered: list[int] = []
    for line in lines:
        match = _READ_DISPLAY_PREFIX_RE.match(line)
        if match:
            numbered.append(int(match.group(1)))
    if len(numbered) < 2 or len(numbered) / len(lines) < 0.6:
        return False
    consecutive = sum(
        1 for previous, current in zip(numbered, numbered[1:])
        if current == previous + 1
    )
    return consecutive >= len(numbered) - 1


def _is_internal_file_tool_content(content: str) -> bool:
    return _is_read_file_display_text(content)


def _task_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "task_id", "") or "")


def _active_backend_env(ctx: ToolContext):
    cfg = getattr(ctx, "config", None)
    if cfg is None:
        return None
    task_id = _task_id(ctx) or "default"
    try:
        from .backends import effective_backend, get_active_environment

        backend = effective_backend(str(cfg.get("tools.terminal_backend", "local") or "local"), task_id)
        if backend == "local":
            return None
        return get_active_environment(task_id, backend)
    except Exception:  # noqa: BLE001
        return None


def _read_text_from_active_backend(path: Path, ctx: ToolContext) -> str | None:
    env = _active_backend_env(ctx)
    if env is None:
        return None
    try:
        from .tool_result_storage import load_persisted_tool_result_path

        content, _metadata = load_persisted_tool_result_path(str(path), env=env)
        return content
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #
class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file. Returns content with 1-based line numbers. Use offset/limit for large files."
    extra_toolsets = ["file"]
    max_result_size_chars = DEFAULT_FILE_READ_MAX_CHARS
    output_limits = {
        "max_chars": "config:tools.file_read_max_chars",
        "max_result_size_chars": DEFAULT_FILE_READ_MAX_CHARS,
        "policy": "paginate",
    }
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
        remote_text: str | None = None
        if fs is None:                       # real filesystem (default)
            if not path.exists():
                remote_text = _read_text_from_active_backend(path, ctx)
                if remote_text is None:
                    return ToolResult.error(f"No such file: {path}")
            if path.is_dir():
                return ToolResult.error(f"{path} is a directory (use list_dir).")
            if remote_text is None:
                from . import file_safety
                denied = file_safety.authorize_read(path, ctx)
                if denied:
                    return ToolResult.error(denied)
                if _has_text_read_binary_extension(path):
                    return ToolResult.error(
                        f"Cannot read {path}: binary file extension - use an appropriate tool instead."
                    )
                try:
                    with open(path, "rb") as sample:
                        sample_bytes = sample.read(1000)
                    if b"\0" in sample_bytes:
                        return ToolResult.error(
                            f"Cannot read {path}: binary file - use an appropriate tool instead."
                        )
                except OSError:
                    pass
        try:
            if remote_text is not None:
                text = remote_text
            else:
                text = fs.read_text(str(path)) if fs else path.read_text(encoding="utf-8", errors="replace")
            text, _had_bom = _strip_bom(text)
            lines = text.splitlines()
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {e}")
        offset = max(1, _coerce_int(args.get("offset", 1), 1))
        limit = _coerce_int(args.get("limit", DEFAULT_FILE_READ_MAX_LINES), DEFAULT_FILE_READ_MAX_LINES)
        limit = max(1, min(limit, _file_read_max_lines(ctx)))
        max_line = _file_read_max_line_length(ctx)
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{offset + i}|{_line_for_read(ln, max_line)}" for i, ln in enumerate(chunk))
        if len(body) > _file_read_max_chars(ctx):
            return ToolResult.error(
                f"read_file result exceeds safety limit ({_file_read_max_chars(ctx)} chars). "
                f"Use offset and limit to read a smaller section. total_lines={len(lines)}"
            )
        if fs is None:
            from . import file_state
            partial = offset > 1 or limit < len(lines)
            # Freshness stamp for later stale-write checks.
            file_state.record_read(_task_id(ctx), path, partial=partial)
        return ToolResult.ok(
            truncate(body, _max_output_chars(ctx)),
            display=f"read {path.name} ({len(chunk)} lines)",
        )


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content. Creates parent directories."
    groups = ["fs"]
    extra_toolsets = ["file"]
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
        content = str(args["content"])
        if _is_internal_file_tool_content(content):
            return ToolResult.error(
                "Refusing to write internal read_file display text as file content. "
                "Strip line-number prefixes or reconstruct the intended file contents before writing."
            )
        fs = getattr(ctx, "fs", None)
        stale = ""
        lock = nullcontext()
        if fs is None:
            from . import file_safety, file_state
            denied = file_safety.authorize_write(path, ctx)
            if denied:
                return ToolResult.error(denied)
            lock = file_state.lock_path(path)
        try:
            with lock:
                if fs:                       # delegate to the editor (ACP fs/write_text_file)
                    fs.write_text(str(path), content)
                else:
                    from . import file_state
                    stale = file_state.stale_warning(path, task_id=_task_id(ctx))
                    _lsp_snapshot(ctx, path)
                    had_bom = False
                    ending = None
                    if path.exists():
                        old_text, had_bom, ending = _read_text_for_edit(path)
                        del old_text
                    normalized_content = _normalize_line_endings(content, ending)
                    _atomic_write_local(path, _restore_bom(normalized_content, had_bom))
                    verification_error = _verify_local_write(path, normalized_content)
                    if verification_error:
                        return ToolResult.error(verification_error)
                    file_state.note_write(_task_id(ctx), path)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {e}")
        n = content.count("\n") + 1
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
        with file_state.lock_path(path):
            stale = file_state.stale_warning(path, task_id=_task_id(ctx))
            text, had_bom, ending = _read_text_for_edit(path)
            old, new = args["old_string"], args["new_string"]
            via = ""
            count = text.count(old)
            if count == 0 and ending:
                old_eol = _normalize_line_endings(old, ending)
                if old_eol != old:
                    count = text.count(old_eol)
                    if count:
                        old = old_eol
                        new = _normalize_line_endings(new, ending)
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
            if ending:
                new = _normalize_line_endings(new, ending)
            if count > 1 and not args.get("replace_all"):
                return ToolResult.error(f"old_string appears {count}× — pass replace_all=true or add context.")
            _lsp_snapshot(ctx, path)
            text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
            _atomic_write_local(path, _restore_bom(text, had_bom))
            verification_error = _verify_local_write(path, text)
            if verification_error:
                return ToolResult.error(verification_error)
            file_state.note_write(_task_id(ctx), path)
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


def _search_pagination(args: dict) -> tuple[int, int]:
    offset = max(0, _coerce_int(args.get("offset", 0), 0))
    limit = max(1, min(_coerce_int(args.get("limit", 50), 50), 500))
    return offset, limit


def _search_json_result(data: dict, ctx: ToolContext, *, display: str) -> ToolResult:
    body = json.dumps(data, ensure_ascii=False)
    return ToolResult.ok(truncate(body, _max_output_chars(ctx)), display=display, data=data)


def _file_search_pattern(pattern: str) -> str:
    if "/" not in pattern and not any(ch in pattern for ch in "*?[]"):
        return f"*{pattern}*"
    return pattern


def _slice_with_truncation(items: list, offset: int, limit: int) -> tuple[list, bool]:
    page = items[offset:offset + limit]
    return page, len(items) > offset + limit


def _add_next_offset(data: dict[str, object], offset: int, limit: int) -> dict[str, object]:
    if data.get("truncated"):
        data["next_offset"] = offset + limit
    return data


def _parse_rg_match_line(line: str) -> dict[str, object] | None:
    match = re.match(r"^([A-Za-z]:)?(.*?):(\d+):(.*)$", line)
    if not match:
        return None
    return {
        "path": (match.group(1) or "") + match.group(2),
        "line": int(match.group(3)),
        "content": match.group(4)[:500],
    }


def _parse_rg_context_line(line: str) -> dict[str, object] | None:
    if not line or line == "--":
        return None
    match = None
    for candidate in re.finditer(r"-(\d+)-", line):
        match = candidate
    if match is None:
        return None
    path = line[:match.start()]
    if not path:
        return None
    return {"path": path, "line": int(match.group(1)), "content": line[match.end():][:500]}


def _densify_matches(matches: list[dict[str, object]]) -> dict[str, object]:
    if len(matches) < 5:
        return {"matches": matches}
    lines: list[str] = []
    current_path = None
    for item in matches:
        path = str(item.get("path", ""))
        if path != current_path:
            lines.append(path)
            current_path = path
        lines.append(f"  {item.get('line')}: {str(item.get('content', '')).rstrip()}")
    return {
        "matches_format": (
            "path-grouped: each file path on its own line, followed by "
            "indented '<line>: <content>' rows for matches in that file"
        ),
        "matches_text": "\n".join(lines),
    }


class SearchTool(Tool):
    name = "search"
    description = "Search file contents or find files by name. Supports reference-style search_files pagination and output modes."
    extra_toolsets = ["file"]
    max_result_size_chars = 100_000
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Dir/file to search (default cwd)."},
            "target": {
                "type": "string",
                "enum": ["content", "files", "grep", "find"],
                "description": "'content'/'grep' searches file text; 'files'/'find' searches file names.",
            },
            "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py'."},
            "file_glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py'."},
            "limit": {"type": "integer", "description": "Maximum results to return (default 50)."},
            "offset": {"type": "integer", "description": "Skip this many results for pagination."},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": "Content search output shape.",
            },
            "context": {"type": "integer", "description": "Context lines around content matches."},
        },
        "required": ["pattern"],
    }

    def run(self, args, ctx) -> ToolResult:
        base = _resolve(ctx, args.get("path", "."))
        pattern = args["pattern"]
        target_map = {"grep": "content", "find": "files"}
        target = target_map.get(str(args.get("target", "content") or "content"), args.get("target", "content"))
        offset, limit = _search_pagination(args)
        file_glob = args.get("file_glob") or args.get("glob")
        output_mode = str(args.get("output_mode", "content") or "content")
        context = max(0, min(_coerce_int(args.get("context", 0), 0), 20))
        if not base.exists():
            return ToolResult.error(f"Path not found: {base}")
        if target == "files":
            return self._search_files(base, pattern, offset, limit, ctx)
        return self._search_content(base, pattern, file_glob, output_mode, context, offset, limit, ctx)

    def _search_files(self, base: Path, pattern: str, offset: int, limit: int, ctx: ToolContext) -> ToolResult:
        rg = shutil.which("rg")
        if rg:
            glob_pattern = _file_search_pattern(pattern)
            fetch_limit = offset + limit + 1
            cmd = [rg, "--files", "--sortr=modified", "-g", glob_pattern, str(base)]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if out.returncode not in (0, 1):
                    cmd = [rg, "--files", "-g", glob_pattern, str(base)]
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                files = [line for line in out.stdout.splitlines() if line][:fetch_limit]
            except subprocess.TimeoutExpired:
                return ToolResult.error("search timed out")
            page, truncated = _slice_with_truncation(files, offset, limit)
            data = _add_next_offset({"total_count": len(files), "files": page, "truncated": truncated}, offset, limit)
            return _search_json_result(data, ctx, display=f"search files ({len(page)} files)")

        glob_pattern = _file_search_pattern(pattern)
        matches = [
            str(path)
            for path in base.rglob(glob_pattern)
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(base).parts)
        ][:offset + limit + 1]
        matches.sort(key=lambda item: Path(item).stat().st_mtime if Path(item).exists() else 0, reverse=True)
        page, truncated = _slice_with_truncation(matches, offset, limit)
        data = _add_next_offset({"total_count": len(matches), "files": page, "truncated": truncated}, offset, limit)
        return _search_json_result(data, ctx, display=f"search files ({len(page)} files)")

    def _search_content(
        self,
        base: Path,
        pattern: str,
        file_glob: str | None,
        output_mode: str,
        context: int,
        offset: int,
        limit: int,
        ctx: ToolContext,
    ) -> ToolResult:
        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "--line-number", "--no-heading", "--with-filename", "--color=never"]
            if context:
                cmd.extend(["-C", str(context)])
            if file_glob:
                cmd.extend(["-g", str(file_glob)])
            if output_mode == "files_only":
                cmd.append("-l")
            elif output_mode == "count":
                cmd.append("-c")
            cmd.extend([pattern, str(base)])
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                return ToolResult.error("search timed out")
            if out.returncode == 2 and not out.stdout.strip():
                return ToolResult.error(f"Search failed: {(out.stderr or out.stdout or 'search error').strip()}")
            lines = [line for line in out.stdout.splitlines() if line and not line.startswith("rg: ")]
            if output_mode == "files_only":
                files, truncated = _slice_with_truncation(lines, offset, limit)
                data = _add_next_offset({"total_count": len(lines), "files": files, "truncated": truncated}, offset, limit)
                return _search_json_result(data, ctx, display=f"search files_only ({len(files)} files)")
            if output_mode == "count":
                counts: dict[str, int] = {}
                for line in lines:
                    path, sep, raw_count = line.rpartition(":")
                    if sep:
                        try:
                            counts[path] = int(raw_count)
                        except ValueError:
                            continue
                data = {"total_count": sum(counts.values()), "counts": counts}
                return _search_json_result(data, ctx, display=f"search count ({len(counts)} files)")
            matches = []
            for line in lines:
                parsed = _parse_rg_match_line(line)
                if parsed is None and context:
                    parsed = _parse_rg_context_line(line)
                if parsed is not None:
                    matches.append(parsed)
            page, truncated = _slice_with_truncation(matches, offset, limit)
            data: dict[str, object] = _add_next_offset({"total_count": len(matches), "truncated": truncated}, offset, limit)
            data.update(_densify_matches(page))
            return _search_json_result(data, ctx, display=f"search ({len(page)} hits)")

        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult.error(f"bad regex: {e}")
        files = [base] if base.is_file() else base.rglob(file_glob or "*")
        matches: list[dict[str, object]] = []
        file_hits: list[str] = []
        counts: dict[str, int] = {}
        for path in files:
            if not path.is_file():
                continue
            if file_glob and not fnmatch.fnmatch(path.name, file_glob) and not fnmatch.fnmatch(str(path), file_glob):
                continue
            try:
                file_count = 0
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        file_count += 1
                        if output_mode == "content":
                            matches.append({"path": str(path), "line": i, "content": line[:500]})
                if file_count:
                    file_hits.append(str(path))
                    counts[str(path)] = file_count
            except Exception:  # noqa: BLE001
                continue
            if len(matches) >= offset + limit + 1:
                break
        if output_mode == "files_only":
            page, truncated = _slice_with_truncation(file_hits, offset, limit)
            data = _add_next_offset({"total_count": len(file_hits), "files": page, "truncated": truncated}, offset, limit)
            return _search_json_result(data, ctx, display=f"search files_only ({len(page)} files)")
        if output_mode == "count":
            data = {"total_count": sum(counts.values()), "counts": counts}
            return _search_json_result(data, ctx, display=f"search count ({len(counts)} files)")
        page, truncated = _slice_with_truncation(matches, offset, limit)
        data = _add_next_offset({"total_count": len(matches), "truncated": truncated}, offset, limit)
        data.update(_densify_matches(page))
        return _search_json_result(data, ctx, display=f"search ({len(page)} hits)")


# --------------------------------------------------------------------------- #
# Shell
# --------------------------------------------------------------------------- #
class BashTool(Tool):
    name = "bash"
    description = "Run a shell command in the working directory and return stdout/stderr."
    groups = ["runtime"]
    extra_toolsets = ["terminal"]
    max_result_size_chars = 100_000
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds (default 120, max 600)."},
            "background": {
                "type": "boolean",
                "description": "Start the command as a managed background process and return a session_id.",
            },
            "pty": {
                "type": "boolean",
                "description": (
                    "Use a pseudo-terminal for a local background process, useful for "
                    "interactive CLIs. Falls back to pipe mode if PTY support is unavailable."
                ),
            },
            "notify_on_complete": {
                "type": "boolean",
                "description": (
                    "Queue a wakeup when a background process exits (default true unless "
                    "watch_patterns is set). Mutually exclusive with watch_patterns."
                ),
            },
            "watch_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Strings to watch for in background output. At most one notification is "
                    "emitted per 15 seconds per process; noisy patterns are disabled after "
                    "three strike windows and promoted to notify_on_complete."
                ),
            },
        },
        "required": ["command"],
    }

    def run(self, args, ctx) -> ToolResult:
        from .backends import create_environment, effective_backend, run_command
        from .command_utils import rewrite_compound_background, validate_command

        command, command_error = validate_command(args.get("command"))
        if command_error:
            return ToolResult.error(command_error)
        command = rewrite_compound_background(command)
        timeout = min(int(args.get("timeout", 120)), 600)
        backend = ctx.config.get("tools.terminal_backend", "local") if ctx.config else "local"
        task_id = getattr(ctx, "task_id", "") or ""
        backend = effective_backend(backend, task_id)
        if args.get("background"):
            from .process_registry import process_registry

            agent = getattr(ctx, "agent", None)
            watch_patterns = _watch_patterns(args.get("watch_patterns"))
            notify_on_complete = bool(args.get("notify_on_complete", not watch_patterns))
            ignored_note = ""
            if notify_on_complete and watch_patterns:
                ignored_note = (
                    "watch_patterns ignored because notify_on_complete=True; "
                    "these two flags produce duplicate notifications when combined"
                )
                watch_patterns = []
            session_key = str(getattr(getattr(ctx, "session", None), "id", "") or task_id)
            watcher_kwargs = {
                "session_key": session_key,
                "watcher_platform": getattr(agent, "platform", "") or "",
                "watcher_chat_id": getattr(agent, "chat_id", "") or "",
                "watcher_user_id": getattr(agent, "user_id", "") or "",
                "watcher_user_name": getattr(agent, "user_name", "") or "",
                "watcher_thread_id": getattr(agent, "thread_id", "") or "",
                "watcher_message_id": getattr(agent, "message_id", "") or "",
            }
            if backend == "local":
                proc = process_registry.spawn_local(
                    command,
                    cwd=ctx.cwd,
                    task_id=task_id,
                    notify_on_complete=notify_on_complete,
                    watch_patterns=watch_patterns,
                    use_pty=bool(args.get("pty", False)),
                    **watcher_kwargs,
                )
            else:
                env, error, backend = create_environment(
                    backend,
                    str(ctx.cwd),
                    timeout,
                    ctx.config,
                    task_id=task_id or None,
                )
                if env is None:
                    return ToolResult.error(error)
                proc = process_registry.spawn_via_env(
                    env,
                    command,
                    cwd=str(ctx.cwd),
                    task_id=task_id,
                    notify_on_complete=notify_on_complete,
                    watch_patterns=watch_patterns,
                    timeout=10,
                    **watcher_kwargs,
                )
                if proc.exited:
                    return ToolResult.error(
                        f"background process failed to start on {backend}: "
                        f"{proc.output_buffer or proc.exit_code}"
                    )
            lines = [
                "Background process started",
                f"session_id: {proc.id}",
                f"pid: {proc.pid}",
                f"backend: {backend}",
            ]
            if proc.pty:
                lines.append("pty: true")
            if proc.pty_fallback:
                lines.append(f"pty_fallback: {proc.pty_fallback}")
            if notify_on_complete:
                lines.append("notify_on_complete: true")
            if watch_patterns:
                lines.append(f"watch_patterns: {', '.join(watch_patterns)}")
            if ignored_note:
                lines.append(ignored_note)
            lines.append(
                "Use process(action='poll'|'log'|'wait'|'kill', session_id=...) "
                "to inspect or control it."
            )
            data = {
                "session_id": proc.id,
                "pid": proc.pid,
                "backend": backend,
                "notify_on_complete": notify_on_complete,
            }
            if proc.pty:
                data["pty"] = True
            if proc.pty_fallback:
                data["pty_fallback"] = proc.pty_fallback
            if watch_patterns:
                data["watch_patterns"] = watch_patterns
            if ignored_note:
                data["watch_patterns_ignored"] = ignored_note
            return ToolResult.ok(
                "\n".join(lines),
                display=f"background {proc.id}",
                data=data,
            )
        out, code = run_command(
            command,
            str(ctx.cwd),
            timeout,
            backend,
            ctx.config,
            task_id=task_id or None,
        )
        out = out.strip() or "(no output)"
        tail = f"\n[exit {code}]"
        return ToolResult(
            content=truncate(out, _max_output_chars(ctx)) + tail,
            is_error=code != 0,
            display=f"$ {command[:60]} (exit {code})",
        )


def _watch_patterns(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return []
    return [str(value) for value in values if str(value)]


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

            workspace = cfg.workspace_dir()        # home root (SOUL.md/AGENTS.md live here)
            # only identity/rule files — not the whole home (which holds .env, auth.json …)
            from ..config import Workspace, _ROOT_WORKSPACE_FILES
            identity = list(_ROOT_WORKSPACE_FILES) + list(Workspace.RULE_FILES)
            workspace_files = sorted({n for n in identity if (workspace / n).is_file()})
            lines.append("")
            lines.append("Identity & rules")
            lines.append(f"path: {workspace}")
            lines.append("files: " + (", ".join(workspace_files) if workspace_files else "(none yet)"))

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
# Secrets
# --------------------------------------------------------------------------- #
class SecretTool(Tool):
    name = "secret"
    description = (
        "Safely capture a local secret into ~/.aegis/.env. Pass only the env var name "
        "and a prompt; never pass the secret value as a tool argument."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Uppercase env var name to store, e.g. TELEGRAM_BOT_TOKEN.",
            },
            "prompt": {
                "type": "string",
                "description": "Human prompt shown beside the hidden input.",
            },
        },
        "required": ["key"],
    }

    def run(self, args, ctx) -> ToolResult:
        extra = set(args) - {"key", "prompt"}
        if extra:
            return ToolResult.error(
                "secret values must not be passed as tool arguments; call secret with key/prompt only"
            )
        key = str(args.get("key") or "").strip()
        prompt = str(args.get("prompt") or f"Enter {key}").strip()
        try:
            from ..secret_capture import validate_secret_key

            key = validate_secret_key(key)
        except ValueError as exc:
            return ToolResult.error(str(exc))
        if ctx.secret_capture is None:
            return ToolResult.error(
                "secret capture is unavailable on this surface; run "
                f"`aegis secret set {key}` or use the dashboard Keys page"
            )
        result = ctx.secret_capture(key, prompt, {"tool": "secret"})
        if result.get("skipped"):
            return ToolResult.ok(
                f"Secret setup skipped for {key}.",
                display=f"secret skipped {key}",
                data={"key": key, "skipped": True},
            )
        if result.get("success"):
            return ToolResult.ok(
                f"Secret stored securely as {key}. The value was not exposed to the model.",
                display=f"secret stored {key}",
                data={"key": key, "stored": True},
            )
        return ToolResult.error(str(result.get("message") or "secret capture failed"))


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
    max_result_size_chars = 100_000
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }

    def run(self, args, ctx) -> ToolResult:
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from .. import net_safety
        blocked = net_safety.guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            r = net_safety.request("GET", url, getattr(ctx, "config", None), timeout=30)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text
        except net_safety.BlockedURL as e:
            return ToolResult.error(str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"fetch failed: {e}")
        text = _html_to_text(body) if "html" in ctype else body
        return ToolResult.ok(truncate(text, _max_output_chars(ctx)), display=f"fetched {url[:60]}")


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets. Backend configurable (web.search_backend)."
    groups = ["network"]
    extra_toolsets = ["web", "search"]
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
_TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")
_MAX_TODO_CONTENT_CHARS = 4000
_MAX_TODO_ITEMS = 256
_TODO_TRUNCATION_MARKER = "... [truncated]"


def _todo_cap_content(content: str) -> str:
    if len(content) <= _MAX_TODO_CONTENT_CHARS:
        return content
    keep = max(0, _MAX_TODO_CONTENT_CHARS - len(_TODO_TRUNCATION_MARKER))
    return content[:keep] + _TODO_TRUNCATION_MARKER


def _normalize_todo_item(item: Any, index: int = 0) -> dict[str, str]:
    if not isinstance(item, dict):
        return {"id": str(index + 1), "content": "(invalid item)", "status": "pending"}
    raw_id = str(item.get("id", "")).strip()
    item_id = raw_id or str(index + 1)
    content = str(item.get("content", "")).strip() or "(no description)"
    status = str(item.get("status", "pending")).strip().lower()
    if status not in _TODO_STATUSES:
        status = "pending"
    return {"id": item_id, "content": _todo_cap_content(content), "status": status}


def _dedupe_todos_by_id(todos: list[Any]) -> list[Any]:
    last_index: dict[str, int] = {}
    for index, item in enumerate(todos):
        if isinstance(item, dict):
            item_id = str(item.get("id", "")).strip() or str(index + 1)
        else:
            item_id = f"__invalid_{index}"
        last_index[item_id] = index
    return [todos[index] for index in sorted(last_index.values())]


def _normalize_todos(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items = [_normalize_todo_item(item, index) for index, item in enumerate(_dedupe_todos_by_id(raw))]
    return items[:_MAX_TODO_ITEMS]


def _merge_todos(current: list[dict[str, str]], updates: list[Any]) -> list[dict[str, str]]:
    existing = {item["id"]: item.copy() for item in _normalize_todos(current)}
    order = [item["id"] for item in _normalize_todos(current)]
    for index, raw in enumerate(_dedupe_todos_by_id(updates)):
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id", "")).strip()
        if not item_id:
            item_id = str(len(order) + 1)
            raw = {**raw, "id": item_id}
        if item_id in existing:
            if "content" in raw and str(raw.get("content", "")).strip():
                existing[item_id]["content"] = _todo_cap_content(str(raw["content"]).strip())
            if "status" in raw and str(raw.get("status", "")).strip().lower() in _TODO_STATUSES:
                existing[item_id]["status"] = str(raw["status"]).strip().lower()
        else:
            item = _normalize_todo_item(raw, len(order) + index)
            existing[item["id"]] = item
            order.append(item["id"])
    return [existing[item_id] for item_id in order if item_id in existing][:_MAX_TODO_ITEMS]


def todo_summary(todos: list[dict[str, str]]) -> dict[str, int]:
    return {
        "total": len(todos),
        "pending": sum(1 for item in todos if item.get("status") == "pending"),
        "in_progress": sum(1 for item in todos if item.get("status") == "in_progress"),
        "completed": sum(1 for item in todos if item.get("status") == "completed"),
        "cancelled": sum(1 for item in todos if item.get("status") == "cancelled"),
    }


def active_todo_injection(todos: list[dict[str, str]] | None) -> str:
    active = [
        item for item in _normalize_todos(todos or [])
        if item["status"] in {"pending", "in_progress"}
    ]
    if not active:
        return ""
    markers = {
        "pending": "[ ]",
        "in_progress": "[>]",
    }
    lines = ["# Active Todo List", "Preserved across context compression/session rebuild:"]
    for item in active:
        lines.append(f"- {markers.get(item['status'], '[ ]')} {item['id']}. {item['content']} ({item['status']})")
    return "\n".join(lines)


class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "Manage the working task list for this session. Call with no parameters to read. "
        "Pass todos to replace the list, or merge=true to update existing items by id. "
        "Use for complex tasks with 3+ steps; list order is priority and only one item "
        "should be in_progress at a time."
    )
    extra_toolsets = ["todo"]
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write. Omit to read the current list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique item identifier."},
                        "content": {"type": "string", "description": "Task description."},
                        "status": {"type": "string", "enum": list(_TODO_STATUSES)},
                    },
                    "required": ["content", "status"],
                },
            },
            "merge": {
                "type": "boolean",
                "description": "true updates by id and appends new items; false replaces the list.",
                "default": False,
            },
        },
        "required": [],
    }

    def run(self, args, ctx) -> ToolResult:
        session = getattr(ctx, "session", None)
        current = _normalize_todos(getattr(session, "todos", []) if session is not None else [])
        raw_todos = args.get("todos")
        if isinstance(raw_todos, str):
            try:
                raw_todos = json.loads(raw_todos)
            except (json.JSONDecodeError, TypeError):
                return ToolResult.error("todos must be a list of objects, got unparseable string")
        if raw_todos is not None and not isinstance(raw_todos, list):
            return ToolResult.error(f"todos must be a list, got {type(raw_todos).__name__}")

        if raw_todos is None:
            todos = current
        elif bool(args.get("merge", False)):
            todos = _merge_todos(current, raw_todos)
        else:
            todos = _normalize_todos(raw_todos)

        if session is not None:
            session.todos = todos
        summary = todo_summary(todos)
        payload = {"todos": todos, "summary": summary}
        return ToolResult.ok(
            json.dumps(payload, ensure_ascii=False),
            display=f"todos {summary['completed']}/{summary['total']} done",
            data=payload,
        )


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class MemoryTool(Tool):
    name = "memory"
    extra_toolsets = ["memory"]
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
        "If one correction contains both a user preference and a stable AEGIS/project/tool "
        "fact, save two entries: one to 'user' and one to 'memory'. Do not collapse them "
        "into only the user profile.\n\n"
        "ACTIONS: add (new entry), replace (update existing — old_text identifies it), "
        "remove (delete — old_text identifies it).\n\n"
        "For multiple changes, use one atomic operations array instead of several calls. "
        "Each operation is {action, content?, old_text?}; the batch checks the final memory "
        "size only, so it can remove/replace stale entries and add the new fact in one durable "
        "write. A successful batch is complete; do not repeat it.\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, temporary task state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "Single-operation action. Omit when using operations.",
            },
            "target": {"type": "string", "enum": ["memory", "user"],
                       "description": "'memory' for personal notes, 'user' for the user profile."},
            "content": {"type": "string",
                        "description": "The entry content. Required for add and replace in single-operation calls."},
            "old_text": {"type": "string",
                         "description": "Short unique substring identifying the entry to "
                                        "replace or remove in single-operation calls."},
            "operations": {
                "type": "array",
                "description": (
                    "Preferred batch shape for multiple changes or consolidation: operations "
                    "are applied atomically against the final char budget."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "content": {"type": "string", "description": "Entry content for add/replace."},
                        "old_text": {"type": "string", "description": "Entry substring for replace/remove."},
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["target"],
    }

    def run(self, args, ctx) -> ToolResult:
        if ctx.memory is None:
            return ToolResult.error("memory is not enabled.")
        origin = "background_review" if bool(getattr(getattr(ctx, "agent", None), "background_review", False)) else "foreground"
        return ctx.memory.handle_tool(args, approver=getattr(ctx, "approver", None), origin=origin)


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
class SkillTool(Tool):
    name = "skill"
    extra_toolsets = ["skills"]
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
        SecretTool(),
        WebFetchTool(), WebSearchTool(),
        TodoWriteTool(),
        MemoryTool(),
        SkillTool(),
    ]
