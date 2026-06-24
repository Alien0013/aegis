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
    _wait_for(lambda: ("c1", "Pick one\n  1. A\n  2. B\n\nReply with the number or exact choice.") in adapter.sent)
    adapter._submit_inbound(_ev("2"))
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


def test_shared_clarify_and_exec_prompts_preserve_delivery_metadata():
    from aegis.gateway.base import BasePlatformAdapter, MessageEvent

    class MetadataAdapter(BasePlatformAdapter):
        name = "whatsapp"

        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
            self.sent.append((chat_id, text, dict(metadata or {})))

    adapter = MetadataAdapter()
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")

    ev = MessageEvent(
        platform="whatsapp",
        chat_id="12025550123-111@g.us",
        text="ask",
        user_id="15551234567@s.whatsapp.net",
        user_name="Ada",
        thread_id="thread-1",
        message_id="BAE599999",
        reply_to_message_id="QUOTE123",
        metadata={
            "remote_jid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "response_url": "https://slack.example/response/secret",
            "raw_payload": {"token": "secret"},
            "authorization": "Bearer secret",
            "headers": {"X-Secret": "secret"},
        },
    )
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick one", ["A", "B"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: len(adapter.sent) == 1)
    adapter._submit_inbound(MessageEvent(platform="whatsapp", chat_id=ev.chat_id, thread_id=ev.thread_id, text="1"))
    thread.join(2)

    assert answer["text"] == "A"
    chat_id, text, metadata = adapter.sent[0]
    assert chat_id == ev.chat_id
    assert text == "Pick one\n  1. A\n  2. B\n\nReply with the number or exact choice."
    base_metadata = {
        "remote_jid": "12025550123-111@g.us",
        "participant": "15551234567@s.whatsapp.net",
        "platform": "whatsapp",
        "thread_id": "thread-1",
        "message_id": "BAE599999",
        "reply_to_message_id": "QUOTE123",
        "user_id": "15551234567@s.whatsapp.net",
        "user_name": "Ada",
    }
    for key, value in base_metadata.items():
        assert metadata[key] == value
    assert metadata["prompt_kind"] == "clarify"
    assert metadata["prompt_id"].startswith("clarify:")
    assert metadata["prompt_user_id"] == "15551234567@s.whatsapp.net"
    assert "response_url" not in metadata
    assert "raw_payload" not in metadata
    assert "authorization" not in metadata
    assert "headers" not in metadata

    approval = {}

    def approve():
        approval["text"] = adapter.ask_exec_approval(ev, "Allow bash(ls)?", timeout=2)

    thread = threading.Thread(target=approve)
    thread.start()
    _wait_for(lambda: len(adapter.sent) == 2)
    adapter._submit_inbound(MessageEvent(platform="whatsapp", chat_id=ev.chat_id, thread_id=ev.thread_id, text="deny"))
    thread.join(2)

    assert approval["text"] == "deny"
    assert adapter.sent[1][1] == "Allow bash(ls)?\nReply approve, always, or deny."
    approval_metadata = adapter.sent[1][2]
    for key, value in base_metadata.items():
        assert approval_metadata[key] == value
    assert approval_metadata["prompt_kind"] == "exec_approval"
    assert approval_metadata["prompt_id"].startswith("exec_approval:")
    assert approval_metadata["prompt_id"] != metadata["prompt_id"]
    assert approval_metadata["prompt_user_id"] == "15551234567@s.whatsapp.net"


def test_shared_prompt_waiter_rejects_stale_prompt_nonce():
    from aegis.gateway.base import BasePlatformAdapter, MessageEvent

    class MetadataAdapter(BasePlatformAdapter):
        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
            self.sent.append((chat_id, text, dict(metadata or {})))

    adapter = MetadataAdapter()
    dispatched = []
    adapter._init_inbound_queue(lambda ev: dispatched.append(ev.text) or f"reply:{ev.text}")
    ev = MessageEvent(platform="webhook", chat_id="c1", text="ask", user_id="u1", session_key="s1")
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick one", ["A", "B"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: len(adapter.sent) == 1)
    prompt_id = adapter.sent[0][2]["prompt_id"]

    stale = MessageEvent(
        platform="webhook",
        chat_id="c1",
        text="B",
        user_id="u1",
        session_key="s1",
        metadata={"prompt_id": "clarify:stale"},
    )
    adapter._submit_inbound(stale)
    _wait_for(lambda: len(adapter.sent) == 2)
    assert adapter.sent[1][1] == "That prompt is no longer active."
    assert answer == {}
    assert dispatched == []

    adapter._submit_inbound(MessageEvent(
        platform="webhook",
        chat_id="c1",
        text="2",
        user_id="u1",
        session_key="s1",
        metadata={"prompt_id": prompt_id},
    ))
    thread.join(2)

    assert answer["text"] == "B"
    assert dispatched == []


def test_gateway_delivery_metadata_keeps_bridge_reply_context():
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import _gateway_delivery_metadata

    ev = MessageEvent(
        platform="whatsapp",
        chat_id="120363@g.us",
        text="hello",
        user_id="15551234567@s.whatsapp.net",
        user_name="A User",
        thread_id="root-1",
        message_id="BAE5",
        reply_to_message_id="OLD",
        metadata={
            "remote_jid": "120363@g.us",
            "group_jid": "120363@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "message_key_id": "BAE5",
            "response_url": "https://mattermost.example/hooks/secret",
            "raw_payload": {"too": "large"},
        },
    )

    metadata = _gateway_delivery_metadata(ev)

    assert metadata["platform"] == "whatsapp"
    assert metadata["thread_id"] == "root-1"
    assert metadata["message_id"] == "BAE5"
    assert metadata["reply_to_message_id"] == "OLD"
    assert metadata["remote_jid"] == "120363@g.us"
    assert metadata["group_jid"] == "120363@g.us"
    assert metadata["participant"] == "15551234567@s.whatsapp.net"
    assert metadata["message_key_id"] == "BAE5"
    assert "response_url" not in metadata
    assert "raw_payload" not in metadata


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
    telegram.send = lambda chat_id, text, *, metadata=None: None
    telegram.send_image("c1", str(path), metadata={"source": "remote"})


def test_telegram_media_upload_retries_without_stale_topic(monkeypatch, tmp_path):
    from aegis.gateway import channels
    from aegis.gateway.channels import TelegramAdapter

    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    calls = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text
            self.request = channels.httpx.Request("POST", "https://api.telegram.org/bottoken/sendPhoto")

        def json(self):
            return {"description": self.text}

        def raise_for_status(self):
            if self.status_code >= 400:
                response = channels.httpx.Response(
                    self.status_code,
                    text=self.text,
                    request=self.request,
                )
                raise channels.httpx.HTTPStatusError(
                    self.text or "telegram error",
                    request=self.request,
                    response=response,
                )

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, data, files):
            calls.append((url, dict(data), sorted(files)))
            if len(calls) == 1:
                return FakeResponse(400, "Bad Request: message thread not found")
            return FakeResponse()

    monkeypatch.setattr(channels.httpx, "Client", FakeClient)

    adapter = TelegramAdapter("token")
    sent = []
    adapter.send = lambda chat_id, text, *, metadata=None: sent.append((chat_id, text, metadata))
    adapter.send_image("42", str(path), caption="cap", metadata={"message_thread_id": "gone"})

    assert calls == [
        (
            "https://api.telegram.org/bottoken/sendPhoto",
            {"chat_id": "42", "caption": "cap", "message_thread_id": "gone"},
            ["photo"],
        ),
        (
            "https://api.telegram.org/bottoken/sendPhoto",
            {"chat_id": "42", "caption": "cap"},
            ["photo"],
        ),
    ]
    assert sent == []


def test_telegram_media_upload_fallback_preserves_metadata(monkeypatch, tmp_path):
    from aegis.gateway import channels
    from aegis.gateway.channels import TelegramAdapter

    path = tmp_path / "doc.pdf"
    path.write_bytes(b"pdf")

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            raise RuntimeError("upload failed")

    monkeypatch.setattr(channels.httpx, "Client", FailingClient)

    adapter = TelegramAdapter("token")
    sent = []
    adapter.send = lambda chat_id, text, *, metadata=None: sent.append((chat_id, text, metadata))
    adapter.send_document("42", str(path), metadata={"message_thread_id": "77"})

    assert sent == [("42", f"📎 file ready: {path}", {"message_thread_id": "77"})]


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
    from aegis.platforms import (
        MAX_DISCORD_APP_COMMANDS,
        capped_command_menu,
        chunk_text_by_units,
        discord_application_command_menu,
        platform_metadata,
        utf16_units,
    )

    commands = capped_command_menu(["/custom", "/bad command", "/custom"], max_commands=4)
    assert commands == ["/help", "/whoami", "/status", "/stop"]

    many_commands = [f"/custom{i}" for i in range(MAX_DISCORD_APP_COMMANDS + 50)]
    discord_commands = discord_application_command_menu(many_commands, max_commands=999)
    assert len(discord_commands) == MAX_DISCORD_APP_COMMANDS
    assert len(set(discord_commands)) == MAX_DISCORD_APP_COMMANDS
    assert discord_commands[0] == "/help"
    assert discord_commands[-1].startswith("/custom")

    chunks = chunk_text_by_units("😀" * 5, limit=4, len_fn=utf16_units)
    assert chunks == ["😀😀", "😀😀", "😀"]

    assert platform_metadata("signal-cli")["id"] == "signal"
    assert platform_metadata("signal-cli")["supports_media"] is True
    assert "SIGNAL_IDEMPOTENCY_CACHE_MAX" in platform_metadata("signal-cli")["optional_env"]
    assert platform_metadata("signal-cli")["security"]["idempotency_env"] == [
        "SIGNAL_IDEMPOTENCY_TTL_SECONDS",
        "SIGNAL_IDEMPOTENCY_CACHE_MAX",
    ]
    assert platform_metadata("matrix")["transport"] == "matrix_sync"
    assert platform_metadata("matrix")["supports_threads"] is True
    assert platform_metadata("baileys")["id"] == "whatsapp"
    assert platform_metadata("whatsapp-web.js")["security"]["bridge"] == "webhook"
    assert platform_metadata("mail")["required_env"] == [
        "EMAIL_IMAP_HOST",
        "EMAIL_SMTP_HOST",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
    ]
    assert "EMAIL_ALLOWED_SENDERS" in platform_metadata("mail")["optional_env"]
    assert platform_metadata("ntfy.sh")["optional_env"] == [
        "NTFY_SERVER",
        "NTFY_TOKEN",
        "NTFY_IDEMPOTENCY_TTL_SECONDS",
        "NTFY_IDEMPOTENCY_CACHE_MAX",
    ]
    assert platform_metadata("ntfy.sh")["security"]["idempotency_env"] == [
        "NTFY_IDEMPOTENCY_TTL_SECONDS",
        "NTFY_IDEMPOTENCY_CACHE_MAX",
    ]
    assert "SLACK_TRIGGER_MODE" in platform_metadata("sl")["optional_env"]
    assert "SLACK_REPLY_IN_THREAD" in platform_metadata("sl")["optional_env"]
    assert "SLACK_BOT_ID" in platform_metadata("sl")["optional_env"]
    assert "SLACK_IDEMPOTENCY_CACHE_MAX" in platform_metadata("sl")["optional_env"]
    assert platform_metadata("sl")["supports_slash_commands"] is True
    assert platform_metadata("sl")["supports_reactions"] is True
    assert platform_metadata("sl")["security"]["idempotency_env"] == [
        "SLACK_IDEMPOTENCY_TTL_SECONDS",
        "SLACK_IDEMPOTENCY_CACHE_MAX",
        "SLACK_IDEMPOTENCY_PERSIST",
        "SLACK_IDEMPOTENCY_STORE_PATH",
    ]
    assert platform_metadata("tg")["supports_interactive_prompts"] is True
    assert "TELEGRAM_ALLOWED_TOPICS" in platform_metadata("tg")["optional_env"]
    assert "TELEGRAM_MEDIA_GROUP_COALESCE_SECONDS" in platform_metadata("tg")["optional_env"]
    assert "TELEGRAM_CALLBACK_TTL_SECONDS" in platform_metadata("tg")["optional_env"]
    assert "TELEGRAM_RATE_LIMIT_PER_MINUTE" in platform_metadata("tg")["optional_env"]
    assert "TELEGRAM_IDEMPOTENCY_PERSIST" in platform_metadata("tg")["optional_env"]
    assert platform_metadata("tg")["security"]["callback_ttl_env"] == "TELEGRAM_CALLBACK_TTL_SECONDS"
    assert platform_metadata("tg")["security"]["rate_limit_env"] == "TELEGRAM_RATE_LIMIT_PER_MINUTE"
    assert "TELEGRAM_IDEMPOTENCY_STORE_PATH" in platform_metadata("tg")["security"]["idempotency_env"]
    assert platform_metadata("tg")["security"]["callback_ttl_default_seconds"] == 3600
    assert platform_metadata("dc")["supports_reactions"] is True
    assert platform_metadata("dc")["supports_interactive_prompts"] is True
    assert "DISCORD_IDEMPOTENCY_STORE_PATH" in platform_metadata("dc")["optional_env"]
    assert platform_metadata("dc")["security"]["idempotency_env"] == [
        "DISCORD_IDEMPOTENCY_TTL_SECONDS",
        "DISCORD_IDEMPOTENCY_CACHE_MAX",
        "DISCORD_IDEMPOTENCY_PERSIST",
        "DISCORD_IDEMPOTENCY_STORE_PATH",
    ]
    mattermost_meta = platform_metadata("mattermost-webhook")
    assert mattermost_meta["security"]["auth_type"] == "bearer"
    assert mattermost_meta["supports_media"] is True
    assert mattermost_meta["supports_interactive_prompts"] is True
    assert "MATTERMOST_ACTION_URL" in mattermost_meta["optional_env"]
    assert mattermost_meta["security"]["action_url_env"] == "MATTERMOST_ACTION_URL"
    assert "MATTERMOST_RATE_LIMIT_PER_MINUTE" in mattermost_meta["optional_env"]
    assert mattermost_meta["security"]["idempotency_env"] == [
        "MATTERMOST_IDEMPOTENCY_TTL_SECONDS",
        "MATTERMOST_IDEMPOTENCY_CACHE_MAX",
        "MATTERMOST_IDEMPOTENCY_PERSIST",
        "MATTERMOST_IDEMPOTENCY_STORE_PATH",
    ]
    webhook_meta = platform_metadata("webhooks")
    assert webhook_meta["supports_threads"] is True
    assert "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE" in webhook_meta["optional_env"]
    assert "WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK" in webhook_meta["optional_env"]
    assert "WEBHOOK_CHANNEL_IDEMPOTENCY_STORE_PATH" in webhook_meta["optional_env"]
    assert "X-Webhook-Signature" in webhook_meta["security"]["signature_schemes"]
    api_meta = platform_metadata("api-server")
    assert api_meta["id"] == "api_server"
    assert api_meta["transport"] == "aiohttp"
    assert "API_SERVER_MAX_CONCURRENT_RUNS" in api_meta["optional_env"]
    assert api_meta["security"]["gateway_config"] == "gateway.api_server"
    assert api_meta["env_bridge"]["api_key"] == "API_SERVER_KEY"
    assert api_meta["env_bridge"]["api_key_legacy"] == "API_SERVER_API_KEY"
    assert api_meta["server_config_bridge"]["model_name"] == "model.default"
    assert api_meta["sender_hooks"] == ["responses_stream", "run_events", "gateway_proxy_chat_completions"]
    cloud_meta = platform_metadata("whatsapp-cloud")
    assert cloud_meta["id"] == "whatsapp_cloud"
    assert cloud_meta["transport"] == "http_bridge"
    assert "WHATSAPP_CLOUD_CHANNEL_SECRET" in cloud_meta["optional_env"]
    assert "interactive_prompts" in cloud_meta["bridge_capabilities"]
    assert cloud_meta["setup_hooks"] == ["env_bridge", "health_probe"]
    assert cloud_meta["cron_delivery_hooks"] == ["deliver_target"]
    assert cloud_meta["sender_hooks"] == ["outbound_webhook"]


