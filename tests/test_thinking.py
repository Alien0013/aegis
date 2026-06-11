"""Thinking/reasoning: adaptive vs legacy wire format, block echo-back, visibility."""

from __future__ import annotations

import pytest

from aegis.providers.anthropic import AnthropicTransport
from aegis.types import LLMResponse, Message


class _Auth:
    def headers(self):
        return {"x-api-key": "test"}


def _payload_for(monkeypatch, model: str, reasoning: str) -> dict:
    tr = AnthropicTransport()
    cap = {}
    monkeypatch.setattr(tr, "_blocking",
                        lambda url, headers, payload, timeout:
                        cap.update(payload=payload) or LLMResponse(text="ok"))
    tr.complete(base_url="https://x", auth=_Auth(), model=model,
                messages=[Message.user("hi")], tools=None, stream=False,
                reasoning=reasoning)
    return cap["payload"]


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-8", "claude-fable-5"])
def test_modern_models_use_adaptive_thinking(monkeypatch, model):
    p = _payload_for(monkeypatch, model, "high")
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in str(p["thinking"])     # would 400 on fable/4.7/4.8


def test_xhigh_maps_to_max_effort(monkeypatch):
    assert _payload_for(monkeypatch, "claude-sonnet-4-6", "xhigh")["output_config"] == {"effort": "max"}


def test_legacy_models_keep_budget_tokens(monkeypatch):
    p = _payload_for(monkeypatch, "claude-sonnet-4-5", "medium")
    assert p["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert "output_config" not in p                      # effort errors on sonnet-4-5


def test_off_sends_no_thinking_field(monkeypatch):
    p = _payload_for(monkeypatch, "claude-fable-5", "off")
    assert "thinking" not in p and "output_config" not in p   # explicit disabled 400s on fable


def test_thinking_blocks_echoed_back_first():
    """Anthropic requires signed thinking blocks to precede text/tool_use on replay."""
    blk = {"type": "thinking", "thinking": "let me check", "signature": "sig=="}
    msg = Message(role="assistant", content="answer", thinking_blocks=[blk])
    _, wire = AnthropicTransport()._to_wire([Message.user("q"), msg])
    asst = next(m for m in wire if m["role"] == "assistant")
    assert asst["content"][0] == blk
    assert asst["content"][1]["type"] == "text"


def test_message_thinking_blocks_survive_serialization():
    blk = {"type": "thinking", "thinking": "t", "signature": "s"}
    m = Message(role="assistant", content="x", reasoning="t", thinking_blocks=[blk])
    assert Message.from_dict(m.to_dict()).thinking_blocks == [blk]


def test_llmresponse_to_message_carries_thinking():
    r = LLMResponse(text="a", reasoning="why",
                    thinking_blocks=[{"type": "thinking", "thinking": "why", "signature": "s"}])
    m = r.to_message()
    assert m.reasoning == "why" and m.thinking_blocks and m.role == "assistant"


def test_presets_drop_unavailable_models():
    from aegis.onboarding import MODEL_PRESETS
    flat = [m for lst in MODEL_PRESETS.values() for m, _ in lst]
    assert "gpt-5.5-pro" not in flat
    assert "claude-fable-5" in flat and "gpt-5.5" in flat
