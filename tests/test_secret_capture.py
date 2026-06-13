from __future__ import annotations

import os


def test_secret_tool_captures_without_returning_value():
    from aegis import config as cfg
    from aegis.config import Config
    from aegis.secret_capture import store_secret_value
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import SecretTool

    secret = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789"

    def capture(key, prompt, metadata):
        assert key == "TELEGRAM_BOT_TOKEN"
        assert "Telegram" in prompt
        return store_secret_value(key, secret)

    ctx = ToolContext(config=Config.load(), secret_capture=capture)
    result = SecretTool().run({"key": "TELEGRAM_BOT_TOKEN", "prompt": "Telegram token"}, ctx)

    assert not result.is_error
    assert "TELEGRAM_BOT_TOKEN" in result.content
    assert secret not in result.content
    assert os.environ["TELEGRAM_BOT_TOKEN"] == secret
    assert cfg.env_path().read_text(encoding="utf-8").strip() == f"TELEGRAM_BOT_TOKEN={secret}"
    assert (cfg.env_path().stat().st_mode & 0o777) == 0o600


def test_secret_tool_rejects_value_argument():
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import SecretTool

    result = SecretTool().run({"key": "OPENAI_API_KEY", "api_key": "sk-live-secret-secret"}, ToolContext())

    assert result.is_error
    assert "must not be passed" in result.content
    assert "sk-live" not in result.content


def test_tool_args_and_results_are_redacted_from_events_traces_and_session(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.tracing import TraceStore
    from aegis.types import LLMResponse, ToolCall

    class FakeProvider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self, script):
            self.script = list(script)

        def complete(self, messages, tools=None, stream=False, on_delta=None, model=None,
                     max_tokens=None, reasoning="off"):
            if self.script:
                return self.script.pop(0)
            return LLMResponse(text="done.")

    secret = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789"
    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = "auto"
    provider = FakeProvider([
        LLMResponse(tool_calls=[
            ToolCall("c1", "bash", {"command": f"printf '%s' '{secret}'"}),
        ]),
        LLMResponse(text="done."),
    ])
    session = Session.create()
    agent = Agent(config=cfg, provider=provider, session=session, cwd=tmp_path)
    events = []

    result = agent.run("run the command", on_event=events.append)

    assert result.content == "done."
    rendered_events = repr(events)
    rendered_session = repr([m.to_dict() for m in session.messages])
    assert secret not in rendered_events
    assert secret not in rendered_session
    assert "[REDACTED]" in rendered_events
    assert "[REDACTED]" in rendered_session

    trace = TraceStore.from_config(cfg).get_trace(agent._trace_context["trace_id"])
    assert secret not in repr(trace)
    assert "[REDACTED]" in repr(trace)
