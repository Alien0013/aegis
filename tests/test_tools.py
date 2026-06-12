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


def test_bash_tool_background_uses_process_registry(tmp_path):
    from types import SimpleNamespace

    from aegis.session import Session
    from aegis.tools.builtin import BashTool
    from aegis.tools.process_registry import process_registry

    ctx = _ctx(tmp_path)
    ctx.task_id = "bash_bg_task"
    ctx.session = Session(id="gateway:room:u1", title="gateway")
    ctx.agent = SimpleNamespace(
        platform="telegram",
        chat_id="room",
        user_id="u1",
        user_name="alien",
        thread_id="topic",
        message_id="msg1",
    )
    res = BashTool().run(
        {
            "command": 'printf "task:%s\\n" "$AEGIS_TASK_ID"',
            "background": True,
        },
        ctx,
    )
    sid = res.data["session_id"]
    try:
        assert not res.is_error
        assert f"session_id: {sid}" in res.content
        session = process_registry.get(sid)
        assert session is not None
        assert session.session_key == "gateway:room:u1"
        assert session.watcher_platform == "telegram"
        assert session.watcher_chat_id == "room"
        assert session.watcher_user_id == "u1"
        assert session.watcher_user_name == "alien"
        assert session.watcher_thread_id == "topic"
        assert session.watcher_message_id == "msg1"
        waited = process_registry.wait(sid, timeout=5)
        assert waited["status"] == "exited"
        log = process_registry.read_log(sid)
        assert "task:bash_bg_task" in log["output"]
    finally:
        process_registry.kill_process(sid)
        process_registry._finished.pop(sid, None)


def test_bash_tool_background_watch_pattern_queues_wakeup(tmp_path):
    import time

    from aegis.agent import wakeups
    from aegis.tools.builtin import BashTool
    from aegis.tools.process_registry import process_registry

    wakeups.drain_wakeups()
    process_registry.drain_notifications()
    ctx = _ctx(tmp_path)
    ctx.task_id = "bash_watch_task"
    res = BashTool().run(
        {
            "command": "printf 'boot\\nREADY\\n'; sleep 0.2",
            "background": True,
            "watch_patterns": ["READY"],
        },
        ctx,
    )
    sid = res.data["session_id"]
    try:
        assert not res.is_error
        assert res.data["notify_on_complete"] is False
        assert res.data["watch_patterns"] == ["READY"]
        events = []
        notes = []
        for _ in range(50):
            events.extend(process_registry.drain_notifications())
            notes = wakeups.drain_wakeups()
            if events and notes:
                break
            time.sleep(0.1)
        assert events
        event, text = events[0]
        assert event["type"] == "watch_match"
        assert event["pattern"] == "READY"
        assert "READY" in text
        assert notes and notes[0]["source"] == "process"
        assert "READY" in notes[0]["text"] or "READY" in notes[0]["title"]
    finally:
        process_registry.kill_process(sid)
        process_registry._finished.pop(sid, None)


def test_bash_tool_background_pty_uses_pty_reader(tmp_path, monkeypatch):
    import time

    from aegis.agent import wakeups
    from aegis.tools import process_registry as registry_mod
    from aegis.tools.builtin import BashTool
    from aegis.tools.process_registry import process_registry

    class FakePty:
        pid = 43210
        exitstatus = 0

        def __init__(self):
            self._alive = True

        def isalive(self):
            return self._alive

        def read(self, _size):
            self._alive = False
            return "PTY READY\n"

        def wait(self):
            return self.exitstatus

        def terminate(self, force=False):
            self._alive = False
            self.exitstatus = -15

        def write(self, _data):
            return None

        def sendeof(self):
            return None

    monkeypatch.setattr(registry_mod, "_spawn_pty_process", lambda *_a, **_k: FakePty())
    wakeups.drain_wakeups()
    process_registry.drain_notifications()
    res = BashTool().run(
        {
            "command": "python -i",
            "background": True,
            "pty": True,
            "watch_patterns": ["READY"],
        },
        _ctx(tmp_path),
    )
    sid = res.data["session_id"]
    try:
        assert not res.is_error
        assert res.data["pty"] is True
        events = []
        for _ in range(50):
            events.extend(process_registry.drain_notifications())
            if events:
                break
            time.sleep(0.1)
        assert events and events[0][0]["type"] == "watch_match"
        assert "PTY READY" in process_registry.read_log(sid)["output"]
    finally:
        process_registry.kill_process(sid)
        process_registry._running.pop(sid, None)
        process_registry._finished.pop(sid, None)


def test_bash_tool_background_pty_falls_back_to_pipes(tmp_path, monkeypatch):
    from aegis.tools import process_registry as registry_mod
    from aegis.tools.builtin import BashTool
    from aegis.tools.process_registry import process_registry

    def no_pty(*_args, **_kwargs):
        raise ImportError("no pty")

    monkeypatch.setattr(registry_mod, "_spawn_pty_process", no_pty)
    res = BashTool().run(
        {
            "command": "echo pipe-fallback",
            "background": True,
            "pty": True,
            "notify_on_complete": False,
        },
        _ctx(tmp_path),
    )
    sid = res.data["session_id"]
    try:
        assert not res.is_error
        assert "ptyprocess is not installed" in res.data["pty_fallback"]
        waited = process_registry.wait(sid, timeout=5)
        assert waited["status"] == "exited"
        assert "pipe-fallback" in waited["output"]
    finally:
        process_registry.kill_process(sid)
        process_registry._finished.pop(sid, None)


