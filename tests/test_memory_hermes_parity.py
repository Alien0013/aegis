"""Hermes-parity memory semantics: limits, delimiter, drift guard, ambiguity, render."""

from __future__ import annotations

import pytest

from aegis.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(base=tmp_path)


def test_default_limits_match_hermes(store):
    assert store._limit("memory") == 2200 and store._limit("user") == 1375


def test_full_delimiter_split_keeps_inline_section_sign(store):
    """A bare '§' inside an entry must not split it (Hermes splits on '\\n§\\n' only)."""
    assert "remembered" in store.add("memory", "uses § as a delimiter in notes")
    assert store.entries("memory") == ["uses § as a delimiter in notes"]


def test_load_dedup_preserves_order(store, tmp_path):
    (tmp_path / "MEMORY.md").write_text("alpha\n§\nbeta\n§\nalpha\n§\ngamma\n")
    assert store.entries("memory") == ["alpha", "beta", "gamma"]


def test_over_limit_refuses_and_lists_entries(store):
    big = "x" * 2100
    assert "remembered" in store.add("memory", big)
    r = store.add("memory", "y" * 200)
    assert r.startswith("memory full") and "action=replace" in r
    assert "(2,100/2,200 chars)" in r
    assert "would put it at 2,303" in r


def test_replace_over_limit_reports_current_and_proposed_usage(store):
    assert "remembered" in store.add("memory", "short")
    r = store.replace("memory", "short", "x" * 2300)
    assert r.startswith("memory full")
    assert "(5/2,200 chars)" in r
    assert "would put it at 2,300" in r


def test_only_exact_duplicate_add_is_rejected(store):
    assert "remembered" in store.add("user", "The user's name is TJ.")
    assert store.add("user", "The user's name is TJ.") == "already remembered"
    assert "remembered" in store.add("user", "User's name is TJ")
    assert store.entries("user") == ["The user's name is TJ.", "User's name is TJ"]


def test_ambiguous_match_refused_with_previews(store):
    store.add("memory", "server runs on port 8080")
    store.add("memory", "dashboard runs on port 9119")
    r = store.replace("memory", "runs on port", "nope")
    assert r.startswith("multiple entries matched")
    r2 = store.remove("memory", "runs on port")
    assert r2.startswith("multiple entries matched")
    assert "removed 1" in store.remove("memory", "9119")    # specific match works


def test_identical_duplicates_are_safe_to_act_on(store, tmp_path):
    (tmp_path / "MEMORY.md").write_text("same fact\n§\nsame fact\n")
    # load-dedup collapses them, so the single survivor is unambiguous
    assert "removed 1" in store.remove("memory", "same fact")


def test_external_drift_backed_up_and_refused(store, tmp_path):
    store.add("memory", "tool-written entry")
    # external writer appends an entry larger than the whole-store limit
    with open(tmp_path / "MEMORY.md", "a") as f:
        f.write("\n§\n" + "Z" * 3000)
    r = store.add("memory", "another fact")
    assert r.startswith("refused") and ".bak." in r
    baks = list(tmp_path.glob("MEMORY.md.bak.*"))
    assert baks and "Z" * 3000 in baks[0].read_text()       # nothing lost


def test_usage_gauge_format(store):
    store.add("user", "name is TJ")
    assert "/1,375 chars" in store.usage("user")


def test_snapshot_renders_hermes_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager
    mm = MemoryManager(Config.load())
    mm.store.add("memory", "the venv lives at .venv")
    mm.store.add("user", "prefers terse replies")
    mm.refresh_snapshot()
    block = mm.build_context_block()
    assert "MEMORY (your personal notes) [" in block
    assert "USER PROFILE (who the user is) [" in block
    assert "═" * 46 in block


def test_tool_old_text_alias_and_no_match_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager
    mm = MemoryManager(Config.load())
    mm.handle_tool({"action": "add", "target": "memory", "content": "fact one"})
    # Hermes param name old_text works; legacy 'match' still accepted
    assert not mm.handle_tool({"action": "replace", "target": "memory",
                               "old_text": "fact one", "content": "fact 1"}).is_error
    assert not mm.handle_tool({"action": "remove", "target": "memory",
                               "match": "fact 1"}).is_error
    assert mm.handle_tool({"action": "replace", "target": "memory",
                           "old_text": "missing", "content": "nope"}).is_error
    assert mm.handle_tool({"action": "remove", "target": "memory",
                           "old_text": "missing"}).is_error


def test_memory_tool_schema_exposes_only_hermes_actions():
    from aegis.tools.builtin import MemoryTool
    assert MemoryTool.parameters["properties"]["action"]["enum"] == ["add", "replace", "remove"]
