"""Stage L Hermes-parity file operation regression contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis.config import Config
from aegis.tools import file_state
from aegis.tools.base import ToolContext, ToolResult
from aegis.tools.builtin import EditFileTool, ReadFileTool, SearchTool, WriteFileTool


@pytest.fixture(autouse=True)
def _reset_file_state():
    file_state.reset()
    yield
    file_state.reset()


def _ctx(tmp_path: Path, *, task_id: str = "stage-l") -> ToolContext:
    return ToolContext(cwd=tmp_path, config=Config.load(), task_id=task_id)


def _payload(result: ToolResult) -> dict:
    if isinstance(result.data, dict):
        return result.data
    try:
        return json.loads(result.content)
    except json.JSONDecodeError:
        # Hermes appends a plain-text truncation hint after the JSON payload.
        head = result.content.split("\n\n", 1)[0]
        try:
            return json.loads(head)
        except json.JSONDecodeError:
            pytest.fail(f"expected structured search metadata, got: {result.content!r}")


def _match_paths(payload: dict) -> list[str]:
    if "matches" in payload:
        return [str(match.get("path", "")) for match in payload["matches"]]
    if "matches_text" in payload:
        paths = []
        for line in str(payload["matches_text"]).splitlines():
            if line and not line.startswith(" "):
                paths.append(line)
        return paths
    return [str(path) for path in payload.get("files", [])]


def test_read_file_uses_compact_line_number_window(tmp_path):
    target = tmp_path / "window.txt"
    target.write_text("\n".join(f"line-{i}" for i in range(1, 7)) + "\n", encoding="utf-8")

    result = ReadFileTool().run({"path": "window.txt", "offset": 3, "limit": 2}, _ctx(tmp_path))

    assert not result.is_error, result.content
    assert result.content.splitlines() == ["3|line-3", "4|line-4"]


def test_search_files_returns_paginated_limit_metadata(tmp_path, monkeypatch):
    for idx in range(5):
        (tmp_path / f"hit_{idx}.txt").write_text(f"needle {idx}\n", encoding="utf-8")
    monkeypatch.setattr("aegis.tools.builtin.shutil.which", lambda _name: None)

    result = SearchTool().run(
        {"pattern": "needle", "path": ".", "limit": 2, "offset": 1},
        _ctx(tmp_path),
    )

    assert not result.is_error, result.content
    payload = _payload(result)
    paths = _match_paths(payload)
    assert payload["total_count"] >= 3
    assert payload["truncated"] is True
    assert len(paths) == 2
    assert "offset=3" in result.content or payload.get("next_offset") == 3


def test_read_file_blocks_device_aliases_and_binary_extensions(tmp_path):
    device_alias = tmp_path / "stdin-alias"
    device_alias.symlink_to("/dev/zero")
    binary_named_text = tmp_path / "archive.zip"
    binary_named_text.write_bytes(b"PK\x03\x04plain-text-payload-without-nul")

    ctx = _ctx(tmp_path)
    device_result = ReadFileTool().run({"path": "stdin-alias"}, ctx)
    binary_result = ReadFileTool().run({"path": "archive.zip"}, ctx)

    assert device_result.is_error
    assert "device" in device_result.content.lower() or "block" in device_result.content.lower()
    assert binary_result.is_error
    assert "binary file" in binary_result.content.lower()


def test_edit_file_reports_post_write_verification_failure(tmp_path, monkeypatch):
    target = tmp_path / "verify.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    def silent_noop(_path: Path, _content: str) -> None:
        return None

    monkeypatch.setattr("aegis.tools.builtin._atomic_write_local", silent_noop)

    result = EditFileTool().run(
        {"path": "verify.txt", "old_string": "beta\n", "new_string": "gamma\n"},
        _ctx(tmp_path),
    )

    assert result.is_error
    lower = result.content.lower()
    assert "verification" in lower or "did not persist" in lower
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_partial_read_warning_is_reported_once_then_refreshed(tmp_path):
    target = tmp_path / "stale.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ctx = _ctx(tmp_path, task_id="stage-l-stale")

    read = ReadFileTool().run({"path": "stale.txt", "offset": 2, "limit": 1}, ctx)
    first_write = WriteFileTool().run({"path": "stale.txt", "content": "fresh\n"}, ctx)
    second_write = WriteFileTool().run({"path": "stale.txt", "content": "fresher\n"}, ctx)

    assert not read.is_error, read.content
    assert not first_write.is_error, first_write.content
    assert "<system-reminder>" in first_write.content
    assert "offset/limit" in first_write.content
    assert "partial" in first_write.content.lower()
    assert not second_write.is_error, second_write.content
    assert "<system-reminder>" not in second_write.content
