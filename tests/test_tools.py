"""Built-in and extended tools."""

from __future__ import annotations


def _ctx(tmp_path):
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    return ToolContext(cwd=tmp_path, config=Config.load())


def test_read_write_edit(tmp_path):
    from aegis.tools.builtin import EditFileTool, ReadFileTool, WriteFileTool
    ctx = _ctx(tmp_path)
    assert not WriteFileTool().run({"path": "a.txt", "content": "one\ntwo"}, ctx).is_error
    r = ReadFileTool().run({"path": "a.txt"}, ctx)
    assert "one" in r.content and "two" in r.content
    EditFileTool().run({"path": "a.txt", "old_string": "two", "new_string": "2"}, ctx)
    assert (tmp_path / "a.txt").read_text().endswith("2")


def test_edit_ambiguous_requires_replace_all(tmp_path):
    from aegis.tools.builtin import EditFileTool, WriteFileTool
    ctx = _ctx(tmp_path)
    WriteFileTool().run({"path": "b.txt", "content": "x x x"}, ctx)
    assert EditFileTool().run({"path": "b.txt", "old_string": "x", "new_string": "y"}, ctx).is_error
    assert not EditFileTool().run(
        {"path": "b.txt", "old_string": "x", "new_string": "y", "replace_all": True}, ctx).is_error


def test_list_glob_search(tmp_path):
    from aegis.tools.builtin import GlobTool, ListDirTool, SearchTool, WriteFileTool
    ctx = _ctx(tmp_path)
    WriteFileTool().run({"path": "src/main.py", "content": "def hello(): pass"}, ctx)
    assert "src" in ListDirTool().run({"path": "."}, ctx).content
    assert "main.py" in GlobTool().run({"pattern": "**/*.py"}, ctx).content
    assert "hello" in SearchTool().run({"pattern": "def hello"}, ctx).content


def test_todo_tool(tmp_path):
    from aegis.session import Session
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import TodoWriteTool
    from aegis.config import Config
    s = Session.create()
    ctx = ToolContext(cwd=tmp_path, config=Config.load(), session=s)
    TodoWriteTool().run({"todos": [{"content": "a", "status": "completed"},
                                   {"content": "b", "status": "pending"}]}, ctx)
    assert len(s.todos) == 2


def test_apply_patch(tmp_path):
    import shutil
    if not shutil.which("git"):
        return
    from aegis.tools.extra_builtin import ApplyPatchTool
    f = tmp_path / "h.txt"
    f.write_text("a\nb\nc\n")
    patch = "--- a/h.txt\n+++ b/h.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    assert not ApplyPatchTool().run({"patch": patch}, _ctx(tmp_path)).is_error
    assert "B" in f.read_text()


def test_schedule_task_tool(tmp_path):
    from aegis.tools.extra_builtin import ScheduleTaskTool
    from aegis.cron import CronStore
    res = ScheduleTaskTool().run({"schedule": "30m", "prompt": "check"}, _ctx(tmp_path))
    assert not res.is_error
    assert len(CronStore().list()) == 1


def test_schedule_task_defaults_to_current_channel(tmp_path):
    from aegis.config import Config
    from aegis.cron import CronStore
    from aegis.tools.base import ToolContext
    from aegis.tools.extra_builtin import ScheduleTaskTool

    agent = type("Agent", (), {"platform": "telegram", "chat_id": "42"})()
    ctx = ToolContext(cwd=tmp_path, config=Config.load(), agent=agent)
    res = ScheduleTaskTool().run({"schedule": "30m", "prompt": "check"}, ctx)

    assert not res.is_error
    job = CronStore().list()[0]
    assert job.deliver == "telegram:42"
    assert "telegram:42" in res.content


def test_bash_tool_runs(tmp_path):
    from aegis.tools.builtin import BashTool
    res = BashTool().run({"command": "echo hello-bash"}, _ctx(tmp_path))
    assert "hello-bash" in res.content and not res.is_error


def test_bash_tool_persists_local_shell_state_by_task(tmp_path):
    from aegis.tools.backends import cleanup_task_environment
    from aegis.tools.builtin import BashTool

    tool = BashTool()
    task_id = f"test_{tmp_path.name}"
    other_task_id = f"{task_id}_other"
    ctx = _ctx(tmp_path)
    ctx.task_id = task_id
    other = _ctx(tmp_path)
    other.task_id = other_task_id
    try:
        first = tool.run(
            {"command": "mkdir -p inner && cd inner && export AEGIS_TEST_PERSIST=ok"},
            ctx,
        )
        assert not first.is_error

        second = tool.run(
            {"command": 'printf "%s|%s" "$AEGIS_TEST_PERSIST" "$(pwd -P)"'},
            ctx,
        )
        assert not second.is_error
        assert f"ok|{tmp_path / 'inner'}" in second.content

        isolated = tool.run(
            {"command": 'printf "%s|%s" "${AEGIS_TEST_PERSIST-unset}" "$(pwd -P)"'},
            other,
        )
        assert not isolated.is_error
        assert f"unset|{tmp_path}" in isolated.content
    finally:
        cleanup_task_environment(task_id)
        cleanup_task_environment(other_task_id)


