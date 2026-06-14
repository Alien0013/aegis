"""The '#' quick-memory REPL shortcut: a line starting with '#' is saved straight to memory
(no model turn); '# user:' routes to the USER profile; non-'#' lines pass through."""

from __future__ import annotations

from types import SimpleNamespace

from aegis.cli import repl
from aegis.cli.repl import _skill_scaffold, quick_memory
from aegis.config import Config
from aegis.memory import MemoryManager


def _agent():
    return SimpleNamespace(memory=MemoryManager(Config.load()))


def test_hash_saves_to_memory():
    agent = _agent()
    assert quick_memory("# the deploy script lives in scripts/ship.sh", agent) is True
    assert "scripts/ship.sh" in agent.memory.store.raw("memory")


def test_user_prefix_routes_to_profile():
    agent = _agent()
    assert quick_memory("# user: prefers concise answers", agent) is True
    assert "concise answers" in agent.memory.store.raw("user")
    assert "concise answers" not in agent.memory.store.raw("memory")


def test_non_hash_passes_through():
    agent = _agent()
    assert quick_memory("run the tests", agent) is False


def test_bare_hash_is_directive_but_saves_nothing():
    agent = _agent()
    assert quick_memory("#", agent) is True
    assert agent.memory.store.raw("memory") == ""


def test_memory_disabled_is_graceful():
    agent = SimpleNamespace(memory=None)
    assert quick_memory("# something", agent) is True   # handled, just not stored


def test_feedback_reflects_dedup(capsys, monkeypatch):
    """The '#' shortcut surfaces the store's real result, not a blanket success —
    a duplicate add reports 'already', not '✓ remembered'."""
    monkeypatch.setattr(repl, "_console", None)
    agent = _agent()
    quick_memory("# the build uses nox not tox", agent)
    capsys.readouterr()
    quick_memory("# the build uses nox not tox", agent)   # exact duplicate
    out = capsys.readouterr().out
    assert "already" in out.lower() and "✓ remembered" not in out


def test_skill_scaffold_is_structured():
    body = _skill_scaffold("deploy-web", "Deploy the website to prod")
    assert "## When to use" in body and "## Procedure" in body and "## Done when" in body
    assert "Deploy the website to prod" in body
