"""The /context token-budget breakdown: system prompt, conversation, and tool schemas."""

from __future__ import annotations

from types import SimpleNamespace

from aegis.cli.repl import _context_breakdown, _render_context
from aegis.config import Config
from aegis.tools.registry import default_registry
from aegis.types import Message


def _agent():
    return SimpleNamespace(
        session=SimpleNamespace(
            messages=[Message.system("S" * 400), Message.user("hello there"), Message.assistant("hi")],
            meta={"prompt_parts": [{"name": "identity", "tier": "stable", "tokens": 120},
                                   {"name": "runtime", "tier": "stable", "tokens": 60}]},
        ),
        provider=SimpleNamespace(context_length=200_000),
        registry=default_registry(),
        config=Config.load(),
    )


def test_breakdown_sums_components():
    b = _context_breakdown(_agent())
    assert b["window"] == 200_000
    assert b["system"] > 0 and b["history"] > 0
    assert b["tool_count"] > 0 and b["tools"] > 0
    assert b["used"] == b["system"] + b["history"] + b["tools"]
    assert b["messages"] == 2          # excludes the system message
    assert len(b["parts"]) == 2


def test_render_shows_window_and_rows():
    lines = _render_context(_agent())
    text = "\n".join(lines)
    assert "Context window" in text
    assert "system prompt" in text and "conversation" in text and "tool schemas" in text
    assert "largest prompt parts" in text


def test_render_handles_unknown_window():
    agent = _agent()
    agent.provider.context_length = 0
    lines = _render_context(agent)
    assert any("window unknown" in line for line in lines)
