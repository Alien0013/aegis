from __future__ import annotations


class _RunResult:
    returncode = 0
    stdout = "Already up to date.\n"
    stderr = ""


def _write_manifest(plugin_dir, *, name="demo"):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        f"name: {name}\ndescription: Demo plugin\nentrypoint: __init__.py\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("def register(api):\n    return None\n", encoding="utf-8")


def test_plugins_update_pulls_git_checkout(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import config as cfg
    from aegis import plugins
    from aegis.cli.main import main

    plugin_dir = cfg.sub("plugins", "demo")
    _write_manifest(plugin_dir)
    (plugin_dir / ".git").mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _RunResult()

    monkeypatch.setattr(plugins.subprocess, "run", fake_run)

    assert main(["plugins", "update", "demo"]) == 0
    out = capsys.readouterr().out

    assert "already up to date" in out.lower()
    assert calls
    assert calls[0][0][:4] == ["git", "-C", str(plugin_dir), "pull"]


def test_plugins_update_rejects_non_git_plugin(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import config as cfg
    from aegis.cli.main import main

    _write_manifest(cfg.sub("plugins", "demo"))

    assert main(["plugins", "update", "demo"]) == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not installed from git" in combined.lower()
