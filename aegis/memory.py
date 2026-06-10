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

# Prompt-injection patterns for STORED memory. Memory enters the system prompt on
# every future session, so a poisoned entry (compromised tool output, sister-session
# write, hand edit) would inject forever. Flagged content is refused at write time;
# entries already on disk are masked in the snapshot but stay visible to the memory
# tool so the user can inspect and remove them.
import re as _re

_INJECTION_PATTERNS: tuple[tuple[_re.Pattern, str], ...] = (
    (_re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|messages|rules)", _re.I),
     "instruction-override"),
    (_re.compile(r"disregard\s+(?:the\s+)?(?:system|previous|your)\s+(?:prompt|instructions|rules)", _re.I),
     "instruction-override"),
    (_re.compile(r"</?\s*(?:system|assistant)\s*>", _re.I), "role-tag smuggling"),
    (_re.compile(r"\byou\s+must\s+(?:always|now)\s+(?:obey|respond|reply|do)\b", _re.I),
     "coercive directive"),
    (_re.compile(r"curl[^\n]{0,100}\|\s*(?:ba)?sh\b", _re.I), "pipe-to-shell"),
    (_re.compile(r"\bnew\s+system\s+prompt\b", _re.I), "prompt replacement"),
)


def scan_entry(text: str) -> str | None:
    """Return why this content must not enter memory, or None if clean."""
    for pat, why in _INJECTION_PATTERNS:
        if pat.search(text or ""):
            return why
    return None


