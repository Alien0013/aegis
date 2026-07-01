"""Stage Z backend environment handoff for large result storage."""

from __future__ import annotations

import json
import shlex


class _RecordingEnv:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cwd = kwargs.get("cwd")
        self.task_id = kwargs.get("task_id")
        self.calls: list[dict] = []
        self.files: dict[str, str] = {}

    def get_temp_dir(self) -> str:
        return "/remote/tmp"

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
        if "cat" in tokens and ">" in tokens:
            self.files[tokens[tokens.index(">") + 1]] = stdin_data or ""
        return {"output": "remote-ok", "returncode": 0}


def _config_with_docker_backend(tmp_path, monkeypatch):
    from aegis.config import Config

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    cfg = Config.load()
    cfg.data.setdefault("tools", {})["terminal_backend"] = "docker"
    return cfg


def _fake_docker_backend(monkeypatch):
    from aegis.tools import backends

    envs: list[_RecordingEnv] = []

    class FakeDockerEnvironment(_RecordingEnv):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            envs.append(self)

    monkeypatch.setattr(
        backends.shutil,
        "which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr(backends, "DockerEnvironment", FakeDockerEnvironment)
    return envs


def test_create_environment_nonlocal_backend_caches_active_environment(tmp_path, monkeypatch):
    from aegis.tools import backends

    cfg = _config_with_docker_backend(tmp_path, monkeypatch)
    envs = _fake_docker_backend(monkeypatch)
    task_id = "stage_z_create_env_cache"

    try:
        env, error, backend = backends.create_environment(
            "docker",
            str(tmp_path),
            10,
            cfg,
            task_id=task_id,
        )
        again, again_error, again_backend = backends.create_environment(
            "docker",
            str(tmp_path),
            10,
            cfg,
            task_id=task_id,
        )

        assert error == ""
        assert again_error == ""
        assert backend == "docker"
        assert again_backend == "docker"
        assert env is not None
        assert again is env
        assert envs == [env]
        assert backends.get_active_environment(task_id, "docker") is env
    finally:
        backends.cleanup_task_environment(task_id, backend="docker")


def test_run_command_nonlocal_backend_hands_active_env_to_tool_executor_spill(
    tmp_path,
    monkeypatch,
):
    from aegis.agent.loop import ToolExecutor
    from aegis.tools import backends
    from aegis.tools.base import ToolContext
    from aegis.types import ToolCall

    cfg = _config_with_docker_backend(tmp_path, monkeypatch)
    envs = _fake_docker_backend(monkeypatch)
    task_id = "stage_z_run_command_storage_handoff"
    content = ("large result payload\n" * 200) + "FULL_RESULT_TAIL"

    try:
        out, code = backends.run_command(
            "echo foreground",
            str(tmp_path),
            10,
            "docker",
            cfg,
            task_id=task_id,
        )
        env = envs[0]
        executor = ToolExecutor(
            None,
            None,
            ToolContext(cwd=tmp_path, config=cfg, task_id=task_id),
            lambda _event: None,
        )
        persisted = executor._spill_to_disk(
            ToolCall("call_backend_handoff", "bash", {}),
            content,
            preview_chars=80,
            reason="stage z handoff test",
        )

        assert (out, code) == ("remote-ok", 0)
        assert backends.get_active_environment(task_id, "docker") is env
        assert "Full output saved to: /remote/tmp/aegis-results/" in persisted
        assert "FULL_RESULT_TAIL" not in persisted
        assert [call["stdin_data"] for call in env.calls[:2]] == [None, content]
        assert content not in env.calls[1]["command"]
        metadata = json.loads(env.calls[2]["stdin_data"])
        assert metadata["storage"] == "environment"
        assert metadata["tool_name"] == "bash"
        assert metadata["tool_use_id"] == "call_backend_handoff"
        assert metadata["path"].startswith("/remote/tmp/aegis-results/")
        assert metadata["metadata_path"].endswith(".metadata.json")
    finally:
        backends.cleanup_task_environment(task_id, backend="docker")


def test_bash_tool_foreground_nonlocal_backend_leaves_active_env_for_storage(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import BashTool

    cfg = _config_with_docker_backend(tmp_path, monkeypatch)
    envs = _fake_docker_backend(monkeypatch)
    task_id = "stage_z_bash_foreground_storage_handoff"
    ctx = ToolContext(cwd=tmp_path, config=cfg, task_id=task_id)

    try:
        result = BashTool().run({"command": "echo foreground"}, ctx)
        env = envs[0]

        assert not result.is_error
        assert "remote-ok" in result.content
        assert backends.get_active_environment(task_id, "docker") is env
    finally:
        backends.cleanup_task_environment(task_id, backend="docker")


def test_read_file_nonlocal_backend_can_page_persisted_result_path(tmp_path, monkeypatch):
    from aegis.agent.loop import ToolExecutor
    from aegis.tools import backends, tool_result_storage
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import ReadFileTool
    from aegis.types import ToolCall

    cfg = _config_with_docker_backend(tmp_path, monkeypatch)
    envs = _fake_docker_backend(monkeypatch)
    task_id = "stage_z_read_remote_persisted_output"
    content = "\n".join(f"remote persisted line {i}" for i in range(1, 9)) + "\nREMOTE_FULL_TAIL"

    try:
        backends.run_command(
            "echo foreground",
            str(tmp_path),
            10,
            "docker",
            cfg,
            task_id=task_id,
        )
        env = envs[0]
        executor = ToolExecutor(
            None,
            None,
            ToolContext(cwd=tmp_path, config=cfg, task_id=task_id),
            lambda _event: None,
        )
        persisted = executor._spill_to_disk(
            ToolCall("call_read_remote_result", "bash", {}),
            content,
            preview_chars=40,
            reason="stage z read_file remote handoff",
        )
        reference = tool_result_storage.parse_persisted_output_reference(persisted)

        result = ReadFileTool().run(
            {"path": reference["file_path"], "offset": 2, "limit": 20},
            ToolContext(cwd=tmp_path, config=cfg, task_id=task_id),
        )

        assert not result.is_error
        assert "remote persisted line 2" in result.content
        assert "REMOTE_FULL_TAIL" in result.content
        assert any(call["command"].startswith("cat /remote/tmp/aegis-results/") for call in env.calls)
    finally:
        backends.cleanup_task_environment(task_id, backend="docker")
