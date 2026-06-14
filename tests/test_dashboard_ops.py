"""System → Operations panel backend: status snapshot and the curator/backup/memory-reset/
update actions wired at /api/ops."""

from __future__ import annotations

from aegis import dashboard as dash
from aegis.config import Config
from aegis.memory import MemoryStore


def _config():
    return Config.load()


def test_ops_status_shape():
    s = dash._ops_status(_config())
    assert "version" in s
    assert set(s["curator"]) >= {"enabled", "interval_hours", "last_run_at"}
    assert set(s["memory"]) == {"memory", "user"}
    assert "systemd" in s["services"]


def test_curator_pause_and_resume_toggle_config():
    cfg = _config()
    assert dash._ops_action("curator_pause", {}, cfg) == {"ok": True, "enabled": False}
    assert dash._ops_status(cfg)["curator"]["enabled"] is False
    assert dash._ops_action("curator_resume", {}, cfg) == {"ok": True, "enabled": True}
    assert dash._ops_status(cfg)["curator"]["enabled"] is True


def test_memory_reset_backs_up_then_truncates():
    store = MemoryStore()
    store.ensure_files()
    path = store._path("memory")
    path.write_text("- something important\n", encoding="utf-8")

    res = dash._ops_action("memory_reset", {}, _config())
    assert res["ok"] and res["target"] == "memory"
    assert path.read_text() == ""                       # truncated
    from pathlib import Path
    backup = Path(res["backup"])
    assert ".bak-" in backup.name
    assert "something important" in backup.read_text()  # prior content preserved in the backup


def test_memory_reset_on_empty_is_noop():
    res = dash._ops_action("memory_reset", {}, _config())
    assert res["ok"] and res.get("note") == "already empty" and res["backup"] == ""


def test_update_check_reports_version():
    res = dash._ops_action("update_check", {}, _config())
    from aegis import __version__
    assert res["version"] == __version__
    assert "hint" in res and "install" in res


def test_gateway_control_requires_systemd(monkeypatch):
    monkeypatch.setattr("aegis.daemon.systemd_available", lambda: False)
    res = dash._ops_action("gateway", {"op": "restart"}, _config())
    assert res["ok"] is False and "systemd" in res["error"]


def test_gateway_bad_op_rejected():
    res = dash._ops_action("gateway", {"op": "frobnicate"}, _config())
    assert res["ok"] is False and "start" in res["error"]


def test_unknown_action():
    assert "error" in dash._ops_action("nope", {}, _config())
