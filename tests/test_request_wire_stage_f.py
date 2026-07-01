from __future__ import annotations

import copy


def _config():
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["agent"]["max_iterations"] = 1
    cfg.data["agent"]["stream"] = False
    return cfg


class CapturingProvider:
    name = "fake"
    model = "fake-model"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self):
        self.calls = []

    def describe(self):
        return "fake"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls.append(copy.deepcopy(messages))
        return LLMResponse(text="ok")


def test_thinking_only_assistant_is_removed_from_provider_copy_only(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.agent import loop
    from aegis.session import Session
    from aegis.types import Message

    provider = CapturingProvider()
    session = Session.create()
    thinking_blocks = [{"type": "thinking", "thinking": "private plan", "signature": "sig"}]
    session.messages = [
        Message.system("system"),
        Message.user("first", images=["data:image/png;base64,abc"]),
        Message(
            role="assistant",
            content="",
            reasoning="private plan",
            thinking_blocks=copy.deepcopy(thinking_blocks),
        ),
        Message.user("second", images=["https://example.test/image.png"]),
    ]
    agent = Agent(config=_config(), provider=provider, session=session, cwd=tmp_path)

    result = loop.run_conversation(agent)

    assert result.content == "ok"
    seen = provider.calls[0]
    assert [m.role for m in seen] == ["system", "user"]
    assert seen[1].content == "first\n\nsecond"
    assert seen[1].images == ["data:image/png;base64,abc", "https://example.test/image.png"]
    assert not any(
        m.role == "assistant" and (m.reasoning or m.thinking_blocks)
        for m in seen
    )

    canonical = agent.session.messages
    assert [m.content for m in canonical if m.role == "user"] == ["first", "second"]
    stored_thinking = canonical[2]
    assert stored_thinking.role == "assistant"
    assert stored_thinking.content == ""
    assert stored_thinking.reasoning == "private plan"
    assert stored_thinking.thinking_blocks == thinking_blocks


def test_pre_llm_hook_thinking_only_assistant_is_governed_before_provider(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.agent import loop
    from aegis.session import Session
    from aegis.types import Message

    provider = CapturingProvider()
    session = Session.create()
    session.messages = [Message.system("system"), Message.user("base")]

    def fire_hook(event, *args, **kwargs):
        if event != "pre_llm_call":
            return None
        messages = list(args[0])
        messages.extend([
            Message(
                role="assistant",
                content="",
                reasoning="hook thought",
                thinking_blocks=[{"type": "thinking", "thinking": "hook thought"}],
            ),
            Message.user("hook user"),
        ])
        return messages

    monkeypatch.setattr("aegis.plugins.fire_hook", fire_hook)
    agent = Agent(config=_config(), provider=provider, session=session, cwd=tmp_path)

    result = loop.run_conversation(agent)

    assert result.content == "ok"
    seen = provider.calls[0]
    assert [m.role for m in seen] == ["system", "user"]
    assert seen[1].content == "base\n\nhook user"
    assert not any(
        m.role == "assistant" and m.reasoning == "hook thought"
        for m in seen
    )
    assert [m.content for m in agent.session.messages] == ["system", "base", "ok"]
