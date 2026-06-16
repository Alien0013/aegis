from __future__ import annotations

from datetime import datetime, timezone


def _epoch(year=2026, month=4, day=28, hour=13, minute=40, second=53) -> float:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


def test_message_timestamp_helpers_strip_and_render_once():
    from aegis.gateway.message_timestamps import (
        coerce_message_timestamp,
        render_user_content_with_timestamp,
        strip_leading_message_timestamps,
    )

    epoch = _epoch()
    rendered = render_user_content_with_timestamp("hello", epoch)

    assert rendered.startswith("[Tue 2026-04-28 ")
    assert rendered.endswith("hello")
    assert coerce_message_timestamp(int(epoch * 1000)) == epoch

    clean, embedded = strip_leading_message_timestamps(f"{rendered}")
    assert clean == "hello"
    assert embedded == epoch


def test_gateway_user_message_timestamps_default_off():
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import _gateway_user_message, _message_timestamps_enabled

    cfg = Config.load()
    ev = MessageEvent(platform="telegram", chat_id="c1", text="hello", timestamp=_epoch())

    msg = _gateway_user_message(cfg, ev, ev.text)

    assert _message_timestamps_enabled(cfg) is False
    assert msg.content == "hello"
    assert msg.meta["gateway"]["message_timestamp"] == _epoch()
    assert msg.meta["gateway"]["timestamp_enabled"] is False


def test_gateway_user_message_timestamps_opt_in():
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import _gateway_user_message, _message_timestamps_enabled

    cfg = Config.load()
    cfg.data["gateway"]["message_timestamps"]["enabled"] = True
    ev = MessageEvent(platform="telegram", chat_id="c1", text="hello", timestamp=_epoch())

    msg = _gateway_user_message(cfg, ev, ev.text)

    assert _message_timestamps_enabled(cfg) is True
    assert msg.content.startswith("[Tue 2026-04-28 ")
    assert msg.content.endswith("hello")
    assert msg.meta["gateway_timestamp_clean_content"] == "hello"


def test_agent_sees_gateway_timestamp_but_persists_clean_text(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import _gateway_user_message
    from aegis.session import Session
    from aegis.types import LLMResponse

    class CaptureProvider:
        name = "capture"
        model = "capture-model"
        context_length = 200_000
        auth = None

        def __init__(self):
            self.user_seen = ""

        def complete(self, messages, tools=None, stream=False, on_delta=None, model=None,
                     max_tokens=None, reasoning="off"):
            self.user_seen = next(m.content for m in messages if m.role == "user")
            return LLMResponse(text="done")

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["gateway"]["message_timestamps"]["enabled"] = True
    provider = CaptureProvider()
    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    ev = MessageEvent(platform="telegram", chat_id="c1", text="hello", timestamp=_epoch())

    agent.run(_gateway_user_message(cfg, ev, ev.text))

    assert provider.user_seen.startswith("[Tue 2026-04-28 ")
    assert provider.user_seen.endswith("hello")
    user_messages = [m for m in agent.session.messages if m.role == "user"]
    assert user_messages[-1].content == "hello"
    assert user_messages[-1].meta["gateway"]["message_timestamp"] == _epoch()
