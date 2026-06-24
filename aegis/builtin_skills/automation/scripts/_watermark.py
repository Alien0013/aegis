"""Shared watermark helper for AEGIS watcher scripts.

A watcher polls an external source and must react only to *new* items. This stores a
bounded set of previously-seen IDs per watcher (keyed by ``--name``) under the state dir
(``$AEGIS_WATCHER_STATE_DIR`` → ``$AEGIS_HOME/watcher-state`` → ``~/.aegis/watcher-state``).

Contract used by every watcher:
- First run records a baseline and emits nothing (never replays an existing feed).
- Subsequent runs emit only IDs not seen before, then extend the watermark.
- The watermark is capped at ``MAX_IDS`` newest IDs to bound memory/disk.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

MAX_IDS = 500


def state_dir() -> Path:
    d = os.environ.get("AEGIS_WATCHER_STATE_DIR")
    if not d:
        home = os.environ.get("AEGIS_HOME") or str(Path.home() / ".aegis")
        d = str(Path(home) / "watcher-state")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "watcher")[:80]


def _path(name: str) -> Path:
    return state_dir() / f"{_safe(name)}.json"


def load_seen(name: str) -> tuple[list[str], bool]:
    """Return ``(seen_ids, first_run)``. ``first_run`` is True when no state exists yet."""
    p = _path(name)
    if not p.exists():
        return [], True
    try:
        data = json.loads(p.read_text())
        return [str(x) for x in data.get("ids", [])], False
    except Exception:  # noqa: BLE001 - corrupt state → treat as first run, rebuild baseline
        return [], True


def save_seen(name: str, ids: list[str]) -> None:
    _path(name).write_text(json.dumps({"ids": list(ids)[-MAX_IDS:]}))


def select_new(name: str, items: list[dict], key: Callable[[dict], str]) -> list[dict]:
    """Return items whose ``key`` hasn't been seen, oldest→newest, and persist the watermark.

    On the first run this records every current id as the baseline and returns ``[]`` so a
    freshly-added watcher never floods the user with a feed's entire backlog."""
    seen, first = load_seen(name)
    seen_set = set(seen)
    all_ids = list(seen)
    new: list[dict] = []
    for it in items:
        iid = str(key(it))
        if iid in seen_set:
            continue
        seen_set.add(iid)
        all_ids.append(iid)
        if not first:
            new.append(it)
    save_seen(name, all_ids)
    return new
