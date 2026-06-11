"""Checkpoint depth: per-turn edit batches, new-file tracking, diff preview."""

from __future__ import annotations

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
    assert paths == ["src/m.py", "new_dir/n.py"]
