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
        },
    )
    answer = {}

    def ask():
        answer["text"] = adapter.ask_user(ev, "Pick one", ["A", "B"], timeout=2)

    thread = threading.Thread(target=ask)
    thread.start()
    _wait_for(lambda: len(adapter.sent) == 1)
    adapter._submit_inbound(MessageEvent(platform="whatsapp", chat_id=ev.chat_id, thread_id=ev.thread_id, text="A"))
    thread.join(2)

    assert answer["text"] == "A"
    chat_id, text, metadata = adapter.sent[0]
    assert chat_id == ev.chat_id
    assert text == "Pick one\n  1. A\n  2. B"
    assert metadata == {
        "remote_jid": "12025550123-111@g.us",
        "participant": "15551234567@s.whatsapp.net",
        "platform": "whatsapp",
        "thread_id": "thread-1",
        "message_id": "BAE599999",
        "reply_to_message_id": "QUOTE123",
        "user_id": "15551234567@s.whatsapp.net",
        "user_name": "Ada",
    }

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
    assert adapter.sent[1][2] == metadata


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
    assert platform_metadata("matrix")["transport"] == "matrix_sync"
    assert platform_metadata("baileys")["id"] == "whatsapp"
    assert platform_metadata("whatsapp-web.js")["security"]["bridge"] == "webhook"
    assert platform_metadata("mail")["required_env"] == [
        "EMAIL_IMAP_HOST",
        "EMAIL_SMTP_HOST",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
    ]
    assert platform_metadata("ntfy.sh")["optional_env"] == ["NTFY_SERVER", "NTFY_TOKEN"]
    assert "SLACK_TRIGGER_MODE" in platform_metadata("sl")["optional_env"]
    assert platform_metadata("mattermost-webhook")["security"]["auth_type"] == "bearer"
    webhook_meta = platform_metadata("webhooks")
    assert webhook_meta["supports_threads"] is True
    assert "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE" in webhook_meta["optional_env"]
    assert "X-Webhook-Signature" in webhook_meta["security"]["signature_schemes"]


def test_adapter_metadata_for_core_platforms(monkeypatch):
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.discord_channel import DiscordAdapter
    from aegis.gateway.mattermost_channel import MattermostAdapter
    from aegis.gateway.slack_channel import SlackAdapter
    from aegis.gateway.webhook_channel import WebhookChannel

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
    assert TelegramAdapter("token").metadata["security"]["group_trigger_mode"] == "all"
    assert DiscordAdapter("token").metadata["supports_threads"] is True
    assert DiscordAdapter("token").metadata["command_cap"] == 100
    assert "DISCORD_ALLOWED_GUILDS" in DiscordAdapter("token").metadata["optional_env"]
    assert DiscordAdapter("token").metadata["security"]["trigger_mode"] == "all"
    assert len(DiscordAdapter("token").command_menu(max_commands=500)) <= 100
    assert SlackAdapter().metadata["typed_command_prefix"] == "!"
    assert "SLACK_ALLOWED_CHANNELS" in SlackAdapter().metadata["optional_env"]
    assert "SLACK_TRIGGER_MODE" in SlackAdapter().metadata["optional_env"]
    assert SlackAdapter().metadata["security"]["trigger_mode"] == "all"
    mattermost = MattermostAdapter().metadata
    assert mattermost["transport"] == "http_webhook"
    assert mattermost["supports_threads"] is True
    assert mattermost["security"]["auth_type"] == "bearer"
    webhook = WebhookChannel().metadata
    assert webhook["transport"] == "http"
    assert webhook["supports_threads"] is True
    assert webhook["security"]["secret_configured"] is False
    assert "X-Secret" in webhook["security"]["signature_schemes"]
    assert webhook["idempotency"]["delivery_cache"]["entries"] == 0
    assert webhook["rate_limiter"]["limit"] >= 1

    from aegis.gateway.channels import build_adapter
    whatsapp = build_adapter("wa")
    assert whatsapp.name == "whatsapp"
    assert whatsapp.metadata["id"] == "whatsapp"
    assert whatsapp.metadata["transport"] == "http_bridge"
    assert whatsapp.metadata["security"]["env_prefix"] == "WHATSAPP_CHANNEL"
    assert whatsapp.port == 18792


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
    assert adapter._message_allowed({**base, "text": "/status"}, "/status") is True
    assert adapter._message_allowed({
        **base,
        "reply_to_message": {"from": {"id": 123, "username": "aegis_bot"}},
    }, "hello") is True
    assert adapter._message_allowed({**base, "chat": {"id": 99, "type": "supergroup"}}, "/status") is False
    assert adapter._message_allowed({**base, "chat": {"id": 43, "type": "supergroup"}}, "/status") is False
    assert adapter._message_allowed({**base, "chat": {"id": 42, "type": "group"}}, "/status") is False

    topic_msg = {
        **base,
        "text": "@aegis_bot hello",
        "message_thread_id": 77,
        "from": {"id": 7, "username": "ada"},
    }
    assert adapter._message_thread_id(topic_msg) == "77"
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
    }

    rows = adapter._attachments_from_message(msg)

    assert [row["kind"] for row in rows] == ["voice", "audio", "document", "photo", "video"]
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
    assert rows[3]["file_id"] == "large-photo"
    assert rows[4]["width"] == 640
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

    assert api_calls[0] == ("getUpdates", {"offset": 0, "timeout": 60})
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


