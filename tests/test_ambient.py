"""Ambient mode: test-command detection, change scanning, and the watch cycle."""

from aegis import ambient
from aegis.config import Config


def test_detect_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert ambient.detect_test_command(tmp_path) == "python -m pytest -q"


def test_detect_npm(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    assert ambient.detect_test_command(tmp_path) == "npm test"


def test_detect_cargo_and_go(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    assert ambient.detect_test_command(tmp_path) == "cargo test"
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "go.mod").write_text("module x\n")
    assert ambient.detect_test_command(tmp_path) == "go test ./..."


def test_config_override_wins(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cfg = Config.load()
    cfg.set("ambient.test_command", "make test")
    assert ambient.detect_test_command(tmp_path, cfg) == "make test"


def test_detect_none(tmp_path):
    assert ambient.detect_test_command(tmp_path) == ""


def test_scan_finds_source_ignores_junk(tmp_path):
    (tmp_path / "app.py").write_text("x=1")
    (tmp_path / "style.css").write_text("a{}")
    (tmp_path / "README.md").write_text("docs")          # not a source ext
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("y")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.py").write_text("z")
    snap = ambient.scan(tmp_path)
    assert "app.py" in snap and "style.css" in snap
    assert "README.md" not in snap
    assert not any("node_modules" in k or "__pycache__" in k for k in snap)


def test_changed_detects_new_and_modified():
    prev = {"a.py": 100.0, "b.py": 100.0}
    curr = {"a.py": 100.0, "b.py": 200.0, "c.py": 50.0}     # b modified, c new
    assert ambient.changed(prev, curr) == ["b.py", "c.py"]


def test_watch_once_runs_tests_on_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("x=1")
    events = []
    # baseline={} → every current file counts as changed → tests run once.
    result = ambient.watch(
        tmp_path, once=True, baseline={}, on_event=events.append,
        runner=lambda cmd, root: (False, "1 failed"),
    )
    assert result["ran"] is True and result["ok"] is False
    kinds = [e["type"] for e in events]
    assert "start" in kinds and "change" in kinds and "tests" in kinds
    tests_ev = [e for e in events if e["type"] == "tests"][0]
    assert tests_ev["ok"] is False and "1 failed" in tests_ev["output"]


def test_watch_no_change_does_not_run(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("x=1")
    snap = ambient.scan(tmp_path)
    ran = {"v": False}

    def runner(cmd, root):
        ran["v"] = True
        return True, ""

    result = ambient.watch(tmp_path, once=True, baseline=snap, runner=runner)
    assert ran["v"] is False and result["ran"] is False     # nothing changed since baseline


def test_watch_no_test_command(tmp_path):
    result = ambient.watch(tmp_path, once=True, baseline={})
    assert result["ok"] is False and result["reason"] == "no test command"
