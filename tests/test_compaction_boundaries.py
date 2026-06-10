"""Compaction boundary safety: agentic single-user turns keep their tail, tool
groups never split, output always passes wire-validity normalization."""

from __future__ import annotations

from aegis.types import Message, ToolCall


class FakeProvider:
    def complete(self, messages, tools=None, **kw):
        return type("R", (), {"text": "summary of the middle"})()


def _tool_turn(i):
    """assistant(tool_calls) + its tool result."""
    return [Message(role="assistant", content="",
                    tool_calls=[ToolCall(id=f"c{i}", name="bash", arguments={"n": i})]),
            Message.tool(f"c{i}", "bash", f"out{i}")]


def _assert_wire_valid(msgs):
    """Every tool result has a preceding tool_call; every tool_call has a result."""
    from aegis.agent.governance import normalize
    assert [
        (m.role, m.content, [tc.id for tc in m.tool_calls], m.tool_call_id) for m in normalize(list(msgs))
    ] == [
        (m.role, m.content, [tc.id for tc in m.tool_calls], m.tool_call_id) for m in msgs
    ], "compaction produced messages that normalization had to repair"


def test_single_user_agentic_turn_keeps_recent_tail():
    """One user message + a long tool loop (the agentic shape): the tail must keep
    the most recent exchanges, not summarize away the model's working state."""
    from aegis.agent.compaction import compress

    msgs = [Message.system("sys"), Message.user("do the big task")]
    for i in range(30):
        msgs += _tool_turn(i)
    out = compress(msgs, FakeProvider(), preserve_first=3, preserve_last=10)

    # the most recent tool exchange survived verbatim
    assert any(m.role == "tool" and m.content == "out29" for m in out)
    assert len(out) < len(msgs)                       # something was actually summarized
    assert any("[Earlier conversation summarized]" in (m.content or "") for m in out)
    _assert_wire_valid([m for m in out if m.role != "system"])


def test_head_never_splits_a_tool_group():
    """preserve_first landing mid tool-group must extend to keep the group whole."""
    from aegis.agent.compaction import compress

    msgs = [Message.system("sys"), Message.user("go"),
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="a1", name="bash", arguments={}),
                                ToolCall(id="a2", name="bash", arguments={})]),
            Message.tool("a1", "bash", "r1"),
            Message.tool("a2", "bash", "r2")]
    for i in range(20):
        msgs += _tool_turn(i)
    msgs += [Message.user("and now finish"), Message.assistant("ok")]

    # preserve_first=3 cuts between the two tool results of the first assistant
    out = compress(msgs, FakeProvider(), preserve_first=3, preserve_last=4)
    kept = [m for m in out if m.role != "system"]
    # both results of the double tool call are still with their assistant
    ids = [m.tool_call_id for m in kept if m.role == "tool"]
    assert "a1" in ids and "a2" in ids
    _assert_wire_valid(kept)


def test_structured_summary_template_used():
    from aegis.agent.compaction import compress

    seen = {}

    class Capture:
        def complete(self, messages, tools=None, **kw):
            seen["system"] = messages[0].content
            return type("R", (), {"text": "s"})()

    msgs = [Message.system("sys")] + [Message.user(f"m{i}") for i in range(40)]
    compress(msgs, Capture(), preserve_first=2, preserve_last=5)
    assert "Primary request" in seen["system"]
    assert "Pending & next step" in seen["system"]


def test_history_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.memory import History

    h = History()
    h.append("user", "x" * 100)
    h._maybe_rotate(max_bytes=1000, keep_lines=2)     # force a tiny cap
    for _ in range(20):
        h.append("user", "y" * 100)
    h._maybe_rotate(max_bytes=1000, keep_lines=2)
    assert len(h.path.read_text().strip().splitlines()) <= 2
