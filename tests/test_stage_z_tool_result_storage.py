"""Stage Z environment-backed tool-result storage contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import time
from pathlib import Path

import pytest


_PERSISTED_OUTPUT_TAG = "<persisted-output>"
_PERSISTED_OUTPUT_CLOSE = "</persisted-output>"


class _RecordingEnv:
    def __init__(self, *, temp_dir: str = "/sandbox/tmp", returncode: int = 0) -> None:
        self.temp_dir = temp_dir
        self.returncode = returncode
        self.calls: list[dict] = []
        self.files: dict[str, str] = {}

    def get_temp_dir(self) -> str:
        return self.temp_dir

    def execute(self, command, timeout=30, stdin_data=None, **kwargs):
        self.calls.append(
            {
                "command": command,
                "timeout": timeout,
                "stdin_data": stdin_data,
                "kwargs": kwargs,
            }
        )
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        if tokens[:1] == ["cat"] and len(tokens) == 2:
            if tokens[1] in self.files:
                return {"output": self.files[tokens[1]], "returncode": 0}
            return {"output": "missing", "returncode": 1}
        if self.returncode == 0 and "cat" in tokens and ">" in tokens:
            path = tokens[tokens.index(">") + 1]
            self.files[path] = stdin_data or ""
        return {"output": "", "returncode": self.returncode}


def _storage_module():
    from aegis.tools import tool_result_storage

    return tool_result_storage


def _saved_path(message: str) -> Path:
    match = re.search(r"^Full output saved to:\s*(.+)$", message, flags=re.MULTILINE)
    assert match is not None, message
    return Path(match.group(1).strip())


def test_oversized_result_is_written_to_environment_via_stdin_data():
    storage = _storage_module()
    env = _RecordingEnv(temp_dir="/env-tmp")
    content = ("alpha beta gamma\n" * 300) + "TAIL_ONLY_IN_FULL_CONTENT"

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_env_store",
        env=env,
        threshold_chars=32,
        preview_chars=64,
    )

    assert len(env.calls) == 2
    call = env.calls[0]
    assert call["timeout"] == 30
    assert call["stdin_data"] == content
    assert content not in call["command"]
    metadata = json.loads(env.calls[1]["stdin_data"])
    assert metadata["tool_name"] == "stage_z_tool"
    assert metadata["tool_use_id"] == "call_env_store"
    assert metadata["path"].startswith("/env-tmp/")
    assert metadata["metadata_path"].endswith(".metadata.json")
    assert metadata["chars"] == len(content)
    assert metadata["bytes"] == len(content.encode("utf-8"))
    assert metadata["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert _PERSISTED_OUTPUT_TAG in persisted
    assert _PERSISTED_OUTPUT_CLOSE in persisted
    assert "Full output saved to: /env-tmp/" in persisted
    assert "Metadata saved to: /env-tmp/" in persisted
    assert "Content SHA-256:" in persisted
    assert "call_env_store" in persisted
    assert "TAIL_ONLY_IN_FULL_CONTENT" not in persisted
    assert len(persisted) < len(content)


def test_tool_executor_uses_context_result_storage_environment(tmp_path):
    from aegis.agent.loop import ToolExecutor
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry
    from aegis.types import ToolCall

    cfg = Config.load()
    cfg.data["tools"]["max_result_tokens"] = 12
    env = _RecordingEnv(temp_dir="/executor-tmp")
    executor = ToolExecutor(
        default_registry(),
        None,
        ToolContext(cwd=tmp_path, config=cfg, result_storage_env=env),
        lambda _event: None,
    )
    content = ("executor payload\n" * 200) + "EXECUTOR_FULL_TAIL"

    persisted = executor._maybe_spill(
        ToolCall("call_executor_env", "bash", {}),
        content,
        is_error=False,
    )

    assert len(env.calls) == 2
    assert env.calls[0]["stdin_data"] == content
    assert json.loads(env.calls[1]["stdin_data"])["tool_use_id"] == "call_executor_env"
    assert "Full output saved to: /executor-tmp/" in persisted
    assert "call_executor_env" in persisted
    assert "EXECUTOR_FULL_TAIL" not in persisted


def test_environment_write_failure_falls_back_to_local_persisted_reference(tmp_path):
    storage = _storage_module()
    env = _RecordingEnv(temp_dir="/env-tmp", returncode=1)
    content = ("fallback payload line\n" * 300) + "LOCAL_FALLBACK_FULL_TAIL"

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_env_fail",
        env=env,
        threshold_chars=32,
        preview_chars=72,
        local_dir=tmp_path,
    )

    assert len(env.calls) == 1
    assert env.calls[0]["stdin_data"] == content
    assert _PERSISTED_OUTPUT_TAG in persisted
    assert _PERSISTED_OUTPUT_CLOSE in persisted
    assert "LOCAL_FALLBACK_FULL_TAIL" not in persisted
    assert len(persisted) < 1_000
    saved_path = _saved_path(persisted)
    assert "/env-tmp/" not in str(saved_path)
    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == content
    metadata_path = Path(storage.parse_persisted_output_reference(persisted)["metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["storage"] == "local"
    assert metadata["path"] == str(saved_path)
    assert metadata["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_local_persisted_reference_write_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    storage = _storage_module()
    target = tmp_path / "stage_z_tool_call_atomic_local.txt"
    target.write_text("previous complete output", encoding="utf-8")
    stale_ts = time.time() - 9 * 86400
    os.utime(target, (stale_ts, stale_ts))

    def fail_replace(src, dst):
        assert Path(src).parent == tmp_path
        assert Path(dst) == target
        raise OSError("simulated replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)

    persisted = storage.maybe_persist_tool_result(
        content="new full output that should not replace the old artifact",
        tool_name="stage_z_tool",
        tool_use_id="call_atomic_local",
        threshold_chars=1,
        preview_chars=16,
        local_dir=tmp_path,
    )

    assert "could not be saved" in persisted
    assert target.read_text(encoding="utf-8") == "previous complete output"
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]


def test_local_persisted_reference_preserves_existing_file_mode(tmp_path):
    storage = _storage_module()
    target = tmp_path / "stage_z_tool_call_mode.txt"
    target.write_text("old output", encoding="utf-8")
    target.chmod(0o640)

    persisted = storage.maybe_persist_tool_result(
        content="new full output with enough content to spill",
        tool_name="stage_z_tool",
        tool_use_id="call_mode",
        threshold_chars=1,
        preview_chars=16,
        local_dir=tmp_path,
    )

    saved_path = _saved_path(persisted)
    assert saved_path == target
    assert saved_path.read_text(encoding="utf-8") == "new full output with enough content to spill"
    assert stat.S_IMODE(saved_path.stat().st_mode) == 0o640


def test_local_persisted_reference_writes_metadata_and_rehydrates(tmp_path):
    storage = _storage_module()
    content = ("rehydrate payload line\n" * 200) + "FULL_ONLY_REHYDRATED_TAIL"

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_rehydrate",
        threshold_chars=32,
        preview_chars=80,
        local_dir=tmp_path,
    )

    reference = storage.parse_persisted_output_reference(persisted)
    assert reference is not None
    assert reference["chars"] == len(content)
    assert reference["bytes"] == len(content.encode("utf-8"))
    assert reference["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    saved_path = Path(reference["file_path"])
    metadata_path = Path(reference["metadata_path"])
    assert saved_path.read_text(encoding="utf-8") == content
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == storage.METADATA_SCHEMA_VERSION
    assert metadata["kind"] == "aegis.tool_result"
    assert metadata["path"] == str(saved_path)
    assert metadata["metadata_path"] == str(metadata_path)

    full, loaded_metadata = storage.load_persisted_tool_result(persisted)
    assert full == content
    assert loaded_metadata["sha256"] == metadata["sha256"]

    message = {"role": "tool", "content": persisted}
    assert storage.rehydrate_tool_result_message(message) is True
    assert message["content"] == content


def test_environment_persisted_reference_rehydrates_through_environment():
    storage = _storage_module()
    env = _RecordingEnv(temp_dir="/env-tmp")
    content = ("remote rehydrate payload\n" * 160) + "REMOTE_FULL_TAIL"

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_remote_rehydrate",
        env=env,
        threshold_chars=32,
        preview_chars=64,
    )

    assert storage.rehydrate_persisted_tool_result(persisted, env=env) == content
    read_commands = [call["command"] for call in env.calls if call["command"].startswith("cat ")]
    assert read_commands == [
        "cat /env-tmp/aegis-results/call_remote_rehydrate.txt.metadata.json",
        "cat /env-tmp/aegis-results/call_remote_rehydrate.txt",
    ]


def test_rehydrate_verifies_checksum_and_size(tmp_path):
    storage = _storage_module()
    content = "original full content " * 100

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_corrupt",
        threshold_chars=16,
        preview_chars=32,
        local_dir=tmp_path,
    )
    saved_path = _saved_path(persisted)
    saved_path.write_text("corrupted content", encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        storage.rehydrate_persisted_tool_result(persisted)


def test_rehydrate_can_recover_when_sidecar_metadata_is_missing(tmp_path):
    storage = _storage_module()
    content = "recover from transcript checksum " * 120

    persisted = storage.maybe_persist_tool_result(
        content=content,
        tool_name="stage_z_tool",
        tool_use_id="call_no_sidecar",
        threshold_chars=16,
        preview_chars=32,
        local_dir=tmp_path,
    )
    reference = storage.parse_persisted_output_reference(persisted)
    Path(reference["metadata_path"]).unlink()

    assert storage.rehydrate_persisted_tool_result(persisted) == content


def test_turn_budget_spills_largest_unpersisted_result_first(tmp_path):
    storage = _storage_module()
    env = _RecordingEnv(temp_dir="/env-tmp")
    already_persisted = (
        f"{_PERSISTED_OUTPUT_TAG}\n"
        "Full output saved to: /env-tmp/already.txt\n"
        f"{_PERSISTED_OUTPUT_CLOSE}"
    )
    largest = ("largest line with several tokens\n" * 500) + "LARGEST_FULL_TAIL"
    medium = ("medium line\n" * 80) + "MEDIUM_STAYS_INLINE"
    small = "small result"
    messages = [
        {"role": "tool", "tool_call_id": "call_small", "name": "small_tool", "content": small},
        {
            "role": "tool",
            "tool_call_id": "call_already",
            "name": "already_tool",
            "content": already_persisted,
        },
        {
            "role": "tool",
            "tool_call_id": "call_largest",
            "name": "largest_tool",
            "content": largest,
        },
        {"role": "tool", "tool_call_id": "call_medium", "name": "medium_tool", "content": medium},
    ]

    result = storage.enforce_turn_budget(
        messages,
        env=env,
        turn_budget_chars=8_000,
        preview_chars=64,
        local_dir=tmp_path,
    )

    assert result[0]["content"] == small
    assert result[1]["content"] == already_persisted
    assert result[3]["content"] == medium
    assert _PERSISTED_OUTPUT_TAG in result[2]["content"]
    assert "Full output saved to: /env-tmp/" in result[2]["content"]
    assert "call_largest" in result[2]["content"]
    assert "LARGEST_FULL_TAIL" not in result[2]["content"]
    assert env.calls[0]["stdin_data"] == largest
    assert json.loads(env.calls[1]["stdin_data"])["tool_use_id"] == "call_largest"


def test_registry_exposes_hermes_style_result_size_caps():
    from aegis.tools.registry import default_registry

    registry = default_registry(include_plugins=False)

    assert registry.get_max_result_size("bash") == 100_000
    assert registry.get_max_result_size("terminal") == 100_000
    assert registry.get_max_result_size("does_not_exist") == 100_000
