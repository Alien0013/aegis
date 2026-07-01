"""Stage Z live backend interrupt propagation."""

from __future__ import annotations

import shlex
import os
import sys
import threading
import time


class _BlockingEnv:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cwd = kwargs.get("cwd", "")
        self.task_id = kwargs.get("task_id", "default")
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.calls: list[dict] = []
        self.cancel_calls = 0

    def get_temp_dir(self) -> str:
        return "/tmp"

    def execute(self, command, timeout=30, stdin_data=None, **kwargs):
        self.calls.append(
            {
                "command": command,
                "timeout": timeout,
                "stdin_data": stdin_data,
                "kwargs": kwargs,
            }
        )
        self.started.set()
        self.cancelled.wait(10)
        return {
            "output": "backend cancel observed" if self.cancelled.is_set() else "backend completed",
            "returncode": 0,
        }

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancelled.set()

    def cleanup(self) -> None:
        self.cancelled.set()


def _config(tmp_path, monkeypatch):
    from aegis.config import Config

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    cfg = Config.load()
    cfg.data.setdefault("tools", {})["terminal_backend"] = "docker"
    return cfg


def _fake_docker(monkeypatch):
    from aegis.tools import backends

    envs: list[_BlockingEnv] = []

    class FakeDockerEnvironment(_BlockingEnv):
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


def _run_in_thread(fn):
    ready = threading.Event()
    result: dict[str, object] = {}
    thread_id: dict[str, int] = {}

    def runner():
        thread_id["value"] = threading.current_thread().ident or 0
        ready.set()
        result["value"] = fn()

    thread = threading.Thread(target=runner)
    thread.start()
    assert ready.wait(1)
    return thread, thread_id, result


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_foreground_local_backend_interrupt_kills_live_process(tmp_path):
    from aegis.tools import backends
    from aegis.tools.interrupt import clear_interrupt, set_interrupt

    task_id = "stage_z_local_interrupt"
    started_path = tmp_path / "started.txt"
    code = (
        "import pathlib, time; "
        f"pathlib.Path({str(started_path)!r}).write_text('started'); "
        "time.sleep(30)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    thread, thread_id, result = _run_in_thread(
        lambda: backends.run_command(
            command,
            str(tmp_path),
            60,
            "local",
            None,
            task_id=task_id,
        )
    )
    try:
        deadline = time.monotonic() + 5
        while not started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started_path.exists()

        set_interrupt(True, thread_id["value"])
        thread.join(timeout=5)

        assert not thread.is_alive()
        out, code = result["value"]
        assert code == 130
        assert "interrupted" in out.lower()
    finally:
        clear_interrupt(thread_id.get("value"))
        backends.cleanup_task_environment(task_id, backend="local")
        thread.join(timeout=1)


def test_foreground_nonlocal_backend_interrupt_fires_cancel_hook(tmp_path, monkeypatch):
    from aegis.tools import backends
    from aegis.tools.interrupt import clear_interrupt, set_interrupt

    cfg = _config(tmp_path, monkeypatch)
    envs = _fake_docker(monkeypatch)
    task_id = "stage_z_docker_interrupt"

    thread, thread_id, result = _run_in_thread(
        lambda: backends.run_command(
            "sleep 30",
            str(tmp_path),
            60,
            "docker",
            cfg,
            task_id=task_id,
        )
    )
    try:
        deadline = time.monotonic() + 2
        while (not envs or not envs[0].started.is_set()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert envs and envs[0].started.is_set()

        set_interrupt(True, thread_id["value"])
        thread.join(timeout=5)

        assert not thread.is_alive()
        out, code = result["value"]
        assert code == 130
        assert "interrupted" in out.lower()
        assert envs[0].cancel_calls == 1
    finally:
        clear_interrupt(thread_id.get("value"))
        backends.cleanup_task_environment(task_id, backend="docker")
        thread.join(timeout=1)


def test_ssh_backend_interrupt_terminates_live_client_process(tmp_path, monkeypatch):
    from aegis.tools import backends
    from aegis.tools.interrupt import clear_interrupt, set_interrupt

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "ssh.pid"
    started_file = tmp_path / "ssh.started"
    fake_ssh = bin_dir / "ssh"
    fake_ssh.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import os, pathlib, time",
                "pathlib.Path(os.environ['AEGIS_FAKE_SSH_PID']).write_text(str(os.getpid()))",
                "pathlib.Path(os.environ['AEGIS_FAKE_SSH_STARTED']).write_text('started')",
                "time.sleep(30)",
            ]
        ),
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("TERMINAL_SSH_HOST", "example.invalid")
    monkeypatch.delenv("TERMINAL_SSH_USER", raising=False)
    monkeypatch.delenv("TERMINAL_SSH_PORT", raising=False)
    monkeypatch.setenv("AEGIS_FAKE_SSH_PID", str(pid_file))
    monkeypatch.setenv("AEGIS_FAKE_SSH_STARTED", str(started_file))
    task_id = "stage_z_ssh_interrupt"

    thread, thread_id, result = _run_in_thread(
        lambda: backends.run_command(
            "sleep 30",
            str(tmp_path),
            60,
            "ssh",
            None,
            task_id=task_id,
        )
    )
    pid = 0
    try:
        deadline = time.monotonic() + 5
        while not started_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started_file.exists()
        pid = int(pid_file.read_text(encoding="utf-8").strip())

        set_interrupt(True, thread_id["value"])
        thread.join(timeout=5)

        assert not thread.is_alive()
        deadline = time.monotonic() + 2
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _pid_alive(pid)
        out, code = result["value"]
        assert code == 130
        assert "interrupted" in out.lower()
    finally:
        clear_interrupt(thread_id.get("value"))
        if pid and _pid_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        backends.cleanup_task_environment(task_id, backend="ssh")
        thread.join(timeout=1)


def test_backend_process_start_reports_interrupted_status(tmp_path, monkeypatch):
    from aegis.tools import backends
    from aegis.tools.interrupt import clear_interrupt, set_interrupt
    from aegis.tools.process_registry import ProcessRegistry

    cfg = _config(tmp_path, monkeypatch)
    envs = _fake_docker(monkeypatch)
    task_id = "stage_z_process_interrupt"
    env, error, backend = backends.create_environment(
        "docker",
        str(tmp_path),
        60,
        cfg,
        task_id=task_id,
    )
    assert error == ""
    assert backend == "docker"
    assert env is envs[0]

    registry = ProcessRegistry()
    thread, thread_id, result = _run_in_thread(
        lambda: registry.spawn_via_env(
            env,
            "sleep 30",
            cwd=str(tmp_path),
            task_id=task_id,
            timeout=60,
        )
    )
    try:
        assert envs[0].started.wait(2)

        set_interrupt(True, thread_id["value"])
        thread.join(timeout=5)

        assert not thread.is_alive()
        session = result["value"]
        assert session.exited is True
        assert session.exit_code == 130
        assert "interrupted" in session.output_buffer.lower()
        assert envs[0].cancel_calls == 1
    finally:
        clear_interrupt(thread_id.get("value"))
        backends.cleanup_task_environment(task_id, backend="docker")
        thread.join(timeout=1)
