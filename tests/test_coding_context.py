"""Coding posture: the system-prompt workspace block reflects git/project state, stays out
of non-code dirs, and is gated by config."""

from __future__ import annotations

import subprocess
from typing import Any, cast

from aegis.agent.coding_context import coding_workspace_block, subdirectory_rule_hint
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


def test_subdirectory_rule_hint_loads_nested_rules_once(tmp_path):
    (tmp_path / "AGENTS.md").write_text("ROOT RULES\n")
    sub = tmp_path / "packages" / "foo"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("SUBPKG RULES\n")
    seen: set[str] = set()

    hint = subdirectory_rule_hint(tmp_path, sub / "module.py", Config.load(), seen=seen)

    assert "Additional directory rules" in hint
    assert "SUBPKG RULES" in hint
    assert "ROOT RULES" not in hint
    assert subdirectory_rule_hint(tmp_path, sub / "other.py", Config.load(), seen=seen) == ""


class _SubdirRulesProvider:
    name = "fake"
    model = "fake-model"
    context_length = 200_000

    def __init__(self):
        self.calls = 0
        self.seen_messages = []

    def complete(self, messages, tools=None, stream=False, on_delta=None, model=None,
                 max_tokens=None, reasoning="off"):
        from aegis.types import LLMResponse, ToolCall

        self.calls += 1
        self.seen_messages.append(list(messages))
        if self.calls == 1:
            return LLMResponse(
                text="checking package",
                tool_calls=[ToolCall("c1", "list_dir", {"path": "packages/foo"})],
            )
        return LLMResponse(text="done.")


def test_agent_tool_result_injects_subdirectory_rules(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    sub = tmp_path / "packages" / "foo"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("SUBPKG RULES: prefer the foo package style.\n")
    (sub / "module.py").write_text("x = 1\n")

    provider = _SubdirRulesProvider()
    agent = Agent(config=Config.load(), provider=cast(Any, provider), session=Session.create(), cwd=tmp_path)

    assert agent.run("inspect packages/foo").content == "done."
    tool_messages = [m for m in provider.seen_messages[-1] if m.role == "tool"]
    assert tool_messages
    assert "Additional directory rules" in tool_messages[-1].content
    assert "SUBPKG RULES" in tool_messages[-1].content
