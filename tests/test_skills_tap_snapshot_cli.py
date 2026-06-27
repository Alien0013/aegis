from __future__ import annotations

import json


def test_skills_tap_add_list_and_remove(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    assert main(["skills", "tap", "add", "acme/skills"] ) == 0
    out = capsys.readouterr().out
    assert "added tap acme" in out.lower()
    assert Config.load().get("skills.taps", {})["acme"] == "acme/skills"

    assert main(["skills", "tap", "list"] ) == 0
    out = capsys.readouterr().out
    assert "acme" in out
    assert "acme/skills" in out

    assert main(["skills", "tap", "remove", "acme"] ) == 0
    out = capsys.readouterr().out
    assert "removed tap acme" in out.lower()
    assert "acme" not in Config.load().get("skills.taps", {})


def test_skills_snapshot_export_and_import_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.set("skills.include_bundled", False)
    cfg.set("skills.taps", {"acme": "acme/skills"})
    snapshot = tmp_path / "skills-snapshot.json"

    assert main(["skills", "snapshot", "export", str(snapshot)]) == 0
    out = capsys.readouterr().out
    assert "exported skills snapshot" in out.lower()
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert data["config"]["include_bundled"] is False
    assert data["config"]["taps"] == {"acme": "acme/skills"}

    Config.load().set("skills.taps", {})
    assert main(["skills", "snapshot", "import", str(snapshot)]) == 0
    out = capsys.readouterr().out
    assert "imported skills snapshot" in out.lower()
    reloaded = Config.load()
    assert reloaded.get("skills.include_bundled") is False
    assert reloaded.get("skills.taps", {}) == {"acme": "acme/skills"}
