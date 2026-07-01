"""Stage L checkpoint policy parity with Hermes guardrails."""

from __future__ import annotations

import json

from aegis.checkpoints import CheckpointStore


def test_checkpoint_workdir_uses_project_root_from_nested_cwd(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    file_path = src / "app.py"
    file_path.write_text("one\n", encoding="utf-8")

    store = CheckpointStore(src)
    cp = store.snapshot(["app.py"], label="nested cwd")

    assert cp is not None
    manifest = json.loads((root / cp / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workdir"] == str(project.resolve())

    file_path.write_text("two\n", encoding="utf-8")
    restore_result = store.restore(cp, file_path="app.py")
    assert restore_result["success"] is True
    assert file_path.read_text(encoding="utf-8") == "one\n"


def test_snapshot_skips_when_file_count_exceeds_cap(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    files = []
    for idx in range(3):
        path = project / f"f{idx}.txt"
        path.write_text(f"{idx}\n", encoding="utf-8")
        files.append(str(path))

    cp = CheckpointStore(project, max_snapshot_files=2).snapshot(files, label="too many")

    assert cp is None
    assert not list(root.glob("cp_*"))


def test_snapshot_skips_files_larger_than_size_cap(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    small = project / "small.py"
    large = project / "large.bin"
    small.write_text("tiny\n", encoding="utf-8")
    large.write_bytes(b"x" * (2 * 1024 * 1024))

    cp = CheckpointStore(project, max_file_size_mb=1).snapshot(
        [str(small), str(large)],
        label="size cap",
    )

    assert cp is not None
    manifest = json.loads((root / cp / "manifest.json").read_text(encoding="utf-8"))
    assert str(small.resolve()) in manifest["files"]
    assert str(large.resolve()) not in manifest["files"]


def test_prune_checkpoints_enforces_total_size_cap_but_keeps_newest_project_checkpoint(
    tmp_path,
    monkeypatch,
):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    monkeypatch.setattr(checkpoints, "_write_git_shadow", lambda _path, _original: "")
    ids = iter(["cp_001", "cp_002"])
    monkeypatch.setattr(checkpoints, "new_id", lambda _prefix="cp": next(ids))
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "payload.bin"
    store = CheckpointStore(project)

    file_path.write_bytes(b"a" * 4096)
    first = store.snapshot([str(file_path)], label="first")
    file_path.write_bytes(b"b" * 4096)
    second = store.snapshot([str(file_path)], label="second")
    assert first == "cp_001"
    assert second == "cp_002"

    result = checkpoints.prune_checkpoints(root=root, max_total_size_mb=0.006)

    assert result["deleted_over_limit"] == 1
    assert not (root / first).exists()
    assert (root / second).exists()
    assert checkpoints.store_status(root)["checkpoint_count"] == 1


def test_checkpoint_history_reports_recent_first_metadata(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    ids = iter(["cp_001", "cp_002"])
    monkeypatch.setattr(checkpoints, "new_id", lambda _prefix="cp": next(ids))
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "tracked.py"
    store = CheckpointStore(project)

    file_path.write_text("one\n", encoding="utf-8")
    first = store.snapshot([str(file_path)], label="first")
    file_path.write_text("two\n", encoding="utf-8")
    second = store.snapshot([str(file_path)], label="second")

    rows = store.history(limit=2, workdir=project)

    assert [row["id"] for row in rows] == [second, first]
    assert rows[0]["short_id"] == "cp_002"
    assert rows[0]["reason"] == "second"
    assert rows[0]["files_changed"] == 1
    assert rows[0]["workdir"] == str(project.resolve())
