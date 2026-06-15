from __future__ import annotations


def test_profile_create_use_show_export_import(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.cli.main import main

    cfg.set_profile(None)

    assert main(["profile", "create", "builder"]) == 0
    home = tmp_path / "profiles" / "builder"
    assert (home / "config.yaml").exists()
    assert (home / "memories" / "MEMORY.md").exists()
    assert (home / "memories" / "USER.md").exists()

    assert main(["profile", "use", "builder"]) == 0
    assert cfg.current_profile() == "builder"
    assert cfg.get_home() == home

    assert main(["profile", "show"]) == 0
    assert "Profile:  builder" in capsys.readouterr().out

    archive = tmp_path / "builder.tar.gz"
    assert main(["profile", "export", "builder", "--out", str(archive)]) == 0
    assert archive.exists()

    assert main(["profile", "import", str(archive), "--name", "builder_copy"]) == 0
    assert (tmp_path / "profiles" / "builder_copy" / "SOUL.md").exists()

    assert main(["profiles"]) == 0
    listed = capsys.readouterr().out
    assert "builder" in listed
    assert "builder_copy" in listed


def test_profile_clone_from_default_copies_core_files(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.cli.main import main
    from aegis.config import Config

    cfg.set_profile(None)
    c = Config.load(profile="default")
    c.set("model.default", "test-model")
    (tmp_path / "memories").mkdir(exist_ok=True)
    (tmp_path / "memories" / "MEMORY.md").write_text("project fact\n", encoding="utf-8")
    (tmp_path / "skills" / "s1").mkdir(parents=True)
    (tmp_path / "skills" / "s1" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

    assert main(["profile", "create", "worker", "--clone-from", "default"]) == 0

    worker = tmp_path / "profiles" / "worker"
    assert "test-model" in (worker / "config.yaml").read_text(encoding="utf-8")
    assert "project fact" in (worker / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert (worker / "skills" / "s1" / "SKILL.md").exists()
