from __future__ import annotations


def _write_skill(skill_dir, *, name="demo-skill", description="Demo skill"):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_skills_inspect_aliases_preview_for_local_skill(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    skill_dir = tmp_path / "demo-skill"
    _write_skill(skill_dir)

    assert main(["skills", "inspect", str(skill_dir)]) == 0
    out = capsys.readouterr().out

    assert "preview:" in out
    assert "demo-skill" in out
    assert "installable" in out


def test_skills_browse_lists_marketplace_results(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import marketplace
    from aegis.cli.main import main

    monkeypatch.setattr(
        marketplace,
        "search",
        lambda query: [
            {"name": "alpha", "description": "Alpha skill", "source": "git:owner/repo/alpha"},
            {"name": "beta", "description": "Beta skill", "source": "git:owner/repo/beta"},
        ],
    )

    assert main(["skills", "browse"]) == 0
    out = capsys.readouterr().out

    assert "alpha" in out
    assert "beta" in out
    assert "git:owner/repo/alpha" in out


def test_skills_check_and_update_tracked_local_skill(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import config as cfg
    from aegis.cli.main import main

    source = tmp_path / "source-skill"
    _write_skill(source, name="demo-skill", description="Version one")

    assert main(["skills", "install", str(source)]) == 0
    capsys.readouterr()

    _write_skill(source, name="demo-skill", description="Version two")

    assert main(["skills", "check"]) == 0
    check_out = capsys.readouterr().out
    assert "demo-skill" in check_out
    assert "outdated" in check_out.lower()

    assert main(["skills", "update", "demo-skill"]) == 0
    update_out = capsys.readouterr().out
    assert "updated demo-skill" in update_out.lower()
    installed = (cfg.skills_dir() / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "Version two" in installed


def test_skills_audit_reports_installed_skill_health(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    source = tmp_path / "source-skill"
    _write_skill(source, name="demo-skill", description="Demo skill")

    assert main(["skills", "install", str(source)]) == 0
    capsys.readouterr()

    assert main(["skills", "audit"]) == 0
    out = capsys.readouterr().out
    assert "demo-skill" in out
    assert "ok" in out.lower()
