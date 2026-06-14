"""The '#' quick-memory REPL shortcut: a line starting with '#' is saved straight to memory
(no model turn); '# user:' routes to the USER profile; non-'#' lines pass through."""

from __future__ import annotations

from types import SimpleNamespace

from aegis.cli.repl import quick_memory
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