def test_api_server_gateway_adapter_metadata_and_lifecycle(tmp_path, monkeypatch):
    import httpx
    from aegis.config import Config
    from aegis.gateway.channels import ApiServerChannel, build_adapter

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    config = Config.load()
    config.data.setdefault("gateway", {}).setdefault("api_server", {})["port"] = 0
    adapter = ApiServerChannel(config)
    assert build_adapter("api-server").name == "api_server"
    assert adapter.metadata["config_path"] == "gateway.api_server"
    assert adapter.metadata["transport"] == "aiohttp"

    thread = threading.Thread(target=adapter.start, args=(lambda _ev: "",), daemon=True)
    thread.start()
    assert adapter._started.wait(5)
    try:
        response = httpx.get(f"http://{adapter.host}:{adapter.port}/v1/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["ok"] is True
    finally:
        adapter.stop()
        thread.join(5)
    assert not thread.is_alive()


def test_gateway_profile_lookup_normalizes_platform_aliases():
    from aegis.config import Config
    from aegis.gateway.runner import _gateway_profile_for_platform

    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["profiles"] = {
        "wa": {"personality": "phone"},
        "tg": {"model": "telegram-model"},
    }

    assert _gateway_profile_for_platform(cfg, "whatsapp") == {"personality": "phone"}
    assert _gateway_profile_for_platform(cfg, "baileys") == {"personality": "phone"}
    assert _gateway_profile_for_platform(cfg, "telegram-bot") == {"model": "telegram-model"}
    assert _gateway_profile_for_platform(cfg, "discord") == {}


def test_adapter_metadata_for_core_platforms(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.discord_channel import DiscordAdapter
    from aegis.gateway.mattermost_channel import MattermostAdapter
    from aegis.gateway.slack_channel import SlackAdapter
    from aegis.gateway.webhook_channel import WebhookChannel
    from aegis.platforms.helpers import platform_metadata

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
        "TELEGRAM_GROUP_TRIGGER_MODE",
        "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_BOT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    for key in (
        "DISCORD_ALLOWED_USERS",
        "DISCORD_ALLOWED_ROLES",
        "DISCORD_ALLOWED_GUILDS",
        "DISCORD_IGNORED_GUILDS",
        "DISCORD_TRIGGER_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("WEBHOOK_CHANNEL_SECRET", raising=False)

    assert TelegramAdapter("token").metadata["transport"] == "long_poll"
    assert "TELEGRAM_ALLOWED_CHATS" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_ALLOWED_TOPICS" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_MEDIA_GROUP_COALESCE_SECONDS" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_REGISTER_COMMANDS" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_CALLBACK_TTL_SECONDS" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_RATE_LIMIT_PER_MINUTE" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_IDEMPOTENCY_CACHE_MAX" in TelegramAdapter("token").metadata["optional_env"]
    assert "TELEGRAM_IDEMPOTENCY_PERSIST" in TelegramAdapter("token").metadata["optional_env"]
    assert TelegramAdapter("token").metadata["security"]["group_trigger_mode"] == "all"
    assert TelegramAdapter("token").metadata["security"]["allow_general_topic"] is True
    assert TelegramAdapter("token").metadata["security"]["register_commands"] is True
    assert TelegramAdapter("token").metadata["security"]["callback_ttl_seconds"] == 3600
    assert TelegramAdapter("token").metadata["security"]["callback_ttl_env"] == "TELEGRAM_CALLBACK_TTL_SECONDS"
    assert TelegramAdapter("token").metadata["security"]["rate_limit_env"] == "TELEGRAM_RATE_LIMIT_PER_MINUTE"
    assert TelegramAdapter("token").metadata["security"]["idempotency_env"] == [
        "TELEGRAM_IDEMPOTENCY_TTL_SECONDS",
        "TELEGRAM_IDEMPOTENCY_CACHE_MAX",
        "TELEGRAM_IDEMPOTENCY_PERSIST",
        "TELEGRAM_IDEMPOTENCY_STORE_PATH",
    ]
    assert TelegramAdapter("token").metadata["idempotency"]["delivery_cache"]["entries"] == 0
    assert TelegramAdapter("token").metadata["idempotency"]["persistent"] is True
    assert TelegramAdapter("token").metadata["idempotency"]["delivery_store"]["entries"] == 0
    assert TelegramAdapter("token").metadata["rate_limiter"]["limit"] == 60
    assert TelegramAdapter("token").metadata["supports_reactions"] is True
    assert TelegramAdapter("token").metadata["supports_interactive_prompts"] is True
    assert TelegramAdapter("token").metadata["supports_slash_commands"] is True
    assert TelegramAdapter("token").metadata["splits_long_messages"] is True
    assert DiscordAdapter("token").metadata["supports_threads"] is True
    assert DiscordAdapter("token").metadata["supports_reactions"] is True
    assert DiscordAdapter("token").metadata["supports_interactive_prompts"] is True
    assert DiscordAdapter("token").metadata["splits_long_messages"] is True
    assert DiscordAdapter("token").metadata["command_cap"] == 100
    assert "DISCORD_ALLOWED_GUILDS" in DiscordAdapter("token").metadata["optional_env"]
    assert DiscordAdapter("token").metadata["security"]["trigger_mode"] == "all"
    assert DiscordAdapter("token").metadata["security"]["idempotency_env"] == [
        "DISCORD_IDEMPOTENCY_TTL_SECONDS",
        "DISCORD_IDEMPOTENCY_CACHE_MAX",
        "DISCORD_IDEMPOTENCY_PERSIST",
        "DISCORD_IDEMPOTENCY_STORE_PATH",
    ]
    assert "DISCORD_IDEMPOTENCY_STORE_PATH" in DiscordAdapter("token").metadata["optional_env"]
    assert DiscordAdapter("token").metadata["idempotency"]["persistent"] is True
    assert DiscordAdapter("token").metadata["idempotency"]["delivery_id_sources"] == [
        "message.id",
        "interaction.id",
    ]
    assert len(DiscordAdapter("token").command_menu(max_commands=500)) <= 100
    assert SlackAdapter().metadata["typed_command_prefix"] == "!"
    assert SlackAdapter().metadata["supports_reactions"] is True
    assert SlackAdapter().metadata["supports_media"] is True
    assert SlackAdapter().metadata["splits_long_messages"] is True
    assert "SLACK_ALLOWED_CHANNELS" in SlackAdapter().metadata["optional_env"]
    assert "SLACK_TRIGGER_MODE" in SlackAdapter().metadata["optional_env"]
    assert "SLACK_IDEMPOTENCY_CACHE_MAX" in SlackAdapter().metadata["optional_env"]
    assert "SLACK_IDEMPOTENCY_STORE_PATH" in SlackAdapter().metadata["optional_env"]
    assert SlackAdapter().metadata["security"]["trigger_mode"] == "all"
    assert SlackAdapter().metadata["security"]["idempotency_env"] == [
        "SLACK_IDEMPOTENCY_TTL_SECONDS",
        "SLACK_IDEMPOTENCY_CACHE_MAX",
        "SLACK_IDEMPOTENCY_PERSIST",
        "SLACK_IDEMPOTENCY_STORE_PATH",
    ]
    assert SlackAdapter().metadata["idempotency"]["persistent"] is True
    assert platform_metadata("slack")["supports_media"] is True
    mattermost = MattermostAdapter().metadata
    assert mattermost["transport"] == "http_webhook"
    assert mattermost["supports_threads"] is True
    assert mattermost["supports_media"] is True
    assert mattermost["supports_reactions"] is True
    assert mattermost["supports_interactive_prompts"] is True
    assert mattermost["splits_long_messages"] is True
    assert mattermost["security"]["action_url_configured"] is False
    assert mattermost["security"]["auth_type"] == "bearer"
    assert "MATTERMOST_IDEMPOTENCY_STORE_PATH" in mattermost["optional_env"]
    assert mattermost["security"]["idempotency_env"] == [
        "MATTERMOST_IDEMPOTENCY_TTL_SECONDS",
        "MATTERMOST_IDEMPOTENCY_CACHE_MAX",
        "MATTERMOST_IDEMPOTENCY_PERSIST",
        "MATTERMOST_IDEMPOTENCY_STORE_PATH",
    ]
    assert mattermost["idempotency"]["persistent"] is True
    webhook = WebhookChannel().metadata
    assert webhook["transport"] == "http"
    assert webhook["supports_threads"] is True
    assert webhook["supports_reactions"] is True
    assert webhook["security"]["secret_configured"] is False
    assert "WEBHOOK_CHANNEL_IDEMPOTENCY_STORE_PATH" in webhook["optional_env"]
    assert "WEBHOOK_CHANNEL_IDEMPOTENCY_STORE_PATH" in webhook["security"]["idempotency_env"]
    assert "X-Secret" in webhook["security"]["signature_schemes"]
    assert webhook["idempotency"]["delivery_cache"]["entries"] == 0
    assert webhook["idempotency"]["persistent"] is True
    assert webhook["idempotency"]["delivery_store"]["entries"] == 0
    assert webhook["rate_limiter"]["limit"] >= 1

    from aegis.gateway.channels import build_adapter
    whatsapp = build_adapter("wa")
    assert whatsapp.name == "whatsapp"
    assert whatsapp.metadata["id"] == "whatsapp"
    assert whatsapp.metadata["transport"] == "http_bridge"
    assert whatsapp.metadata["security"]["env_prefix"] == "WHATSAPP_CHANNEL"
    assert whatsapp.port == 18792


def test_platform_adapters_send_native_reactions(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from aegis.gateway import discord_channel
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.discord_channel import DiscordAdapter
    from aegis.gateway.slack_channel import SlackAdapter

    calls = []
    telegram = TelegramAdapter("token")
    telegram._api = lambda method, **params: calls.append((method, params)) or {"ok": True}

    telegram.add_reaction("42", "101", "👍")
    telegram.remove_reaction("42", "101", "👍")

    assert calls[0][0] == "setMessageReaction"
    assert calls[0][1]["chat_id"] == "42"
    assert calls[0][1]["message_id"] == "101"
    assert json.loads(calls[0][1]["reaction"]) == [{"type": "emoji", "emoji": "👍"}]
    assert calls[1] == (
        "setMessageReaction",
        {"chat_id": "42", "message_id": "101", "reaction": "[]"},
    )

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    slack_calls = []

    class FakeSlackClient:
        def reactions_add(self, **kwargs):
            slack_calls.append(("add", kwargs))

        def reactions_remove(self, **kwargs):
            slack_calls.append(("remove", kwargs))

    slack = SlackAdapter()
    slack._app = SimpleNamespace(client=FakeSlackClient())

    slack.add_reaction("C1", "171.1", "✅")
    slack.remove_reaction("C1", "171.1", ":eyes:")

    assert slack_calls == [
        ("add", {"channel": "C1", "timestamp": "171.1", "name": "white_check_mark"}),
        ("remove", {"channel": "C1", "timestamp": "171.1", "name": "eyes"}),
    ]

    discord_calls = []

    class FakeFuture:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):  # noqa: ARG002
            return self.value

    def run_coroutine_threadsafe(coro, loop):  # noqa: ARG001
        return FakeFuture(asyncio.run(coro))

    class FakeMessage:
        async def add_reaction(self, reaction):
            discord_calls.append(("add", reaction))

        async def clear_reaction(self, reaction):
            discord_calls.append(("clear", reaction))

    class FakeChannel:
        async def fetch_message(self, message_id):
            discord_calls.append(("fetch", message_id))
            return FakeMessage()

    class FakeDiscordClient:
        user = object()

        def get_channel(self, channel_id):
            discord_calls.append(("channel", channel_id))
            return FakeChannel()

    monkeypatch.setattr(discord_channel.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)

    discord = DiscordAdapter("discord-token")
    discord._client = FakeDiscordClient()
    discord._loop = object()
    discord.add_reaction("99", "123", "🚀")
    discord.remove_reaction("99", "123", "🚀")
    discord.add_reaction("99", "124", "✅", metadata={"thread_id": "100"})
    discord.remove_reaction("99", "124", "✅", metadata={"thread_id": "100"})

    assert discord_calls == [
        ("channel", 99),
        ("fetch", 123),
        ("add", "🚀"),
        ("channel", 99),
        ("fetch", 123),
        ("clear", "🚀"),
        ("channel", 100),
        ("fetch", 124),
        ("add", "✅"),
        ("channel", 100),
        ("fetch", 124),
        ("clear", "✅"),
    ]


def test_discord_adapter_enforces_guild_filters_and_trigger_mode(monkeypatch):
    from aegis.gateway.discord_channel import DiscordAdapter

    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    bot = Obj(id="BOT")
    client = Obj(user=bot)

    def message(content="hello", *, guild_id="G1", mentions=None, reply_author_id="", channel_id="C1"):
        reference = Obj(resolved=Obj(author=Obj(id=reply_author_id))) if reply_author_id else None
        return Obj(
            content=content,
            guild=Obj(id=guild_id) if guild_id else None,
            channel=Obj(id=channel_id, parent=None, name="ops"),
            author=Obj(id="U1", roles=[], bot=False),
            mentions=mentions or [],
            reference=reference,
            type=None,
        )

    monkeypatch.setenv("DISCORD_ALLOWED_GUILDS", "G1,dm")
    monkeypatch.setenv("DISCORD_IGNORED_GUILDS", "G9")
    monkeypatch.setenv("DISCORD_TRIGGER_MODE", "addressed")

    adapter = DiscordAdapter("token")

    assert adapter._guild_allowed(message(guild_id="G1")) is True
    assert adapter._guild_allowed(message(guild_id="G2")) is False
    assert adapter._guild_allowed(message(guild_id="G9")) is False
    assert adapter._guild_allowed(message(guild_id="")) is True
    assert adapter._trigger_allowed(message("plain", guild_id="G1"), client) is False
    assert adapter._trigger_allowed(message("!status", guild_id="G1"), client) is True
    assert adapter._trigger_allowed(message("hello", guild_id="G1", mentions=[bot]), client) is True
    assert adapter._trigger_allowed(message("hello", guild_id="G1", reply_author_id="BOT"), client) is True
    assert adapter._trigger_allowed(message("plain dm", guild_id=""), client) is True
    category = Obj(id="CAT", type=Obj(name="category"))
    text_channel = Obj(id="C1", parent=category, type=Obj(name="text"))
    thread_channel = Obj(id="T1", parent=Obj(id="C1"), type=Obj(name="public_thread"))
    assert adapter._chat_and_thread_ids_from_channel(text_channel) == ("C1", None)
    assert adapter._chat_and_thread_ids_from_channel(thread_channel) == ("C1", "T1")

    attachment = Obj(
        id="A1",
        filename="voice.ogg",
        url="https://cdn.discord.test/voice.ogg",
        proxy_url="https://proxy.discord.test/voice.ogg",
        content_type="audio/ogg",
        size=12345,
        description="voice memo",
    )
    rows = adapter._attachments_from_message(Obj(attachments=[attachment]))
    assert rows == [{
        "id": "A1",
        "type": "audio/ogg",
        "media_type": "audio/ogg",
        "filename": "voice.ogg",
        "url": "https://cdn.discord.test/voice.ogg",
        "proxy_url": "https://proxy.discord.test/voice.ogg",
        "size": 12345,
        "description": "voice memo",
    }]
    assert adapter._attachment_reference_text(rows) == "[audio/ogg attached: voice.ogg]"


def test_discord_role_allowlist_is_scoped_to_active_guild(monkeypatch):
    from aegis.gateway.discord_channel import DiscordAdapter

    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    for key in (
        "DISCORD_ALLOWED_USERS",
        "DISCORD_ALLOWED_ROLES",
        "DISCORD_ALLOW_ROLE_AUTH_IN_DMS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_ALLOWED_ROLES", "R1")
    adapter = DiscordAdapter("token")
    role = Obj(id="R1")
    member = Obj(id="U1", roles=[role], guild=Obj(id="G1"), bot=False)

    assert adapter._author_allowed(Obj(author=member, guild=Obj(id="G1"))) is True
    assert adapter._author_allowed(Obj(author=member, guild=Obj(id="G2"))) is False
    assert adapter._author_allowed(Obj(author=member, guild=None)) is False

    monkeypatch.setenv("DISCORD_ALLOW_ROLE_AUTH_IN_DMS", "1")
    dm_adapter = DiscordAdapter("token")
    assert dm_adapter._author_allowed(Obj(author=member, guild=None)) is True

    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "U1")
    user_adapter = DiscordAdapter("token")
    assert user_adapter._author_allowed(Obj(author=Obj(id="U1", roles=[], bot=False), guild=None)) is True


def test_discord_adapter_registers_and_handles_app_commands(monkeypatch):
    import asyncio
    import inspect
    import sys
    import types
    from types import SimpleNamespace

    from aegis.gateway import discord_channel
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.discord_channel import DiscordAdapter

    for key in (
        "DISCORD_ALLOWED_GUILDS",
        "DISCORD_ALLOWED_CHANNELS",
        "DISCORD_ALLOWED_USERS",
        "DISCORD_ALLOWED_ROLES",
        "DISCORD_TRIGGER_MODE",
    ):
        monkeypatch.delenv(key, raising=False)

    configured = DiscordAdapter("token")

    class FakeConfig:
        def get(self, dotted, default=None):
            if dotted == "gateway.user_commands":
                return ["/deploy"]
            return default

    configured._config = FakeConfig()
    assert "/deploy" in configured.command_menu(max_commands=50)

    adapter = DiscordAdapter("token")
    adapter.command_menu = lambda max_commands=100: ["/help", "/model"]  # noqa: ARG005
    registered = []
    described = []

    class FakeTree:
        def __init__(self, client):
            self.client = client
            self.callbacks = {}

        def command(self, *, name, description):
            registered.append((name, description))

            def decorator(callback):
                registered.append(("callback", name, callback.__name__))
                self.callbacks[name] = callback
                return callback

            return decorator

    def describe(**kwargs):
        def decorator(callback):
            described.append((callback.__name__, kwargs))
            return callback

        return decorator

    fake_discord = SimpleNamespace(
        app_commands=SimpleNamespace(CommandTree=lambda client: FakeTree(client), describe=describe),
    )
    tree = adapter._build_command_tree(fake_discord, object())

    assert isinstance(tree, FakeTree)
    assert registered == [
        ("help", "AEGIS help"),
        ("callback", "help", "aegis_help"),
        ("model", "AEGIS model"),
        ("callback", "model", "aegis_model"),
    ]
    assert described == [("aegis_model", {"args": "Optional text for the AEGIS command."})]
    assert "args" in inspect.signature(tree.callbacks["model"]).parameters

    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    deferred = []

    class Response:
        async def defer(self, *, thinking=False):
            deferred.append(thinking)

    channel = SimpleNamespace(id=99, name="ops", parent=None)
    interaction = SimpleNamespace(
        id=123,
        response=Response(),
        channel=channel,
        guild=SimpleNamespace(id=456),
        user=SimpleNamespace(id=7, __str__=lambda self: "ada"),
        created_at="now",
    )

    ev = asyncio.run(adapter._handle_app_command(interaction, "status"))

    assert deferred == [True]
    assert ev.platform == "discord"
    assert ev.chat_id == "99"
    assert ev.text == "/status"
    assert ev.user_id == "7"
    assert ev.message_id == "123"
    assert ev.metadata["source"] == "app_command"
    assert ev.metadata["command"] == "/status"
    assert ev.metadata["args"] == ""
    assert seen == [(ev, "/status")]

    model_interaction = SimpleNamespace(
        id=124,
        response=Response(),
        channel=channel,
        guild=SimpleNamespace(id=456),
        user=SimpleNamespace(id=7, __str__=lambda self: "ada"),
        created_at="now",
        data={"options": [{"name": "args", "value": "openai/gpt-5"}]},
    )
    model_ev = asyncio.run(adapter._handle_app_command(model_interaction, "model"))

    assert model_ev.text == "/model openai/gpt-5"
    assert model_ev.metadata["command"] == "/model"
    assert model_ev.metadata["args"] == "openai/gpt-5"
    assert seen[-1] == (model_ev, "/model openai/gpt-5")

    class FakeFuture:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):  # noqa: ARG002
            return self.value

    def run_coroutine_threadsafe(coro, loop):  # noqa: ARG001
        return FakeFuture(asyncio.run(coro))

    monkeypatch.setitem(sys.modules, "discord", types.SimpleNamespace(
        AllowedMentions=types.SimpleNamespace(none=lambda: "none"),
    ))
    monkeypatch.setattr(discord_channel.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)
    followups = []

    class Followup:
        async def send(self, *args, **kwargs):
            followups.append((args, kwargs))

    reply_ev = MessageEvent(platform="discord", chat_id="99", text="/status")
    reply_ev._discord_interaction = SimpleNamespace(followup=Followup(), channel=channel)
    reply_ev._discord_loop = object()
    adapter._deliver_reply(reply_ev, "done")

    assert followups == [(("done",), {"allowed_mentions": "none"})]


