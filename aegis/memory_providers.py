"""Pluggable external memory backends implementing the MemoryProvider interface.

Selected via ``memory.provider`` in config: "" (builtin only), "jsonl", or "mem0".
The builtin file memory (MEMORY.md/USER.md) is always active alongside these.
"""

from __future__ import annotations

import json

from . import config as cfg
from .memory import MemoryProvider
from .util import append_line, read_text


class JSONLMemoryProvider(MemoryProvider):
    """Zero-dependency external memory: appends turn notes to ext_memory.jsonl."""

    def __init__(self, max_recent: int = 12):
        self.path = cfg.sub("ext_memory.jsonl")
        self.max_recent = max_recent

    def system_prompt_block(self) -> str:
        raw = read_text(self.path)
        if not raw.strip():
            return ""
        lines = raw.strip().splitlines()[-self.max_recent:]
        notes = []
        for ln in lines:
            try:
                notes.append("- " + json.loads(ln).get("note", ""))
            except json.JSONDecodeError:
                continue
        return "# Recalled context\n" + "\n".join(notes) if notes else ""

    def sync_turn(self, messages) -> None:
        # store the last user/assistant exchange as a compact note
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        last_asst = next((m.content for m in reversed(messages) if m.role == "assistant"), "")
        if last_user:
            note = f"user asked: {last_user[:160]}"
            if last_asst:
                note += f" | replied: {last_asst[:160]}"
            append_line(self.path, json.dumps({"note": note}))


class Mem0Provider(MemoryProvider):
    """Vector memory via the `mem0ai` package (optional dependency)."""

    def __init__(self, user_id: str = "aegis"):
        try:
            from mem0 import Memory
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("mem0 provider needs `pip install mem0ai`") from e
        self.user_id = user_id
        self._mem = Memory()
        self._last_query = ""

    def system_prompt_block(self) -> str:
        try:
            results = self._mem.search(self._last_query or "recent context",
                                       user_id=self.user_id, limit=8)
            items = results.get("results", results) if isinstance(results, dict) else results
            mems = [r.get("memory", "") for r in (items or [])]
            return "# Long-term memory (mem0)\n" + "\n".join(f"- {m}" for m in mems if m) if mems else ""
        except Exception:  # noqa: BLE001
            return ""

    def sync_turn(self, messages) -> None:
        try:
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            wire = [{"role": m.role, "content": m.content} for m in messages[-6:]
                    if m.role in ("user", "assistant") and m.content]
            if wire:
                self._mem.add(wire, user_id=self.user_id)
        except Exception:  # noqa: BLE001
            pass


def build_memory_provider(name: str, config) -> MemoryProvider | None:
    name = (name or "").strip().lower()
    if name == "jsonl":
        return JSONLMemoryProvider()
    if name == "mem0":
        try:
            return Mem0Provider()
        except RuntimeError as e:
            print(f"  ! {e}")
            return None
    return None
