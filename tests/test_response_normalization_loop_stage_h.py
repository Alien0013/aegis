from __future__ import annotations

import copy


def _config():
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["agent"]["max_iterations"] = 1
    cfg.data["agent"]["stream"] = False
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["tools"]["defer_schemas"] = False
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data["learn"]["auto"] = False
    cfg.data["learn"]["auto_apply_skills"] = False
    return cfg


class ResponseProvider:
    name = "stage-h"
    model = "response-normalization"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        return self.response


def _run_with_response(response, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session, SessionStore

    session = Session.create()
    store = SessionStore()
    agent = Agent(
        config=_config(),
        provider=ResponseProvider(response),
        session=session,
        store=store,
        cwd=tmp_path,
    )
    events = []
    result = agent.run("normalize this provider response", on_event=events.append)
    saved = store.load(session.id)
    assert saved is not None
    assistant_event = next(e for e in events if e["type"] == "assistant_message")
    final_event = next(e for e in events if e["type"] == "final")
    assistant_message = next(m for m in reversed(agent.session.messages) if m.role == "assistant")
    saved_assistant = next(m for m in reversed(saved.messages) if m.role == "assistant")
    return result, assistant_message, saved_assistant, assistant_event, final_event, events


def test_loop_normalizes_inline_reasoning_and_tool_call_xml_before_surface_and_persistence(tmp_path):
    from aegis.types import LLMResponse

    raw = (
        "<think>private chain of thought</think>\n"
        "Visible answer before.\n"
        "<tool_call>{\"name\":\"bash\",\"arguments\":{\"cmd\":\"cat ~/.aegis/.env\"}}</tool_call>\n"
        "Visible answer after."
    )

    result, session_msg, saved_msg, assistant_event, final_event, events = _run_with_response(
        LLMResponse(text=raw),
        tmp_path,
    )

    expected_text = "Visible answer before.\nVisible answer after."
    assert result.content == expected_text
    assert session_msg.content == expected_text
    assert saved_msg.content == expected_text
    assert assistant_event["text"] == expected_text
    assert final_event["text"] == expected_text

    for surfaced in (result.content, session_msg.content, saved_msg.content, assistant_event["text"]):
        assert "private chain of thought" not in surfaced
        assert "<think>" not in surfaced
        assert "<tool_call>" not in surfaced
        assert "cat ~/.aegis/.env" not in surfaced

    assert session_msg.reasoning == "private chain of thought"
    assert saved_msg.reasoning == "private chain of thought"
    assert any(
        e["type"] == "reasoning_delta" and e["text"] == "private chain of thought"
        for e in events
    )


def test_loop_preserves_structured_reasoning_and_anthropic_thinking_blocks(tmp_path):
    from aegis.types import LLMResponse

    thinking_blocks = [
        {
            "type": "thinking",
            "thinking": "signed private plan",
            "signature": "sig-stage-h",
        },
        {"type": "redacted_thinking", "data": "opaque-provider-state"},
    ]
    original_blocks = copy.deepcopy(thinking_blocks)

    result, session_msg, saved_msg, assistant_event, final_event, events = _run_with_response(
        LLMResponse(
            text="Visible structured answer.",
            reasoning="structured provider reasoning",
            thinking_blocks=thinking_blocks,
        ),
        tmp_path,
    )

    assert result.content == "Visible structured answer."
    assert assistant_event["text"] == "Visible structured answer."
    assert final_event["text"] == "Visible structured answer."
    assert session_msg.reasoning == "structured provider reasoning"
    assert saved_msg.reasoning == "structured provider reasoning"
    assert session_msg.thinking_blocks == original_blocks
    assert saved_msg.thinking_blocks == original_blocks
    assert thinking_blocks == original_blocks
    assert any(
        e["type"] == "reasoning_delta" and e["text"] == "structured provider reasoning"
        for e in events
    )


def test_loop_redacts_obvious_assistant_secrets_before_events_result_and_persistence(tmp_path):
    from aegis.types import LLMResponse

    secret = "sk-stagehsecretstagehsecretstagehsecret"
    raw = (
        f"Do not persist this key: {secret}\n"
        "Authorization: Bearer github_pat_stagehsecretstagehsecretstageh\n"
        '{"api_key": "pplx-stagehsecret"}'
    )

    result, session_msg, saved_msg, assistant_event, final_event, _events = _run_with_response(
        LLMResponse(text=raw),
        tmp_path,
    )

    for surfaced in (
        result.content,
        session_msg.content,
        saved_msg.content,
        assistant_event["text"],
        final_event["text"],
    ):
        assert "[REDACTED]" in surfaced
        assert secret not in surfaced
        assert "github_pat_stagehsecretstagehsecretstageh" not in surfaced
        assert "pplx-stagehsecret" not in surfaced
