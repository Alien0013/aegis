"""Gateway queue, trajectory tooling, process tool, tool-gateway status, backends, channels."""

from __future__ import annotations


def test_delivery_queue_backoff():
    from aegis.gateway.queue import DeliveryQueue
    q = DeliveryQueue()
    q.enqueue("telegram", "chat1", "hello")
    due = q.due()
    assert len(due) == 1 and q.pending_count() == 1
    q.mark_failed(due[0]["id"], attempts=0)        # schedules a retry, still pending
    assert q.pending_count() == 1
    q.mark_sent(due[0]["id"])
    assert q.pending_count() == 0


def test_delivery_queue_gives_up_after_max():
    from aegis.gateway.queue import DeliveryQueue
    q = DeliveryQueue()
    q.enqueue("x", "c", "t")
    rid = q.due()[0]["id"]
    q.mark_failed(rid, attempts=4, max_attempts=5)  # 5th attempt -> failed
    assert q.pending_count() == 0


def test_trajectory_record_export_stats(tmp_path):
    from aegis import trajectory
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    s = Session.create()
    s.messages = [Message.user("hi"), Message.assistant("hello there")]
    SessionStore().save(s)
    traj = trajectory.record(s.id)
    assert traj["n_steps"] == 2 and traj["approx_tokens"] > 0
    out = tmp_path / "t.jsonl"
    assert trajectory.export(str(out)) >= 1 and out.exists()
    assert trajectory.stats()["trajectories"] >= 1


def test_trajectory_compress_truncates():
    from aegis import trajectory
    traj = {"messages": [{"role": "tool", "content": "x" * 5000}], "approx_tokens": 1}
    out = trajectory.compress(traj, None)
    assert len(out["messages"][0]["content"]) < 600 and "truncated" in out["messages"][0]["content"]


def test_process_tool_lifecycle(tmp_path):
    import time
    from aegis.tools.base import ToolContext
    from aegis.tools.process import ProcessTool
    ctx = ToolContext(cwd=tmp_path)
    t = ProcessTool()
    res = t.run({"action": "start", "command": "sleep 5"}, ctx)
    pid_id = res.display.split()[-1]
    assert not res.is_error
    assert pid_id in t.run({"action": "list"}, ctx).content
    time.sleep(0.2)
    t.run({"action": "stop", "id": pid_id}, ctx)
    assert pid_id not in t.run({"action": "list"}, ctx).content


def test_tools_status():
    from aegis.config import Config
    from aegis.tools.cloud import tools_status
    st = tools_status(Config.load())
    assert "web_search" in st and "terminal_backend" in st


def test_singularity_backend_fails_closed(tmp_path, monkeypatch):
    import aegis.tools.backends as b
    monkeypatch.setattr(b.shutil, "which", lambda *_: None)   # no apptainer/singularity
    from aegis.config import Config
    out, code = b.run_command("echo hi", str(tmp_path), 10, "singularity", Config.load())
    assert code == 126 and "efus" in out


def test_email_adapter_requires_env(monkeypatch):
    for k in ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    from aegis.gateway.email_channel import EmailAdapter
    import pytest
    with pytest.raises(RuntimeError):
        EmailAdapter()


def test_mcp_server_exposes_tools():
    # the server lists the registry's tools
    from aegis.tools.registry import default_registry
    assert len(default_registry().all()) >= 25


def test_dashboard_serves(monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from aegis.config import Config
    from aegis.dashboard import make_handler, PAGE

    assert "AEGIS" in PAGE and "/api/" in PAGE
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Config.load()))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.handle_request, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/status")
    body = conn.getresponse().read().decode()
    httpd.server_close()
    import json
    data = json.loads(body)
    assert "tools" in data and "version" in data


def test_cli_status_surfaces_inventory(monkeypatch, capsys):
    import aegis.daemon as daemon
    from aegis.cli.main import main

    monkeypatch.setattr(daemon, "status", lambda: {"aegis-dashboard.service": "inactive"})
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Model" in out
    assert "Surface" in out
    assert "tools:" in out
    assert "skills:" in out
    assert "plugins:" in out
    assert "Dashboard" in out


def test_cli_bare_first_run_guard(capsys):
    from aegis.cli.main import main

    rc = main([])

    assert rc == 2
    out = capsys.readouterr().out
    assert "not configured" in out
    assert "aegis setup" in out


def test_cli_bare_existing_config_opens_repl(monkeypatch):
    from aegis.cli.main import main
    from aegis.config import Config

    Config.load().save()
    called = {}

    def fake_interactive(*_args, **_kwargs):
        called["ok"] = True

    monkeypatch.setattr("aegis.cli.repl.interactive", fake_interactive)

    assert main([]) == 0
    assert called["ok"]


