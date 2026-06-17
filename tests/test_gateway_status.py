"""Gateway planned-stop marker helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def test_planned_stop_marker_write_and_consume_self(tmp_path, monkeypatch):
    from aegis.gateway import status

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: "42")

    assert status.write_planned_stop_marker(os.getpid()) is True
    marker = tmp_path / ".gateway-planned-stop.json"
    payload = json.loads(marker.read_text())
    assert payload["target_pid"] == os.getpid()
    assert payload["target_start_time"] == "42"
    assert payload["stopper_pid"] == os.getpid()

    assert status.consume_planned_stop_marker_for_self() is True
    assert not marker.exists()


def test_planned_stop_marker_consume_false_for_different_pid_and_unlinks(tmp_path, monkeypatch):
    from aegis.gateway import status

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: "42")

    assert status.write_planned_stop_marker(os.getpid() + 9999) is True
    marker = tmp_path / ".gateway-planned-stop.json"

    assert status.consume_planned_stop_marker_for_self() is False
    assert not marker.exists()


def test_planned_stop_marker_stale_cleanup(tmp_path, monkeypatch):
    from aegis.gateway import status

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    marker = tmp_path / ".gateway-planned-stop.json"
    marker.write_text(json.dumps({
        "target_pid": os.getpid(),
        "target_start_time": None,
        "stopper_pid": os.getpid(),
        "written_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    }))

    assert status.planned_stop_marker_targets_self() is False
    assert not marker.exists()


def test_planned_stop_marker_probe_is_non_destructive(tmp_path, monkeypatch):
    from aegis.gateway import status

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: "42")

    assert status.write_planned_stop_marker(os.getpid()) is True
    marker = tmp_path / ".gateway-planned-stop.json"
    assert status.planned_stop_marker_targets_self() is True
    assert marker.exists()
    status.clear_planned_stop_marker()
    assert not marker.exists()


def test_gateway_shutdown_signal_records_planned_stop(tmp_path, monkeypatch):
    import signal
    import pytest
    from aegis.config import Config
    from aegis.gateway import status
    from aegis.gateway.runner import GatewayRunner

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: "42")
    status.write_planned_stop_marker(os.getpid())
    runner = GatewayRunner(Config.load(), cwd=tmp_path)

    with pytest.raises(KeyboardInterrupt):
        runner._on_shutdown_signal(signal.SIGTERM, None)

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "shutdowns.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert rows[-1]["cause"] == "planned_stop"
    assert not (tmp_path / ".gateway-planned-stop.json").exists()
    runner._record_shutdown("KeyboardInterrupt")
    rows_after = (tmp_path / "logs" / "shutdowns.jsonl").read_text().splitlines()
    assert len(rows_after) == len(rows)


def test_gateway_shutdown_marks_locked_session_resume_pending(tmp_path, monkeypatch):
    import signal
    import threading

    import pytest
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import GatewayRunner

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    runner = GatewayRunner(Config.load(), cwd=tmp_path)
    ev = MessageEvent(platform="telegram", chat_id="c1", text="work", user_id="u1")
    key = runner._key(ev)
    session = runner._session(key)
    runner.store.save(session)
    lock = threading.Lock()
    runner._key_locks[key] = lock
    lock.acquire()
    try:
        with pytest.raises(KeyboardInterrupt):
            runner._on_shutdown_signal(signal.SIGTERM, None)
    finally:
        lock.release()

    loaded = runner.store.load(key)
    assert loaded.meta["resume_pending"] is True
    assert loaded.meta["resume_reason"] == "SIGTERM"
    assert loaded.meta["last_resume_marked_at"]


def test_planned_stop_watcher_marks_locked_session_and_interrupts(tmp_path, monkeypatch):
    import threading

    from aegis.config import Config
    from aegis.gateway import status
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import GatewayRunner

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: "42")
    status.write_planned_stop_marker(os.getpid())

    runner = GatewayRunner(Config.load(), cwd=tmp_path)
    ev = MessageEvent(platform="telegram", chat_id="c1", text="work", user_id="u1")
    key = runner._key(ev)
    session = runner._session(key)
    runner.store.save(session)
    lock = threading.Lock()
    runner._key_locks[key] = lock
    lock.acquire()
    interrupted = []
    try:
        assert runner._consume_planned_stop_request(
            interrupt_main=lambda: interrupted.append(True),
        ) is True
    finally:
        lock.release()

    loaded = runner.store.load(key)
    assert loaded.meta["resume_pending"] is True
    assert loaded.meta["resume_reason"] == "planned_stop"
    assert loaded.meta["last_resume_marked_at"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "shutdowns.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert rows[-1]["cause"] == "planned_stop"
    assert interrupted == [True]
    assert not (tmp_path / ".gateway-planned-stop.json").exists()
    assert runner._consume_planned_stop_request(
        interrupt_main=lambda: interrupted.append(False),
    ) is False
    assert interrupted == [True]


def test_gateway_startup_recovers_stale_running_gateway_runs(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.gateway.runner import GatewayRunner
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    session = Session(id="telegram:c1:u1", title="gateway chat")
    SessionStore().save(session)
    run = RunStore().start(
        surface="gateway",
        kind="gateway",
        title="stale turn",
        session_id=session.id,
        prompt="finish the deployment",
    )
    runner = GatewayRunner(Config.load(), cwd=tmp_path)

    assert runner._recover_stale_gateway_runs() == 1

    loaded = runner.store.load(session.id)
    assert loaded.meta["resume_pending"] is True
    assert loaded.meta["resume_reason"] == "restart_interrupted"
    saved_run = RunStore().get(run["id"])
    assert saved_run["status"] == "interrupted"
    assert "Gateway restarted" in saved_run["error"]
    assert saved_run["data"]["resume_pending"] is True
    assert saved_run["data"]["recovered_by_gateway_start"] is True
    assert runner._recover_stale_gateway_runs() == 0


def test_daemon_gateway_stop_marks_planned_stop(monkeypatch):
    from aegis import daemon

    calls = []
    marked = []

    def fake_systemctl(*args):
        calls.append(args)
        if args[:3] == ("show", "aegis-gateway.service", "--property=MainPID"):
            return type("R", (), {"returncode": 0, "stdout": "12345\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "stopped", "stderr": ""})()

    monkeypatch.setattr(daemon.shutil, "which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(daemon, "_systemctl", fake_systemctl)
    monkeypatch.setattr("aegis.gateway.status.write_planned_stop_marker", lambda pid: marked.append(pid) or True)

    result = daemon.control_gateway_service("stop")

    assert result.ok is True
    assert marked == [12345]
    assert ("stop", "aegis-gateway.service") in calls
