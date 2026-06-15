"""Record/replay A/B: extract a session's user turns, replay on model B, diff the outcomes."""

import pytest

from aegis import ab
from aegis.session import Session, SessionStore
from aegis.types import Message, ToolCall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))


def _session_with_turns():
    s = Session.create(title="orig")
    s.messages = [
        Message.system("you are helpful"),
        Message.user("add a function add(a,b)"),
        Message(role="assistant", content="", tool_calls=[ToolCall("1", "write_file", {})]),
        Message(role="tool", content="ok", tool_call_id="1", name="write_file"),
        Message.assistant("done — added add()"),
        Message.user("now add subtract"),
        Message.assistant("added subtract() too"),
    ]
    return s


def test_extract_user_prompts():
    s = _session_with_turns()
    assert ab.extract_user_prompts(s) == ["add a function add(a,b)", "now add subtract"]


def test_final_text_and_tools():
    s = _session_with_turns()
    assert ab.final_text(s) == "added subtract() too"
    assert ab.tools_used(s) == ["write_file"]


def test_session_result_shape():
    r = ab.session_result(_session_with_turns())
    assert r["final_text"] == "added subtract() too"
    assert r["tools"] == ["write_file"]
    assert r["turns"] == 3          # three assistant messages


def test_compare_identical():
    a = {"final_text": "same", "tools": ["x"], "turns": 1}
    b = {"final_text": "same", "tools": ["x"], "turns": 1}
    cmp = ab.compare_results(a, b)
    assert cmp["identical"] is True and cmp["text_similarity"] == 1.0
    assert cmp["tools_only_a"] == [] and cmp["tools_only_b"] == []


def test_compare_differs_with_tool_delta():
    a = {"final_text": "hello world", "tools": ["read_file"], "turns": 2}
    b = {"final_text": "hello there", "tools": ["read_file", "web_search"], "turns": 3}
    cmp = ab.compare_results(a, b)
    assert cmp["identical"] is False
    assert 0 < cmp["text_similarity"] < 1
    assert cmp["tools_only_b"] == ["web_search"]
    assert cmp["turns_a"] == 2 and cmp["turns_b"] == 3


def test_run_ab_with_injected_runner():
    store = SessionStore()
    s = _session_with_turns()
    store.save(s)

    captured = {}

    def fake_runner(prompts, model, provider, config):
        captured["prompts"] = prompts
        captured["model"] = model
        return {"final_text": "B did it differently", "tools": ["edit_file"], "turns": 2,
                "session_id": "sess-b"}

    out = ab.run_ab(s.id, model="claude-haiku-4-5", config=None, store=store, runner=fake_runner)
    assert captured["prompts"] == ["add a function add(a,b)", "now add subtract"]
    assert captured["model"] == "claude-haiku-4-5"
    assert out["a"]["final_text"] == "added subtract() too"
    assert out["b"]["final_text"] == "B did it differently"
    assert out["comparison"]["label_b"] == "claude-haiku-4-5"
    assert out["comparison"]["tools_only_b"] == ["edit_file"]


def test_run_ab_missing_session():
    with pytest.raises(LookupError):
        ab.run_ab("nope", store=SessionStore(), runner=lambda *a: {})


def test_run_ab_no_user_turns():
    store = SessionStore()
    s = Session.create()
    s.messages = [Message.system("sys")]
    store.save(s)
    with pytest.raises(ValueError):
        ab.run_ab(s.id, store=store, runner=lambda *a: {})