def test_bash_tool_explains_outer_bwrap_loopback_failure(tmp_path, monkeypatch):
    import aegis.tools.backends as backends
    from aegis.tools.builtin import BashTool

    class FakeEnvironment:
        def execute(self, *_a, **_k):
            return {
                "output": "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n",
                "returncode": 1,
            }

    monkeypatch.setattr(
        backends,
        "_get_or_create_local_environment",
        lambda *_a, **_k: FakeEnvironment(),
    )
    res = BashTool().run({"command": "pwd"}, _ctx(tmp_path))

    assert res.is_error
    assert "bubblewrap wrapper failed before the command ran" in res.content
    assert "Subagents can still spawn" in res.content
    assert "[exit 126]" in res.content


def test_docker_environment_injects_task_id(tmp_path, monkeypatch):
    import aegis.tools.environments.docker as docker_env
    from aegis.tools.environments.docker import DockerEnvironment

    seen = {}

    class Proc:
        stdout = "ok"
        stderr = ""
        returncode = 0

    def fake_run(argv, **_kwargs):
        seen["argv"] = argv
        return Proc()

    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)

    result = DockerEnvironment(
        image="python:3.12-slim",
        cwd=str(tmp_path),
        timeout=10,
        task_id="sub_test",
    ).execute("echo hi")

    assert result == {"output": "ok", "returncode": 0}
    assert "AEGIS_TASK_ID=sub_test" in seen["argv"]


def test_daytona_backend_fails_closed(tmp_path):
    from aegis.config import Config
    from aegis.tools.backends import run_command

    out, code = run_command("echo hi", str(tmp_path), 10, "daytona", Config.load())

    assert code == 126
    assert "daytona backend is not configured" in out


def test_process_tool_submit_and_wait(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.process import ProcessTool
    from aegis.tools.process_registry import process_registry

    ctx = ToolContext(cwd=tmp_path)
    ctx.task_id = "proc_submit_task"
    tool = ProcessTool()
    start = tool.run({"action": "start", "command": "read line; echo got:$line"}, ctx)
    sid = start.data["session_id"]
    try:
        submit = tool.run({"action": "submit", "session_id": sid, "data": "hello"}, ctx)
        assert not submit.is_error
        waited = tool.run({"action": "wait", "session_id": sid, "timeout": 5}, ctx)
        assert not waited.is_error
        logs = tool.run({"action": "log", "session_id": sid}, ctx)
        assert "got:hello" in logs.content
        assert process_registry.list_sessions(task_id="proc_submit_task")
    finally:
        process_registry.kill_process(sid)
        process_registry._finished.pop(sid, None)


def test_task_env_override_updates_live_local_cwd(tmp_path):
    from aegis.tools.backends import (
        cleanup_task_environment,
        clear_task_env_overrides,
        register_task_env_overrides,
    )
    from aegis.tools.builtin import BashTool

    task_id = f"override_{tmp_path.name}"
    ctx = _ctx(tmp_path)
    ctx.task_id = task_id
    next_dir = tmp_path / "next"
    next_dir.mkdir()
    try:
        assert not BashTool().run({"command": "pwd -P"}, ctx).is_error
        register_task_env_overrides(task_id, {"cwd": str(next_dir)})
        res = BashTool().run({"command": "pwd -P"}, ctx)
        assert not res.is_error
        assert str(next_dir) in res.content
    finally:
        clear_task_env_overrides(task_id)
        cleanup_task_environment(task_id)


def test_docker_backend_uses_task_image_override(tmp_path, monkeypatch):
    import aegis.tools.backends as backends
    from aegis.config import Config

    seen = {}

    class FakeDockerEnvironment:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def execute(self, command, **_kwargs):
            seen["command"] = command
            return {"output": "ok", "returncode": 0}

    monkeypatch.setattr(backends.shutil, "which", lambda *_args: "/usr/bin/docker")
    monkeypatch.setattr(backends, "DockerEnvironment", FakeDockerEnvironment)
    backends.register_task_env_overrides("sub_img", {"docker_image": "custom:latest"})
    try:
        out, code = backends.run_command(
            "echo hi",
            str(tmp_path),
            10,
            "docker",
            Config.load(),
            task_id="sub_img",
        )
    finally:
        backends.clear_task_env_overrides("sub_img")

    assert (out, code) == ("ok", 0)
    assert seen["image"] == "custom:latest"
    assert seen["task_id"] == "sub_img"


def test_registry_has_full_surface():
    from aegis.tools.registry import default_registry
    names = {t.name for t in default_registry().all()}
    for expected in ("read_file", "write_file", "edit_file", "bash", "web_search", "web_fetch",
                     "memory", "skill", "execute_code", "browser", "lsp", "session_search",
                     "agent_state", "apply_patch", "spawn_subagent", "generate_image"):
        assert expected in names, expected