def test_discord_app_and_component_idempotency_reopens_on_failure():
    import asyncio
    from types import SimpleNamespace

    import pytest

    from aegis.gateway.discord_channel import DiscordAdapter

    adapter = DiscordAdapter("token")
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    deferred = []

    class Response:
        async def defer(self, *args, **kwargs):  # noqa: ANN002, ANN003
            deferred.append((args, kwargs))

    channel = SimpleNamespace(id="C1", name="ops", parent=None)
    interaction = SimpleNamespace(
        id=123,
        response=Response(),
        channel=channel,
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U1", roles=[], bot=False),
        created_at="now",
    )

    ev = asyncio.run(adapter._handle_app_command(interaction, "status"))
    duplicate = asyncio.run(adapter._handle_app_command(interaction, "status"))

    assert duplicate is None
    assert seen == [(ev, "/status")]
    assert ev.metadata["delivery_id"] == "app_command:C1:123"
    assert adapter._delivery_cache.stats()["accepted_count"] == 1
    assert adapter._delivery_cache.stats()["duplicate_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = DiscordAdapter("token")
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    restart_duplicate = asyncio.run(after_restart._handle_app_command(interaction, "status"))
    assert restart_duplicate is None
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1

    component = SimpleNamespace(
        id=124,
        response=Response(),
        channel=channel,
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U1", roles=[], bot=False),
        created_at="now",
    )
    component_ev = asyncio.run(adapter._handle_component_interaction(
        component,
        "approve",
        action_id="aegis_exec_approval_0",
        action_type="exec_approval",
    ))
    component_duplicate = asyncio.run(adapter._handle_component_interaction(
        component,
        "approve",
        action_id="aegis_exec_approval_0",
        action_type="exec_approval",
    ))

    assert component_duplicate is None
    assert seen[-1] == (component_ev, "approve")
    assert component_ev.metadata["delivery_id"] == "component_interaction:C1:124:aegis_exec_approval_0"
    assert adapter._delivery_cache.stats()["duplicate_count"] == 2
    component_restart_duplicate = asyncio.run(after_restart._handle_component_interaction(
        component,
        "approve",
        action_id="aegis_exec_approval_0",
        action_type="exec_approval",
    ))
    assert component_restart_duplicate is None
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 2

    def fail_once(_ev, *, raw_text=None):  # noqa: ANN001, ARG001
        raise RuntimeError("discord dispatch down")

    adapter._submit_inbound = fail_once
    failing = SimpleNamespace(
        id=125,
        response=Response(),
        channel=channel,
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U1", roles=[], bot=False),
        created_at="now",
    )
    with pytest.raises(RuntimeError, match="discord dispatch down"):
        asyncio.run(adapter._handle_app_command(failing, "status"))

    assert adapter._delivery_cache.stats()["discarded_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    retry_ev = asyncio.run(adapter._handle_app_command(failing, "status"))
    assert retry_ev is not None
    assert seen[-1] == (retry_ev, "/status")


def test_discord_app_commands_respect_security_filters(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from aegis.gateway.discord_channel import DiscordAdapter

    monkeypatch.setenv("DISCORD_ALLOWED_GUILDS", "G1")
    monkeypatch.setenv("DISCORD_ALLOWED_CHANNELS", "C1")
    monkeypatch.setenv("DISCORD_IGNORED_CHANNELS", "C9")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "U1")
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")

    adapter = DiscordAdapter("token")
    security = adapter.metadata["security"]
    assert security["allowed_channels_configured"] is True
    assert security["ignored_channels_configured"] is True
    assert security["bot_policy"] == "mentions"
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    deferred = []
    denied = []

    class Response:
        async def defer(self, *, thinking=False):
            deferred.append(thinking)

        async def send_message(self, text, *, ephemeral=False):
            denied.append((text, ephemeral))

    def interaction(*, guild_id="G1", channel_id="C1", user_id="U1", parent_id=None):
        parent = SimpleNamespace(id=parent_id) if parent_id else None
        return SimpleNamespace(
            id=123,
            response=Response(),
            channel=SimpleNamespace(id=channel_id, name="ops", parent=parent),
            guild=SimpleNamespace(id=guild_id) if guild_id else None,
            user=SimpleNamespace(id=user_id, roles=[], bot=False),
            created_at="now",
        )

    assert asyncio.run(adapter._handle_app_command(interaction(guild_id="G2"), "status")) is None
    assert asyncio.run(adapter._handle_app_command(interaction(channel_id="C2"), "status")) is None
    assert asyncio.run(adapter._handle_app_command(interaction(user_id="U2"), "status")) is None
    ev = asyncio.run(adapter._handle_app_command(interaction(), "status"))

    assert denied == [
        ("Not authorized.", True),
        ("Not authorized.", True),
        ("Not authorized.", True),
    ]
    assert ev is not None
    assert ev.chat_id == "C1"
    assert ev.text == "/status"
    assert deferred == [True]
    assert seen == [(ev, "/status")]


def test_discord_adapter_interactive_prompts_and_callbacks(monkeypatch):
    import asyncio
    import sys
    import types
    from types import SimpleNamespace

    from aegis.gateway import discord_channel
    from aegis.gateway.discord_channel import DiscordAdapter

    class FakeFuture:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):  # noqa: ARG002
            return self.value

    def run_coroutine_threadsafe(coro, loop):  # noqa: ARG001
        return FakeFuture(asyncio.run(coro))

    class FakeButton:
        def __init__(self, *, label, style, custom_id):
            self.label = label
            self.style = style
            self.custom_id = custom_id
            self.callback = None

    class FakeView:
        def __init__(self, *, timeout=None):
            self.timeout = timeout
            self.items = []

        def add_item(self, item):
            self.items.append(item)

    monkeypatch.setitem(sys.modules, "discord", types.SimpleNamespace(
        AllowedMentions=types.SimpleNamespace(none=lambda: "none"),
        ButtonStyle=types.SimpleNamespace(
            primary="primary",
            secondary="secondary",
            success="success",
            danger="danger",
        ),
        ui=types.SimpleNamespace(View=FakeView, Button=FakeButton),
    ))
    monkeypatch.setattr(discord_channel.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)

    class FakeChannel:
        def __init__(self, channel_id="C1", *, parent=None):
            self.id = channel_id
            self.name = "ops"
            self.parent = parent
            self.sent = []

        async def send(self, *args, **kwargs):
            self.sent.append((args, kwargs))
            return {"ok": True}

    class FakeClient:
        def __init__(self):
            self.channels = {10: FakeChannel("10")}

        def get_channel(self, channel_id):
            return self.channels.get(channel_id)

        async def fetch_channel(self, channel_id):
            return self.channels[channel_id]

    adapter = DiscordAdapter("token")
    adapter._client = FakeClient()
    adapter._loop = object()
    adapter.send_clarify("10", "Pick a deploy lane?", ["stable", "canary"])
    adapter.send_exec_approval("10", "Run deploy?")

    sent = adapter._client.channels[10].sent
    assert sent[0][0] == ("Pick a deploy lane?",)
    assert sent[0][1]["allowed_mentions"] == "none"
    assert [button.label for button in sent[0][1]["view"].items] == ["stable", "canary"]
    assert [button.custom_id for button in sent[0][1]["view"].items] == ["aegis_clarify_0", "aegis_clarify_1"]
    assert [button.style for button in sent[0][1]["view"].items] == ["secondary", "secondary"]
    assert sent[1][0] == ("Run deploy?",)
    assert [button.label for button in sent[1][1]["view"].items] == ["Approve", "Always", "Deny"]
    assert [button.custom_id for button in sent[1][1]["view"].items] == [
        "aegis_exec_approval_0",
        "aegis_exec_approval_1",
        "aegis_exec_approval_2",
    ]
    assert [button.style for button in sent[1][1]["view"].items] == ["success", "primary", "danger"]

    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    deferred = []
    denied = []

    class Response:
        async def defer(self, *args, **kwargs):  # noqa: ANN002, ANN003
            deferred.append((args, kwargs))

        async def send_message(self, text, *, ephemeral=False):
            denied.append((text, ephemeral))

    allowed = SimpleNamespace(
        id=321,
        response=Response(),
        channel=FakeChannel("10"),
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U1", roles=[], bot=False),
        created_at="now",
    )
    ev = asyncio.run(adapter._handle_component_interaction(
        allowed,
        "approve",
        action_id="aegis_exec_approval_0",
        action_type="exec_approval",
    ))

    assert deferred == [((), {})]
    assert ev.platform == "discord"
    assert ev.chat_id == "10"
    assert ev.text == "approve"
    assert ev.user_id == "U1"
    assert ev.message_id == "321"
    assert ev.metadata["source"] == "component_interaction"
    assert ev.metadata["action_id"] == "aegis_exec_approval_0"
    assert ev.metadata["action_type"] == "exec_approval"
    assert seen == [(ev, "approve")]

    bound = SimpleNamespace(
        id=323,
        response=Response(),
        channel=FakeChannel("10"),
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U1", roles=[], bot=False),
        created_at="now",
    )
    bound_ev = asyncio.run(adapter._handle_component_interaction(
        bound,
        "stable",
        action_id="aegis_clarify_0",
        action_type="clarify",
        prompt_id="clarify:prompt-1",
        prompt_kind="clarify",
    ))
    assert bound_ev.metadata["prompt_id"] == "clarify:prompt-1"
    assert bound_ev.metadata["prompt_kind"] == "clarify"

    asyncio.run(sent[0][1]["view"].items[1].callback(allowed))
    assert seen[-1][0].text == "canary"
    assert seen[-1][0].metadata["action_id"] == "aegis_clarify_1"
    assert seen[-1][0].metadata["action_type"] == "clarify"
    assert seen[-1][1] == "canary"

    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "U1")
    guarded = DiscordAdapter("token")
    guarded._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    blocked = SimpleNamespace(
        id=322,
        response=Response(),
        channel=FakeChannel("10"),
        guild=SimpleNamespace(id="G1"),
        user=SimpleNamespace(id="U2", roles=[], bot=False),
        created_at="now",
    )
    assert asyncio.run(guarded._handle_component_interaction(
        blocked,
        "deny",
        action_id="aegis_exec_approval_2",
        action_type="exec_approval",
    )) is None
    assert denied == [("Not authorized.", True)]


def test_discord_adapter_native_media_upload_targets_threads(monkeypatch, tmp_path):
    import asyncio
    import pytest
    import sys
    import types

    from aegis.gateway.discord_channel import DiscordAdapter

    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    missing = tmp_path / "missing.png"

    monkeypatch.setitem(sys.modules, "discord", types.SimpleNamespace(
        File=lambda p: {"file": str(p)},
        AllowedMentions=types.SimpleNamespace(none=lambda: "none"),
    ))

    class FakeChannel:
        def __init__(self, *, fail_upload=False):
            self.fail_upload = fail_upload
            self.sent = []

        async def send(self, *args, **kwargs):
            if self.fail_upload and "file" in kwargs:
                raise RuntimeError("upload failed")
            self.sent.append((args, kwargs))
            return {"ok": True}

    class FakeClient:
        def __init__(self):
            self.channels = {
                10: FakeChannel(),
                20: FakeChannel(),
                30: FakeChannel(fail_upload=True),
            }
            self.fetched = []

        def get_channel(self, channel_id):
            return self.channels.get(channel_id)

        async def fetch_channel(self, channel_id):
            self.fetched.append(channel_id)
            return self.channels[channel_id]

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert ready.wait(2)
    try:
        adapter = DiscordAdapter("token")
        adapter._client = FakeClient()
        adapter._loop = loop

        adapter.send("10", "stale thread fallback", metadata={"thread_id": "99"})
        assert adapter._client.channels[10].sent == [
            (("stale thread fallback",), {"allowed_mentions": "none"}),
        ]

        with pytest.raises(KeyError):
            adapter.send("404", "missing target", metadata={"thread_id": "99"})

        adapter.send_image("10", str(path), caption="cap", metadata={"thread_id": "20"})
        assert adapter._client.channels[20].sent == [
            ((), {"file": {"file": str(path)}, "content": "cap", "allowed_mentions": "none"}),
        ]
        assert adapter._client.channels[10].sent == [
            (("stale thread fallback",), {"allowed_mentions": "none"}),
        ]

        adapter.send_document("10", str(missing), caption="missing", metadata={"thread_id": "20"})
        assert adapter._client.channels[20].sent[-1] == (
            (f"missing\n(file not found: {missing})",),
            {"allowed_mentions": "none"},
        )

        adapter.send_image("10", str(path), caption="retry", metadata={"thread_id": "30"})
        assert adapter._client.channels[30].sent == [
            ((f"retry\n📎 {path}",), {"allowed_mentions": "none"}),
        ]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


def test_discord_reply_media_uses_shared_safety_gate(monkeypatch, tmp_path):
    import asyncio
    import sys
    import types

    from aegis.gateway.base import MessageEvent
    from aegis.gateway.discord_channel import DiscordAdapter

    path = tmp_path / "secret.png"
    path.write_bytes(b"png")

    monkeypatch.setitem(sys.modules, "discord", types.SimpleNamespace(
        File=lambda p: {"file": str(p)},
        AllowedMentions=types.SimpleNamespace(none=lambda: "none"),
    ))

    class FakeChannel:
        def __init__(self):
            self.sent = []

        async def send(self, *args, **kwargs):
            self.sent.append((args, kwargs))
            return {"ok": True}

        def typing(self):
            raise RuntimeError("not used")

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert ready.wait(2)
    try:
        channel = FakeChannel()
        adapter = DiscordAdapter("token")
        adapter._client = object()
        adapter._loop = loop
        adapter.filter_media_path = lambda _path: (False, "outside workspace")
        ev = MessageEvent(platform="discord", chat_id="10", text="")
        ev._discord_channel = channel
        ev._discord_loop = loop

        adapter._deliver_reply(ev, f"report\nMEDIA:{path}")

        assert channel.sent == [
            (("report",), {"allowed_mentions": "none"}),
            (("📎 blocked media path: outside workspace",), {"allowed_mentions": "none"}),
        ]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


def test_telegram_adapter_enforces_chat_filters_and_group_addressing(monkeypatch):
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7,@ada")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHATS", "42")
    monkeypatch.setenv("TELEGRAM_IGNORED_CHATS", "99")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_TYPES", "private,supergroup")
    monkeypatch.setenv("TELEGRAM_GROUP_TRIGGER_MODE", "addressed")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "aegis_bot")
    monkeypatch.setenv("TELEGRAM_BOT_ID", "123")

    adapter = TelegramAdapter("token")

    assert adapter._author_allowed("7", "ada") is True
    assert adapter._author_allowed("8", "ada") is True
    assert adapter._author_allowed("8", "grace") is False

    base = {"chat": {"id": 42, "type": "supergroup"}, "text": "hello", "from": {"id": 7}}
    assert adapter._message_allowed(base, "hello") is False
    assert adapter._message_allowed({**base, "text": "@aegis_bot hello"}, "@aegis_bot hello") is True
    assert adapter._message_allowed({**base, "text": "hello @aegis_bot."}, "hello @aegis_bot.") is True
    assert adapter._message_allowed({**base, "text": "@aegis_bot_backup hello"}, "@aegis_bot_backup hello") is False
    assert adapter._message_allowed({**base, "text": "not@aegis_bot"}, "not@aegis_bot") is False
    assert adapter._message_allowed({
        **base,
        "text": "mail foo@aegis_bot.example",
    }, "mail foo@aegis_bot.example") is False
    entity_text = "👋 @aegis_bot hi"
    assert adapter._message_allowed({
        **base,
        "text": entity_text,
        "entities": [{"type": "mention", "offset": 3, "length": 10}],
    }, entity_text) is True
    assert adapter._message_allowed({**base, "text": "/status"}, "/status") is True
    assert adapter._message_allowed({
        **base,
        "reply_to_message": {"from": {"id": 123, "username": "aegis_bot"}},
    }, "hello") is True
    assert adapter._message_allowed({**base, "chat": {"id": 99, "type": "supergroup"}}, "/status") is False
    assert adapter._message_allowed({**base, "chat": {"id": 43, "type": "supergroup"}}, "/status") is False
    assert adapter._strip_own_addressing("mail foo@aegis_bot.example") == "mail foo@aegis_bot.example"
    assert adapter._strip_own_addressing("@aegis_bot hello") == "hello"
    assert adapter._message_allowed({**base, "chat": {"id": 42, "type": "group"}}, "/status") is False

    topic_msg = {
        **base,
        "text": "@aegis_bot hello",
        "message_thread_id": 77,
        "is_topic_message": True,
        "from": {"id": 7, "username": "ada"},
    }
    assert adapter._message_thread_id(topic_msg) == "77"
    assert adapter._message_thread_id({**topic_msg, "is_topic_message": False}) is None
    assert adapter._event_text(topic_msg, "@aegis_bot hello") == "[ada]: hello"
    assert adapter._conversation_key(MessageEvent(
        platform="telegram",
        chat_id="42",
        text="hello",
        thread_id="77",
    )) == "42:thread:77"

    api_calls = []

    def fake_api(method, **params):
        api_calls.append((method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 123}}
        return {}

    adapter._api = fake_api
    adapter._edit = lambda *_args: False
    ev = MessageEvent(
        platform="telegram",
        chat_id="42",
        text="hello",
        thread_id="77",
        metadata={"message_thread_id": "77"},
    )
    state = adapter._before_dispatch(ev)
    adapter._deliver_reply(ev, "topic reply", state)
    assert api_calls[0] == ("sendChatAction", {
        "chat_id": "42",
        "action": "typing",
        "message_thread_id": "77",
    })
    assert api_calls[1] == ("sendMessage", {
        "chat_id": "42",
        "text": "🤔 working…",
        "message_thread_id": "77",
    })
    assert api_calls[-1] == ("sendMessage", {
        "chat_id": "42",
        "text": "topic reply",
        "message_thread_id": "77",
    })

    api_calls.clear()
    reply_ev = MessageEvent(
        platform="telegram",
        chat_id="42",
        text="reply",
        thread_id="77",
        message_id="321",
        metadata={"message_thread_id": "77"},
    )
    reply_state = adapter._before_dispatch(reply_ev)
    adapter._deliver_reply(reply_ev, "anchored reply", reply_state)
    assert api_calls[0] == ("sendChatAction", {
        "chat_id": "42",
        "action": "typing",
        "message_thread_id": "77",
    })
    assert api_calls[1] == ("sendMessage", {
        "chat_id": "42",
        "text": "🤔 working…",
        "message_thread_id": "77",
        "reply_to_message_id": "321",
        "allow_sending_without_reply": "true",
    })
    assert api_calls[-1] == ("sendMessage", {
        "chat_id": "42",
        "text": "anchored reply",
        "message_thread_id": "77",
        "reply_to_message_id": "321",
        "allow_sending_without_reply": "true",
    })

    api_calls.clear()

    def fake_api_thread_missing(method, **params):
        api_calls.append((method, params))
        if params.get("message_thread_id") == "gone":
            raise RuntimeError("Bad Request: message thread not found")
        if method == "sendMessage":
            return {"result": {"message_id": 456}}
        return {}

    adapter._api = fake_api_thread_missing
    adapter.send("42", "fallback reply", metadata={"message_thread_id": "gone"})
    assert api_calls == [
        ("sendMessage", {"chat_id": "42", "text": "fallback reply", "message_thread_id": "gone"}),
        ("sendMessage", {"chat_id": "42", "text": "fallback reply"}),
    ]


