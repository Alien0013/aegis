from __future__ import annotations


def _config():
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["agent"]["max_iterations"] = 2
    cfg.data["agent"]["stream"] = False
    return cfg


def _agent(provider, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    return Agent(config=_config(), provider=provider, session=Session.create(), cwd=tmp_path)


class _ReadyFakeProvider:
    name = "fake"
    model = "fake-model"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        from aegis.types import LLMResponse

        self.calls += 1
        return LLMResponse(text="ready")


def test_ready_fake_provider_still_runs(tmp_path):
    provider = _ReadyFakeProvider()
    agent = _agent(provider, tmp_path)
    events = []

    result = agent.run("hello", events.append)

    assert result.content == "ready"
    assert provider.calls == 1
    assert agent.session.meta["provider_readiness"]["ok"] is True
    assert any(event["type"] == "provider_start" for event in events)


def test_provider_complete_not_called_when_required_auth_missing(tmp_path):
    class MissingAuthProvider:
        name = "openai"
        model = "gpt-5.5"
        context_length = 400_000
        api_mode = None
        auth = None

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            raise AssertionError("complete should not be called")

    provider = MissingAuthProvider()
    agent = _agent(provider, tmp_path)
    events = []

    result = agent.run("hello", events.append)

    assert provider.calls == 0
    assert "[provider error]" in result.content
    assert "auth is unavailable" in result.content
    readiness = agent.session.meta["provider_readiness"]
    assert readiness["ok"] is False
    assert readiness["missing"] == ["auth"]
    assert agent.session.meta["runtime"]["provider_readiness"]["ok"] is False
    assert [event for event in events if event["type"] == "provider_start"] == []
    readiness_events = [event for event in events if event["type"] == "provider_readiness"]
    assert readiness_events and readiness_events[-1]["status"] == "error"
    assert readiness_events[-1]["missing"] == ["auth"]
    assert agent.session.messages[-2].role == "user"
    assert agent.session.messages[-2].content == "hello"


def test_provider_complete_not_called_when_provider_unusable(tmp_path):
    class UnusableProvider:
        name = "fake"
        model = "fake-model"
        context_length = 200_000
        api_mode = None
        auth = None

    agent = _agent(UnusableProvider(), tmp_path)
    events = []

    result = agent.run("hello", events.append)

    assert "[provider error]" in result.content
    assert "provider.complete is not callable" in result.content
    readiness = agent.session.meta["provider_readiness"]
    assert readiness["ok"] is False
    assert readiness["missing"] == ["complete"]
    assert [event for event in events if event["type"] == "provider_start"] == []