def test_process_registry_env_background_polls_log_and_exit(tmp_path, monkeypatch):
    from aegis.tools import process_registry as registry_mod
    from aegis.tools.process_registry import process_registry

    class FakeEnv:
        cwd = str(tmp_path)

        def __init__(self):
            self.log_reads = 0
            self.exit_code = None

        def get_temp_dir(self):
            return "/tmp"

        def execute(self, command, cwd="", timeout=None):
            if "nohup bash -lc" in command:
                return {"output": "4242\n", "returncode": 0}
            if command.startswith("cat ") and ".log" in command:
                self.log_reads += 1
                if self.log_reads >= 2:
                    self.exit_code = 0
                    return {"output": "boot\nREADY\n", "returncode": 0}
                return {"output": "boot\n", "returncode": 0}
            if "echo exited" in command:
                if self.exit_code is None:
                    return {"output": "running\n", "returncode": 0}
                return {"output": f"exited\n{self.exit_code}\n", "returncode": 0}
            return {"output": "", "returncode": 0}

    monkeypatch.setattr(registry_mod, "ENV_POLL_INTERVAL_SECONDS", 0.01)
    process_registry.drain_notifications()
    session = process_registry.spawn_via_env(
        FakeEnv(),
        "printf READY",
        cwd=str(tmp_path),
        task_id="env_bg_task",
        watch_patterns=["READY"],
    )
    try:
        assert session.pid == 4242
        waited = process_registry.wait(session.id, timeout=5)
        assert waited["status"] == "exited"
        assert waited["exit_code"] == 0
        assert "READY" in waited["output"]
        events = process_registry.drain_notifications()
        assert events and events[0][0]["type"] == "watch_match"
    finally:
        process_registry.kill_process(session.id)
        process_registry._running.pop(session.id, None)
        process_registry._finished.pop(session.id, None)


