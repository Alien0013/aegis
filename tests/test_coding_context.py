"""Coding posture: the system-prompt workspace block reflects git/project state, stays out
of non-code dirs, and is gated by config."""

from __future__ import annotations

import subprocess

from aegis.agent.coding_context import coding_workspace_block
from aegis.config import Config


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "commit", "--allow-empty", "-qm", "first commit")


def test_git_workspace_block_has_brief_and_snapshot(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.py").write_text("x = 1\n")          # an untracked, dirty file
    block = coding_workspace_block(tmp_path)
    assert "# Coding workspace" in block                 # operating brief
    assert "Repository snapshot" in block
    assert "branch:" in block
    assert "1 changed file" in block and "new.py" in block
    assert "first commit" in block                       # recent commits


def test_clean_repo_reports_clean(tmp_path):
    _init_repo(tmp_path)
    block = coding_workspace_block(tmp_path)
    assert "working tree: clean" in block


def test_non_git_project_uses_marker_layout(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    block = coding_workspace_block(tmp_path)
    assert "no git repository detected" in block
    assert "package.json" in block
    assert "Repository snapshot" not in block


def test_non_code_dir_yields_nothing(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    assert coding_workspace_block(tmp_path) == ""


def test_disabled_by_config(tmp_path):
    _init_repo(tmp_path)
    cfg = Config({"agent": {"coding_context": False}})
    assert coding_workspace_block(tmp_path, cfg) == ""
    # default (flag absent) is on
    assert coding_workspace_block(tmp_path, Config({})) != ""


def test_status_truncation(tmp_path):
    _init_repo(tmp_path)
    for i in range(20):
        (tmp_path / f"f{i}.py").write_text("x\n")
    block = coding_workspace_block(tmp_path)
    assert "20 changed file(s)" in block
    assert "more)" in block                              # the …(+N more) tail