def test_telegram_adapter_filters_supergroup_topics(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_TOPICS", "77,general")
    monkeypatch.setenv("TELEGRAM_IGNORED_TOPICS", "88")
    monkeypatch.setenv("TELEGRAM_ALLOW_GENERAL_TOPIC", "1")
    adapter = TelegramAdapter("token")
    base = {
        "chat": {"id": 42, "type": "supergroup"},
        "text": "/status",
        "from": {"id": 7, "username": "ada"},
    }

    assert adapter._message_allowed({
        **base,
        "message_thread_id": 77,
        "is_topic_message": True,
    }, "/status") is True
    assert adapter._message_allowed({
        **base,
        "message_thread_id": 88,
        "is_topic_message": True,
    }, "/status") is False
    assert adapter._message_allowed(base, "/status") is True

    monkeypatch.setenv("TELEGRAM_ALLOW_GENERAL_TOPIC", "0")
    blocked_general = TelegramAdapter("token")
    assert blocked_general._message_allowed(base, "/status") is False
    assert blocked_general.metadata["security"]["allowed_topics_configured"] is True
    assert blocked_general.metadata["security"]["ignored_topics_configured"] is True
    assert blocked_general.metadata["security"]["allow_general_topic"] is False


def test_telegram_adapter_coalesces_media_group_updates(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    for name in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_MEDIA_GROUP_COALESCE_SECONDS", "0.02")
    adapter = TelegramAdapter("token")
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None

    base = {
        "date": 123456,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 7, "username": "ada"},
        "media_group_id": "album-1",
    }

    first = {
        "update_id": 701,
        "message": {
            **base,
            "message_id": 1,
            "caption": "release photos",
            "photo": [{"file_id": "photo-1", "width": 800, "height": 600, "file_size": 1000}],
        },
    }
    second = {
        "update_id": 702,
        "message": {
            **base,
            "message_id": 2,
            "document": {"file_id": "doc-1", "file_name": "brief.pdf", "mime_type": "application/pdf"},
        },
    }

    assert adapter._handle_update(first) is True
    assert adapter._handle_update(second) is True
    assert seen == []
    _wait_for(lambda: len(seen) == 1)

    ev, raw_text = seen[0]
    assert raw_text == "release photos"
    assert ev.text == "release photos"
    assert ev.chat_id == "42"
    assert ev.metadata["media_group_id"] == "album-1"
    assert ev.metadata["media_group_size"] == 2
    assert [row["kind"] for row in ev.attachments] == ["photo", "document"]
    assert {row["media_group_id"] for row in ev.attachments} == {"album-1"}


def test_telegram_adapter_builds_inbound_attachment_rows(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    for name in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = TelegramAdapter("token")
    msg = {
        "voice": {
            "file_id": "voice-file",
            "file_unique_id": "voice-unique",
            "mime_type": "audio/ogg",
            "duration": 4,
            "file_size": 1024,
        },
        "audio": {
            "file_id": "audio-file",
            "mime_type": "audio/mpeg",
            "file_name": "song.mp3",
            "file_size": 2048,
        },
        "document": {
            "file_id": "doc-file",
            "mime_type": "application/pdf",
            "file_name": "brief.pdf",
            "file_size": 4096,
        },
        "photo": [
            {"file_id": "small-photo", "width": 90, "height": 90, "file_size": 100},
            {"file_id": "large-photo", "width": 900, "height": 600, "file_size": 5000},
        ],
        "video": {
            "file_id": "video-file",
            "mime_type": "video/mp4",
            "file_name": "clip.mp4",
            "width": 640,
            "height": 480,
            "duration": 9,
            "file_size": 8192,
        },
        "animation": {
            "file_id": "anim-file",
            "mime_type": "video/mp4",
            "file_name": "loop.mp4",
            "duration": 2,
            "file_size": 1024,
        },
        "video_note": {
            "file_id": "note-file",
            "duration": 5,
            "length": 240,
            "file_size": 512,
        },
        "sticker": {
            "file_id": "sticker-file",
            "emoji": "ok",
            "set_name": "aegis",
            "is_animated": True,
            "file_size": 256,
        },
        "contact": {
            "phone_number": "+15551234567",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "user_id": 99,
        },
        "location": {"latitude": 49.2827, "longitude": -123.1207, "horizontal_accuracy": 8},
        "venue": {
            "location": {"latitude": 40.7128, "longitude": -74.006},
            "title": "Ops HQ",
            "address": "1 Main St",
            "google_place_id": "place-1",
        },
        "poll": {
            "id": "poll-1",
            "question": "Ship it?",
            "options": [{"text": "Yes", "voter_count": 3}, {"text": "No", "voter_count": 1}],
            "total_voter_count": 4,
            "is_closed": False,
            "type": "regular",
        },
    }

    rows = adapter._attachments_from_message(msg)

    assert [row["kind"] for row in rows] == [
        "voice",
        "audio",
        "document",
        "animation",
        "video_note",
        "sticker",
        "photo",
        "video",
        "contact",
        "location",
        "venue",
        "poll",
    ]
    assert rows[0] == {
        "id": "voice-file",
        "type": "audio/ogg",
        "media_type": "audio/ogg",
        "filename": "voice.ogg",
        "size": 1024,
        "source": "telegram",
        "kind": "voice",
        "file_id": "voice-file",
        "file_unique_id": "voice-unique",
        "duration": 4,
    }
    assert rows[1]["filename"] == "song.mp3"
    assert rows[2]["type"] == "application/pdf"
    assert rows[3]["kind"] == "animation"
    assert rows[3]["filename"] == "loop.mp4"
    assert rows[4]["kind"] == "video_note"
    assert rows[4]["filename"] == "video_note.mp4"
    assert rows[5]["kind"] == "sticker"
    assert rows[5]["emoji"] == "ok"
    assert rows[5]["is_animated"] is True
    assert rows[6]["file_id"] == "large-photo"
    assert rows[7]["width"] == 640
    assert rows[8]["kind"] == "contact"
    assert rows[8]["filename"] == "Ada Lovelace"
    assert rows[8]["phone_number"] == "+15551234567"
    assert rows[9]["kind"] == "location"
    assert rows[9]["latitude"] == 49.2827
    assert rows[10]["kind"] == "venue"
    assert rows[10]["filename"] == "Ops HQ"
    assert rows[10]["google_place_id"] == "place-1"
    assert rows[11]["kind"] == "poll"
    assert rows[11]["question"] == "Ship it?"
    assert rows[11]["options"][0] == {"text": "Yes", "voter_count": 3}
    assert rows[11]["poll_type"] == "regular"
    assert adapter._event_text(
        {"chat": {"type": "private"}},
        "",
        attachments=[rows[0]],
    ) == "[voice attached: voice.ogg]"


def test_telegram_long_poll_submits_media_only_updates(monkeypatch):
    import pytest

    from aegis.gateway.channels import TelegramAdapter

    for name in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_AUTO_DISCOVER_BOT", "0")
    monkeypatch.setenv("TELEGRAM_REGISTER_COMMANDS", "0")
    adapter = TelegramAdapter("token")
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 123456,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 7, "username": "ada"},
            "voice": {
                "file_id": "voice-file",
                "mime_type": "audio/ogg",
                "duration": 3,
                "file_size": 12,
            },
        },
    }
    seen = []
    api_calls = []

    def fake_api(method, **params):
        api_calls.append((method, params))
        if len(api_calls) > 1:
            raise KeyboardInterrupt
        return {"result": [update]}

    def fake_submit(ev, *, raw_text=None):
        seen.append((ev, raw_text))
        raise KeyboardInterrupt

    adapter._api = fake_api
    adapter._submit_inbound = fake_submit

    with pytest.raises(KeyboardInterrupt):
        adapter.start(lambda _ev: "")

    assert api_calls[0] == ("getUpdates", {
        "offset": 0,
        "timeout": 60,
        "allowed_updates": (
            '["message", "edited_message", "channel_post", "edited_channel_post", "callback_query"]'
        ),
    })
    ev, raw_text = seen[0]
    assert raw_text == ""
    assert ev.platform == "telegram"
    assert ev.chat_id == "42"
    assert ev.user_id == "7"
    assert ev.user_name == "ada"
    assert ev.text == "[voice attached: voice.ogg]"
    assert ev.message_id == "10"
    assert ev.timestamp == 123456
    assert ev.attachments == [{
        "id": "voice-file",
        "type": "audio/ogg",
        "media_type": "audio/ogg",
        "filename": "voice.ogg",
        "size": 12,
        "source": "telegram",
        "kind": "voice",
        "file_id": "voice-file",
        "duration": 3,
    }]


def test_telegram_update_idempotency_drops_retries_and_reopens_on_failure(monkeypatch):
    import pytest

    from aegis.gateway.channels import TelegramAdapter

    for name in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = TelegramAdapter("token")
    update = {
        "update_id": 42,
        "message": {
            "message_id": 10,
            "date": 123456,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 7, "username": "ada"},
            "text": "hello",
        },
    }
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev.text, raw_text)) or None

    assert adapter._handle_update(update) is True
    assert adapter._handle_update(update) is True

    assert seen == [("hello", "hello")]
    assert adapter._delivery_cache.stats()["accepted_count"] == 1
    assert adapter._delivery_cache.stats()["duplicate_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = TelegramAdapter("token")
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    assert after_restart._handle_update(update) is True
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1

    callback_seen = []
    callback_update = {
        "callback_query": {
            "id": "cb1",
            "data": "/status",
            "from": {"id": 7, "username": "ada"},
            "message": {"message_id": 55, "chat": {"id": 42, "type": "private"}},
        },
    }
    adapter._submit_inbound = lambda ev, *, raw_text=None: callback_seen.append((ev.text, raw_text)) or None
    adapter._api = lambda *_args, **_kwargs: {"ok": True}

    assert adapter._handle_update(callback_update) is True
    assert adapter._handle_update(callback_update) is True
    assert callback_seen == [("/status", "/status")]

    failing = TelegramAdapter("token")
    failing_update = {
        **update,
        "update_id": 43,
        "message": {**update["message"], "message_id": 11, "text": "retry me"},
    }
    failing._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue down"))
    with pytest.raises(RuntimeError):
        failing._handle_update(failing_update)
    assert failing._delivery_cache.stats()["entries"] == 0
    assert failing._delivery_cache.stats()["discarded_count"] == 1
    assert failing.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    recovered = []
    failing._submit_inbound = lambda ev, *, raw_text=None: recovered.append(ev.text) or None
    assert failing._handle_update(failing_update) is True
    assert recovered == ["retry me"]


def test_telegram_rate_limit_drops_excess_updates(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_RATE_LIMIT_PER_MINUTE", "1")
    for name in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_IGNORED_CHATS",
        "TELEGRAM_ALLOWED_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = TelegramAdapter("token")
    calls = []
    seen = []
    adapter._api = lambda method, **params: calls.append((method, params)) or {"ok": True}
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev.text, raw_text)) or None

    base_message = {
        "date": 123456,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 7, "username": "ada"},
    }
    assert adapter._handle_update({"update_id": 501, "message": {**base_message, "message_id": 1, "text": "hello"}}) is True
    assert adapter._handle_update({"update_id": 502, "message": {**base_message, "message_id": 2, "text": "again"}}) is True

    assert seen == [("hello", "hello")]
    assert ("sendMessage", {"chat_id": "42", "text": "⏳ rate limit exceeded."}) in calls
    assert adapter.metadata["rate_limiter"]["limited_count"] == 1


def test_telegram_startup_discovers_identity_and_registers_commands(monkeypatch):
    import json

    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_COMMAND_SCOPE_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_COMMAND_LANGUAGE_CODE", "en")
    adapter = TelegramAdapter("token")

    class FakeConfig:
        def get(self, dotted, default=None):
            if dotted == "gateway.user_commands":
                return ["/deploy", "/bad command", "/deploy-now", "/deploy_now", "/deploy"]
            return default

    adapter._config = FakeConfig()
    calls = []

    def fake_api(method, **params):
        calls.append((method, params))
        if method == "getMe":
            return {"result": {"id": 123, "username": "aegis_bot"}}
        return {"ok": True}

    adapter._api = fake_api
    adapter._startup_sync()

    assert adapter.bot_id == "123"
    assert adapter.bot_username == "aegis_bot"
    assert calls[0] == ("getMe", {})
    method, params = calls[1]
    assert method == "setMyCommands"
    commands = json.loads(params["commands"])
    assert commands[0] == {"command": "help", "description": "Show available AEGIS commands"}
    assert {"command": "deploy", "description": "Run /deploy"} in commands
    assert {"command": "bad", "description": "Run /bad"} in commands
    assert {"command": "deploy_now", "description": "Run /deploy_now"} in commands
    assert {"command": "deploy-now", "description": "Run /deploy-now"} not in commands
    assert all(" " not in row["command"] for row in commands)
    assert all("-" not in row["command"] for row in commands)
    assert json.loads(params["scope"]) == {"type": "chat", "chat_id": "42"}
    assert params["language_code"] == "en"


def test_telegram_callback_queries_dispatch_as_commands(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "aegis_bot")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7")
    adapter = TelegramAdapter("token")
    calls = []
    seen = []
    adapter._api = lambda method, **params: calls.append((method, params)) or {"ok": True}
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None

    handled = adapter._handle_callback_update({
        "callback_query": {
            "id": "cb1",
            "data": "/status@aegis_bot",
            "from": {"id": 7, "username": "ada"},
            "message": {
                "message_id": 55,
                "chat": {"id": 42, "type": "supergroup"},
                "message_thread_id": 77,
                "is_topic_message": True,
                "date": 1781906000,
            },
            "chat_instance": "opaque-chat-instance",
        },
    })

    assert handled is True
    assert calls == [("answerCallbackQuery", {"callback_query_id": "cb1"})]
    ev, raw_text = seen[0]
    assert raw_text == "/status@aegis_bot"
    assert ev.platform == "telegram"
    assert ev.chat_id == "42"
    assert ev.text == "/status"
    assert ev.user_id == "7"
    assert ev.user_name == "ada"
    assert ev.thread_id == "77"
    assert ev.message_id == "55"
    assert ev.timestamp == 1781906000
    assert ev.metadata["source"] == "callback_query"
    assert ev.metadata["callback_query_id"] == "cb1"
    assert ev.metadata["command"] == "/status"


def test_telegram_clarify_and_approval_prompts_use_inline_buttons(monkeypatch):
    import json

    from aegis.gateway.base import MessageEvent
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7")
    adapter = TelegramAdapter("token")
    calls = []

    def fake_api(method, **params):
        calls.append((method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 101}}
        return {"ok": True}

    adapter._api = fake_api
    long_choice = "Approve release with the extended migration checklist " + ("x" * 80)
    ev = MessageEvent(
        platform="telegram",
        chat_id="42",
        text="question",
        user_id="7",
        thread_id="77",
        metadata={"message_thread_id": "77"},
    )
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick one", [long_choice], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: any(method == "sendMessage" for method, _params in calls))

    method, params = next(row for row in calls if row[0] == "sendMessage")
    assert method == "sendMessage"
    assert params["chat_id"] == "42"
    assert params["message_thread_id"] == "77"
    assert params["text"] == f"Pick one\n  1. {long_choice}"
    markup = json.loads(params["reply_markup"])
    callback_data = markup["inline_keyboard"][0][0]["callback_data"]
    assert callback_data.startswith("aegis:clarify:")
    assert len(callback_data.encode("utf-8")) <= 64
    assert adapter._resolve_callback_data(callback_data) == long_choice
    callback_meta = adapter._resolve_callback_payload(callback_data)[1]
    assert callback_meta["prompt_id"].startswith("clarify:")
    assert callback_meta["prompt_kind"] == "clarify"

    assert adapter._handle_callback_update({
        "callback_query": {
            "id": "cb-choice",
            "data": callback_data,
            "from": {"id": 7, "username": "ada"},
            "message": {
                "message_id": 101,
                "chat": {"id": 42, "type": "private"},
                "message_thread_id": 77,
                "is_topic_message": True,
            },
        },
    }) is True
    thread.join(2)

    assert answer["text"] == long_choice
    assert ("answerCallbackQuery", {"callback_query_id": "cb-choice"}) in calls

    calls.clear()
    adapter.send_exec_approval("42", "Allow bash(ls)?", metadata={"message_thread_id": "77"})
    approval_params = calls[0][1]
    approval_markup = json.loads(approval_params["reply_markup"])
    assert approval_params["text"] == "Allow bash(ls)?"
    assert approval_params["message_thread_id"] == "77"
    approval_data = [
        button["callback_data"]
        for button in approval_markup["inline_keyboard"][0]
    ]
    assert all(item.startswith("aegis:approval:") for item in approval_data)
    assert [adapter._resolve_callback_data(item) for item in approval_data] == ["approve", "always", "deny"]


