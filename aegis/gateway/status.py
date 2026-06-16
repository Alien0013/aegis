"""Gateway runtime status helpers.

These helpers carry the small pieces of gateway lifecycle state that need to be
shared between service-control code and the running gateway process.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config as cfg
from ..util import atomic_write

_PLANNED_STOP_MARKER_FILENAME = ".gateway-planned-stop.json"
_PLANNED_STOP_MARKER_TTL_SECONDS = 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _planned_stop_marker_path() -> Path:
    return cfg.sub(_PLANNED_STOP_MARKER_FILENAME)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, separators=(",", ":")))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _get_process_start_time(pid: int) -> str | None:
    """Return the Linux /proc start-time field for PID reuse protection."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    # Field 2 may contain spaces inside parens. Everything after ") " starts at
    # field 3, so index 19 is field 22 (starttime).
    try:
        after_name = stat.rsplit(") ", 1)[1]
        fields = after_name.split()
        return fields[19]
    except (IndexError, ValueError):
        return None


def _marker_is_stale(written_at: Any, ttl_seconds: int = _PLANNED_STOP_MARKER_TTL_SECONDS) -> bool:
    try:
        dt = datetime.fromisoformat(str(written_at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > ttl_seconds
    except (TypeError, ValueError):
        return True


def write_planned_stop_marker(target_pid: int) -> bool:
    """Record that ``target_pid`` is being stopped intentionally."""
    try:
        pid = int(target_pid)
        if pid <= 0:
            return False
        _write_json_file(_planned_stop_marker_path(), {
            "target_pid": pid,
            "target_start_time": _get_process_start_time(pid),
            "stopper_pid": os.getpid(),
            "written_at": _utc_now_iso(),
        })
        return True
    except (OSError, ValueError):
        return False


def _marker_matches_self(record: dict[str, Any]) -> bool:
    try:
        target_pid = int(record["target_pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if target_pid != os.getpid():
        return False
    target_start = record.get("target_start_time")
    our_start = _get_process_start_time(os.getpid())
    if target_start is not None and our_start is not None:
        return str(target_start) == str(our_start)
    return True


def planned_stop_marker_targets_self() -> bool:
    """Non-destructively check whether a live planned-stop marker names us."""
    path = _planned_stop_marker_path()
    record = _read_json_file(path)
    if not record:
        return False
    if _marker_is_stale(record.get("written_at")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return _marker_matches_self(record)


def consume_planned_stop_marker_for_self() -> bool:
    """Return True and unlink when a live planned-stop marker names us."""
    path = _planned_stop_marker_path()
    record = _read_json_file(path)
    if not record:
        return False
    stale = _marker_is_stale(record.get("written_at"))
    matches = (not stale) and _marker_matches_self(record)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return matches


def clear_planned_stop_marker() -> None:
    try:
        _planned_stop_marker_path().unlink(missing_ok=True)
    except OSError:
        pass