def test_slack_adapter_enforces_workspace_filters_and_strips_mentions(monkeypatch):
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
    assert adapter._event_allowed({"user": "U3", "channel": "C1", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C2", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C9", "team": "T1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T2"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T1", "bot_id": "B1"}) is False
    assert adapter._event_allowed({"user": "U1", "channel": "C1", "team": "T1", "subtype": "message_changed"}) is False
    assert adapter._strip_own_mentions("<@UBOT> !status") == "!status"
    assert adapter._strip_own_mentions("<@UBOT|aegis> hello") == "hello"

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
    assert ev.metadata == {"bridge": "baileys"}

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
    assert WebhookChannel()._delivery_id({}, {"key": {"id": "BAE599999"}}) == "body:key.id:BAE599999"


def test_gateway_webhook_channel_prefix_insecure_auth_override(monkeypatch):
    from aegis.gateway.webhook_channel import WebhookChannel

    monkeypatch.setenv("WHATSAPP_CHANNEL_INSECURE_NO_AUTH", "1")
    adapter = WebhookChannel(name="whatsapp", default_platform="whatsapp", env_prefix="WHATSAPP_CHANNEL")

    assert adapter._insecure_no_auth() is True
    assert adapter.metadata["security"]["insecure_env_override"] is True


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

    adapter._submit_inbound = lambda ev, *, wait=False: f"reply:{ev.text}"
    retry_status, retry_payload = adapter._handle_inbound_payload(headers, body)
    duplicate_status, duplicate_payload = adapter._handle_inbound_payload(headers, body)

    assert retry_status == 200
    assert retry_payload == {"reply": "reply:hello"}
    assert duplicate_status == 200
    assert duplicate_payload == {"reply": "", "duplicate": True}
    assert attempts == [("c1", "hello", True)]


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

        def post(self, url, *, headers, json):
            sent.append((url, dict(headers), dict(json)))
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

    assert sent == [(
        "https://bridge.test/send",
        {"Content-Type": "application/json", "X-Secret": "outbound-secret"},
        {
            "platform": "whatsapp",
            "chat_id": "12025550123-111@g.us",
            "text": "hello",
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
        },
    )]
    metadata = adapter.metadata
    assert metadata["security"]["outbound_configured"] is True
    assert metadata["security"]["outbound_secret_configured"] is True


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


def test_gateway_mattermost_webhook_secret_accepts_headers_and_body(monkeypatch):
    from aegis.gateway.mattermost_channel import MattermostAdapter

    monkeypatch.setenv("MATTERMOST_URL", "https://mattermost.test")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-token")
    monkeypatch.setenv("MATTERMOST_WEBHOOK_SECRET", "secret-token")

    adapter = MattermostAdapter()

    assert adapter._verify_webhook({"X-Secret": "secret-token"}, {}) is True
    assert adapter._verify_webhook({"X-Mattermost-Token": "secret-token"}, {}) is True
    assert adapter._verify_webhook({}, {"token": "secret-token"}) is True
    assert adapter._verify_webhook({}, {"token": "wrong"}) is False


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