def test_telegram_expired_prompt_callback_is_not_dispatched(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7")
    adapter = TelegramAdapter("token")
    calls = []
    adapter._api = lambda method, **params: calls.append((method, params)) or {"ok": True}
    adapter._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not dispatch"))

    assert adapter._handle_callback_update({
        "callback_query": {
            "id": "cb-expired",
            "data": "aegis:clarify:missing",
            "from": {"id": 7, "username": "ada"},
            "message": {"message_id": 55, "chat": {"id": 42, "type": "private"}},
        },
    }) is True
    assert calls == [("answerCallbackQuery", {
        "callback_query_id": "cb-expired",
        "text": "Prompt expired",
        "show_alert": "true",
    })]


def test_telegram_cached_callback_payloads_expire_by_ttl(monkeypatch):
    from aegis.gateway import channels
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7")
    adapter = TelegramAdapter("token")
    adapter.callback_ttl_seconds = 1
    calls = []
    seen = []
    adapter._api = lambda method, **params: calls.append((method, params)) or {"ok": True}
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None

    monkeypatch.setattr(channels.time, "time", lambda: 1000.0)
    callback_data = adapter._callback_data_for("approve release", prefix="clarify")
    assert callback_data.startswith("aegis:clarify:")
    assert adapter._resolve_callback_data(callback_data) == "approve release"

    monkeypatch.setattr(channels.time, "time", lambda: 1002.0)
    assert adapter._resolve_callback_data(callback_data) == ""
    assert adapter._handle_callback_update({
        "callback_query": {
            "id": "cb-ttl",
            "data": callback_data,
            "from": {"id": 7, "username": "ada"},
            "message": {"message_id": 55, "chat": {"id": 42, "type": "private"}},
        },
    }) is True
    assert seen == []
    assert calls == [("answerCallbackQuery", {
        "callback_query_id": "cb-ttl",
        "text": "Prompt expired",
        "show_alert": "true",
    })]


def test_telegram_callback_rejects_unauthorized_users(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7")
    adapter = TelegramAdapter("token")
    calls = []
    adapter._api = lambda method, **params: calls.append((method, params)) or {"ok": True}
    adapter._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not dispatch"))

    assert adapter._handle_callback_update({
        "callback_query": {
            "id": "cb1",
            "data": "/status",
            "from": {"id": 8, "username": "mallory"},
            "message": {"message_id": 55, "chat": {"id": 42, "type": "private"}},
        },
    }) is True
    assert calls == [("answerCallbackQuery", {
        "callback_query_id": "cb1",
        "text": "Not authorized",
        "show_alert": "true",
    })]


def test_telegram_channel_post_uses_sender_chat_identity(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter

    adapter = TelegramAdapter("token")
    msg = {
        "message_id": 9,
        "chat": {"id": -1001, "type": "channel"},
        "sender_chat": {"id": -1001, "username": "updates"},
        "text": "/status",
    }

    assert adapter._message_from_update({"channel_post": msg}) == (msg, "channel_post")
    assert adapter._author_from_message(msg) == ("-1001", "updates")
    assert adapter._message_allowed(msg, "/status") is True
    assert adapter._event_text({
        "chat": {"id": -1002, "type": "supergroup"},
        "sender_chat": {"id": -1002, "title": "Ops Announcements"},
        "text": "deploy window",
    }, "deploy window") == "[Ops Announcements]: deploy window"
    assert adapter._event_text({
        "chat": {"id": -1002, "type": "supergroup"},
        "author_signature": "Ada Admin",
        "text": "anonymous note",
    }, "anonymous note") == "[Ada Admin]: anonymous note"


def test_signal_adapter_preserves_attachment_metadata(monkeypatch, tmp_path):
    import json
    import shutil

    monkeypatch.setenv("SIGNAL_CLI_ACCOUNT", "+15550001")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/signal-cli")
    from aegis.gateway.signal_channel import SignalAdapter

    adapter = SignalAdapter()
    payload = {
        "envelope": {
            "sourceNumber": "+15550002",
            "sourceName": "Ada",
            "sourceUuid": "uuid-1",
            "sourceDevice": 1,
            "serverGuid": "srv-1",
            "timestamp": 123,
            "dataMessage": {
                "groupInfo": {"groupId": "group-1"},
                "attachments": [{
                    "id": "att-1",
                    "contentType": "image/png",
                    "filename": "chart.png",
                    "size": 42,
                    "width": 100,
                    "height": 50,
                }],
            },
        },
    }

    events = adapter._parse(json.dumps(payload))

    assert len(events) == 1
    ev = events[0]
    assert ev.platform == "signal"
    assert ev.chat_id == "group:group-1"
    assert ev.text == "[image/png attached: chart.png]"
    assert ev.user_id == "+15550002"
    assert ev.message_id == "123"
    assert ev.attachments == [{
        "id": "att-1",
        "type": "image/png",
        "media_type": "image/png",
        "filename": "chart.png",
        "size": 42,
        "source": "signal",
        "width": 100,
        "height": 50,
    }]
    assert ev.metadata["group_id"] == "group-1"
    assert ev.metadata["source_uuid"] == "uuid-1"
    assert ev.metadata["server_guid"] == "srv-1"
    assert ev.metadata["delivery_id"] == "signal:guid:srv-1"
    assert adapter.metadata["idempotency"]["delivery_cache"]["entries"] == 0
    assert adapter.metadata["security"]["idempotency_env"] == [
        "SIGNAL_IDEMPOTENCY_TTL_SECONDS",
        "SIGNAL_IDEMPOTENCY_CACHE_MAX",
    ]
    sent = []
    media_path = tmp_path / "chart.png"
    media_path.write_text("png", encoding="utf-8")
    adapter._run = lambda *args, **kwargs: sent.append((args, kwargs)) or ""
    adapter.send("+15550002", "ok", metadata={"ignored": True})
    adapter.send_media("group:group-1", str(media_path), "chart", metadata={"ignored": True})
    adapter.send_media("+15550002", "/tmp/missing.png")
    assert sent == [
        (("send", "-m", "ok", "+15550002"), {"timeout": 60}),
        (("send", "-m", "chart", "--attachment", str(media_path), "-g", "group-1"), {"timeout": 120}),
        (("send", "-m", "(file not found: /tmp/missing.png)", "+15550002"), {"timeout": 60}),
    ]
    assert adapter.metadata["supports_media"] is True


def test_signal_adapter_dedupes_receive_envelopes_and_allows_retry(monkeypatch):
    import json
    import shutil

    import pytest

    monkeypatch.setenv("SIGNAL_CLI_ACCOUNT", "+15550001")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/signal-cli")
    from aegis.gateway.signal_channel import SignalAdapter

    adapter = SignalAdapter()
    payload = {
        "envelope": {
            "sourceNumber": "+15550002",
            "timestamp": 123,
            "dataMessage": {"message": "hello"},
        },
    }
    ev = adapter._parse(json.dumps(payload))[0]
    assert ev.metadata["delivery_id"] == "signal:+15550002:+15550002:123"

    seen = []
    adapter._submit_inbound = lambda event: seen.append(event.text)
    assert adapter._handle_event(ev) is ev
    assert adapter._handle_event(ev) is None
    assert seen == ["hello"]
    assert adapter.metadata["idempotency"]["delivery_cache"]["duplicate_count"] == 1

    failing = SignalAdapter()
    failing_ev = failing._parse(json.dumps(payload))[0]
    attempts = []

    def fail_once(event):  # noqa: ANN001
        attempts.append(event.text)
        raise RuntimeError("signal dispatch failed")

    failing._submit_inbound = fail_once
    with pytest.raises(RuntimeError):
        failing._handle_event(failing_ev)
    failing._submit_inbound = lambda event: attempts.append(f"retry:{event.text}")
    assert failing._handle_event(failing_ev) is failing_ev
    assert attempts == ["hello", "retry:hello"]


def test_matrix_adapter_threads_inbound_and_outbound_messages(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.test")
    monkeypatch.setenv("MATRIX_USER", "@aegis:matrix.test")
    monkeypatch.setenv("MATRIX_PASSWORD", "pw")
    from aegis.gateway.matrix_channel import MatrixAdapter

    adapter = MatrixAdapter()
    event = SimpleNamespace(
        source={"content": {"m.relates_to": {"rel_type": "m.thread", "event_id": "$root"}}},
        content={},
    )

    assert adapter._thread_id_from_event(event) == "$root"
    assert adapter._message_content("hi", {"thread_id": "$root"}) == {
        "msgtype": "m.text",
        "body": "hi",
        "m.relates_to": {
            "rel_type": "m.thread",
            "event_id": "$root",
            "is_falling_back": True,
        },
    }


def test_email_adapter_preserves_attachments_and_reply_headers(monkeypatch):
    from email.message import EmailMessage

    monkeypatch.setenv("EMAIL_ADDRESS", "bot@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "ada@example.com")
    from aegis.gateway.email_channel import EmailAdapter

    adapter = EmailAdapter()
    assert adapter.allowed_senders == {"ada@example.com"}

    msg = EmailMessage()
    msg["From"] = "Ada <ada@example.com>"
    msg["Subject"] = "Report"
    msg["Message-ID"] = "<m1@example.com>"
    msg.set_content("See attached.")
    msg.add_attachment(b"pdf", maintype="application", subtype="pdf", filename="report.pdf", cid="<cid-1>")

    assert adapter._body(msg).strip() == "See attached."
    assert adapter._attachments(msg) == [{
        "id": "cid-1",
        "type": "application/pdf",
        "media_type": "application/pdf",
        "filename": "report.pdf",
        "size": 3,
        "source": "email",
    }]

    sent = []

    class FakeSMTP:
        def __init__(self, host, port):
            assert (host, port) == ("smtp.example.com", 465)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login(self, address, password):
            assert (address, password) == ("bot@example.com", "pw")

        def send_message(self, message):
            sent.append(message)

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    adapter.send(
        "ada@example.com",
        "done",
        subject="Re: Report",
        metadata={"message_id": "<m1@example.com>", "references": "<root@example.com>"},
    )

    assert sent[0]["In-Reply-To"] == "<m1@example.com>"
    assert sent[0]["References"] == "<root@example.com> <m1@example.com>"

    from aegis.gateway.base import MessageEvent

    original = MessageEvent(
        platform="email",
        chat_id="ada@example.com",
        text="Report\n\nNeed approval",
        user_id="ada@example.com",
        thread_id="Report",
        message_id="<m1@example.com>",
        metadata={
            "subject": "Report",
            "message_id": "<m1@example.com>",
            "references": "<root@example.com>",
        },
    )
    assert adapter._conversation_key(original) == "ada@example.com:thread:Report"
    assert adapter._conversation_key(MessageEvent(
        platform="email",
        chat_id="ada@example.com",
        text="reply",
        thread_id="Re: Fwd: Report",
    )) == "ada@example.com:thread:Report"

    adapter.send_clarify(
        "ada@example.com",
        "Pick a deploy lane?",
        ["stable", "canary"],
        metadata=adapter._event_delivery_metadata(original),
    )
    adapter.send_exec_approval(
        "ada@example.com",
        "Run deploy?",
        metadata=adapter._event_delivery_metadata(original),
    )

    assert sent[1]["Subject"] == "Re: Report"
    assert "Reply with the number or exact choice." in sent[1].get_content()
    assert sent[1]["In-Reply-To"] == "<m1@example.com>"
    assert sent[2]["Subject"] == "Re: Report"
    assert "Reply approve, always, or deny." in sent[2].get_content()


def test_email_adapter_prompt_waiter_matches_re_subject(monkeypatch):
    import threading

    monkeypatch.setenv("EMAIL_ADDRESS", "bot@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.email_channel import EmailAdapter

    sent = []

    class FakeSMTP:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login(self, *_args):
            return None

        def send_message(self, message):
            sent.append(message)

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    adapter = EmailAdapter()
    ev = MessageEvent(
        platform="email",
        chat_id="ada@example.com",
        text="Report\n\nNeed input",
        user_id="ada@example.com",
        thread_id="Report",
        message_id="<m1@example.com>",
        metadata={"subject": "Report", "message_id": "<m1@example.com>"},
    )
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick one?", ["stable", "canary"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: sent)
    adapter._submit_inbound(MessageEvent(
        platform="email",
        chat_id="ada@example.com",
        text="Re: Report\n\n2\n\n> Pick one?",
        user_id="ada@example.com",
        thread_id="Re: Report",
        message_id="<m2@example.com>",
        reply_to_message_id="<m1@example.com>",
        metadata={"subject": "Re: Report", "in_reply_to": "<m1@example.com>"},
    ))
    thread.join(2)

    assert answer == {"text": "canary"}
    assert sent[0]["Subject"] == "Re: Report"


def test_ntfy_adapter_preserves_metadata_headers_and_attachments(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "aegis-alerts")
    monkeypatch.setenv("NTFY_TOKEN", "secret-token")
    from aegis.gateway.ntfy_channel import NtfyAdapter

    adapter = NtfyAdapter()
    event = {
        "id": "evt1",
        "topic": "aegis-alerts",
        "message": "build done",
        "title": "Build",
        "tags": ["white_check_mark"],
        "priority": 4,
        "click": "https://ci.example.com",
        "attachment": {
            "url": "https://ci.example.com/log.txt",
            "name": "log.txt",
            "type": "text/plain",
            "size": 123,
        },
    }
    assert adapter._attachments_from_event(event) == [{
        "id": "https://ci.example.com/log.txt",
        "type": "text/plain",
        "media_type": "text/plain",
        "filename": "log.txt",
        "url": "https://ci.example.com/log.txt",
        "size": 123,
        "source": "ntfy",
    }]
    assert adapter._delivery_id_from_event(event) == "ntfy:aegis-alerts:evt1"
    assert adapter.metadata["idempotency"]["delivery_cache"]["entries"] == 0
    assert adapter._send_headers({
        "title": "Build",
        "tags": ["white_check_mark", "robot"],
        "priority": 4,
        "click": "https://ci.example.com",
    }) == {
        "Authorization": "Bearer secret-token",
        "Title": "Build",
        "Tags": "white_check_mark,robot",
        "Priority": "4",
        "Click": "https://ci.example.com",
    }

    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append(ev) or None
    handled = adapter._handle_stream_event(event)
    duplicate = adapter._handle_stream_event(dict(event))

    assert handled is seen[0]
    assert duplicate is None
    assert seen[0].platform == "ntfy"
    assert seen[0].chat_id == "aegis-alerts"
    assert seen[0].text == "build done"
    assert seen[0].message_id == "evt1"
    assert seen[0].metadata["delivery_id"] == "ntfy:aegis-alerts:evt1"
    assert adapter._delivery_cache.stats()["accepted_count"] == 1
    assert adapter._delivery_cache.stats()["duplicate_count"] == 1

    media_only = {
        "topic": "aegis-alerts",
        "time": 12345,
        "attachment": {
            "url": "https://ci.example.com/artifact.zip",
            "name": "artifact.zip",
            "type": "application/zip",
        },
    }
    media_event = adapter._message_event_from_event(media_only)

    assert media_event is not None
    assert media_event.text == "[application/zip attached: artifact.zip]"

    failing = NtfyAdapter()
    failing._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue down"))
    try:
        failing._handle_stream_event(event)
    except RuntimeError:
        pass
    assert failing._delivery_cache.stats()["entries"] == 0
    assert failing._delivery_cache.stats()["discarded_count"] == 1


def test_slack_adapter_enforces_workspace_filters_and_strips_mentions(monkeypatch, tmp_path):
    import pytest

    from aegis.gateway.slack_channel import SlackAdapter

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U1,U2")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_IGNORED_CHANNELS", "C9")
    monkeypatch.setenv("SLACK_ALLOWED_TEAMS", "T1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")

    adapter = SlackAdapter()

    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T1"}) is True
    assert adapter._resolve_thread_ts({"ts": "171.1"}) is None
    assert adapter._resolve_thread_ts({"ts": "171.2", "thread_ts": "171.1"}) == "171.1"
    assert adapter.metadata["supports_slash_commands"] is True
    assert adapter.command_menu(max_commands=3) == ["/help", "/whoami", "/status"]

    class FakeConfig:
        def get(self, dotted, default=None):
            if dotted == "gateway.user_commands":
                return ["/deploy"]
            return default

    adapter._config = FakeConfig()
    assert "/deploy" in adapter.command_menu(max_commands=50)
    assert adapter._event_allowed({"user": "U3", "channel": "C1", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C2", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C9", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T2"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T1", "bot_id": "B1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T1", "subtype": "message_changed"}) is False
    assert adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "subtype": "file_share",
        "files": [{"id": "F1", "name": "brief.pdf"}],
    }) is True
    assert adapter._strip_own_mentions("<@UBOT> !status") == "!status"
    assert adapter._strip_own_mentions("<@UBOT|aegis> hello") == "hello"
    slack_attachments = adapter._attachments_from_event({
        "files": [{
            "id": "F1",
            "name": "brief.pdf",
            "mimetype": "application/pdf",
            "url_private": "https://slack.test/files/F1",
            "size": 4096,
            "filetype": "pdf",
            "pretty_type": "PDF",
            "title": "Brief",
        }],
    })
    assert slack_attachments == [{
        "id": "F1",
        "type": "application/pdf",
        "media_type": "application/pdf",
        "filename": "brief.pdf",
        "url": "https://slack.test/files/F1",
        "size": 4096,
        "source": "slack",
        "filetype": "pdf",
        "pretty_type": "PDF",
        "title": "Brief",
    }]
    assert adapter._attachment_reference_text(slack_attachments) == "[application/pdf attached: brief.pdf]"

    monkeypatch.setenv("SLACK_TRIGGER_MODE", "addressed")
    addressed_adapter = SlackAdapter()
    assert addressed_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "text": "plain",
    }) is False
    assert addressed_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "text": "!status",
    }) is True
    assert addressed_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "text": "<@UBOT> hello",
    }) is True
    assert addressed_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "thread_ts": "171.1",
        "parent_user_id": "UBOT",
        "text": "thread reply",
    }) is True
    assert addressed_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "im",
        "text": "dm",
    }) is True

    monkeypatch.setenv("SLACK_TRIGGER_MODE", "command")
    command_adapter = SlackAdapter()
    assert command_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "text": "<@UBOT> hello",
    }) is False
    assert command_adapter._event_allowed({
        "user": "U1",
        "channel": "C1",
        "team": "T1",
        "channel_type": "channel",
        "text": "/status",
    }) is True

    monkeypatch.setenv("SLACK_ALLOW_BOTS", "1")
    monkeypatch.setenv("SLACK_TRIGGER_MODE", "all")
    bot_adapter = SlackAdapter()
    assert bot_adapter._event_allowed({
        "bot_id": "B1",
        "subtype": "bot_message",
        "channel": "C1",
        "team": "T1",
    }) is True
    assert bot_adapter._event_allowed({
        "bot_id": "B1",
        "subtype": "message_changed",
        "channel": "C1",
        "team": "T1",
    }) is False

    monkeypatch.setenv("SLACK_REPLY_IN_THREAD", "1")
    threaded_adapter = SlackAdapter()
    assert threaded_adapter._resolve_thread_ts({"ts": "171.1"}) == "171.1"

    posts = []
    uploads = []

    class FakeSlackClient:
        def chat_postMessage(self, **kwargs):
            posts.append(kwargs)

        def files_upload_v2(self, **kwargs):
            uploads.append(("v2", kwargs))

    class FakeSlackApp:
        client = FakeSlackClient()

    from aegis.gateway.base import MessageEvent

    with pytest.raises(RuntimeError):
        adapter.send("C1", "not started")

    adapter._app = FakeSlackApp()
    adapter.send("C1", "async reply", metadata={"thread_id": "171.1"})
    assert posts == [
        {"channel": "C1", "text": "async reply", "thread_ts": "171.1"},
    ]
    posts.clear()

    adapter.send("C1", "notify <!channel> <@U123> <#C2|ops>")
    assert posts == [
        {"channel": "C1", "text": "notify &lt;!channel&gt; &lt;@U123&gt; &lt;#C2|ops&gt;"},
    ]
    posts.clear()

    adapter.send_clarify("C1", "Pick a deploy lane?", ["stable", "canary"], metadata={"thread_ts": "171.1"})
    assert posts == [{
        "channel": "C1",
        "text": "Pick a deploy lane?",
        "thread_ts": "171.1",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Pick a deploy lane?"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "stable"},
                        "value": "stable",
                        "action_id": "aegis_clarify",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "canary"},
                        "value": "canary",
                        "action_id": "aegis_clarify",
                    },
                ],
            },
        ],
    }]
    posts.clear()

    adapter.send_clarify("C1", "Pick <!here>?", ["<@U123>"])
    assert posts[0]["text"] == "Pick &lt;!here&gt;?"
    assert posts[0]["blocks"][0]["text"]["text"] == "Pick &lt;!here&gt;?"
    assert posts[0]["blocks"][1]["elements"][0]["text"]["text"] == "&lt;@U123&gt;"
    assert posts[0]["blocks"][1]["elements"][0]["value"] == "<@U123>"
    posts.clear()

    adapter.send_exec_approval("C1", "Run deploy?", metadata={"thread_id": "171.1"})
    assert posts == [{
        "channel": "C1",
        "text": "Run deploy?",
        "thread_ts": "171.1",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Run deploy?"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "value": "approve",
                        "action_id": "aegis_exec_approval",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Always"},
                        "value": "always",
                        "action_id": "aegis_exec_approval",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "value": "deny",
                        "action_id": "aegis_exec_approval",
                        "style": "danger",
                    },
                ],
            },
        ],
    }]
    posts.clear()

    adapter._deliver_reply(MessageEvent(platform="slack", chat_id="C1", text="", thread_id=None), "flat reply")
    adapter._deliver_reply(MessageEvent(platform="slack", chat_id="C1", text="", thread_id="171.1"), "thread reply")
    adapter._deliver_reply(
        MessageEvent(platform="slack", chat_id="C1", text="", metadata={"thread_ts": "171.2"}),
        "metadata thread reply",
    )
    assert posts == [
        {"channel": "C1", "text": "flat reply"},
        {"channel": "C1", "text": "thread reply", "thread_ts": "171.1"},
        {"channel": "C1", "text": "metadata thread reply", "thread_ts": "171.2"},
    ]
    posts.clear()

    media_path = tmp_path / "report.txt"
    media_path.write_text("hello", encoding="utf-8")
    adapter.send_media("C1", str(media_path), caption="report <!channel>", metadata={"thread_ts": "171.1"})
    assert uploads == [("v2", {
        "channel": "C1",
        "file": str(media_path),
        "title": "report.txt",
        "initial_comment": "report &lt;!channel&gt;",
        "thread_ts": "171.1",
    })]

    adapter.send_media("C1", str(tmp_path / "missing.txt"), caption="lost <@U1>")
    assert posts == [{"channel": "C1", "text": f"lost &lt;@U1&gt;\n(file not found: {tmp_path / 'missing.txt'})"}]


