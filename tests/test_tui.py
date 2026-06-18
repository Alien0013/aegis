"""Terminal cockpit command."""

from __future__ import annotations

import io

from rich.console import Console


def test_cli_parser_accepts_tui():
    from aegis.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["tui", "--once", "--no-color"])

    assert args.command == "tui"
    assert args.once is True
    assert args.no_color is True


def test_tui_renders_dashboard_sections(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.config import Config
    from aegis.cli.tui import render_dashboard

    cfg.set_profile(None)
    config = Config.load()
    config.set("model.provider", "fake")
    config.set("model.default", "fake-model")
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120, no_color=True)

    snapshot = render_dashboard(config, console=console)
    output = buffer.getvalue()

    assert "AEGIS Terminal Cockpit" in output
    assert "Model" in output
    assert "Sessions" in output
    assert "Runs" in output
    assert "Cron" in output
    assert "Kanban" in output
    assert "Integrity" in output
    assert "e config" in output
    assert "s secrets" in output
    assert snapshot["dashboard_url"].startswith("http://")
    assert snapshot["cross_session"]["object"] == "hermes.cross_session_integrity_report"


def test_tui_surfaces_cross_session_integrity_issues(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from datetime import datetime, timedelta, timezone

    from aegis import config as cfg
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.cli.tui import render_dashboard

    cfg.set_profile(None)
    config = Config.load()
    config.data.setdefault("server", {})["stale_run_health_seconds"] = 0
    store = SessionStore()
    runs = RunStore()
    session = Session(id="tui-cross-session", title="tui cross session")
    store.save(session)
    run = runs.start(surface="tui", kind="chat", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    runs.write(run)
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120, no_color=True)

    snapshot = render_dashboard(config, console=console)
    output = buffer.getvalue()

    assert snapshot["cross_session"]["ok"] is False
    assert "Integrity" in output
    assert "degraded" in output
    assert "stale_running_run" in output


def test_tui_redacts_dashboard_token_in_terminal_output(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.config import Config
    from aegis.cli.tui import render_dashboard

    cfg.set_profile(None)
    config = Config.load()
    config.data.setdefault("server", {})["dashboard_token"] = "plain-tui-token"
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120, no_color=True)

    snapshot = render_dashboard(config, console=console)
    output = buffer.getvalue()

    assert "?token=[REDACTED]" in output
    assert snapshot["dashboard_url"].endswith("?token=[REDACTED]")
    assert "plain-tui-token" not in output
    assert "plain-tui-token" not in snapshot["dashboard_url"]


def test_tui_config_actions_delegate_to_safe_editor(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.config import Config
    from aegis.cli import tui

    cfg.set_profile(None)
    config = Config.load()
    calls = []

    def fake_edit(_config, *, secrets=False):
        calls.append(secrets)
        return 0

    monkeypatch.setattr(tui, "_edit_config", fake_edit)
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100, no_color=True)

    assert tui._handle_choice("e", config, console) is None
    assert tui._handle_choice("config", config, console) is None
    assert tui._handle_choice("s", config, console) is None
    assert tui._handle_choice("q", config, console) == 0
    assert calls == [False, False, True]
