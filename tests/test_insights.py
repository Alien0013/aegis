"""Deepened insights: real billed usage (from the usage log) and tool-call breakdown
(from stored sessions) appear in the report alongside the activity counts."""

from __future__ import annotations

from aegis import insights, usage_log
from aegis.session import Session, SessionStore
from aegis.types import Message, ToolCall, Usage


def _seed_session_with_tools():
    store = SessionStore()
    s = Session.create(title="work")
    s.messages = [
        Message.user("do it"),
        Message.assistant(tool_calls=[ToolCall(id="1", name="bash", arguments={}),
                                      ToolCall(id="2", name="read_file", arguments={})]),
        Message.tool("1", "bash", "ok"),
        Message.tool("2", "read_file", "data"),
        Message.assistant(tool_calls=[ToolCall(id="3", name="bash", arguments={})]),
        Message.tool("3", "bash", "ok"),
        Message.assistant("done"),
    ]
    store.save(s)


def test_insights_reports_tool_breakdown_and_real_usage():
    _seed_session_with_tools()
    usage_log.log("anthropic", "claude-test", Usage(input_tokens=1000, output_tokens=200, cache_read=50))

    d = insights.insights(days=0)            # all time

    # tool-call breakdown from the stored session
    assert d["tool_calls"] == 3
    by_name = {t["name"]: t["count"] for t in d["top_tools"]}
    assert by_name["bash"] == 2 and by_name["read_file"] == 1

    # real billed usage from the usage log
    u = d["usage"]
    assert u["calls"] == 1
    assert u["input_tokens"] == 1000 and u["output_tokens"] == 200
    assert u["cache_read_tokens"] == 50
    assert u["cost_usd"] >= 0.0
    assert "claude-test" in u["by_model"]

    # render surfaces both sections
    text = insights.render(d)
    assert "Top tools" in text and "bash" in text
    assert "Model usage" in text


def test_insights_empty_home_is_safe():
    d = insights.insights(days=0)
    assert d["tool_calls"] == 0
    assert d["top_tools"] == []
    assert d["usage"]["calls"] == 0
    # render must not crash with no data
    assert "usage insights" in insights.render(d)
