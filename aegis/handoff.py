"""Cross-surface session handoff: move a CLI conversation to a messaging channel.

`/handoff <platform> <chat_id>` in the REPL records a pending handoff and pings
the chat. When the gateway next receives a message from that chat it ADOPTS the
handed-off session (full history) instead of its usual per-chat session — so
the conversation continues where the terminal left off.

File-backed (handoffs.json) because the REPL and the gateway are separate
processes.
"""

from __future__ import annotations

import json

from . import config as cfg
from ._locks import STORE_LOCK, file_lock
from .util import atomic_write, now_iso, read_text


def _path():
    return cfg.sub("handoffs.json")


def _load() -> dict:
    raw = read_text(_path())
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def set_handoff(platform: str, chat_id: str, session_id: str) -> None:
    with STORE_LOCK, file_lock(_path()):
        data = _load()
        data[f"{platform}:{chat_id}"] = {"session_id": session_id, "at": now_iso()}
        atomic_write(_path(), json.dumps(data, indent=2))


def pop_handoff(platform: str, chat_id: str) -> str | None:
    """Session id pending for this chat (consumed), or None. Cheap when no file exists."""
    if not _path().exists():
        return None
    with STORE_LOCK, file_lock(_path()):
        data = _load()
        entry = data.pop(f"{platform}:{chat_id}", None)
        if entry is not None:
            atomic_write(_path(), json.dumps(data, indent=2))
    return entry["session_id"] if entry else None
