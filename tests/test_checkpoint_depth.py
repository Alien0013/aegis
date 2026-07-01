"""Checkpoint depth: per-turn edit batches, new-file tracking, diff preview."""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from aegis.checkpoints import CheckpointStore
from aegis.types import ToolCall


def test_batch_keeps_pre_turn_state_and_diffs(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one\n")
    store = CheckpointStore(tmp_path)
    cp = store.snapshot([str(f)], label="turn edits")
    f.write_text("two\n")
    store.add_to(cp, [str(f)])              # second edit in the same batch
    f.write_text("three\n")
    # shadow must still hold the PRE-BATCH content, not the intermediate state
    d = store.diff(cp)
    assert "-one" in d and "+three" in d and "two" not in d
    restored = store.rollback(cp)
    assert restored and f.read_text() == "one\n"


def test_new_file_recorded_and_removed_on_rollback(tmp_path):
    new = tmp_path / "made.txt"
    store = CheckpointStore(tmp_path)
    cp = store.snapshot([str(new)], label="new file")
    assert cp is not None                    # new files still open a checkpoint
    new.write_text("created\n")
    assert "(new file)" in store.diff(cp) and "+created" in store.diff(cp)
    restored = store.rollback(cp)
    assert any("removed" in r for r in restored) and not new.exists()


def test_single_file_restore_keeps_other_checkpoint_files_changed(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a1\n")
    b.write_text("b1\n")
    store = CheckpointStore(tmp_path)
    cp = store.snapshot([str(a), str(b)], label="multi-file edit")
    assert cp is not None
    a.write_text("a2\n")
    b.write_text("b2\n")

    result = store.restore(cp, file_path="a.txt")

    assert result["success"] is True
    assert result["restored"] == [str(a)]
    assert result["file"] == "a.txt"
    assert a.read_text() == "a1\n"
    assert b.read_text() == "b2\n"


def test_single_file_restore_removes_checkpointed_new_file(tmp_path):
    made = tmp_path / "made.txt"
    store = CheckpointStore(tmp_path)
    cp = store.snapshot([str(made)], label="new file")
    assert cp is not None
    made.write_text("created\n")

    result = store.restore(cp, file_path="made.txt")

    assert result["success"] is True
    assert any("removed" in item for item in result["restored"])
    assert not made.exists()


def test_single_file_restore_rejects_absolute_and_traversal_paths(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("before\n")
    store = CheckpointStore(tmp_path)
    cp = store.snapshot([str(f)], label="before edit")
    assert cp is not None
    f.write_text("after\n")

    absolute = store.restore(cp, file_path="/etc/passwd")
    traversal = store.restore(cp, file_path="../outside.txt")

    assert absolute["success"] is False
    assert "absolute path" in absolute["error"]
    assert traversal["success"] is False
    assert "escapes the working directory" in traversal["error"]
    assert f.read_text() == "after\n"


def test_checkpoint_uses_shared_git_blob_store_and_dedupes_content(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git is required for shared checkpoint object-store coverage")
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    file_a = project_a / "same.txt"
    file_b = project_b / "same.txt"
    file_a.write_text("shared bytes\n")
    file_b.write_text("shared bytes\n")

    cp_a = CheckpointStore(project_a).snapshot([str(file_a)], label="a")
    cp_b = CheckpointStore(project_b).snapshot([str(file_b)], label="b")
    assert cp_a and cp_b

    manifest_a = json.loads((root / cp_a / "manifest.json").read_text(encoding="utf-8"))
    manifest_b = json.loads((root / cp_b / "manifest.json").read_text(encoding="utf-8"))
    shadow_a = manifest_a["files"][str(file_a)]
    shadow_b = manifest_b["files"][str(file_b)]
    assert shadow_a.startswith("git:")
    assert shadow_b.startswith("git:")
    assert shadow_a.split(":", 2)[1] == shadow_b.split(":", 2)[1]
    assert manifest_a["git_commit"]
    assert manifest_b["git_commit"]
    assert (root / "store" / "HEAD").exists()
    assert checkpoints.store_status(root)["git_store"]["exists"] is True
    assert list((root / cp_a).iterdir()) == [root / cp_a / "manifest.json"]

    file_a.write_text("changed\n")
    restored = CheckpointStore(project_a).rollback(cp_a)
    assert restored == [str(file_a)]
    assert file_a.read_text() == "shared bytes\n"


def test_checkpoint_keep_prune_removes_git_refs(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git is required for shared checkpoint object-store coverage")
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    ids = iter(f"cp_{idx:03d}" for idx in range(41))
    monkeypatch.setattr(checkpoints, "new_id", lambda _prefix="cp": next(ids))
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "tracked.txt"
    store = CheckpointStore(project)

    for idx in range(41):
        file_path.write_text(f"value {idx}\n")
        assert store.snapshot([str(file_path)], label=f"checkpoint {idx}")

    assert not (root / "cp_000").exists()
    assert checkpoints._run_git_store(
        ["show-ref", "--verify", checkpoints._git_ref("cp_000")],
        root=root,
        allowed_returncodes={1},
    )[0] is False
    assert (root / "cp_040").exists()
    assert checkpoints._run_git_store(
        ["show-ref", "--verify", checkpoints._git_ref("cp_040")],
        root=root,
        allowed_returncodes={1},
    )[0] is True


def test_executor_batches_edits_into_one_checkpoint(tmp_path):
    from aegis.agent.loop import ToolExecutor
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    a, b = tmp_path / "x.py", tmp_path / "y.py"
    a.write_text("ax\n")
    b.write_text("bx\n")
    ex = ToolExecutor(None, None, ToolContext(cwd=tmp_path, config=Config.load()), lambda e: None)
    before = len(CheckpointStore(tmp_path).list())
    ex._maybe_checkpoint(ToolCall("1", "write_file", {"path": str(a)}))
    ex._maybe_checkpoint(ToolCall("2", "edit_file", {"path": str(b)}))
    cps = CheckpointStore(tmp_path).list()
    assert len(cps) == before + 1            # one batch, not two checkpoints
    assert len(cps[0].files) == 2


def test_apply_patch_paths_extracted():
    from aegis.agent.loop import ToolExecutor
    patch = "--- a/src/m.py\n+++ b/src/m.py\n@@\n--- a/other\n+++ b/new_dir/n.py\n@@\n"
    paths = ToolExecutor._edit_paths(ToolCall("1", "apply_patch", {"patch": patch}))
    assert paths == ["src/m.py", "other", "new_dir/n.py"]


def test_checkpoint_status_groups_project_metadata(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n")

    store = CheckpointStore(project)
    cp = store.snapshot([str(file_path)], label="before edit")
    assert cp is not None

    manifest = json.loads((root / cp / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workdir"] == str(project.resolve())

    info = checkpoints.store_status(root)
    assert info["checkpoint_count"] == 1
    assert info["project_count"] == 1
    assert info["projects"][0]["workdir"] == str(project.resolve())
    assert info["projects"][0]["exists"] is True
    assert info["projects"][0]["checkpoints"] == 1


def test_checkpoint_prune_removes_orphan_stale_and_over_limit(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    live = tmp_path / "live"
    live.mkdir()
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    live_file = live / "a.txt"
    orphan_file = orphan / "b.txt"
    live_file.write_text("live\n")
    orphan_file.write_text("orphan\n")

    live_store = CheckpointStore(live)
    first = live_store.snapshot([str(live_file)], label="live-1")
    live_file.write_text("live2\n")
    second = live_store.snapshot([str(live_file)], label="live-2")
    orphan_cp = CheckpointStore(orphan).snapshot([str(orphan_file)], label="orphan")
    assert first and second and orphan_cp

    stale_dir = root / first
    old = time.time() - 60 * 86400
    for path in stale_dir.rglob("*"):
        os.utime(path, (old, old))
    os.utime(stale_dir, (old, old))
    orphan_file.unlink()
    orphan.rmdir()

    result = checkpoints.prune_checkpoints(
        older_than_days=30,
        delete_orphans=True,
        keep=1,
        root=root,
    )

    assert result["scanned"] == 3
    assert result["deleted_orphan"] == 1
    assert result["deleted_stale"] == 1
    assert result["deleted_over_limit"] == 0
    assert not (root / first).exists()
    assert not (root / orphan_cp).exists()
    assert (root / second).exists()
    if (root / "store" / "HEAD").exists():
        assert checkpoints._run_git_store(
            ["show-ref", "--verify", checkpoints._git_ref(first)],
            root=root,
            allowed_returncodes={1},
        )[0] is False
        assert checkpoints._run_git_store(
            ["show-ref", "--verify", checkpoints._git_ref(second)],
            root=root,
            allowed_returncodes={1},
        )[0] is True


def test_checkpoint_auto_prune_marker_skips_repeat(tmp_path, monkeypatch):
    import aegis.checkpoints as checkpoints

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n")
    assert CheckpointStore(project).snapshot([str(file_path)], label="first")

    first = checkpoints.maybe_auto_prune_checkpoints(root=root)
    second = checkpoints.maybe_auto_prune_checkpoints(root=root)

    assert first["skipped"] is False
    assert second["skipped"] is True
    assert (root / ".last_prune").exists()


def test_checkpoints_cli_status_and_prune(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("one\n")
    assert CheckpointStore(project).snapshot([str(file_path)], label="first")

    assert main(["checkpoints", "status"]) == 0
    out = capsys.readouterr().out
    assert "checkpoint base:" in out
    assert "git store:" in out
    assert "checkpoints: 1" in out

    assert main(["checkpoints", "prune", "--keep", "0"]) == 0
    out = capsys.readouterr().out
    assert "over-limit" in out
    assert checkpoints.store_status(root)["checkpoint_count"] == 0


def test_checkpoints_cli_rollback_file_restores_one_relative_path(tmp_path, monkeypatch, capsys):
    import aegis.checkpoints as checkpoints
    from aegis.cli.main import main

    root = tmp_path / "checkpoint-root"
    monkeypatch.setattr(checkpoints, "_root", lambda: root)
    project = tmp_path / "project"
    project.mkdir()
    a = project / "a.txt"
    b = project / "b.txt"
    a.write_text("a1\n")
    b.write_text("b1\n")
    cp = CheckpointStore(project).snapshot([str(a), str(b)], label="multi-file")
    assert cp is not None
    a.write_text("a2\n")
    b.write_text("b2\n")
    monkeypatch.chdir(project)

    assert main(["checkpoints", "rollback", cp, "--file", "a.txt"]) == 0

    out = capsys.readouterr().out
    assert "rolled back 1 file(s)" in out
    assert a.read_text() == "a1\n"
    assert b.read_text() == "b2\n"
