from __future__ import annotations

import shutil

import pytest

from aegis.checkpoints import CheckpointStore


def _write_legacy_archive(root, name: str, payload: bytes):
    archive = root / name
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "payload.bin").write_bytes(payload)
    return archive


def test_stage_l_checkpoint_status_and_list_show_store_workdir_metadata(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    cp_id = CheckpointStore(project).snapshot([str(file_path)], label="stage-l metadata")
    assert cp_id is not None

    assert main(["checkpoints", "status", "--limit", "5"]) == 0
    out = capsys.readouterr().out

    assert f"checkpoint base: {root}" in out
    assert f"git store: {root / 'store'}" in out
    assert "checkpoints: 1" in out
    assert "project workdirs:" in out
    assert "workdir" in out
    assert "last touch" in out
    assert "stage-l-project" in out
    assert "live" in out

    assert main(["checkpoints", "list", "--limit", "5"]) == 0
    out = capsys.readouterr().out

    assert f"git store: {root / 'store'}" in out
    assert "workdir" in out
    assert "files" in out
    assert "git" in out
    assert cp_id[:12] in out
    assert "  1  " in out
    assert "stage-l-project" in out
    assert "stage-l metadata" in out


def test_stage_l_checkpoint_prune_prints_settings_and_counts(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-prune-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    assert CheckpointStore(project).snapshot([str(file_path)], label="stage-l prune")

    assert main([
        "checkpoints",
        "prune",
        "--older-than-days",
        "30",
        "--keep-orphans",
        "--keep",
        "0",
        "--max-size-mb",
        "25",
    ]) == 0
    out = capsys.readouterr().out

    assert "pruning checkpoint store" in out
    assert f"checkpoint base: {root}" in out
    assert f"git store: {root / 'store'}" in out
    assert "older_than_days: 30.0" in out
    assert "delete_orphans: False" in out
    assert "keep: 0" in out
    assert "max_size_mb: 25.0" in out
    assert "scanned: 1" in out
    assert "deleted over-limit: 1" in out
    assert "bytes reclaimed:" in out


def test_stage_l_checkpoint_rollback_file_prints_checkpoint_workdir_and_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-rollback-project"
    project.mkdir()
    a = project / "a.txt"
    b = project / "b.txt"
    a.write_text("a1\n", encoding="utf-8")
    b.write_text("b1\n", encoding="utf-8")
    cp_id = CheckpointStore(project).snapshot([str(a), str(b)], label="stage-l rollback")
    assert cp_id is not None
    a.write_text("a2\n", encoding="utf-8")
    b.write_text("b2\n", encoding="utf-8")
    monkeypatch.chdir(project)

    assert main(["checkpoints", "rollback", cp_id, "--file", "a.txt"]) == 0
    out = capsys.readouterr().out

    assert "rolled back 1 file(s)" in out
    assert f"checkpoint: {cp_id}" in out
    assert f"workdir: {project.resolve()}" in out
    assert "file: a.txt" in out
    assert a.read_text(encoding="utf-8") == "a1\n"
    assert b.read_text(encoding="utf-8") == "b2\n"


def test_stage_l_checkpoint_status_reports_legacy_archives(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-legacy-status-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    assert CheckpointStore(project).snapshot([str(file_path)], label="legacy status")
    _write_legacy_archive(root, "legacy-20260101000000", b"x" * 128)

    info = checkpoints.store_status(root)
    assert info["legacy_size_bytes"] >= 128
    assert any(archive["name"] == "legacy-20260101000000" for archive in info["legacy_archives"])

    assert main(["checkpoints", "status", "--limit", "5"]) == 0
    out = capsys.readouterr().out

    assert "legacy" in out.lower()
    assert "legacy-20260101000000" in out
    assert "clear-legacy" in out


def test_stage_l_checkpoint_clear_legacy_preserves_store_and_checkpoints(
    tmp_path,
    monkeypatch,
    capsys,
):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-clear-legacy-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    cp_id = CheckpointStore(project).snapshot([str(file_path)], label="clear legacy")
    assert cp_id is not None
    _write_legacy_archive(root, "legacy-20260101000000", b"a" * 64)
    _write_legacy_archive(root, "legacy-20260202000000", b"b" * 64)
    preserved_dir = root / "not-legacy"
    preserved_dir.mkdir()
    preserved_file = root / "legacy-note.txt"
    preserved_file.write_text("not an archive\n", encoding="utf-8")

    assert main(["checkpoints", "clear-legacy"]) == 0
    out = capsys.readouterr().out.lower()

    assert "deleted 2" in out
    assert "reclaimed" in out
    assert not (root / "legacy-20260101000000").exists()
    assert not (root / "legacy-20260202000000").exists()
    assert (root / cp_id / "manifest.json").exists()
    assert (root / "store" / "HEAD").exists()
    assert preserved_dir.exists()
    assert preserved_file.exists()


def test_stage_l_checkpoint_clear_reports_reclaimed_bytes(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-clear-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    assert CheckpointStore(project).snapshot([str(file_path)], label="clear all")
    assert checkpoints.store_status(root)["total_size_bytes"] > 0

    assert main(["checkpoints", "clear"]) == 0
    out = capsys.readouterr().out.lower()

    assert "reclaimed" in out
    assert any(unit in out for unit in (" b", " kb", " mb", " gb", " tb"))
    assert not root.exists()


def test_stage_l_checkpoint_list_shows_commit_history_metadata(tmp_path, monkeypatch, capsys):
    if shutil.which("git") is None:
        pytest.skip("git is required for checkpoint commit metadata")

    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-history-project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n", encoding="utf-8")
    first = CheckpointStore(project).snapshot([str(file_path)], label="stage-l history first")
    file_path.write_text("two\n", encoding="utf-8")
    second = CheckpointStore(project).snapshot([str(file_path)], label="stage-l history second")
    assert first is not None
    assert second is not None
    by_id = {cp.id: cp for cp in CheckpointStore(project).list()}
    assert by_id[first].git_commit
    assert by_id[second].git_commit

    assert main(["checkpoints", "list", "--limit", "5"]) == 0
    out = capsys.readouterr().out

    assert "files" in out
    assert "git" in out
    assert "workdir" in out
    assert project.name in out
    for cp_id, label in (
        (first, "stage-l history first"),
        (second, "stage-l history second"),
    ):
        cp = by_id[cp_id]
        assert cp_id in out
        assert cp.created_at[:10] in out
        assert cp.git_commit[:8] in out
        assert label in out


def test_stage_l_checkpoint_status_and_clear_legacy_preserve_store(
    tmp_path,
    monkeypatch,
    capsys,
):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-legacy-project"
    project.mkdir()
    tracked = project / "tracked.py"
    tracked.write_text("one\n", encoding="utf-8")
    cp_id = CheckpointStore(project).snapshot([str(tracked)], label="stage-l legacy")
    assert cp_id is not None
    legacy = root / "legacy-20200101-000000"
    legacy.mkdir(parents=True)
    (legacy / "old-shadow").write_text("legacy bytes\n", encoding="utf-8")

    assert main(["checkpoints", "status"]) == 0
    out = capsys.readouterr().out
    assert "legacy archives: 1" in out
    assert "legacy-20200101-000000" in out
    assert "clear with: aegis checkpoints clear-legacy" in out

    assert main(["checkpoints", "clear-legacy"]) == 0
    out = capsys.readouterr().out
    assert "deleted 1 legacy archive(s)" in out
    assert not legacy.exists()
    assert (root / cp_id / "manifest.json").exists()
    assert checkpoints.store_status(root)["checkpoint_count"] == 1


def test_stage_l_checkpoint_clear_reports_reclaimed_history(
    tmp_path,
    monkeypatch,
    capsys,
):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "stage-l-clear-project"
    project.mkdir()
    tracked = project / "tracked.py"
    tracked.write_text("one\n", encoding="utf-8")
    assert CheckpointStore(project).snapshot([str(tracked)], label="stage-l clear")

    assert main(["checkpoints", "clear"]) == 0
    out = capsys.readouterr().out
    assert "cleared checkpoint base:" in out
    assert "1 checkpoint(s)" in out
    assert "reclaimed" in out
    assert not root.exists()