def test_slack_adapter_handles_native_slash_commands(monkeypatch):
    from aegis.gateway.slack_channel import SlackAdapter

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U1")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNELS", "C1,ops")
    monkeypatch.setenv("SLACK_ALLOWED_TEAMS", "T1")

    adapter = SlackAdapter()
    seen = []
    acked = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None

    blocked = adapter._handle_slash_command(
        {
            "command": "/status",
            "channel_id": "C2",
            "user_id": "U1",
            "team_id": "T1",
        },
        ack=lambda: acked.append("blocked"),
    )
    assert blocked is None
    assert seen == []

    ev = adapter._handle_slash_command(
        {
            "command": "/status",
            "text": "full",
            "channel_id": "C1",
            "channel_name": "ops",
            "user_id": "U1",
            "user_name": "ada",
            "team_id": "T1",
            "trigger_id": "trigger-1",
            "response_url": "https://slack.test/response",
        },
        ack=lambda: acked.append(True),
    )

    assert acked == ["blocked", True]
    assert ev.platform == "slack"
    assert ev.chat_id == "C1"
    assert ev.text == "/status full"
    assert ev.user_id == "U1"
    assert ev.user_name == "ada"
    assert ev.message_id == "trigger-1"
    assert ev.metadata["source"] == "slash_command"
    assert ev.metadata["command"] == "/status"
    assert ev.metadata["response_url"] == "https://slack.test/response"
    assert seen == [(ev, "/status full")]

    action_acks = []
    blocked_action = adapter._handle_block_action(
        {
            "user": {"id": "U9", "name": "mallory"},
            "channel": {"id": "C1", "name": "ops"},
            "team": {"id": "T1"},
            "message": {"ts": "171.2", "thread_ts": "171.1"},
        },
        action={"action_id": "aegis_clarify", "value": "stable", "action_ts": "171.3"},
        ack=lambda: action_acks.append("blocked"),
    )
    assert blocked_action is None

    action_ev = adapter._handle_block_action(
        {
            "user": {"id": "U1", "name": "ada"},
            "channel": {"id": "C1", "name": "ops"},
            "team": {"id": "T1"},
            "message": {"ts": "171.2", "thread_ts": "171.1"},
            "container": {"message_ts": "171.2"},
            "response_url": "https://slack.test/action-response",
        },
        action={"action_id": "aegis_exec_approval", "value": "approve", "action_ts": "171.3"},
        ack=lambda: action_acks.append(True),
    )

    assert action_acks == ["blocked", True]
    assert action_ev.platform == "slack"
    assert action_ev.chat_id == "C1"
    assert action_ev.text == "approve"
    assert action_ev.user_id == "U1"
    assert action_ev.user_name == "ada"
    assert action_ev.thread_id == "171.1"
    assert action_ev.message_id == "171.2"
    assert action_ev.metadata["source"] == "block_action"
    assert action_ev.metadata["action_id"] == "aegis_exec_approval"
    assert action_ev.metadata["response_url"] == "https://slack.test/action-response"
    assert seen[-1] == (action_ev, "approve")

    encoded_value = adapter._button(
        "Approve",
        "approve",
        "aegis_exec_approval",
        metadata={"prompt_id": "exec_approval:prompt-1", "prompt_kind": "exec_approval"},
    )["value"]
    bound_action = adapter._handle_block_action(
        {
            "trigger_id": "trigger-bound",
            "user": {"id": "U1", "name": "ada"},
            "channel": {"id": "C1", "name": "ops"},
            "team": {"id": "T1"},
            "message": {"ts": "171.4", "thread_ts": "171.1"},
            "container": {"message_ts": "171.4"},
        },
        action={"action_id": "aegis_exec_approval", "value": encoded_value, "action_ts": "171.5"},
    )
    assert bound_action.text == "approve"
    assert bound_action.metadata["prompt_id"] == "exec_approval:prompt-1"
    assert bound_action.metadata["prompt_kind"] == "exec_approval"


def test_slack_adapter_dedupes_slash_commands_and_block_actions(monkeypatch):
    import pytest

    from aegis.gateway.slack_channel import SlackAdapter

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    adapter = SlackAdapter()
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None

    command = {
        "command": "/status",
        "text": "full",
        "channel_id": "C1",
        "channel_name": "ops",
        "user_id": "U1",
        "user_name": "ada",
        "team_id": "T1",
        "trigger_id": "trigger-1",
        "response_url": "https://slack.test/response",
    }

    ev = adapter._handle_slash_command(command)
    duplicate = adapter._handle_slash_command(dict(command))

    assert duplicate is None
    assert seen == [(ev, "/status full")]
    assert ev.metadata["delivery_id"] == "slash:trigger:trigger-1"
    assert adapter._delivery_cache.stats()["duplicate_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = SlackAdapter()
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    restart_duplicate = after_restart._handle_slash_command(dict(command))
    assert restart_duplicate is None
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1

    body = {
        "trigger_id": "trigger-action-1",
        "user": {"id": "U1", "name": "ada"},
        "channel": {"id": "C1", "name": "ops"},
        "team": {"id": "T1"},
        "message": {"ts": "171.2", "thread_ts": "171.1"},
        "container": {"message_ts": "171.2"},
        "response_url": "https://slack.test/action-response",
    }
    action = {"action_id": "aegis_exec_approval", "value": "approve", "action_ts": "171.3"}

    action_ev = adapter._handle_block_action(body, action=action)
    action_duplicate = adapter._handle_block_action(dict(body), action=dict(action))

    assert action_duplicate is None
    assert seen[-1] == (action_ev, "approve")
    assert action_ev.metadata["delivery_id"] == "block:trigger:trigger-action-1"
    assert adapter._delivery_cache.stats()["duplicate_count"] == 2
    action_restart_duplicate = after_restart._handle_block_action(dict(body), action=dict(action))
    assert action_restart_duplicate is None
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 2

    def fail_once(_ev, *, raw_text=None):  # noqa: ANN001, ARG001
        raise RuntimeError("slack slash dispatch down")

    adapter._submit_inbound = fail_once
    failing = dict(command, trigger_id="trigger-2")
    with pytest.raises(RuntimeError, match="slack slash dispatch down"):
        adapter._handle_slash_command(failing)

    assert adapter._delivery_cache.stats()["discarded_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    retried = adapter._handle_slash_command(failing)
    assert retried is seen[-1][0]


def test_slack_adapter_dedupes_message_events_and_ignores_self_echoes(monkeypatch):
    import pytest

    from aegis.gateway.slack_channel import SlackAdapter

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setenv("SLACK_BOT_ID", "BSELF")

    adapter = SlackAdapter()

    assert adapter._event_allowed({"user": "UBOT", "channel": "C1", "text": "echo"}) is False
    assert adapter._event_allowed({"bot_id": "BSELF", "channel": "C1", "text": "echo"}) is False
    assert adapter.metadata["idempotency"]["delivery_id_sources"] == [
        "event.event_id",
        "event.client_msg_id",
        "event.channel + event.ts",
        "slash.trigger_id",
        "block.trigger_id",
        "block.container.message_ts + action.action_ts",
    ]

    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    event = {
        "channel": "C1",
        "channel_type": "channel",
        "team": "T1",
        "user": "U1",
        "text": "!status",
        "client_msg_id": "client-1",
        "ts": "171.1",
    }

    ev = adapter._handle_message_event(event)
    duplicate = adapter._handle_message_event(dict(event))

    assert ev is seen[0][0]
    assert ev.text == "/status"
    assert ev.metadata["delivery_id"] == "client_msg:client-1"
    assert seen == [(ev, "!status")]
    assert duplicate is None
    assert adapter._delivery_cache.stats()["duplicate_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = SlackAdapter()
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    restart_duplicate = after_restart._handle_message_event(dict(event))
    assert restart_duplicate is None
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1

    def fail_once(_ev, *, raw_text=None):  # noqa: ANN001, ARG001
        raise RuntimeError("slack dispatch down")

    adapter._submit_inbound = fail_once
    failing = dict(event, client_msg_id="client-2", ts="171.2")
    with pytest.raises(RuntimeError, match="slack dispatch down"):
        adapter._handle_message_event(failing)

    assert adapter._delivery_cache.stats()["discarded_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    retried = adapter._handle_message_event(failing)
    assert retried is seen[-1][0]
    assert retried.metadata["delivery_id"] == "client_msg:client-2"


def test_slack_adapter_extracts_rich_block_text(monkeypatch):
    from aegis.gateway.slack_channel import SlackAdapter

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")

    adapter = SlackAdapter()
    seen = []
    adapter._submit_inbound = lambda ev, *, raw_text=None: seen.append((ev, raw_text)) or None
    event = {
        "channel": "C1",
        "channel_type": "channel",
        "team": "T1",
        "user": "U1",
        "text": "",
        "client_msg_id": "client-blocks",
        "ts": "172.1",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [{
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "text", "text": "Quoted deploy note"},
                        {"type": "user", "user_id": "U2"},
                    ],
                }],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "fallback *mrkdwn*"},
            },
        ],
    }

    ev = adapter._handle_message_event(event)

    assert ev is seen[0][0]
    assert "Quoted deploy note" in ev.text
    assert "<@U2>" in ev.text
    assert "fallback *mrkdwn*" in ev.text
    assert seen[0][1] == ev.text


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
    assert ev.user_id == "7"
    assert ev.thread_id == "9"
    assert ev.message_id == "m1"
    assert ev.attachments == [{"type": "image"}]
    assert ev.metadata == {"source": "bridge"}


def test_gateway_webhook_channel_accepts_whatsapp_bridge_aliases():
    from aegis.gateway.webhook_channel import WebhookChannel
    from aegis.platforms import normalize_platform_name

    channel = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    defaulted = channel._event_from_body({
        "remote_jid": "12025550123@s.whatsapp.net",
        "text": "default platform",
    })
    assert defaulted.platform == "whatsapp"
    assert defaulted.chat_id == "12025550123@s.whatsapp.net"
    assert defaulted.user_id == "12025550123@s.whatsapp.net"
    assert defaulted.metadata["identity_fallback"] == "chat_id"
    assert defaulted.metadata["remote_jid"] == "12025550123@s.whatsapp.net"

    ev = WebhookChannel()._event_from_body({
        "platform": "baileys",
        "remote_jid": "12025550123@s.whatsapp.net",
        "message": {"extendedTextMessage": {"text": "hello from whatsapp"}},
        "sender": {"id": "15551234567@s.whatsapp.net", "name": "Ada"},
        "key": {"id": "BAE512345"},
        "pushName": "Ada Lovelace",
        "metadata": {"bridge": "baileys"},
    })

    assert normalize_platform_name("wa") == "whatsapp"
    assert ev.platform == "whatsapp"
    assert ev.chat_id == "12025550123@s.whatsapp.net"
    assert ev.text == "hello from whatsapp"
    assert ev.user_id == "15551234567@s.whatsapp.net"
    assert ev.user_name == "Ada Lovelace"
    assert ev.message_id == "BAE512345"
    assert ev.metadata["bridge"] == "baileys"
    assert ev.metadata["bridge_platform"] == "baileys"
    assert ev.metadata["normalized_platform"] == "whatsapp"
    assert ev.metadata["remote_jid"] == "12025550123@s.whatsapp.net"
    assert ev.metadata["participant"] == "15551234567@s.whatsapp.net"
    assert ev.metadata["message_key_id"] == "BAE512345"

    nested = WebhookChannel()._event_from_body({
        "platform": "baileys",
        "key": {
            "remoteJid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "id": "BAE599999",
        },
        "message": {
            "extendedTextMessage": {
                "text": "replying from nested shape",
                "contextInfo": {
                    "stanzaId": "QUOTE123",
                    "quotedMessage": {"conversation": "the previous message"},
                },
            },
        },
    })

    assert nested.platform == "whatsapp"
    assert nested.chat_id == "12025550123-111@g.us"
    assert nested.text == "replying from nested shape"
    assert nested.user_id == "15551234567@s.whatsapp.net"
    assert nested.message_id == "BAE599999"
    assert nested.reply_to_message_id == "QUOTE123"
    assert nested.reply_to_text == "the previous message"
    assert nested.metadata["bridge_platform"] == "baileys"
    assert nested.metadata["normalized_platform"] == "whatsapp"
    assert nested.metadata["remote_jid"] == "12025550123-111@g.us"
    assert nested.metadata["group_jid"] == "12025550123-111@g.us"
    assert nested.metadata["is_group"] is True
    assert nested.metadata["participant"] == "15551234567@s.whatsapp.net"
    assert nested.metadata["message_key_id"] == "BAE599999"

    media_only = WebhookChannel()._event_from_body({
        "platform": "whatsapp",
        "chat_id": "12025550123@s.whatsapp.net",
        "attachments": [{"type": "image/png", "filename": "photo.png"}],
    })
    assert media_only.text == "[image/png attached: photo.png]"
    assert media_only.attachments == [{"type": "image/png", "filename": "photo.png"}]
    assert WebhookChannel()._delivery_id({}, {"key": {"id": "BAE599999"}}) == "body:key.id:BAE599999"

    image = WebhookChannel()._event_from_body({
        "platform": "baileys",
        "key": {
            "remoteJid": "12025550123@s.whatsapp.net",
            "id": "BAEIMAGE1",
        },
        "message": {
            "imageMessage": {
                "caption": "look at this",
                "mimetype": "image/jpeg",
                "fileName": "photo.jpg",
                "fileLength": "2048",
                "url": "https://mmg.whatsapp.net/o1/v/example",
                "mediaKey": "redacted-by-adapter",
            },
        },
    })
    assert image.text == "look at this"
    assert image.attachments == [{
        "id": "BAEIMAGE1",
        "type": "image/jpeg",
        "media_type": "image/jpeg",
        "filename": "photo.jpg",
        "source": "whatsapp",
        "caption": "look at this",
        "url": "https://mmg.whatsapp.net/o1/v/example",
        "size": 2048,
        "media_key_present": True,
    }]

    voice_only = WebhookChannel()._event_from_body({
        "platform": "whatsapp-web.js",
        "event": {
            "messages": [{
                "key": {
                    "remoteJid": "12025550123@s.whatsapp.net",
                    "id": "BAEVOICE1",
                },
                "message": {
                    "viewOnceMessage": {
                        "message": {
                            "audioMessage": {
                                "mimetype": "audio/ogg",
                                "seconds": "9",
                                "ptt": True,
                                "directPath": "/v/t62.7117-24/voice.enc",
                                "localPath": "/tmp/voice.ogg",
                            },
                        },
                    },
                },
            }],
        },
    })
    assert voice_only.text == "[audio/ogg attached: audio]"
    assert voice_only.attachments == [{
        "id": "BAEVOICE1",
        "type": "audio/ogg",
        "media_type": "audio/ogg",
        "filename": "audio",
        "source": "whatsapp",
        "direct_path": "/v/t62.7117-24/voice.enc",
        "path": "/tmp/voice.ogg",
        "seconds": 9,
        "ptt": True,
    }]

    document = WebhookChannel()._event_from_body({
        "platform": "whatsapp",
        "chat_id": "12025550123@s.whatsapp.net",
        "message": {
            "documentWithCaptionMessage": {
                "message": {
                    "documentMessage": {
                        "mimetype": "application/pdf",
                        "fileName": "brief.pdf",
                        "caption": "brief",
                        "fileLength": 4096,
                    },
                },
            },
        },
    })
    assert document.text == "[application/pdf attached: brief.pdf]"
    assert document.attachments == [{
        "id": "",
        "type": "application/pdf",
        "media_type": "application/pdf",
        "filename": "brief.pdf",
        "source": "whatsapp",
        "caption": "brief",
        "size": 4096,
    }]

    button_reply = WebhookChannel()._event_from_body({
        "platform": "baileys",
        "key": {
            "remoteJid": "12025550123@s.whatsapp.net",
            "id": "BAEBUTTON1",
        },
        "message": {
            "buttonsResponseMessage": {
                "selectedButtonId": "approve",
                "selectedDisplayText": "Approve",
            },
        },
    })
    assert button_reply.text == "Approve"
    assert button_reply.metadata["source"] == "interactive_response"

    list_reply = WebhookChannel()._event_from_body({
        "platform": "whatsapp-web.js",
        "message": {
            "listResponseMessage": {
                "title": "canary",
                "singleSelectReply": {"selectedRowId": "deploy-canary"},
            },
        },
        "chat_id": "12025550123@s.whatsapp.net",
    })
    assert list_reply.text == "canary"
    assert list_reply.metadata["source"] == "interactive_response"

    generic_action = WebhookChannel()._event_from_body({
        "platform": "webhook",
        "chat_id": "ops",
        "type": "approval_response",
        "action": {
            "value": "deny",
            "prompt_id": "exec_approval:prompt-1",
            "prompt_kind": "exec_approval",
        },
    })
    assert generic_action.text == "deny"
    assert generic_action.metadata["source"] == "interactive_response"
    assert generic_action.metadata["prompt_id"] == "exec_approval:prompt-1"
    assert generic_action.metadata["prompt_kind"] == "exec_approval"

    data_wrapped = WebhookChannel()._event_from_body({
        "platform": "whatsapp-web.js",
        "data": {
            "key": {
                "remoteJid": "12025550123-222@g.us",
                "participant": "15557654321@s.whatsapp.net",
                "id": "BAE511111",
            },
            "message": {
                "extendedTextMessage": {
                    "text": "nested under data.message",
                    "contextInfo": {"stanzaId": "QUOTE999"},
                },
            },
        },
    })

    assert data_wrapped.platform == "whatsapp"
    assert data_wrapped.chat_id == "12025550123-222@g.us"
    assert data_wrapped.text == "nested under data.message"
    assert data_wrapped.user_id == "15557654321@s.whatsapp.net"
    assert data_wrapped.message_id == "BAE511111"
    assert data_wrapped.reply_to_message_id == "QUOTE999"
    assert data_wrapped.metadata["remote_jid"] == "12025550123-222@g.us"
    assert data_wrapped.metadata["participant"] == "15557654321@s.whatsapp.net"

    event_wrapped = WebhookChannel()._event_from_body({
        "platform": "baileys",
        "event": {
            "messages": [{
                "key": {
                    "remoteJid": "12025550123-333@g.us",
                    "participant": "15550001111@s.whatsapp.net",
                    "id": "BAE522222",
                },
                "message": {
                    "extendedTextMessage": {
                        "text": "array wrapped text",
                        "contextInfo": {
                            "stanzaId": "QUOTEARRAY",
                            "quotedMessage": {
                                "extendedTextMessage": {"text": "array quoted text"},
                            },
                        },
                    },
                },
            }],
        },
    })

    assert event_wrapped.platform == "whatsapp"
    assert event_wrapped.chat_id == "12025550123-333@g.us"
    assert event_wrapped.text == "array wrapped text"
    assert event_wrapped.user_id == "15550001111@s.whatsapp.net"
    assert event_wrapped.message_id == "BAE522222"
    assert event_wrapped.reply_to_message_id == "QUOTEARRAY"
    assert event_wrapped.reply_to_text == "array quoted text"
    assert event_wrapped.metadata["remote_jid"] == "12025550123-333@g.us"
    assert event_wrapped.metadata["group_jid"] == "12025550123-333@g.us"
    assert event_wrapped.metadata["participant"] == "15550001111@s.whatsapp.net"
    assert WebhookChannel()._delivery_id({}, {
        "event": {"messages": [{"key": {"id": "BAE522222"}}]},
    }) == "body:event.messages.0.key.id:BAE522222"