def test_bash_tool_background_uses_nonlocal_env_backend(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.tools import backends
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import BashTool
    from aegis.tools.process_registry import process_registry

    config = Config.load()
    config.data["tools"]["terminal_backend"] = "ssh"
    seen = {}
    fake_env = object()

    def fake_create_environment(backend, cwd, timeout, config, task_id=None):
        seen["create"] = (backend, cwd, timeout, task_id)
        return fake_env, "", "ssh"

    def fake_spawn(env, command, **kwargs):
        seen["spawn"] = (env, command, kwargs)
        return SimpleNamespace(
            id="proc_env",
            pid=4242,
            exited=False,
            pty=False,
            pty_fallback="",
        )

    monkeypatch.setattr(backends, "create_environment", fake_create_environment)
    monkeypatch.setattr(process_registry, "spawn_via_env", fake_spawn)
    ctx = ToolContext(cwd=tmp_path, config=config)
    ctx.task_id = "nonlocal_task"

    res = BashTool().run({"command": "sleep 60", "background": True}, ctx)

    assert not res.is_error
    assert res.data["backend"] == "ssh"
    assert seen["create"] == ("ssh", str(tmp_path), 120, "nonlocal_task")
    assert seen["spawn"][0] is fake_env
    assert seen["spawn"][1] == "sleep 60"
    assert seen["spawn"][2]["task_id"] == "nonlocal_task"


def test_process_tool_start_uses_nonlocal_env_backend(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.tools import backends
    from aegis.tools.base import ToolContext
    from aegis.tools.process import ProcessTool
    from aegis.tools.process_registry import process_registry

    config = Config.load()
    config.data["tools"]["terminal_backend"] = "ssh"
    seen = {}
    fake_env = object()

    def fake_create_environment(backend, cwd, timeout, config, task_id=None):
        seen["create"] = (backend, cwd, timeout, task_id)
        return fake_env, "", "ssh"

    def fake_spawn(env, command, **kwargs):
        seen["spawn"] = (env, command, kwargs)
        return SimpleNamespace(
            id="proc_env",
            pid=4242,
            exited=False,
            pty=False,
            pty_fallback="",
        )

    monkeypatch.setattr(backends, "create_environment", fake_create_environment)
    monkeypatch.setattr(process_registry, "spawn_via_env", fake_spawn)
    ctx = ToolContext(cwd=tmp_path, config=config)
    ctx.task_id = "proc_nonlocal_task"

    res = ProcessTool().run({"action": "start", "command": "sleep 60"}, ctx)

    assert not res.is_error
    assert res.data["backend"] == "ssh"
    assert seen["create"] == ("ssh", str(tmp_path), 120, "proc_nonlocal_task")
    assert seen["spawn"][0] is fake_env
    assert seen["spawn"][1] == "sleep 60"
    assert seen["spawn"][2]["task_id"] == "proc_nonlocal_task"


def test_bash_tool_background_rejects_unavailable_nonlocal_backend(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.tools import backends
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import BashTool

    config = Config.load()
    config.data.setdefault("tools", {})["terminal_backend"] = "ssh"
    ctx = ToolContext(cwd=tmp_path, config=config)
    monkeypatch.setattr(
        backends,
        "create_environment",
        lambda *_args, **_kwargs: (None, "sandbox unavailable: ssh not configured", "ssh"),
    )

    res = BashTool().run({"command": "echo hi", "background": True}, ctx)

    assert res.is_error
    assert "ssh not configured" in res.content


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
    from aegis.session import Session
    from aegis.tools.base import ToolContext
    from aegis.tools.process import ProcessTool
    from aegis.tools.process_registry import process_registry

    ctx = ToolContext(cwd=tmp_path)
    ctx.task_id = "proc_submit_task"
    ctx.session = Session(id="terminal:proc-session", title="terminal")
    tool = ProcessTool()
    start = tool.run({"action": "start", "command": "read line; echo got:$line"}, ctx)
    sid = start.data["session_id"]
    try:
        session = process_registry.get(sid)
        assert session is not None
        assert session.session_key == "terminal:proc-session"
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


def test_process_registry_cleans_ansi_and_shell_noise(tmp_path):
    import time

    from aegis.tools.process_registry import ProcessRegistry, ProcessSession

    registry = ProcessRegistry()
    sid = "proc_clean"
    session = ProcessSession(
        id=sid,
        command="fake",
        cwd=str(tmp_path),
        started_at=time.time(),
        exited=True,
        exit_code=0,
        output_buffer=(
            "bash: cannot set terminal process group (123): Inappropriate ioctl for device\n"
            "\x1b[31mREADY\x1b[0m\n"
        ),
        notify_on_complete=True,
    )
    try:
        registry._finished[sid] = session
        registry._queue_completion(session)
        event, _text = registry.drain_notifications()[0]
        poll = registry.poll(sid)
        log = registry.read_log(sid)
        waited = registry.wait(sid, timeout=1)

        for output in (poll["output_preview"], log["output"], waited["output"], event["output"]):
            assert "READY" in output
            assert "\x1b" not in output
            assert "cannot set terminal process group" not in output
    finally:
        registry._finished.pop(sid, None)


def test_process_registry_recovers_running_checkpoint(tmp_path, monkeypatch):
    from aegis.tools.process_registry import ProcessRegistry

    home = tmp_path / "home"
    monkeypatch.setenv("AEGIS_HOME", str(home))
    registry = ProcessRegistry()
    session = registry.spawn_local(
        "sleep 5",
        cwd=tmp_path,
        task_id="recover_task",
        watch_patterns=["READY"],
    )
    try:
        recovered = ProcessRegistry()
        poll = recovered.poll(session.id)
        assert poll["status"] == "running"
        assert poll["detached"] is True
        assert poll["watch_patterns"] == ["READY"]
        assert recovered.has_active_processes("recover_task")
    finally:
        registry.kill_process(session.id)
        ProcessRegistry().kill_process(session.id)


def test_process_registry_does_not_recover_sandbox_pid_as_host(tmp_path, monkeypatch):
    import json
    import os
    import time

    from aegis.tools.process_registry import ProcessRegistry

    home = tmp_path / "home"
    monkeypatch.setenv("AEGIS_HOME", str(home))
    home.mkdir()
    (home / "processes.json").write_text(
        json.dumps({
            "running": [{
                "id": "proc_sandbox",
                "command": "sleep 99",
                "task_id": "sandbox_task",
                "pid": os.getpid(),
                "pid_scope": "sandbox",
                "cwd": str(tmp_path),
                "started_at": time.time(),
                "exited": False,
                "watch_patterns": ["READY"],
            }],
            "finished": [],
        }),
        encoding="utf-8",
    )

    recovered = ProcessRegistry()
    poll = recovered.poll("proc_sandbox")

    assert poll["status"] == "exited"
    assert poll["detached"] is True
    assert poll["pid_scope"] == "sandbox"
    assert "sandbox-local PID" in poll["note"]
    assert "sandbox-local PID" in poll["output_preview"]
    assert not recovered.has_active_processes("sandbox_task")


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


def test_task_terminal_backend_override_changes_dispatch(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.tools import backends

    seen = {}

    def fake_docker(command, cwd, timeout, config, task_id=None):
        seen.update({
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
            "task_id": task_id,
        })
        return "docker-ok", 0

    monkeypatch.setattr(backends, "_run_docker", fake_docker)
    backends.register_task_env_overrides("sub_backend", {"terminal_backend": "docker"})
    try:
        out, code = backends.run_command(
            "echo hi",
            str(tmp_path),
            7,
            "local",
            Config.load(),
            task_id="sub_backend",
        )
    finally:
        backends.clear_task_env_overrides("sub_backend")

    assert (out, code) == ("docker-ok", 0)
    assert seen == {
        "command": "echo hi",
        "cwd": str(tmp_path),
        "timeout": 7,
        "task_id": "sub_backend",
    }


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
