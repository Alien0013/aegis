from __future__ import annotations


class StageProvider:
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.calls = 0

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls += 1
        return LLMResponse(text=f"{self.name}/{self.model}")


def _agent_config():
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["model"] = {"provider": "base", "default": "strong"}
    return cfg


def _stub_provider_rebuild(monkeypatch):
    built: list[StageProvider] = []

    def fake_build(config, *, model=None, name=None):
        provider = StageProvider(
            name or config.get("model.provider", "base"),
            model or config.get("model.default", "strong"),
        )
        built.append(provider)
        return provider

    monkeypatch.setattr("aegis.providers.fallback.build_with_fallbacks", fake_build)
    return built


def test_prompt_route_records_selection_event_and_restores_base(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    cfg = _agent_config()
    cfg.data["routing"] = [
        {"match": "deploy", "provider": "routed", "model": "routed-model"},
    ]
    _stub_provider_rebuild(monkeypatch)
    agent = Agent(
        config=cfg,
        provider=StageProvider("base", "strong"),
        session=Session.create(),
        cwd=tmp_path,
    )
    events = []

    result = agent.run("please deploy this", on_event=events.append)

    assert result.content == "routed/routed-model"
    assert (agent.provider.name, agent.provider.model) == ("base", "strong")
    selection = agent.session.meta["runtime_selection"]
    assert selection["source"] == "prompt_route"
    assert selection["selected"] == {"provider": "routed", "model": "routed-model"}
    assert selection["base"] == {"provider": "base", "model": "strong"}
    assert selection["one_turn"] is True
    assert selection["restored"] is True
    route_event = next(e for e in events if e["type"] == "runtime_route")
    assert route_event["source"] == "prompt_route"
    assert route_event["provider"] == "routed"
    assert route_event["model"] == "routed-model"
    assert agent.session.meta["runtime"]["provider"] == "routed"
    assert agent.session.meta["runtime"]["model"] == "routed-model"

    events.clear()
    second = agent.run("refactor the architecture", on_event=events.append)

    assert second.content == "base/strong"
    assert (agent.provider.name, agent.provider.model) == ("base", "strong")
    assert agent.session.meta["runtime_selection"]["source"] == "base"
    provider_start = next(e for e in events if e["type"] == "provider_start")
    assert provider_start["provider"] == "base"
    assert provider_start["model"] == "strong"


def test_budget_downshift_records_selection_event_and_does_not_stick(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    cfg = _agent_config()
    cfg.set("budget.auto_downshift", True)
    cfg.set("budget.cheap_model", "cheap")
    _stub_provider_rebuild(monkeypatch)
    agent = Agent(
        config=cfg,
        provider=StageProvider("base", "strong"),
        session=Session.create(),
        cwd=tmp_path,
    )
    events = []

    result = agent.run("rename foo to bar", on_event=events.append)

    assert result.content == "base/cheap"
    assert (agent.provider.name, agent.provider.model) == ("base", "strong")
    selection = agent.session.meta["runtime_selection"]
    assert selection["source"] == "budget_downshift"
    assert selection["selected"] == {"provider": "base", "model": "cheap"}
    assert selection["base"] == {"provider": "base", "model": "strong"}
    assert selection["one_turn"] is True
    assert selection["restored"] is True
    downshift = next(e for e in events if e["type"] == "model_downshift")
    assert downshift["provider"] == "base"
    assert downshift["model"] == "cheap"
    assert downshift["base_model"] == "strong"

    events.clear()
    second = agent.run("refactor the architecture", on_event=events.append)

    assert second.content == "base/strong"
    assert (agent.provider.name, agent.provider.model) == ("base", "strong")
    assert agent.session.meta["runtime_selection"]["source"] == "base"
    assert not any(e["type"] == "model_downshift" for e in events)
    provider_start = next(e for e in events if e["type"] == "provider_start")
    assert provider_start["provider"] == "base"
    assert provider_start["model"] == "strong"


def test_prompt_route_wins_over_budget_downshift(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    cfg = _agent_config()
    cfg.data["routing"] = [
        {"match": "show", "provider": "routed", "model": "route-model"},
    ]
    cfg.set("budget.auto_downshift", True)
    cfg.set("budget.cheap_model", "cheap")
    _stub_provider_rebuild(monkeypatch)
    agent = Agent(
        config=cfg,
        provider=StageProvider("base", "strong"),
        session=Session.create(),
        cwd=tmp_path,
    )
    events = []

    result = agent.run("show the status", on_event=events.append)

    assert result.content == "routed/route-model"
    selection = agent.session.meta["runtime_selection"]
    assert selection["source"] == "prompt_route"
    assert selection["selected"] == {"provider": "routed", "model": "route-model"}
    assert not any(e["type"] == "model_downshift" for e in events)


def test_budget_block_prevents_provider_call_and_records_turn_block(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    cfg = _agent_config()
    cfg.set("budget.enabled", True)
    cfg.set("budget.enforce", "block")
    cfg.set("budget.session_usd", 1.0)
    provider = StageProvider("base", "strong")
    agent = Agent(
        config=cfg,
        provider=provider,
        session=Session.create(),
        cwd=tmp_path,
    )
    monkeypatch.setattr(agent, "_session_spend_usd", lambda: 2.0)
    events = []

    result = agent.run("rename foo to bar", on_event=events.append)

    assert provider.calls == 0
    assert "[budget_blocked]" in result.content
    assert "budget blocked" in result.content
    assert not any(e["type"] == "provider_start" for e in events)
    warning = next(e for e in events if e["type"] == "budget_warning")
    assert warning["blocked"] is True
    assert warning["over_session"] is True
    blocked = next(e for e in events if e["type"] == "turn_blocked")
    assert blocked["reason"] == "budget_blocked"
    assert blocked["session_spend"] == 2.0
    assert blocked["session_cap"] == 1.0
    assert agent.session.meta["last_turn_blocked"]["reason"] == "budget_blocked"