def test_gateway_webhook_channel_ignores_whatsapp_broadcast_pseudo_chats():
    from aegis.gateway.webhook_channel import WebhookChannel

    seen = []
    channel = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    channel._init_inbound_queue(lambda ev: seen.append(ev) or "should not run")

    status, payload = channel._handle_inbound_payload(
        {},
        {"platform": "whatsapp", "chatId": "status@broadcast", "text": "story update"},
    )

    assert status == 200
    assert payload == {"reply": "", "ignored": True, "reason": "whatsapp_broadcast_chat"}
    assert seen == []

    status, payload = channel._handle_inbound_payload(
        {},
        {"platform": "whatsapp", "data": {"chatId": "120363000000000000@newsletter", "text": "news"}},
    )

    assert status == 200
    assert payload["ignored"] is True
    assert seen == []


def test_gateway_webhook_channel_ignores_whatsapp_bridge_self_echoes():
    from aegis.gateway.webhook_channel import WebhookChannel

    seen = []
    channel = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    channel._init_inbound_queue(lambda ev: seen.append(ev) or "should not run")

    status, payload = channel._handle_inbound_payload(
        {},
        {
            "platform": "baileys",
            "key": {
                "remoteJid": "12025550123@s.whatsapp.net",
                "id": "BAESELF",
                "fromMe": True,
            },
            "message": {"conversation": "echoed outbound reply"},
        },
    )

    assert status == 200
    assert payload == {"reply": "", "ignored": True, "reason": "whatsapp_self_echo"}
    assert seen == []
    assert channel._delivery_cache.stats()["entries"] == 0

    channel._submit_inbound = lambda ev, *, wait=False: seen.append((ev.chat_id, ev.text, wait)) or "reply"
    retry_status, retry_payload = channel._handle_inbound_payload(
        {},
        {
            "platform": "baileys",
            "key": {
                "remoteJid": "12025550123@s.whatsapp.net",
                "id": "BAESELF",
            },
            "message": {"conversation": "real inbound"},
        },
    )

    assert retry_status == 200
    assert retry_payload == {"reply": "reply"}
    assert seen == [("12025550123@s.whatsapp.net", "real inbound", True)]

    status, payload = channel._handle_inbound_payload(
        {},
        {
            "platform": "whatsapp-web.js",
            "data": {
                "messages": [{
                    "key": {
                        "remoteJid": "12025550123@s.whatsapp.net",
                        "id": "BAESELF2",
                        "from_me": "true",
                    },
                    "message": {"conversation": "echoed nested reply"},
                }],
            },
        },
    )

    assert status == 200
    assert payload["ignored"] is True
    assert payload["reason"] == "whatsapp_self_echo"
    assert seen == [("12025550123@s.whatsapp.net", "real inbound", True)]


def test_gateway_webhook_channel_prefix_insecure_auth_override(monkeypatch):
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WHATSAPP_CHANNEL_INSECURE_NO_AUTH", "1")
    adapter = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")

    assert adapter._insecure_no_auth() is True
    assert adapter.metadata["security"]["insecure_env_override"] is True


def test_gateway_webhook_channel_can_disable_unsigned_loopback(monkeypatch):
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.delenv("WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK", raising=False)
    adapter = WebhookChannel()
    assert adapter.allow_unsigned_loopback is False
    assert adapter.metadata["security"]["loopback_unsigned_allowed"] is False
    assert adapter._auth_allowed({}, b"{}", "127.0.0.1") is False

    monkeypatch.setenv("WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK", "1")
    loopback = WebhookChannel()
    assert loopback.allow_unsigned_loopback is True
    assert loopback._auth_allowed({}, b"{}", "127.0.0.1") is True
    assert loopback._auth_allowed({}, b"{}", "203.0.113.9") is False

    monkeypatch.setenv("WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK", "0")
    adapter = WebhookChannel()

    assert adapter.allow_unsigned_loopback is False
    assert adapter.metadata["security"]["loopback_unsigned_allowed"] is False
    assert adapter._auth_allowed({}, b"{}", "127.0.0.1") is False

    monkeypatch.setenv("WEBHOOK_CHANNEL_INSECURE_NO_AUTH", "1")
    insecure = WebhookChannel()
    assert insecure._auth_allowed({}, b"{}", "127.0.0.1") is True


def test_gateway_webhook_channel_auth_and_delivery_headers_are_case_insensitive(monkeypatch):
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WEBHOOK_CHANNEL_SECRET", "bridge-secret")
    adapter = WebhookChannel()

    assert adapter._auth_allowed({"x-secret": "bridge-secret"}, b"{}", "203.0.113.10") is True
    assert adapter._auth_allowed({"x-secret": "wrong"}, b"{}", "203.0.113.10") is False
    assert adapter._delivery_id({"idempotency-key": "delivery-1"}, {}) == "idempotency-key:delivery-1"

    adapter._submit_inbound = lambda ev, *, wait=False: f"reply:{ev.platform}:{ev.text}"
    headers = {"idempotency-key": "delivery-1"}
    body = {"platform": "webhook", "chat_id": "c1", "text": "hello"}

    status, payload = adapter._handle_inbound_payload(headers, body)
    duplicate_status, duplicate_payload = adapter._handle_inbound_payload(headers, body)

    assert status == 200
    assert payload == {"reply": "reply:webhook:hello"}
    assert duplicate_status == 200
    assert duplicate_payload == {"reply": "", "duplicate": True}
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = WebhookChannel()
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    restart_status, restart_payload = after_restart._handle_inbound_payload(headers, body)
    assert restart_status == 200
    assert restart_payload == {"reply": "", "duplicate": True}
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1


def test_gateway_webhook_channel_handler_only_accepts_in_path(monkeypatch):
    import http.client
    import json
    from http.server import ThreadingHTTPServer

    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.delenv("WEBHOOK_CHANNEL_SECRET", raising=False)
    monkeypatch.setenv("WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK", "1")
    adapter = WebhookChannel()
    adapter._init_inbound_queue(lambda ev: f"reply:{ev.text}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), adapter._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path: str):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            conn.request(
                "POST",
                path,
                body=b'{"chat_id":"c1","text":"hello"}',
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = response.read()
            return response.status, body
        finally:
            conn.close()

    try:
        wrong_status, wrong_body = post("/wrong")
        ok_status, ok_body = post("/in")
    finally:
        server.shutdown()
        thread.join(2)

    assert wrong_status == 404
    assert wrong_body == b""
    assert ok_status == 200
    assert json.loads(ok_body.decode("utf-8")) == {"reply": "reply:hello"}


def test_gateway_webhook_channel_allowed_platforms_normalize_aliases(monkeypatch):
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WEBHOOK_CHANNEL_ALLOWED_PLATFORMS", "telegram, whatsapp")
    adapter = WebhookChannel()
    seen = []
    adapter._submit_inbound = lambda ev, *, wait=False: seen.append((ev.platform, ev.chat_id, ev.text, wait)) or "ok"

    status, payload = adapter._handle_inbound_payload(
        {},
        {"platform": "tg", "chat_id": "42", "text": "hello"},
    )
    blocked_status, blocked_payload = adapter._handle_inbound_payload(
        {},
        {"platform": "discord", "chat_id": "99", "text": "nope"},
    )

    assert status == 200
    assert payload == {"reply": "ok"}
    assert seen == [("telegram", "42", "hello", True)]
    assert blocked_status == 403
    assert blocked_payload == {
        "reply": "",
        "error": "platform not allowed",
        "platform": "discord",
        "allowed_platforms": ["telegram", "whatsapp"],
    }
    assert adapter.metadata["security"]["allowed_platforms"] == ["telegram", "whatsapp"]
    assert adapter.metadata["security"]["allowed_platforms_env"] == "WEBHOOK_CHANNEL_ALLOWED_PLATFORMS"


def test_gateway_webhook_channel_allows_retry_after_dispatch_failure():
    from aegis.gateway.webhook_channel import WebhookChannel

    adapter = WebhookChannel()
    attempts = []

    def fail_once(ev, *, wait=False):
        attempts.append((ev.chat_id, ev.text, wait))
        raise RuntimeError("bridge down")

    adapter._submit_inbound = fail_once
    headers = {"Idempotency-Key": "delivery-1"}
    body = {"chat_id": "c1", "text": "hello"}

    status, payload = adapter._handle_inbound_payload(headers, body)
    assert status == 500
    assert "bridge down" in payload["error"]
    assert adapter._delivery_cache.stats()["entries"] == 0
    assert adapter._delivery_cache.stats()["discarded_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    adapter._submit_inbound = lambda ev, *, wait=False: f"reply:{ev.text}"
    retry_status, retry_payload = adapter._handle_inbound_payload(headers, body)
    duplicate_status, duplicate_payload = adapter._handle_inbound_payload(headers, body)

    assert retry_status == 200
    assert retry_payload == {"reply": "reply:hello"}
    assert duplicate_status == 200
    assert duplicate_payload == {"reply": "", "duplicate": True}
    assert attempts == [("c1", "hello", True)]


def _assert_signed_bridge_posts(sent, expected_payloads):
    import hashlib
    import hmac
    import json

    from aegis.webhook import verify_signature

    assert len(sent) == len(expected_payloads)
    for (url, headers, content), expected in zip(sent, expected_payloads, strict=True):
        assert url == "https://bridge.test/send"
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Secret"] == "outbound-secret"
        payload = json.loads(content.decode("utf-8"))
        delivery_id = payload.get("delivery_id")
        assert delivery_id
        assert headers["Idempotency-Key"] == delivery_id
        assert headers["X-Aegis-Delivery-Id"] == delivery_id
        timestamp = headers["X-Webhook-Timestamp"]
        assert timestamp.isdigit()
        expected_signature = "sha256=" + hmac.new(
            b"outbound-secret",
            f"{timestamp}.{delivery_id}.".encode("utf-8") + content,
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Webhook-Signature"] == expected_signature
        assert verify_signature("outbound-secret", content, headers) is True
        assert payload == {**expected, "delivery_id": delivery_id}


def test_gateway_webhook_channel_outbound_payload_reuses_delivery_id(monkeypatch):
    import json

    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WEBHOOK_CHANNEL_OUTBOUND_URL", "https://bridge.test/send")
    monkeypatch.setenv("WEBHOOK_CHANNEL_OUTBOUND_SECRET", "outbound-secret")
    adapter = WebhookChannel()
    payload = adapter._outbound_payload("ops", "hello", {"thread_id": "t1"})

    first_payload, first_body, first_headers = adapter._prepare_outbound_request(payload)
    second_payload, second_body, second_headers = adapter._prepare_outbound_request(payload)

    assert first_payload["delivery_id"] == second_payload["delivery_id"]
    assert first_headers["Idempotency-Key"] == second_headers["Idempotency-Key"]
    assert json.loads(first_body)["delivery_id"] == json.loads(second_body)["delivery_id"]
    assert first_headers["X-Aegis-Delivery-Id"] == second_headers["X-Aegis-Delivery-Id"]


def test_gateway_webhook_channel_outbound_bridge_send(monkeypatch):
    from aegis.gateway import webhook_channel
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_URL", "https://bridge.test/send")
    monkeypatch.setenv("WHATSAPP_CHANNEL_SECRET", "inbound-secret")
    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_SECRET", "outbound-secret")
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, content):
            sent.append((url, dict(headers), bytes(content)))
            return FakeResponse()

    monkeypatch.setattr(webhook_channel.httpx, "Client", FakeClient)

    adapter = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    adapter.send(
        "12025550123-111@g.us",
        "hello",
        metadata={
            "bridge_platform": "baileys",
            "remote_jid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "message_key_id": "BAE599999",
            "thread_id": "thread-1",
        },
    )
    adapter.send_clarify(
        "12025550123-111@g.us",
        "Pick a deploy lane?",
        ["stable", "canary"],
        metadata={
            "bridge_platform": "baileys",
            "remote_jid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "message_key_id": "BAE599999",
            "thread_id": "thread-1",
        },
    )
    adapter.send_exec_approval(
        "12025550123-111@g.us",
        "Run deploy?",
        metadata={
            "bridge_platform": "baileys",
            "remote_jid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "message_key_id": "BAE599999",
            "thread_id": "thread-1",
        },
    )

    base_payload = {
        "platform": "whatsapp",
        "chat_id": "12025550123-111@g.us",
        "metadata": {
            "bridge_platform": "baileys",
            "remote_jid": "12025550123-111@g.us",
            "participant": "15551234567@s.whatsapp.net",
            "message_key_id": "BAE599999",
            "thread_id": "thread-1",
        },
        "thread_id": "thread-1",
        "remote_jid": "12025550123-111@g.us",
        "participant": "15551234567@s.whatsapp.net",
        "reply_to_message_id": "BAE599999",
    }
    _assert_signed_bridge_posts(sent, [
        {**base_payload, "text": "hello"},
        {
            **base_payload,
            "text": "Pick a deploy lane?",
            "type": "clarify",
            "question": "Pick a deploy lane?",
            "choices": ["stable", "canary"],
        },
        {
            **base_payload,
            "text": "Run deploy?",
            "type": "exec_approval",
            "prompt": "Run deploy?",
            "choices": ["approve", "always", "deny"],
        },
    ])
    metadata = adapter.metadata
    assert metadata["security"]["outbound_configured"] is True
    assert metadata["security"]["outbound_secret_configured"] is True
    assert metadata["security"]["outbound_signature_schemes"] == [
        "X-Secret",
        "X-Webhook-Signature",
        "Idempotency-Key",
        "X-Aegis-Delivery-Id",
    ]


def test_gateway_webhook_channel_outbound_bridge_media(monkeypatch, tmp_path):
    from aegis.gateway import webhook_channel
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_URL", "https://bridge.test/send")
    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_SECRET", "outbound-secret")
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"voice")
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, content):
            sent.append((url, dict(headers), bytes(content)))
            return FakeResponse()

    monkeypatch.setattr(webhook_channel.httpx, "Client", FakeClient)

    adapter = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    adapter.deliver(
        "12025550123@s.whatsapp.net",
        f"Here\nMEDIA:{path}",
        metadata={
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
    )

    _assert_signed_bridge_posts(sent, [
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "text": "Here",
            "metadata": {
                "remote_jid": "12025550123@s.whatsapp.net",
                "reply_to_message_id": "BAE599999",
            },
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "text": "",
            "metadata": {
                "remote_jid": "12025550123@s.whatsapp.net",
                "reply_to_message_id": "BAE599999",
            },
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
            "type": "media",
            "path": str(path),
            "caption": "",
        },
    ])
    sent.clear()

    missing = tmp_path / "missing.ogg"
    adapter.deliver(
        "12025550123@s.whatsapp.net",
        f"Here\nMEDIA:{missing}",
        metadata={
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
    )
    _assert_signed_bridge_posts(sent, [
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "text": "Here",
            "metadata": {
                "remote_jid": "12025550123@s.whatsapp.net",
                "reply_to_message_id": "BAE599999",
            },
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "text": "📎 blocked media path: file not found",
            "metadata": {
                "remote_jid": "12025550123@s.whatsapp.net",
                "reply_to_message_id": "BAE599999",
            },
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
    ])
    sent.clear()

    adapter.send_media(
        "12025550123@s.whatsapp.net",
        str(missing),
        metadata={
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
    )
    _assert_signed_bridge_posts(sent, [
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "text": f"(file not found: {missing})",
            "metadata": {
                "remote_jid": "12025550123@s.whatsapp.net",
                "reply_to_message_id": "BAE599999",
            },
            "remote_jid": "12025550123@s.whatsapp.net",
            "reply_to_message_id": "BAE599999",
        },
    ])


def test_gateway_webhook_channel_outbound_bridge_reactions(monkeypatch):
    from aegis.gateway import webhook_channel
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_URL", "https://bridge.test/send")
    monkeypatch.setenv("WHATSAPP_CHANNEL_OUTBOUND_SECRET", "outbound-secret")
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, content):
            sent.append((url, dict(headers), bytes(content)))
            return FakeResponse()

    monkeypatch.setattr(webhook_channel.httpx, "Client", FakeClient)

    adapter = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")
    adapter.add_reaction("12025550123@s.whatsapp.net", "BAE599999", "✅")
    adapter.remove_reaction("12025550123@s.whatsapp.net", "BAE599999", "✅")

    _assert_signed_bridge_posts(sent, [
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "type": "reaction",
            "action": "add",
            "message_id": "BAE599999",
            "reaction": "✅",
        },
        {
            "platform": "whatsapp",
            "chat_id": "12025550123@s.whatsapp.net",
            "type": "reaction",
            "action": "remove",
            "message_id": "BAE599999",
            "reaction": "✅",
        },
    ])


def test_gateway_delivery_preserves_event_metadata_for_adapter_send():
    from aegis.gateway.base import BasePlatformAdapter, MessageEvent

    class MetadataAdapter(BasePlatformAdapter):
        name = "metadata"

        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
            self.sent.append((chat_id, text, dict(metadata or {})))

    adapter = MetadataAdapter()
    ev = MessageEvent(
        platform="whatsapp",
        chat_id="c1",
        text="prompt",
        user_id="u1",
        thread_id="thread-1",
        message_id="m1",
        metadata={"remote_jid": "c1"},
    )

    adapter._deliver_reply(ev, "reply")

    assert adapter.sent == [(
        "c1",
        "reply",
        {
            "remote_jid": "c1",
            "platform": "whatsapp",
            "thread_id": "thread-1",
            "message_id": "m1",
            "user_id": "u1",
        },
    )]


