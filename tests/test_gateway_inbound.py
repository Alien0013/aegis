from __future__ import annotations

import time
import threading


def _adapter(config=None):
    from aegis.gateway.base import BasePlatformAdapter

    class FakeAdapter(BasePlatformAdapter):
        name = "fake"

        def __init__(self, cfg=None):
            self.sent = []
            if cfg is not None:
                self._config = cfg

        def send(self, chat_id: str, text: str) -> None:
            self.sent.append((chat_id, text))

    return FakeAdapter(config)


def _ev(text: str, chat: str = "c1"):
    from aegis.gateway.base import MessageEvent

    return MessageEvent(platform="fake", chat_id=chat, text=text, user_id="u1")


def _wait_for(fn, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_shared_inbound_queue_preserves_order():
    adapter = _adapter()
    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append(ev.text) or f"reply:{ev.text}")

    for text in ["a", "b", "c"]:
        adapter._submit_inbound(_ev(text))

    _wait_for(lambda: [r for _c, r in adapter.sent] == ["reply:a", "reply:b", "reply:c"])
    assert seen == ["a", "b", "c"]


def test_shared_inbound_stop_and_steer_controls_do_not_start_turns():
    adapter = _adapter()
    started = threading.Event()
    release = threading.Event()
    seen = []
    interrupted = []
    steered = []

    def dispatch(ev):
        seen.append(ev.text)
        started.set()
        release.wait(2)
        return f"reply:{ev.text}"

    adapter._init_inbound_queue(dispatch)
    adapter._interrupt_cb = lambda ev: interrupted.append(ev.text) or True
    adapter._steer_cb = lambda ev, text: steered.append(text) or True

    adapter._submit_inbound(_ev("first"))
    assert started.wait(2)
    assert adapter._submit_inbound(_ev("stop")) == ""
    assert adapter._submit_inbound(_ev("/steer adjust plan")) == ""
    release.set()

    _wait_for(lambda: ("c1", "reply:first") in adapter.sent)
    assert seen == ["first"]
    assert interrupted == ["stop"]
    assert steered == ["adjust plan"]
    assert ("c1", "🛑 stopped.") in adapter.sent
    assert ("c1", "🧭 steering noted.") in adapter.sent


def test_shared_inbound_new_interrupts_and_queues_reset():
    adapter = _adapter()
    started = threading.Event()
    release = threading.Event()
    seen = []
    interrupted = []

    def dispatch(ev):
        seen.append(ev.text)
        if ev.text == "first":
            started.set()
            release.wait(2)
        return f"reply:{ev.text}"

    adapter._init_inbound_queue(dispatch)
    adapter._interrupt_cb = lambda ev: interrupted.append(ev.text) or True

    adapter._submit_inbound(_ev("first"))
    assert started.wait(2)
    adapter._submit_inbound(_ev("/new"))
    release.set()

    _wait_for(lambda: seen == ["first", "/new"])
    assert interrupted == ["/new"]
    assert ("c1", "🛑 stopping current turn; reset queued.") in adapter.sent


def test_shared_inbound_clarify_waiter_consumes_next_reply():
    adapter = _adapter()
    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append(ev.text) or f"reply:{ev.text}")
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(_ev("ask"), "Pick one", ["A", "B"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: ("c1", "Pick one\n  1. A\n  2. B") in adapter.sent)
    adapter._submit_inbound(_ev("B"))
    thread.join(2)

    assert answer["text"] == "B"
    assert seen == []


def test_shared_inbound_exec_approval_waiter_uses_exec_prompt():
    adapter = _adapter()
    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append(ev.text) or f"reply:{ev.text}")
    answer = {}

    def ask():
        answer["text"] = adapter.ask_exec_approval(_ev("ask"), "Allow bash(ls)?", timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: ("c1", "Allow bash(ls)?\nReply approve, always, or deny.") in adapter.sent)
    adapter._submit_inbound(_ev("approve"))
    thread.join(2)

    assert answer["text"] == "approve"
    assert seen == []