def test_cli_bare_first_run_can_be_bypassed(monkeypatch):
    from aegis.cli.main import main

    called = {}

    def fake_interactive(*_args, **_kwargs):
        called["ok"] = True

    monkeypatch.setenv("AEGIS_SKIP_FIRST_RUN", "1")
    monkeypatch.setattr("aegis.cli.repl.interactive", fake_interactive)

    assert main([]) == 0
    assert called["ok"]


def test_cli_plugins_lists_loaded_plugins(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main

    pdir = cfg.sub("plugins")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "hello.py").write_text(
        "from aegis.tools.base import Tool, ToolResult\n"
        "class Hello(Tool):\n"
        "    name='hello_plugin'\n"
        "    description='Hello plugin tool.'\n"
        "    parameters={'type':'object','properties':{}}\n"
        "    def run(self,args,ctx): return ToolResult.ok('hi')\n"
        "def register(api): api.register_tool(Hello())\n",
        encoding="utf-8",
    )

    assert main(["plugins"]) == 0
    out = capsys.readouterr().out
    assert "hello.py" in out
    assert "hello_plugin" in out
    assert "errors: none" in out


def test_cli_plugins_doctor_fails_on_load_error(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main

    pdir = cfg.sub("plugins")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "broken.py").write_text("def register(api):\n    raise RuntimeError('boom')\n", encoding="utf-8")

    assert main(["plugins", "doctor"]) == 1
    out = capsys.readouterr().out
    assert "broken.py" in out
    assert "boom" in out


def test_daemon_install_reports_gateway_failure(monkeypatch, capsys):
    from types import SimpleNamespace

    import aegis.daemon as daemon
    from aegis.config import Config

    monkeypatch.setattr(daemon, "install_dashboard_service",
                        lambda *_args, **_kwargs: daemon.ServiceResult(True, "dashboard ok"))
    monkeypatch.setattr(daemon, "install_gateway_service",
                        lambda *_args, **_kwargs: daemon.ServiceResult(False, "gateway failed"))

    cfg = Config.load()
    rc = daemon.cmd_daemon(
        SimpleNamespace(action="install", channels="telegram", no_start=True),
        cfg,
    )

    assert rc == 1
    out = capsys.readouterr().out
    assert "dashboard ok" in out
    assert "gateway failed" in out


def test_daemon_status_handles_missing_systemd(monkeypatch):
    import aegis.daemon as daemon

    monkeypatch.setattr(daemon.shutil, "which", lambda *_args: None)

    st = daemon.status()

    assert st["aegis-dashboard.service"] == "user systemd unavailable"
    assert st["aegis-gateway.service"] == "user systemd unavailable"


def test_daemon_status_includes_failed_unit_hint(monkeypatch):
    import subprocess

    import aegis.daemon as daemon

    monkeypatch.setattr(daemon, "systemd_available", lambda: True)
    monkeypatch.setattr(daemon.shutil, "which", lambda *_: "/usr/bin/systemctl")  # CI may lack it

    def fake_systemctl(*args):
        assert args[0] == "show"
        return subprocess.CompletedProcess(
            ["systemctl", *args],
            0,
            stdout=(
                "LoadState=loaded\n"
                "ActiveState=failed\n"
                "SubState=failed\n"
                "UnitFileState=enabled\n"
                "Result=exit-code\n"
                "ExecMainStatus=1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(daemon, "_systemctl", fake_systemctl)

    st = daemon.status()

    assert "failed" in st["aegis-dashboard.service"]
    assert "journalctl --user -u aegis-dashboard.service" in st["aegis-dashboard.service"]


def test_daemon_status_parses_active_running_unit(monkeypatch):
    import subprocess

    import aegis.daemon as daemon

    monkeypatch.setattr(daemon, "systemd_available", lambda: True)
    monkeypatch.setattr(daemon.shutil, "which", lambda *_: "/usr/bin/systemctl")  # CI may lack it
    monkeypatch.setattr(
        daemon,
        "_systemctl",
        lambda *_args: subprocess.CompletedProcess(
            ["systemctl"],
            0,
            stdout=(
                "Result=success\n"
                "ExecMainStatus=0\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
        ),
    )

    st = daemon.status()

    assert st["aegis-dashboard.service"] == "active (running, enabled)"


def test_dashboard_service_refuses_occupied_port(monkeypatch):
    import aegis.daemon as daemon
    from aegis.config import Config

    monkeypatch.setattr(daemon.shutil, "which", lambda *_args: "/usr/bin/systemctl")
    monkeypatch.setattr(daemon, "port_available", lambda _host, _port: False)

    res = daemon.install_dashboard_service(Config.load())

    assert not res.ok
    assert "already in use" in res.message


def test_github_tool_needs_gh(tmp_path, monkeypatch):
    import aegis.tools.devtools as dt
    monkeypatch.setattr(dt.shutil, "which", lambda *_: None)
    from aegis.tools.base import ToolContext
    res = dt.GithubTool().run({"action": "issues"}, ToolContext(cwd=tmp_path))
    assert res.is_error and "gh CLI" in res.content
