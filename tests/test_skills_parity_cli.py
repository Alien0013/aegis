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