def test_media_helpers_accept_metadata_kwargs(tmp_path):
    from aegis.gateway.base import BasePlatformAdapter
    from aegis.gateway.channels import TelegramAdapter

    path = tmp_path / "image.png"
    path.write_bytes(b"png")

    class CapturingAdapter(BasePlatformAdapter):
        name = "capture"

        def __init__(self):
            self.sent = []
            self.media = []

        def send(self, chat_id: str, text: str) -> None:
            self.sent.append((chat_id, text))

        def send_media(self, chat_id: str, path: str, caption: str = "", *, metadata=None, **kwargs) -> None:
            self.media.append((chat_id, path, caption, metadata, kwargs))

    adapter = CapturingAdapter()
    adapter.send_image("c1", str(path), caption="cap", metadata={"source": "remote"}, ephemeral=True)
    assert adapter.media == [("c1", str(path), "cap", {"source": "remote"}, {"ephemeral": True})]

    fallback = _adapter()
    fallback.send_document("c1", str(path), caption="doc", metadata={"source": "remote"})
    assert fallback.sent == [("c1", f"doc\n📎 file ready: {path}")]

    telegram = TelegramAdapter("token")
    telegram.send = lambda chat_id, text: None
    telegram.send_image("c1", str(path), metadata={"source": "remote"})


def test_shared_inbound_busy_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config

    cfg = Config.load()
    adapter = _adapter(cfg)
    started = threading.Event()
    release = threading.Event()
    seen = []
    interrupted = []
    steered = []

    def dispatch(ev):
        seen.append(ev.text)
        if ev.text == "first":
            started.set()
            release.wait(2)
        return f"reply:{ev.text}"

    adapter._init_inbound_queue(dispatch)
    adapter._interrupt_cb = lambda ev: interrupted.append(ev.text) or True
    adapter._steer_cb = lambda ev, text: steered.append(text) or True

    cfg.data.setdefault("gateway", {})["busy_mode"] = "queue"
    adapter._submit_inbound(_ev("first"))
    assert started.wait(2)
    adapter._submit_inbound(_ev("queued"))
    release.set()
    _wait_for(lambda: seen == ["first", "queued"])

    started.clear()
    release.clear()
    seen.clear()
    adapter.sent.clear()
    cfg.data["gateway"]["busy_mode"] = "steer"
    adapter._submit_inbound(_ev("first"))
    assert started.wait(2)
    adapter._submit_inbound(_ev("guidance"))
    release.set()
    _wait_for(lambda: seen == ["first"])
    assert steered[-1] == "guidance"

    started.clear()
    release.clear()
    seen.clear()
    adapter.sent.clear()
    cfg.data["gateway"]["busy_mode"] = "interrupt"
    adapter._submit_inbound(_ev("first"))
    assert started.wait(2)
    adapter._submit_inbound(_ev("replacement"))
    release.set()
    _wait_for(lambda: seen == ["first", "replacement"])
    assert interrupted[-1] == "replacement"


def test_shared_inbound_wait_mode_returns_reply_without_delivery():
    adapter = _adapter()
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")

    reply = adapter._submit_inbound(_ev("webhook"), wait=True)

    assert reply == "reply:webhook"
    assert adapter.sent == []


def test_inbound_normalizes_platform_alias_and_bot_command_suffix():
    from aegis.gateway.base import MessageEvent

    adapter = _adapter()
    adapter.bot_username = "aegis_bot"
    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append((ev.platform, ev.text)) or "ok")

    adapter._submit_inbound(MessageEvent(platform="tg", chat_id="c1", text="/status@aegis_bot", user_id="u1"))

    _wait_for(lambda: seen)
    assert seen == [("telegram", "/status")]


def test_telegram_command_suffix_requires_matching_bot_username():
    from aegis.platforms import normalize_inbound_command

    assert normalize_inbound_command("/status@other_bot", platform="telegram") == "/status@other_bot"
    assert (
        normalize_inbound_command("/status@other_bot", platform="telegram", bot_username="aegis_bot")
        == "/status@other_bot"
    )


def test_inbound_normalizes_slack_bang_command_alias():
    from aegis.gateway.base import MessageEvent

    adapter = _adapter()
    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append(ev.text) or "ok")

    adapter._submit_inbound(MessageEvent(platform="slack", chat_id="c1", text="!stop", user_id="u1"))

    _wait_for(lambda: seen)
    assert seen == ["/stop"]


def test_platform_helper_command_caps_and_utf16_chunks():
    from aegis.platforms import capped_command_menu, chunk_text_by_units, utf16_units

    commands = capped_command_menu(["/custom", "/bad command", "/custom"], max_commands=4)
    assert commands == ["/help", "/whoami", "/status", "/stop"]

    chunks = chunk_text_by_units("😀" * 5, limit=4, len_fn=utf16_units)
    assert chunks == ["😀😀", "😀😀", "😀"]


