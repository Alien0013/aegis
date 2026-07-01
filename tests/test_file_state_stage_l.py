"""Stage L task-aware file freshness regression contracts."""

from __future__ import annotations

import os
import time

import pytest

from aegis.tools import file_state


@pytest.fixture(autouse=True)
def _reset_file_state():
    file_state.reset()
    yield
    file_state.reset()


def _stamp_external_mtime(path, *, offset_seconds=5.0) -> None:
    ts = time.time() + offset_seconds
    os.utime(path, (ts, ts))


def _norm(path) -> str:
    return os.path.realpath(str(path))


def _paths_from_events(events) -> set[str]:
    if events is None:
        return set()
    if isinstance(events, (str, os.PathLike)):
        return {_norm(events)}
    if isinstance(events, dict):
        direct = next(
            (
                events[key]
                for key in ("path", "file", "target")
                if isinstance(events.get(key), (str, os.PathLike))
            ),
            None,
        )
        if direct is not None:
            return {_norm(direct)}
        paths: set[str] = set()
        for key, value in events.items():
            if isinstance(key, (str, os.PathLike)) and os.path.isabs(str(key)):
                paths.add(_norm(key))
            paths.update(_paths_from_events(value))
        return paths

    paths: set[str] = set()
    try:
        iterator = iter(events)
    except TypeError:
        return paths
    for event in iterator:
        for attr in ("path", "file", "target"):
            if hasattr(event, attr):
                paths.add(_norm(getattr(event, attr)))
                break
        else:
            if isinstance(event, tuple):
                paths.update(_norm(value) for value in event if isinstance(value, (str, os.PathLike)))
            else:
                paths.update(_paths_from_events(event))
    return paths


def test_sibling_write_after_agent_read_warns_then_agent_write_clears(tmp_path):
    path = tmp_path / "shared.txt"
    path.write_text("version one\n")

    file_state.record_read("agent-a", path, partial=False)
    path.write_text("version two\n")
    _stamp_external_mtime(path)
    file_state.note_write("sibling-b", path)

    warning = file_state.stale_warning(path, task_id="agent-a")

    assert warning
    lower = warning.lower()
    assert "sibling" in lower or "subagent" in lower
    assert "read" in lower
    assert "re-read" in lower or "reread" in lower

    file_state.note_write("agent-a", path)

    assert not file_state.stale_warning(path, task_id="agent-a")
    assert file_state.check_stale("agent-a", path) in ("", None)


def test_partial_read_warns_to_reread_whole_file_before_overwrite(tmp_path):
    path = tmp_path / "partial.txt"
    path.write_text("line one\nline two\nline three\n")

    file_state.record_read("agent-a", path, partial=True)

    warning = file_state.stale_warning(path, task_id="agent-a")

    assert warning
    lower = warning.lower()
    assert "re-read" in lower or "reread" in lower
    assert "whole file" in lower
    assert "overwrit" in lower


def test_writes_since_reports_sibling_writes_and_excludes_parent_writes(tmp_path):
    parent_task = "parent"
    shared = tmp_path / "shared.txt"
    parent_only = tmp_path / "parent-only.txt"
    shared.write_text("shared before\n")
    parent_only.write_text("parent before\n")

    file_state.record_read(parent_task, shared, partial=False)
    file_state.record_read(parent_task, parent_only, partial=False)
    read_paths = _paths_from_events(file_state.known_reads(parent_task))
    since_ts = time.time() - 1.0

    parent_only.write_text("parent after\n")
    _stamp_external_mtime(parent_only)
    file_state.note_write(parent_task, parent_only)

    shared.write_text("shared after\n")
    _stamp_external_mtime(shared)
    file_state.note_write("sibling-b", shared)

    assert _norm(shared) in read_paths
    assert _norm(parent_only) in read_paths

    writes = file_state.writes_since(parent_task, since_ts, read_paths)
    written_paths = _paths_from_events(writes)

    assert _norm(shared) in written_paths
    assert _norm(parent_only) not in written_paths


def test_legacy_note_and_stale_warning_still_catch_external_mtime_drift(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_text("before\n")

    file_state.note(path)

    assert not file_state.stale_warning(path)

    path.write_text("after\n")
    _stamp_external_mtime(path)

    warning = file_state.stale_warning(path)

    assert warning
    assert "changed on disk" in warning.lower()
