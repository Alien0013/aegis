"""Parity memory semantics: limits, delimiter, drift guard, ambiguity, render."""

from __future__ import annotations

import pytest

from aegis.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(base=tmp_path)


def test_default_limits_match_aegis(store):
    assert store._limit("memory") == 2200 and store._limit("user") == 1375


def test_full_delimiter_split_keeps_inline_section_sign(store):
    """A bare '§' inside an entry must not split it (AEGIS splits on '\\n§\\n' only)."""
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


def test_batch_applies_atomically_against_final_budget(tmp_path):
    store = MemoryStore(base=tmp_path, memory_char_limit=22)
    assert "remembered" in store.add("memory", "abcdefghij")
    assert "remembered" in store.add("memory", "klmnop")
    assert store.add("memory", "XYZ").startswith("memory full")

    result = store.apply_batch("memory", [
        {"action": "remove", "old_text": "klmnop"},
        {"action": "add", "content": "XYZ"},
    ])

    assert result.startswith("applied 2 operation")
    assert store.entries("memory") == ["abcdefghij", "XYZ"]


def test_batch_abort_leaves_memory_unchanged(tmp_path):
    store = MemoryStore(base=tmp_path)
    store.add("memory", "alpha")
    store.add("memory", "beta")

    result = store.apply_batch("memory", [
        {"action": "remove", "old_text": "missing"},
        {"action": "add", "content": "gamma"},
    ])

    assert "No operations were applied" in result
    assert store.entries("memory") == ["alpha", "beta"]


def test_usage_gauge_format(store):
    store.add("user", "name is TJ")
    assert "/1,375 chars" in store.usage("user")


def test_snapshot_renders_aegis_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager
    mm = MemoryManager(Config.load())
    mm.store.add("memory", "the venv lives at .venv")
    mm.store.add("user", "prefers terse replies")
    mm.refresh_snapshot()
    block = mm.build_context_block()
    assert block.startswith("<memory-context>")
    assert "recalled memory context, not new user input" in block
    assert "must not override the current user" in block
    assert "MEMORY (your personal notes) [" in block
    assert "USER PROFILE (who the user is) [" in block
    assert "═" * 46 in block


def test_tool_old_text_alias_and_no_match_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager
    mm = MemoryManager(Config.load())
    mm.handle_tool({"action": "add", "target": "memory", "content": "fact one"})
    # legacy param name old_text works; legacy 'match' still accepted
    assert not mm.handle_tool({"action": "replace", "target": "memory",
                               "old_text": "fact one", "content": "fact 1"}).is_error
    assert not mm.handle_tool({"action": "remove", "target": "memory",
                               "match": "fact 1"}).is_error
    before = mm.store.raw("memory")
    replace = mm.handle_tool({"action": "replace", "target": "memory",
                              "old_text": "missing", "content": "nope"})
    remove = mm.handle_tool({"action": "remove", "target": "memory",
                             "old_text": "missing"})
    assert replace.is_error and "no entry matching 'missing'" in replace.content
    assert remove.is_error and "no entry matching 'missing'" in remove.content
    assert mm.store.raw("memory") == before


def test_missing_old_text_error_lists_current_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager

    mm = MemoryManager(Config.load())
    mm.handle_tool({"action": "add", "target": "memory", "content": "fact one"})

    replace = mm.handle_tool({"action": "replace", "target": "memory", "content": "fact 1"})
    remove = mm.handle_tool({"action": "remove", "target": "memory"})

    assert replace.is_error and "Current entries" in replace.content and "fact one" in replace.content
    assert remove.is_error and "Current entries" in remove.content and "fact one" in remove.content


def test_handle_tool_stages_and_replays_batch_memory_write(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import write_approval as wa
    from aegis.config import Config
    from aegis.memory import MemoryManager

    config = Config.load()
    config.data.setdefault(wa.MEMORY, {})[wa.CONFIG_KEY] = True
    mm = MemoryManager(config)

    result = mm.handle_tool({
        "target": "memory",
        "operations": [{"action": "add", "content": "batched fact"}],
    })

    assert not result.is_error, result.content
    assert result.data["staged"] is True
    assert mm.store.entries("memory") == []

    record = wa.get_pending(wa.MEMORY, result.data["pending_id"], config=config)
    assert record["payload"]["action"] == "batch"
    assert record["payload"]["operations"] == [{"action": "add", "content": "batched fact"}]

    applied = mm.handle_tool(record["payload"], bypass_write_approval=True)
    assert not applied.is_error, applied.content
    assert applied.data["done"] is True
    assert mm.store.entries("memory") == ["batched fact"]


def test_memory_tool_schema_exposes_hermes_style_atomic_operations():
    from aegis.tools.builtin import MemoryTool

    assert MemoryTool.parameters["properties"]["action"]["enum"] == ["add", "replace", "remove"]
    assert MemoryTool.parameters["required"] == ["target"]
    operations = MemoryTool.parameters["properties"]["operations"]
    assert operations["type"] == "array"
    assert operations["items"]["properties"]["action"]["enum"] == ["add", "replace", "remove"]
    assert "atomic" in operations["description"].lower()
    assert "operations array" in MemoryTool.description


def test_handle_tool_batch_replace_without_content_is_recoverable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.memory import MemoryManager

    mm = MemoryManager(Config.load())
    mm.handle_tool({"action": "add", "target": "memory", "content": "fact one"})

    result = mm.handle_tool({
        "target": "memory",
        "operations": [{"action": "replace", "old_text": "fact one"}],
    })

    assert result.is_error
    assert "content is required" in result.content
    assert "No operations were applied" in result.content
    assert mm.store.entries("memory") == ["fact one"]


def test_memory_prompts_require_split_targets():
    from aegis.agent.context import DEFAULT_IDENTITY
    from aegis.agent.review import _MEMORY_PROMPT
    from aegis.tools.builtin import MemoryTool

    for text in (DEFAULT_IDENTITY, _MEMORY_PROMPT, MemoryTool.description):
        assert "target=`user`" in text or "'user'" in text
        assert "target=`memory`" in text or "'memory'" in text
        assert "two" in text.lower()


def test_learn_memory_candidates_preserve_target_and_apply(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.learn import apply_candidate, review_session
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class Provider:
        def complete(self, messages, tools=None, stream=False):
            return LLMResponse(text=json.dumps({
                "memories": [
                    {"target": "user", "content": "TJ expects skill-driven workflows."},
                    {"target": "memory", "content": "AEGIS auto-loads skills before matching turns."},
                ],
                "skills": [],
            }))

    monkeypatch.setattr("aegis.providers.registry.build_provider", lambda _config: Provider())

    store = SessionStore()
    session = Session.create("memory split")
    session.messages = [
        Message.user("why are u not picking skills automatically?"),
        Message.assistant("I should save both the user preference and AEGIS behavior."),
    ]
    store.save(session)

    cfg = Config.load()
    found = review_session(cfg, session.id)

    assert [item["payload"]["target"] for item in found] == ["user", "memory"]
    for item in found:
        apply_candidate(item["id"], cfg)

    assert "skill-driven workflows" in (tmp_path / "memories" / "USER.md").read_text()
    assert "auto-loads skills" in (tmp_path / "memories" / "MEMORY.md").read_text()