def test_adapter_metadata_for_core_platforms(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.discord_channel import DiscordAdapter
    from aegis.gateway.slack_channel import SlackAdapter
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    assert TelegramAdapter("token").metadata["transport"] == "long_poll"
    assert DiscordAdapter("token").metadata["supports_threads"] is True
    assert SlackAdapter().metadata["typed_command_prefix"] == "!"
    assert WebhookChannel().metadata["transport"] == "http"


def test_gateway_webhook_channel_normalizes_event_body():
    from aegis.gateway.webhook_channel import WebhookChannel

    ev = WebhookChannel()._event_from_body({
        "platform": "tg",
        "chat_id": 42,
        "text": "hello",
        "user_id": 7,
        "thread_id": 9,
        "message_id": "m1",
        "attachments": [{"type": "image"}],
        "metadata": {"source": "bridge"},
    })

    assert ev.platform == "telegram"
    assert ev.chat_id == "42"
    assert ev.thread_id == "9"
    assert ev.message_id == "m1"
    assert ev.attachments == [{"type": "image"}]
    assert ev.metadata == {"source": "bridge"}


def test_shared_inbound_records_delivery_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.runs import RunStore

    adapter = _adapter()
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")

    adapter._submit_inbound(_ev("telemetry", chat="room1"))

    def delivery_runs():
        return [r for r in RunStore().list(surface="gateway", limit=10)
                if r["kind"] == "delivery" and r["status"] == "ok"]

    _wait_for(lambda: delivery_runs())
    run = delivery_runs()[0]
    assert run["status"] == "ok"
    assert run["session_id"] == "room1"
    assert run["prompt_preview"] == "telemetry"
    assert run["result_preview"] == "reply:telemetry"
    assert run["data"]["platform"] == "fake"
    assert run["data"]["chat_id"] == "room1"
    assert run["data"]["queue_wait_ms"] >= 0
    assert run["data"]["dispatch_ms"] >= 0
    assert run["data"]["delivery_status"] == "ok"


def test_shared_inbound_records_reply_context(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.gateway.base import MessageEvent
    from aegis.runs import RunStore

    adapter = _adapter()
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")
    adapter._submit_inbound(MessageEvent(
        platform="fake",
        chat_id="room1",
        text="follow up",
        user_id="u1",
        message_id="43",
        reply_to_message_id="42",
        reply_to_text="quoted context",
    ))

    def delivery_runs():
        return [r for r in RunStore().list(surface="gateway", limit=10)
                if r["kind"] == "delivery" and r["status"] == "ok"]

    _wait_for(lambda: delivery_runs())
    run = delivery_runs()[0]
    assert run["data"]["message_id"] == "43"
    assert run["data"]["reply_to_message_id"] == "42"
    assert run["data"]["has_reply_context"] is True


def test_gateway_delivery_runs_use_runner_session_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.gateway.runner import GatewayRunner
    from aegis.runs import RunStore

    runner = GatewayRunner(Config.load(), cwd=tmp_path)
    adapter = _adapter()
    runner.add(adapter)
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")
    ev = _ev("telemetry", chat="room1")

    adapter._submit_inbound(ev)

    key = runner._key(ev)

    def delivery_runs():
        return [r for r in RunStore().list(session_id=key, limit=10)
                if r["kind"] == "delivery" and r["status"] == "ok"]

    _wait_for(lambda: delivery_runs())
    run = delivery_runs()[0]
    assert run["session_id"] == "fake:room1:u1"
    assert run["session_id"] == key
    assert run["data"]["chat_id"] == "room1"
    assert run["result_preview"] == "reply:telemetry"


def test_shared_inbound_records_delivery_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.gateway.base import BasePlatformAdapter
    from aegis.runs import RunStore

    class BrokenAdapter(BasePlatformAdapter):
        name = "broken"

        def send(self, chat_id: str, text: str) -> None:
            raise RuntimeError("offline")

    adapter = BrokenAdapter()
    adapter._init_inbound_queue(lambda ev: "cannot send this")
    adapter._submit_inbound(_ev("deliver failure"))

    def errored():
        rows = [r for r in RunStore().list(surface="gateway", limit=10)
                if r["kind"] == "delivery" and r["status"] == "error"]
        return rows[0] if rows else None

    _wait_for(errored)
    run = errored()
    assert run is not None
    assert "deliver RuntimeError: offline" in run["error"]
    assert run["data"]["delivery_status"] == "error"
