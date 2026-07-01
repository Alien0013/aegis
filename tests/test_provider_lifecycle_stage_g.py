from __future__ import annotations

import copy


def _config():
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["agent"]["max_iterations"] = 1
    cfg.data["agent"]["stream"] = True
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["tools"]["defer_schemas"] = False
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    return cfg


class StreamingThinkingProvider:
    name = "stage-g"
    model = "stream-thinking"
    context_length = 200_000
    api_mode = None
    auth = None

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        for chunk in ("<thi", "nk>private plan", "</think>", "Visible answer."):
            kwargs["on_delta"](chunk)
        return LLMResponse(text="<think>private plan</think>Visible answer.")


class LifecycleProvider:
    name = "stage-g"
    model = "lifecycle"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self, agent_ref):
        self.agent_ref = agent_ref
        self.active_request_ids = []

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.active_request_ids.append(self.agent_ref._current_api_request_id)
        return LLMResponse(text="ok")


def test_streaming_inline_thinking_split_across_deltas_does_not_leak(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    agent = Agent(
        config=_config(),
        provider=StreamingThinkingProvider(),
        session=Session.create(),
        cwd=tmp_path,
    )
    events = []

    result = agent.run("stream with private thinking", on_event=events.append)

    delta_text = "".join(e["text"] for e in events if e["type"] == "assistant_delta")
    assert delta_text == "Visible answer."
    assert "<thi" not in delta_text
    assert "private plan" not in delta_text
    assert "</think>" not in delta_text

    assistant_message = next(e for e in events if e["type"] == "assistant_message")
    assert assistant_message["text"] == "Visible answer."
    assert result.content == "Visible answer."


def test_provider_observer_lifecycle_uses_stable_api_request_id_and_clears_current(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    observer_events = []

    def fake_fire_hook(event, payload=None, agent=None):
        if event in {"pre_api_request", "post_api_request", "api_request_error"}:
            observer_events.append((event, payload))
        return None

    monkeypatch.setattr("aegis.plugins.fire_hook", fake_fire_hook)
    agent = Agent(
        config=_config(),
        provider=None,
        session=Session.create(),
        cwd=tmp_path,
    )
    provider = LifecycleProvider(agent)
    agent.provider = provider
    events = []

    result = agent.run("check lifecycle", on_event=events.append)

    assert result.content == "ok"
    assert len(provider.active_request_ids) == 1
    active_request_id = provider.active_request_ids[0]
    assert active_request_id.startswith("api_")
    assert agent._last_api_request_id == active_request_id
    assert agent._current_api_request_id == ""

    pre = next(payload for event, payload in observer_events if event == "pre_api_request")
    post = next(payload for event, payload in observer_events if event == "post_api_request")
    assert pre["api_request_id"] == active_request_id
    assert post["api_request_id"] == active_request_id
    assert post["status"] == "ok"
    assert post["response"]["finish_reason"] is None
    assert not any(event == "api_request_error" for event, _payload in observer_events)
