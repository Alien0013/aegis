"""Persistent memory: file-backed MEMORY.md / USER.md + append-only history.

§-delimited entries, char limits, atomic writes, and a *frozen
snapshot* taken at session start so the system prompt stays byte-stable for
prefix-cache reuse. Tool writes land on disk immediately and surface at the next
session/compaction rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config as cfg
from .constants import MEMORY_CHAR_LIMIT, MEMORY_DELIM, USER_CHAR_LIMIT
from .util import append_line, atomic_write, now_iso, read_text

_FILES = {"memory": "MEMORY.md", "user": "USER.md"}
_LIMITS = {"memory": MEMORY_CHAR_LIMIT, "user": USER_CHAR_LIMIT}


class MemoryStore:
    def __init__(self, base: Path | None = None):
        self.base = base or cfg.memories_dir()

    def _path(self, target: str) -> Path:
        return self.base / _FILES[target]

    def raw(self, target: str) -> str:
        return read_text(self._path(target)).strip()

    def entries(self, target: str) -> list[str]:
        raw = self.raw(target)
        return [e.strip() for e in raw.split("§") if e.strip()] if raw else []

    def _write_entries(self, target: str, entries: list[str]) -> None:
        # enforce char limit by dropping oldest entries
        limit = _LIMITS[target]
        while entries and len(MEMORY_DELIM.join(entries)) > limit:
            entries.pop(0)
        atomic_write(self._path(target), MEMORY_DELIM.join(entries) + "\n" if entries else "")

    @staticmethod
    def _norm(text: str) -> str:
        """Normalized form for near-duplicate detection — \"The user's name is TJ.\"
        and \"User's name is TJ\" are the same fact."""
        import re
        text = text.lower().replace("'", "").replace("’", "")   # user's == users
        words = re.sub(r"[^a-z0-9 ]+", " ", text).split()
        stop = {"the", "a", "an", "is", "are", "was", "were"}
        return " ".join(w for w in words if w not in stop)

    def add(self, target: str, content: str) -> str:
        from ._locks import STORE_LOCK
        content = content.strip()
        with STORE_LOCK:                       # serialize read-modify-write (no lost updates)
            entries = self.entries(target)
            norm = self._norm(content)
            if norm and norm in {self._norm(e) for e in entries}:
                return "already remembered"
            entries.append(content)
            self._write_entries(target, entries)
        return f"remembered in {_FILES[target]}"

    def replace(self, target: str, match: str, content: str) -> str:
        from ._locks import STORE_LOCK
        with STORE_LOCK:
            entries = self.entries(target)
            for i, e in enumerate(entries):
                if match in e:
                    entries[i] = content.strip()
                    self._write_entries(target, entries)
                    return f"updated entry in {_FILES[target]}"
        return f"no entry matching '{match}'"

    def remove(self, target: str, match: str) -> str:
        from ._locks import STORE_LOCK
        with STORE_LOCK:
            entries = self.entries(target)
            kept = [e for e in entries if match not in e]
            if len(kept) == len(entries):
                return f"no entry matching '{match}'"
            self._write_entries(target, kept)
        return f"removed {len(entries) - len(kept)} entry(ies) from {_FILES[target]}"


class History:
    """Append-only conversation log (history.jsonl), durable with fsync."""

    def __init__(self, base: Path | None = None):
        self.path = (base or cfg.memories_dir()) / "history.jsonl"

    def append(self, role: str, content: str, session: str = "") -> None:
        append_line(self.path, json.dumps({"ts": now_iso(), "role": role,
                                           "content": content, "session": session}))
        self._maybe_rotate()

    def _maybe_rotate(self, max_bytes: int = 10_000_000, keep_lines: int = 2000) -> None:
        """history.jsonl grows forever otherwise; past the cap keep the recent tail."""
        try:
            if self.path.stat().st_size <= max_bytes:
                return
            lines = read_text(self.path).strip().splitlines()[-keep_lines:]
            atomic_write(self.path, "\n".join(lines) + "\n")
        except OSError:
            pass

    def recent(self, n: int = 50, max_chars: int = 32_000) -> list[dict]:
        raw = read_text(self.path)
        if not raw:
            return []
        lines = raw.strip().splitlines()[-n:]
        out, total = [], 0
        for line in reversed(lines):
            total += len(line)
            if total > max_chars:
                break
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(out))


class MemoryProvider:
    """Pluggable external memory backend (vector DB, etc.). Built-in is always on."""

    def system_prompt_block(self) -> str:  # pragma: no cover - interface
        return ""

    def sync_turn(self, messages) -> None:  # pragma: no cover - interface
        ...


class MemoryManager:
    """Builtin file memory + (optionally) one external provider."""

    def __init__(self, config: cfg.Config, external: MemoryProvider | None = None):
        self.config = config
        self.store = MemoryStore()
        self.history = History()
        if external is None and config.get("memory.provider"):
            try:
                from .memory_providers import build_memory_provider
                external = build_memory_provider(config.get("memory.provider"), config)
            except Exception as e:  # noqa: BLE001
                print(f"  ! external memory provider failed: {e}")
        self.external = external
        self.enabled = bool(config.get("memory.enabled", True))
        self.user_enabled = bool(config.get("memory.user_profile_enabled", True))
        # frozen snapshot at construction (session start)
        self._snapshot = {"memory": self.store.raw("memory"), "user": self._read_user()}

    def _read_user(self) -> str:
        """The full user profile: the hand-edited workspace/USER.md (if it's been
        edited past the onboarding template) + facts the memory tool learned.
        Both must reach the prompt — splitting them was how 'remembered' facts
        went missing from new sessions."""
        learned = self.store.raw("user")
        manual = read_text(cfg.workspace_dir() / "USER.md").strip()
        if manual.startswith("# User Profile") and "Add stable preferences" in manual:
            manual = ""                       # untouched template — pure noise
        return "\n\n".join(p for p in (manual, learned) if p)

    def refresh_snapshot(self) -> None:
        self._snapshot = {"memory": self.store.raw("memory"), "user": self._read_user()}

    def build_context_block(self) -> str:
        if not self.enabled:
            return ""
        parts: list[str] = []
        mem = self._snapshot.get("memory", "")
        if mem:
            parts.append("# Long-term memory (facts)\n" + mem)
        if self.user_enabled and self._snapshot.get("user"):
            parts.append("# About the user\n" + self._snapshot["user"])
        if self.external:
            ext = self.external.system_prompt_block().strip()
            if ext:
                parts.append(ext)
        if not parts:
            return ""
        return "<memory>\n" + "\n\n".join(parts) + "\n</memory>"

    def handle_tool(self, args: dict):
        from .tools.base import ToolResult

        action = args.get("action")
        target = args.get("target", "memory")
        if target not in _FILES:
            return ToolResult.error("target must be 'memory' or 'user'")
        if action == "add":
            if not args.get("content"):
                return ToolResult.error("content is required for add")
            return ToolResult.ok(self.store.add(target, args["content"]),
                                 display="memory updated (loads on the next session or /new)")
        if action == "replace":
            if not args.get("match") or not args.get("content"):
                return ToolResult.error("replace needs match and content")
            return ToolResult.ok(self.store.replace(target, args["match"], args["content"]))
        if action == "remove":
            if not args.get("match"):
                return ToolResult.error("remove needs match")
            return ToolResult.ok(self.store.remove(target, args["match"]))
        return ToolResult.error(f"unknown action '{action}'")
