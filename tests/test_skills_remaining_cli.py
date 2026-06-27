from __future__ import annotations

import json
import zipfile


def _write_skill(skill_dir, *, name="demo-skill", description="Demo skill", body="original"):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )


def test_skills_list_modified_diff_and_reset(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import config as cfg
    from aegis.cli.main import main

    source = tmp_path / "source-skill"
    _write_skill(source, body="source body")

    assert main(["skills", "install", str(source)]) == 0
    capsys.readouterr()

    installed = cfg.skills_dir() / "demo-skill" / "SKILL.md"
    installed.write_text(installed.read_text(encoding="utf-8").replace("source body", "local edit"), encoding="utf-8")

    assert main(["skills", "list-modified"]) == 0
    out = capsys.readouterr().out
    assert "demo-skill" in out

    assert main(["skills", "diff", "demo-skill"]) == 0
    out = capsys.readouterr().out
    assert "-source body" in out
    assert "+local edit" in out

    assert main(["skills", "reset", "demo-skill"]) == 0
    out = capsys.readouterr().out
    assert "reset demo-skill baseline" in out.lower()

    assert main(["skills", "list-modified"]) == 0
    out = capsys.readouterr().out
    assert "no modified skills" in out.lower()


def test_skills_repair_official_restores_bundled_discovery(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    Config.load().set("skills.include_bundled", False)

    assert main(["skills", "repair-official"]) == 0
    out = capsys.readouterr().out
    assert "official skill discovery repaired" in out.lower()
    assert Config.load().get("skills.include_bundled") is True


def test_skills_publish_prepares_local_zip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    skill_dir = tmp_path / "demo-skill"
    _write_skill(skill_dir, body="publish me")
    out_zip = tmp_path / "published.zip"

    assert main(["skills", "publish", str(skill_dir), "--to", "local", "--repo", str(out_zip)]) == 0
    out = capsys.readouterr().out
    assert "prepared skill package" in out.lower()
    assert out_zip.exists()
    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
    assert "demo-skill/SKILL.md" in names

    manifest = json.loads((tmp_path / "published.zip.manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "demo-skill"
