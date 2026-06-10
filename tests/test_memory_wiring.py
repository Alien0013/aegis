"""Regression tests for the memory-wiring bugs: saved user facts must reach new
sessions, /new must thaw the snapshot, and natural-language recall must match."""

from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    return Config.load()


def test_saved_user_facts_reach_prompt_after_refresh(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager

    mm = MemoryManager(config)
    mm.store.add("user", "The user's name is TJ; call him Jarvis-style.")
    # snapshot is frozen at construction (prompt-cache stability) ...
    assert "TJ" not in mm.build_context_block()
    # ... and thaws on refresh — this is what /new and new processes rely on
    mm.refresh_snapshot()
    block = mm.build_context_block()
    assert "About the user" in block and "TJ" in block


def test_manual_workspace_profile_merges_into_prompt(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis import config as cfg
    from aegis.memory import MemoryManager

    # untouched onboarding template -> skipped (pure noise)
    (cfg.workspace_dir() / "USER.md").write_text(
        "# User Profile\n\nAdd stable preferences, aliases, or project notes here.\n")
    mm = MemoryManager(config)
    assert "Add stable preferences" not in mm.build_context_block()

    # a real hand-edited profile -> included alongside learned facts
    (cfg.workspace_dir() / "USER.md").write_text("Prefers dark mode and short answers.\n")
    mm.store.add("user", "Name: TJ")
    mm.refresh_snapshot()
    block = mm.build_context_block()
    assert "dark mode" in block and "Name: TJ" in block


def test_slash_new_refreshes_memory_snapshot(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.cli.repl import handle_slash
    from aegis.memory import MemoryManager
    from aegis.session import Session

    class StubAgent:
        def __init__(self):
            self.session = Session.create()
            self.tool_context = type("TC", (), {"session": None})()
            self.memory = MemoryManager(config)
            self.refreshed = False

        def refresh_volatile(self):
            self.refreshed = True

    agent = StubAgent()
    old_id = agent.session.id
    handle_slash("/new", agent)
    assert agent.session.id != old_id
    assert agent.refreshed                      # the bug: this never happened before
    assert agent.tool_context.session is agent.session


def test_session_search_matches_natural_language(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    s = Session.create(title="parser work")
    s.messages = [Message.user("we fixed the parser bug and shipped v2"),
                  Message.assistant("done — the parser bug is fixed")]
    store = SessionStore()
    store.save(s)

    # the old code phrase-matched the ENTIRE query -> zero hits for questions like this
    hits = store.search_messages("what did we do about the parser bug?")
    assert hits and "parser" in hits[0]["snippet"].lower()

    # stopword-only queries degrade to a phrase rather than matching everything
    assert store.search_messages("what did we do") == []


def test_fts_query_tokenization():
    from aegis.session import SessionStore
    q = SessionStore._fts_query("what did we decide about the auth refactor?")
    assert '"auth"' in q and '"refactor"' in q and " OR " in q
    assert '"what"' not in q and '"the"' not in q


def test_memory_add_dedups_near_duplicates(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryStore

    store = MemoryStore()
    assert "remembered" in store.add("user", "The user's name is TJ.")
    # same fact, different phrasing -> rejected (this is the exact dup from the field report)
    assert store.add("user", "User's name is TJ") == "already remembered"
    assert store.add("user", "the users name is tj.") == "already remembered"
    # a genuinely different fact still lands
    assert "remembered" in store.add("user", "TJ prefers dark mode.")
    assert len(store.entries("user")) == 2