def test_gateway_mattermost_channel_normalizes_event_body_and_alias(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter
    from aegis.platforms import normalize_platform_name

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")

    adapter = MattermostAdapter()
    ev = adapter._event_from_body({
        "channel_id": "channel-1",
        "text": "!status",
        "user_id": "user-1",
        "user_name": "ada",
        "post_id": "post-1",
        "root_id": "root-1",
        "team_id": "team-1",
        "channel_name": "ops",
    })

    assert normalize_platform_name("mm") == "mattermost"
    assert ev.platform == "mattermost"
    assert ev.chat_id == "channel-1"
    assert ev.text == "/status"
    assert ev.user_id == "user-1"
    assert ev.user_name == "ada"
    assert ev.thread_id == "root-1"
    assert ev.message_id == "post-1"
    assert ev.metadata["team_id"] == "team-1"
    assert ev.metadata["root_id"] == "root-1"

    root_post = adapter._event_from_body({
        "channel_id": "channel-1",
        "text": "hello",
        "user_id": "user-1",
        "post_id": "post-2",
        "root_id": "",
    })
    assert root_post.thread_id is None
    assert root_post.message_id == "post-2"
    assert root_post.metadata["post_id"] == "post-2"
    assert root_post.metadata["root_id"] == ""

    parent = adapter._event_from_body({
        "channel_id": "channel-1",
        "text": "parent alias",
        "user_id": "user-1",
        "post_id": "post-3",
        "parent_id": "root-3",
    })
    assert parent.thread_id == "root-3"
    assert parent.metadata["root_id"] == "root-3"

    self_root = adapter._event_from_body({
        "channel_id": "channel-1",
        "text": "self root",
        "user_id": "user-1",
        "post_id": "post-4",
        "root_id": "post-4",
    })
    assert self_root.thread_id is None
    assert self_root.metadata["root_id"] == ""

    file_only = adapter._event_from_body({
        "channel_id": "channel-1",
        "user_id": "user-1",
        "post_id": "post-5",
        "file_ids": ["file-1"],
        "files": [{
            "id": "file-2",
            "name": "diagram.png",
            "mime_type": "image/png",
            "size": 2048,
        }],
    })
    assert file_only.text == "[file attached: file-1]\n[image/png attached: diagram.png]"
    assert file_only.attachments == [
        {
            "id": "file-1",
            "type": "file",
            "filename": "file-1",
            "source": "mattermost",
        },
        {
            "id": "file-2",
            "type": "image/png",
            "media_type": "image/png",
            "filename": "diagram.png",
            "url": "",
            "source": "mattermost",
            "size": 2048,
        },
    ]
    scalar_files = adapter._event_from_body({
        "channel_id": "channel-1",
        "user_id": "user-1",
        "post_id": "post-6",
        "file_ids": "file-3,file-4",
    })
    assert scalar_files.text == "[file attached: file-3]\n[file attached: file-4]"
    assert scalar_files.attachments == [
        {
            "id": "file-3",
            "type": "file",
            "filename": "file-3",
            "source": "mattermost",
        },
        {
            "id": "file-4",
            "type": "file",
            "filename": "file-4",
            "source": "mattermost",
        },
    ]

    action = adapter._event_from_body({
        "channel_id": "channel-1",
        "user_id": "user-1",
        "user_name": "ada",
        "post_id": "post-action",
        "action_id": "aegis_exec_approval",
        "context": {"type": "exec_approval", "value": "approve"},
    })
    assert action.text == "approve"
    assert action.metadata["source"] == "interactive_action"
    assert action.metadata["action_id"] == "aegis_exec_approval"
    assert action.metadata["action_type"] == "exec_approval"


def test_gateway_mattermost_native_slash_commands_preserve_command_name(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")

    adapter = MattermostAdapter()
    ev = adapter._event_from_body({
        "command": "/status",
        "text": "full",
        "channel_id": "channel-1",
        "channel_name": "ops",
        "user_id": "user-1",
        "user_name": "ada",
        "team_id": "team-1",
        "trigger_id": "trigger-1",
        "response_url": "https://mattermost.test/hooks/response",
    })

    assert ev.platform == "mattermost"
    assert ev.chat_id == "channel-1"
    assert ev.text == "/status full"
    assert ev.user_id == "user-1"
    assert ev.user_name == "ada"
    assert ev.metadata["source"] == "slash_command"
    assert ev.metadata["command"] == "/status"
    assert ev.metadata["response_url"] == "https://mattermost.test/hooks/response"
    assert ev.metadata["trigger_id"] == "trigger-1"


def test_gateway_mattermost_webhook_secret_accepts_headers_and_body(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_WEBHOOK_SECRET", "secret-token")

    adapter = MattermostAdapter()

    assert adapter._verify_webhook({"X-Secret": "secret-token"}, {}) is True
    assert adapter._verify_webhook({"x-secret": "secret-token"}, {}) is True
    assert adapter._verify_webhook({"X-Mattermost-Token": "secret-token"}, {}) is True
    assert adapter._verify_webhook({"x-mattermost-token": "secret-token"}, {}) is True
    assert adapter._verify_webhook({}, {"token": "secret-token"}) is True
    assert adapter._verify_webhook({}, {"token": "wrong"}) is False


def test_gateway_mattermost_inbound_auth_idempotency_and_rate_limit(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.delenv("MATTERMOST_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("MATTERMOST_ALLOW_UNSIGNED_LOOPBACK", "0")
    monkeypatch.setenv("MATTERMOST_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("MATTERMOST_CHANNEL_MAX_BYTES", "2048")

    adapter = MattermostAdapter()
    assert adapter.metadata["security"]["loopback_unsigned_allowed"] is False
    assert adapter.metadata["security"]["max_body_bytes"] == 2048
    assert adapter._payload_size_error(2048) is None
    assert adapter._payload_size_error(2049) == (413, {"error": "payload too large"})
    assert adapter._auth_allowed({}, {}, "127.0.0.1") is False
    for _ in range(3):
        status, payload = adapter._handle_inbound_payload(
            {},
            {"channel_id": "channel-1", "text": "bad auth", "post_id": "bad-auth"},
            client_host="203.0.113.10",
        )
        assert status == 401
        assert payload == {"error": "invalid webhook token"}
    assert adapter._rate_limiter.stats()["allowed_count"] == 0

    monkeypatch.setenv("MATTERMOST_INSECURE_NO_AUTH", "1")
    adapter = MattermostAdapter()
    seen = []
    adapter._submit_inbound = lambda ev, *, wait=False: seen.append((ev.chat_id, ev.text, wait)) or "reply"

    body = {"channel_id": "channel-1", "text": "hello"}
    headers = {"idempotency-key": "post-1"}
    status, payload = adapter._handle_inbound_payload(headers, body, client_host="203.0.113.10")
    duplicate_status, duplicate_payload = adapter._handle_inbound_payload(headers, body, client_host="203.0.113.10")

    assert status == 200
    assert payload == {"text": "reply", "response_type": "comment"}
    assert duplicate_status == 200
    assert duplicate_payload == {"text": "", "response_type": "comment", "duplicate": True}
    assert seen == [("channel-1", "hello", True)]
    assert adapter.metadata["idempotency"]["delivery_store"]["entries"] == 1

    after_restart = MattermostAdapter()
    after_restart._submit_inbound = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("durable duplicate should not dispatch")
    )
    restart_status, restart_payload = after_restart._handle_inbound_payload(
        headers,
        body,
        client_host="203.0.113.10",
    )
    assert restart_status == 200
    assert restart_payload == {"text": "", "response_type": "comment", "duplicate": True}
    assert after_restart.metadata["idempotency"]["delivery_store"]["duplicate_count"] == 1

    limited_status, limited_payload = adapter._handle_inbound_payload(
        {},
        {"channel_id": "channel-1", "text": "again", "post_id": "post-2"},
        client_host="203.0.113.10",
    )
    assert limited_status == 429
    assert limited_payload == {"error": "rate limit exceeded"}


def test_gateway_mattermost_ignores_bot_self_echo(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_BOT_USER_ID", "bot-1")
    monkeypatch.delenv("MATTERMOST_WEBHOOK_SECRET", raising=False)

    adapter = MattermostAdapter()
    seen = []
    adapter._submit_inbound = lambda ev, *, wait=False: seen.append(ev) or "should not run"

    status, payload = adapter._handle_inbound_payload(
        {},
        {"channel_id": "channel-1", "text": "echo", "user_id": "bot-1", "post_id": "post-1"},
    )

    assert status == 200
    assert payload == {"text": "", "response_type": "comment", "ignored": True, "reason": "bot_self_echo"}
    assert seen == []
    assert adapter._delivery_cache.stats()["entries"] == 0


def test_gateway_mattermost_allows_retry_after_dispatch_failure(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_WEBHOOK_SECRET", "secret-token")

    adapter = MattermostAdapter()
    attempts = []

    def fail_once(ev, *, wait=False):
        attempts.append((ev.chat_id, ev.text, wait))
        raise RuntimeError("mattermost down")

    adapter._submit_inbound = fail_once
    headers = {"X-Secret": "secret-token", "Idempotency-Key": "delivery-1"}
    body = {"channel_id": "channel-1", "text": "hello"}

    status, payload = adapter._handle_inbound_payload(headers, body)
    assert status == 500
    assert "mattermost down" in payload["error"]
    assert adapter._delivery_cache.stats()["entries"] == 0
    assert adapter._delivery_cache.stats()["discarded_count"] == 1
    assert adapter.metadata["idempotency"]["delivery_store"]["discarded_count"] == 1

    adapter._submit_inbound = lambda ev, *, wait=False: f"reply:{ev.text}"
    retry_status, retry_payload = adapter._handle_inbound_payload(headers, body)
    duplicate_status, duplicate_payload = adapter._handle_inbound_payload(headers, body)

    assert retry_status == 200
    assert retry_payload == {"text": "reply:hello", "response_type": "comment"}
    assert duplicate_status == 200
    assert duplicate_payload == {"text": "", "response_type": "comment", "duplicate": True}
    assert attempts == [("channel-1", "hello", True)]


def test_gateway_mattermost_send_uses_clean_root_id(monkeypatch):
    from aegis.gateway import mattermost_channel
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            sent.append((url, headers, dict(json)))
            return FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    adapter.send("channel-1", "first", metadata={"thread_id": "channel-1"})
    adapter.send("channel-1", "reply", metadata={"thread_id": "root-1"})
    adapter.send("channel-1", "root post", metadata={"post_id": "post-1"})
    adapter.send("channel-1", "null root", metadata={"root_id": "undefined"})
    adapter.send("channel-1", "parent reply", metadata={"parent_id": "root-2"})
    adapter.send("channel-1", "self root", metadata={"root_id": "post-2", "post_id": "post-2"})

    assert sent[0][2] == {"channel_id": "channel-1", "message": "first"}
    assert sent[1][2] == {"channel_id": "channel-1", "message": "reply", "root_id": "root-1"}
    assert sent[2][2] == {"channel_id": "channel-1", "message": "root post"}
    assert sent[3][2] == {"channel_id": "channel-1", "message": "null root"}
    assert sent[4][2] == {"channel_id": "channel-1", "message": "parent reply", "root_id": "root-2"}
    assert sent[5][2] == {"channel_id": "channel-1", "message": "self root"}


def test_gateway_mattermost_send_media_uploads_native_file(monkeypatch, tmp_path):
    from aegis.gateway import mattermost_channel
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    path = tmp_path / "diagram.png"
    path.write_bytes(b"png-bytes")
    calls = []

    class FakeResponse:
        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, headers):
            calls.append(("GET", url, headers, None))
            return FakeResponse({"id": "root-1", "root_id": ""})

        def post(self, url, *, headers, json=None, data=None, files=None):
            if files:
                filename, handle = files["files"]
                calls.append(("UPLOAD", url, headers, dict(data or {}), filename, handle.read()))
                return FakeResponse({"file_infos": [{"id": "file-1"}]})
            calls.append(("POST", url, headers, dict(json or {})))
            return FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    adapter.send_media("channel-1", str(path), caption="see diagram", metadata={"root_id": "root-1"})

    assert calls == [
        (
            "GET",
            "https://mattermost.test/api/v4/posts/root-1",
            {"Authorization": "Bearer mm-token"},
            None,
        ),
        (
            "UPLOAD",
            "https://mattermost.test/api/v4/files",
            {"Authorization": "Bearer mm-token"},
            {"channel_id": "channel-1"},
            "diagram.png",
            b"png-bytes",
        ),
        (
            "POST",
            "https://mattermost.test/api/v4/posts",
            {"Authorization": "Bearer mm-token"},
            {
                "channel_id": "channel-1",
                "message": "see diagram",
                "file_ids": ["file-1"],
                "root_id": "root-1",
            },
        ),
    ]


def test_gateway_mattermost_interactive_prompts_use_action_buttons(monkeypatch):
    from aegis.gateway import mattermost_channel
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_ACTION_URL", "https://aegis.example/mattermost/action")
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            sent.append((url, headers, dict(json)))
            return FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    assert adapter.metadata["security"]["action_url_configured"] is True
    adapter.send_clarify("channel-1", "Pick a deploy lane?", ["stable", "canary"])
    adapter.send_exec_approval("channel-1", "Run deploy?")

    assert sent == [
        (
            "https://mattermost.test/api/v4/posts",
            {"Authorization": "Bearer mm-token"},
            {
                "channel_id": "channel-1",
                "message": "Pick a deploy lane?",
                "props": {
                    "attachments": [{
                        "text": "Pick a deploy lane?",
                        "actions": [
                            {
                                "id": "aegis_clarify",
                                "name": "stable",
                                "integration": {
                                    "url": "https://aegis.example/mattermost/action",
                                    "context": {
                                        "source": "aegis",
                                        "type": "clarify",
                                        "value": "stable",
                                    },
                                },
                            },
                            {
                                "id": "aegis_clarify",
                                "name": "canary",
                                "integration": {
                                    "url": "https://aegis.example/mattermost/action",
                                    "context": {
                                        "source": "aegis",
                                        "type": "clarify",
                                        "value": "canary",
                                    },
                                },
                            },
                        ],
                    }],
                },
            },
        ),
        (
            "https://mattermost.test/api/v4/posts",
            {"Authorization": "Bearer mm-token"},
            {
                "channel_id": "channel-1",
                "message": "Run deploy?",
                "props": {
                    "attachments": [{
                        "text": "Run deploy?",
                        "actions": [
                            {
                                "id": "aegis_exec_approval",
                                "name": "Approve",
                                "integration": {
                                    "url": "https://aegis.example/mattermost/action",
                                    "context": {
                                        "source": "aegis",
                                        "type": "exec_approval",
                                        "value": "approve",
                                    },
                                },
                            },
                            {
                                "id": "aegis_exec_approval",
                                "name": "Always",
                                "integration": {
                                    "url": "https://aegis.example/mattermost/action",
                                    "context": {
                                        "source": "aegis",
                                        "type": "exec_approval",
                                        "value": "always",
                                    },
                                },
                            },
                            {
                                "id": "aegis_exec_approval",
                                "name": "Deny",
                                "integration": {
                                    "url": "https://aegis.example/mattermost/action",
                                    "context": {
                                        "source": "aegis",
                                        "type": "exec_approval",
                                        "value": "deny",
                                    },
                                },
                            },
                        ],
                    }],
                },
            },
        ),
    ]


def test_gateway_mattermost_threaded_interactive_response_consumes_waiter(monkeypatch):
    from aegis.gateway import mattermost_channel
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_ACTION_URL", "https://aegis.example/mattermost/action")
    monkeypatch.delenv("MATTERMOST_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("MATTERMOST_OUTGOING_TOKEN", raising=False)
    sent = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            sent.append((url, headers, dict(json)))
            return FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    adapter._init_inbound_queue(lambda _ev: (_ for _ in ()).throw(AssertionError("should not dispatch")))
    answer = {}
    ev = MessageEvent(
        platform="mattermost",
        chat_id="channel-1",
        text="question",
        user_id="u1",
        thread_id="root-1",
        message_id="post-1",
        metadata={"root_id": "root-1", "post_id": "post-1"},
    )

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick a deploy lane?", ["stable", "canary"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: sent)
    action_context = (
        sent[0][2]["props"]["attachments"][0]["actions"][0]["integration"]["context"]
    )
    assert action_context["value"] == "stable"
    assert action_context["root_id"] == "root-1"
    assert action_context["thread_id"] == "root-1"
    assert action_context["prompt_id"].startswith("clarify:")
    assert action_context["prompt_kind"] == "clarify"

    status, payload = adapter._handle_inbound_payload({}, {
        "type": "interactive",
        "channel_id": "channel-1",
        "post_id": "button-post",
        "user_id": "u1",
        "user_name": "ada",
        "context": action_context,
    })
    thread.join(2)

    assert status == 200
    assert payload == {"text": "", "response_type": "comment"}
    assert answer["text"] == "stable"


def test_gateway_mattermost_reactions_use_bot_identity(monkeypatch):
    from aegis.gateway import mattermost_channel
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.delenv("MATTERMOST_BOT_USER_ID", raising=False)
    calls = []

    class FakeResponse:
        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, headers):
            calls.append(("GET", url, headers, None))
            return FakeResponse({"id": "bot-user-1"})

        def post(self, url, *, headers, json):
            calls.append(("POST", url, headers, dict(json)))
            return FakeResponse()

        def delete(self, url, *, headers):
            calls.append(("DELETE", url, headers, None))
            return FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    adapter.add_reaction("channel-1", "post-1", "✅")
    adapter.remove_reaction("channel-1", "post-1", ":eyes:")

    assert calls == [
        (
            "GET",
            "https://mattermost.test/api/v4/users/me",
            {"Authorization": "Bearer mm-token"},
            None,
        ),
        (
            "POST",
            "https://mattermost.test/api/v4/reactions",
            {"Authorization": "Bearer mm-token"},
            {"user_id": "bot-user-1", "post_id": "post-1", "emoji_name": "white_check_mark"},
        ),
        (
            "DELETE",
            "https://mattermost.test/api/v4/users/bot-user-1/posts/post-1/reactions/eyes",
            {"Authorization": "Bearer mm-token"},
            None,
        ),
    ]


def test_gateway_mattermost_resolves_child_roots_and_falls_back_flat(monkeypatch):
    import pytest

    from aegis.gateway import mattermost_channel
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    calls = []
    post_responses = []
    get_payloads = {}

    class FakeResponse:
        def __init__(self, status_code=200, text="", payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}
            self.request = mattermost_channel.httpx.Request("POST", "https://mattermost.test/api/v4/posts")

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                response = mattermost_channel.httpx.Response(
                    self.status_code,
                    text=self.text,
                    request=self.request,
                )
                raise mattermost_channel.httpx.HTTPStatusError(
                    self.text or "mattermost error",
                    request=self.request,
                    response=response,
                )

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, headers):
            calls.append(("GET", url, headers, None))
            post_id = url.rsplit("/", 1)[-1]
            payload = get_payloads.get(post_id)
            if payload is None:
                return FakeResponse(404, "root post not found")
            return FakeResponse(payload=payload)

        def post(self, url, *, headers, json):
            calls.append(("POST", url, headers, dict(json)))
            return post_responses.pop(0) if post_responses else FakeResponse()

    monkeypatch.setattr(mattermost_channel.httpx, "Client", FakeClient)

    adapter = MattermostAdapter()
    get_payloads["child-1"] = {"id": "child-1", "root_id": "root-1"}
    adapter.send("channel-1", "resolved", metadata={"root_id": "child-1"})
    assert calls[-1][3] == {"channel_id": "channel-1", "message": "resolved", "root_id": "root-1"}

    calls.clear()
    post_responses[:] = [FakeResponse(404, "invalid root_id"), FakeResponse()]
    adapter.send("channel-1", "fallback", metadata={"root_id": "stale-root"})
    assert calls[-2][3] == {"channel_id": "channel-1", "message": "fallback", "root_id": "stale-root"}
    assert calls[-1][3] == {"channel_id": "channel-1", "message": "fallback"}

    calls.clear()
    post_responses[:] = [FakeResponse(500, "server exploded")]
    with pytest.raises(mattermost_channel.httpx.HTTPStatusError):
        adapter.send("channel-1", "boom", metadata={"root_id": "root-500"})


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