class MemoryStore:
    def __init__(self, base: Path | None = None):
        self.base = base or cfg.memories_dir()

    def _path(self, target: str) -> Path:
        return self.base / _FILES[target]

    def ensure_files(self) -> None:
        """Create empty MEMORY.md / USER.md if missing so the store is always present
        and hand-editable, instead of appearing only after the first write. Empty files
        parse as zero entries, so nothing spurious enters the prompt."""
        for name in _FILES.values():
            p = self.base / name
            if not p.exists():
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("", encoding="utf-8")
                except OSError:
                    pass

    def raw(self, target: str) -> str:
        return read_text(self._path(target)).strip()

    def entries(self, target: str) -> list[str]:
        raw = self.raw(target)
        return [e.strip() for e in raw.split("§") if e.strip()] if raw else []

    def _write_entries(self, target: str, entries: list[str]) -> None:
        atomic_write(self._path(target), MEMORY_DELIM.join(entries) + "\n" if entries else "")

    def _over_limit(self, target: str, entries: list[str]) -> str:
        """'' if the entries fit, else a consolidation directive. Old facts are never
        silently dropped — the model is told to merge/remove instead (refusing beats
        quietly forgetting)."""
        limit = _LIMITS[target]
        total = len(MEMORY_DELIM.join(entries))
        if total <= limit:
            return ""
        listing = "\n".join(f"  - {e[:90]}" for e in entries[:-1])
        return (f"memory full ({total:,}/{limit:,} chars): this write would exceed the limit. "
                "Nothing was dropped. Consolidate NOW in this turn: use action=replace to merge "
                "overlapping entries into shorter ones, or action=remove for stale ones, then "
                f"retry. Current entries:\n{listing}")

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
        from ._locks import STORE_LOCK, file_lock
        content = content.strip()
        why = scan_entry(content)
        if why:
            return (f"refused: content matches a prompt-injection pattern ({why}) and must "
                    "not enter persistent memory. Rephrase as a plain factual note.")
        # STORE_LOCK serializes threads; file_lock serializes PROCESSES (gateway + CLI +
        # cron share these files). entries() re-reads from disk inside the locks, so we
        # always append to the other writer's latest state instead of clobbering it.
        with STORE_LOCK, file_lock(self._path(target)):
            entries = self.entries(target)
            norm = self._norm(content)
            if norm and norm in {self._norm(e) for e in entries}:
                return "already remembered"
            entries.append(content)
            over = self._over_limit(target, entries)
            if over:
                return over
            self._write_entries(target, entries)
        return f"remembered in {_FILES[target]}"

    def replace(self, target: str, match: str, content: str) -> str:
        from ._locks import STORE_LOCK, file_lock
        why = scan_entry(content)
        if why:
            return (f"refused: replacement matches a prompt-injection pattern ({why}) and "
                    "must not enter persistent memory.")
        with STORE_LOCK, file_lock(self._path(target)):
            entries = self.entries(target)
            for i, e in enumerate(entries):
                if match in e:
                    entries[i] = content.strip()
                    over = self._over_limit(target, entries)
                    if over:
                        return over
                    self._write_entries(target, entries)
                    return f"updated entry in {_FILES[target]}"
        return f"no entry matching '{match}'"

    def remove(self, target: str, match: str) -> str:
        from ._locks import STORE_LOCK, file_lock
        with STORE_LOCK, file_lock(self._path(target)):
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
        self.store.ensure_files()             # MEMORY.md + USER.md always present + editable
        # Frozen snapshot, captured at construction and re-captured by refresh_snapshot().
        # Freezing keeps the system prompt byte-stable for prefix-cache reuse WITHIN a
        # turn; `is_stale()` lets the loop re-capture as soon as the files actually change
        # (a memory-tool write, background review, or a hand edit) so saved facts surface
        # on the very next turn instead of only on the next process/compaction.
        self._snapshot = {"memory": self._sanitized("memory"), "user": self._read_user()}
        self._snapshot_mtimes = self._memory_mtimes()

    def _sanitized(self, target: str) -> str:
        """Entries for the system-prompt snapshot, with any injection-matching entry
        masked. Disk state is untouched — the memory tool still shows the original so
        the user can inspect and remove it (silently hiding it would hide the attack)."""
        out = []
        for e in self.store.entries(target):
            why = scan_entry(e)
            out.append(f"[BLOCKED: stored entry matched a prompt-injection pattern ({why}); "
                       "inspect with the memory tool and remove it]" if why else e)
        return MEMORY_DELIM.join(out)

    def _memory_files(self) -> list:
        # workspace/USER.md is watched ONLY so that if someone drops a legacy file
        # there mid-run, is_stale() fires and the refresh migrates it immediately.
        return [self.store._path("memory"), self.store._path("user"),
                cfg.workspace_dir() / "USER.md"]

    def _memory_mtimes(self) -> dict:
        out = {}
        for p in self._memory_files():
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                out[str(p)] = 0.0
        return out

    def is_stale(self) -> bool:
        """True if any memory file changed since the snapshot was captured — the cue
        for the loop to rebuild the system prompt so newly-saved facts become visible."""
        if not self.enabled:
            return False
        return self._memory_mtimes() != self._snapshot_mtimes

    def _read_user(self) -> str:
        """The user profile — memories/USER.md is the ONE canonical file (like the
        reference layout: profile = memory store, workspace = persona/rules only).
        A legacy workspace/USER.md from older installs is folded in once by
        :meth:`_migrate_workspace_profile` and parked, so there is never a second
        live profile file to wonder about."""
        self._migrate_workspace_profile()
        return self._sanitized("user")

    def _migrate_workspace_profile(self) -> None:
        """One-time: import a legacy hand-edited workspace/USER.md into
        memories/USER.md (deduped), then rename it to USER.md.migrated. The rename
        is the done-marker; nothing re-reads the old location afterwards."""
        legacy = cfg.workspace_dir() / "USER.md"
        if not legacy.exists():
            return
        try:
            manual = read_text(legacy).strip()
            if manual.startswith("# User Profile") and "Add stable preferences" in manual:
                manual = ""                   # untouched onboarding template — nothing to keep
            for block in manual.split("\n\n"):
                block = block.strip()
                if block and not block.startswith("#"):   # skip bare headings
                    self.store.add("user", block)
            legacy.rename(legacy.with_suffix(".md.migrated"))
        except OSError:
            pass                              # unwritable workspace — try again next refresh

    def refresh_snapshot(self) -> None:
        self._snapshot = {"memory": self._sanitized("memory"), "user": self._read_user()}
        self._snapshot_mtimes = self._memory_mtimes()

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
            result = self.store.add(target, args["content"])
            if result.startswith(("memory full", "refused")):
                return ToolResult.error(result)      # the model must consolidate / rephrase
            return ToolResult.ok(f"{result} — now in context from your next message on.",
                                 display=f"remembered in memories/{_FILES[target]}")
        if action == "replace":
            if not args.get("match") or not args.get("content"):
                return ToolResult.error("replace needs match and content")
            result = self.store.replace(target, args["match"], args["content"])
            if result.startswith(("memory full", "refused")):
                return ToolResult.error(result)
            return ToolResult.ok(result)
        if action == "remove":
            if not args.get("match"):
                return ToolResult.error("remove needs match")
            return ToolResult.ok(self.store.remove(target, args["match"]))
        return ToolResult.error(f"unknown action '{action}'")
