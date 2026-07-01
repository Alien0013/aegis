"""Cross-task file freshness tracking.

The registry records the mtime each task saw for a file, plus the last task to
write it. Before a write/edit, callers can warn when the file changed on disk,
when a sibling task wrote it after this task's last read, or when the task only
read a paginated slice. Warnings are intentionally advisory: tool policy stays
with the executor and approval layer.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

ReadStamp = tuple[float, float, bool]  # mtime, read timestamp, partial read
_DEFAULT_TASK_ID = "default"
_MAX_PATHS_PER_TASK = 4096
_MAX_GLOBAL_WRITERS = 4096


def _task_id(task_id: str | None = None) -> str:
    value = str(task_id or "").strip()
    return value or _DEFAULT_TASK_ID


def _key(path: str | Path) -> str:
    return os.path.realpath(str(Path(path).expanduser()))


def _disabled() -> bool:
    return os.environ.get("AEGIS_DISABLE_FILE_STATE_GUARD", "").strip() == "1"


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _cap_dict(data: dict, limit: int) -> None:
    over = len(data) - limit
    if over <= 0:
        return
    keys = iter(data)
    for _ in range(over):
        try:
            data.pop(next(keys))
        except (KeyError, StopIteration):
            break


class FileStateRegistry:
    """Process-wide coordinator for same-process agent file edits."""

    def __init__(self) -> None:
        self._reads: dict[str, dict[str, ReadStamp]] = defaultdict(dict)
        self._last_writer: dict[str, tuple[str, float]] = {}
        self._path_locks: dict[str, threading.Lock] = {}
        self._path_locks_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def _lock_for(self, path: str) -> threading.Lock:
        with self._path_locks_lock:
            lock = self._path_locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[path] = lock
            return lock

    @contextmanager
    def lock_path(self, path: str | Path):
        key = _key(path)
        lock = self._lock_for(key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def record_read(self, task_id: str | None, path: str | Path, *, partial: bool = False) -> None:
        if _disabled():
            return
        key = _key(path)
        mtime = _mtime(key)
        if mtime is None:
            return
        task = _task_id(task_id)
        with self._state_lock:
            self._reads[task][key] = (float(mtime), time.time(), bool(partial))
            _cap_dict(self._reads[task], _MAX_PATHS_PER_TASK)

    def note_write(self, task_id: str | None, path: str | Path) -> None:
        if _disabled():
            return
        key = _key(path)
        mtime = _mtime(key)
        if mtime is None:
            return
        task = _task_id(task_id)
        now = time.time()
        with self._state_lock:
            self._last_writer[key] = (task, now)
            _cap_dict(self._last_writer, _MAX_GLOBAL_WRITERS)
            self._reads[task][key] = (float(mtime), now, False)
            _cap_dict(self._reads[task], _MAX_PATHS_PER_TASK)

    def check_stale(self, task_id: str | None, path: str | Path) -> str:
        if _disabled():
            return ""
        key = _key(path)
        task = _task_id(task_id)
        with self._state_lock:
            stamp = self._reads.get(task, {}).get(key)
            last_writer = self._last_writer.get(key)
        current_mtime = _mtime(key)
        if current_mtime is None:
            return ""

        if last_writer is not None:
            writer_task, writer_ts = last_writer
            if writer_task != task:
                if stamp is None:
                    return (
                        f"{key} was modified by sibling subagent {writer_task!r}, "
                        "but this task has not read it. Re-read the file before writing "
                        "so you do not overwrite another agent's changes."
                    )
                _read_mtime, read_ts, _partial = stamp
                if writer_ts > read_ts:
                    return (
                        f"{key} was modified by sibling subagent {writer_task!r} at "
                        f"{_fmt_ts(writer_ts)}, after this task last read it at "
                        f"{_fmt_ts(read_ts)}. Re-read the file before writing."
                    )

        if stamp is not None:
            read_mtime, _read_ts, partial = stamp
            if current_mtime > read_mtime + 1e-6 or current_mtime < read_mtime - 1e-6:
                return (
                    f"{key} changed on disk after this task last read it "
                    "(another agent, the user, or a tool may have modified it). "
                    "Re-read it before writing, and do not revert changes you did not make."
                )
            if partial:
                return (
                    f"{key} was last read with offset/limit pagination, so this task only "
                    "saw a partial view. Re-read the whole file before overwriting it."
                )
            return ""

        if last_writer is not None:
            return (
                f"{key} was not read by this task. Re-read the file before writing so "
                "the edit is based on current content."
            )
        return ""

    def writes_since(
        self,
        exclude_task_id: str | None,
        since_ts: float,
        paths: Iterable[str | Path],
    ) -> dict[str, list[str]]:
        if _disabled():
            return {}
        exclude = _task_id(exclude_task_id)
        wanted = {_key(p) for p in paths}
        out: dict[str, list[str]] = defaultdict(list)
        with self._state_lock:
            for path, (writer_task, write_ts) in self._last_writer.items():
                if writer_task == exclude or write_ts < since_ts or path not in wanted:
                    continue
                out[writer_task].append(path)
        return dict(out)

    def known_reads(self, task_id: str | None) -> list[str]:
        if _disabled():
            return []
        task = _task_id(task_id)
        with self._state_lock:
            return list(self._reads.get(task, {}).keys())

    def reset(self) -> None:
        with self._state_lock:
            self._reads.clear()
            self._last_writer.clear()
        with self._path_locks_lock:
            self._path_locks.clear()


_registry = FileStateRegistry()


def get_registry() -> FileStateRegistry:
    return _registry


def record_read(task_id: str | None, path: str | Path, *, partial: bool = False) -> None:
    _registry.record_read(task_id, path, partial=partial)


def note_write(task_id: str | None, path: str | Path) -> None:
    _registry.note_write(task_id, path)


def check_stale(task_id: str | None, path: str | Path) -> str:
    return _registry.check_stale(task_id, path)


@contextmanager
def lock_path(path: str | Path):
    """Serialize a read/check/write region for one resolved path."""
    with _registry.lock_path(path):
        yield


def stale_warning(path: str | Path, task_id: str | None = None) -> str:
    """Return a model-facing warning, or ``""`` when the task's view is fresh."""
    warning = _registry.check_stale(task_id, path)
    if not warning:
        return ""
    return f"\n\n<system-reminder>WARNING: {warning}</system-reminder>"


def writes_since(
    exclude_task_id: str | None,
    since_ts: float,
    paths: Iterable[str | Path],
) -> dict[str, list[str]]:
    return _registry.writes_since(exclude_task_id, since_ts, paths)


def known_reads(task_id: str | None) -> list[str]:
    return _registry.known_reads(task_id)


def note(path: str | Path) -> None:
    """Legacy alias: record the default task's current view of a file."""
    _registry.record_read(_DEFAULT_TASK_ID, path)


def reset() -> None:
    _registry.reset()


__all__ = [
    "FileStateRegistry",
    "check_stale",
    "get_registry",
    "known_reads",
    "lock_path",
    "note",
    "note_write",
    "record_read",
    "reset",
    "stale_warning",
    "writes_since",
]
