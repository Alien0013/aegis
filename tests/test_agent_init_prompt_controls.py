from __future__ import annotations


class _RuntimeAuth:
    def describe(self):
        return "test auth"

    def available(self):
        return True


class _RuntimeProvider:
    name = "fake"
    model = "plain-test-model"
    context_length = 200_000
    api_mode = type("Mode", (), {"value": "responses"})()
    auth = _RuntimeAuth()

    def complete(
        self,
        messages,
        tools=None,
        stream=False,
        on_delta=None,
        model=None,
        max_tokens=None,
        reasoning="off",
    ):
        from aegis.types import LLMResponse

        return LLMResponse(text="done.")


def _config(**agent_overrides):
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["skills"]["include_bundled"] = False
    cfg.data["agent"]["stream"] = False
    cfg.data["agent"].update(agent_overrides)
    return cfg


def _agent(tmp_path, *, platform=None, config=None, **agent_overrides):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    agent = Agent(
        config=config or _config(**agent_overrides),
        provider=_RuntimeProvider(),
        session=Session.create(),
        cwd=tmp_path,
    )
    agent.platform = platform
    return agent


def _prompt(tmp_path, **agent_overrides) -> str:
    return _agent(tmp_path, **agent_overrides)._build_system_prompt(include_volatile=False)


def _wire_system_prompt(agent) -> str:
    from aegis.agent.loop import _provider_wire_messages

    agent.ensure_system_prompt()
    return _provider_wire_messages(agent, agent.session.messages)[0].content


def test_platform_hints_append_and_replace_are_platform_scoped(tmp_path):
    cfg = _config()
    cfg.data["platform_hints"] = {"telegram": "TELEGRAM_BARE_APPEND_SENTINEL"}

    telegram = _agent(tmp_path, platform="telegram", config=cfg)._build_system_prompt(
        include_volatile=False
    )
    discord = _agent(tmp_path, platform="discord", config=cfg)._build_system_prompt(
        include_volatile=False
    )

    assert "You are on Telegram" in telegram
    assert "NO table syntax" in telegram
    assert "TELEGRAM_BARE_APPEND_SENTINEL" in telegram
    assert "You are on Discord" in discord
    assert "TELEGRAM_BARE_APPEND_SENTINEL" not in discord

    cfg = _config()
    cfg.data["platform_hints"] = {"telegram": {"append": "TELEGRAM_DICT_APPEND_SENTINEL"}}
    telegram = _agent(tmp_path, platform="telegram", config=cfg)._build_system_prompt(
        include_volatile=False
    )
    discord = _agent(tmp_path, platform="discord", config=cfg)._build_system_prompt(
        include_volatile=False
    )

    assert "You are on Telegram" in telegram
    assert "NO table syntax" in telegram
    assert "TELEGRAM_DICT_APPEND_SENTINEL" in telegram
    assert "You are on Discord" in discord
    assert "TELEGRAM_DICT_APPEND_SENTINEL" not in discord

    cfg = _config()
    cfg.data["platform_hints"] = {"telegram": {"replace": "TELEGRAM_REPLACE_SENTINEL"}}
    telegram = _agent(tmp_path, platform="telegram", config=cfg)._build_system_prompt(
        include_volatile=False
    )
    discord = _agent(tmp_path, platform="discord", config=cfg)._build_system_prompt(
        include_volatile=False
    )

    assert "TELEGRAM_REPLACE_SENTINEL" in telegram
    assert "You are on Telegram" not in telegram
    assert "NO table syntax" not in telegram
    assert "You are on Discord" in discord
    assert "TELEGRAM_REPLACE_SENTINEL" not in discord


def test_task_completion_guidance_can_be_removed_without_dropping_prompt_parts(tmp_path):
    assert "# Finish the job" in _prompt(tmp_path)

    prompt = _prompt(tmp_path, task_completion_guidance=False)

    assert "# Finish the job" not in prompt
    assert "WORKING artifact" not in prompt
    assert "You are AEGIS" in prompt
    assert "# AEGIS runtime" in prompt
    assert "# You ARE the AEGIS harness" in prompt


def test_parallel_tool_call_guidance_defaults_on_and_can_be_removed(tmp_path):
    default_prompt = _prompt(tmp_path)

    assert "# Parallel tool calls" in default_prompt
    assert "batch them" in default_prompt

    disabled_prompt = _prompt(tmp_path, parallel_tool_call_guidance=False)

    assert "# Parallel tool calls" not in disabled_prompt
    assert "batch them" not in disabled_prompt
    assert "# AEGIS runtime" in disabled_prompt


def test_tool_use_enforcement_can_be_forced_for_any_model(tmp_path):
    prompt = _prompt(tmp_path, tool_use_enforcement=True)

    assert "tool-use enforcement" in prompt
    assert "You MUST use your tools to take action" in prompt


def test_tool_use_enforcement_can_be_removed_without_removing_finish_guidance(tmp_path):
    prompt = _prompt(tmp_path, tool_use_enforcement=False)

    assert "tool-use enforcement" not in prompt
    assert "You MUST use your tools to take action" not in prompt
    assert "# Finish the job" in prompt


def test_environment_probe_controls_provider_wire_environment_block(tmp_path):
    default_wire = _wire_system_prompt(_agent(tmp_path))
    disabled_wire = _wire_system_prompt(_agent(tmp_path, environment_probe=False))

    assert "# Environment" in default_wire
    assert "# Environment" not in disabled_wire
    assert "# AEGIS runtime" in disabled_wire


def test_api_max_retries_defaults_to_three_total_attempts(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.types import LLMResponse

    class FlakyProvider(_RuntimeProvider):
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("slow")
            return LLMResponse(text="recovered")

    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)
    cfg = _config()
    provider = FlakyProvider()
    events = []
    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)

    result = agent.run("work", events.append)

    assert result.content == "recovered"
    assert provider.calls == 3
    assert [e["n"] for e in events if e.get("type") == "api_retry"] == [1, 2]


def test_api_max_retries_one_disables_loop_retry(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.types import LLMResponse

    class FlakyProvider(_RuntimeProvider):
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("slow")
            return LLMResponse(text="would have recovered")

    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)
    cfg = _config(api_max_retries=1)
    provider = FlakyProvider()
    events = []
    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)

    result = agent.run("work", events.append)

    assert provider.calls == 1
    assert result.content.startswith("[provider error]")
    assert not [e for e in events if e.get("type") == "api_retry"]


def test_api_max_retries_invalid_config_falls_back_to_three(tmp_path):
    cfg = _config(api_max_retries="not-an-int")
    agent = _agent(tmp_path, config=cfg)

    assert agent._api_max_retries == 3
