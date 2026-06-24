from __future__ import annotations

import threading
import time

import pytest

from aegis.platforms import BRIDGE_PLATFORM_DEFINITIONS, normalize_platform_name, platform_metadata


BUILTIN_CHANNELS = [
    "api_server",
    "telegram",
    "discord",
    "slack",
    "signal",
    "matrix",
    "email",
    "mattermost",
    "webhook",
    "whatsapp",
    "ntfy",
]
CONTRACT_CHANNELS = BUILTIN_CHANNELS + sorted(BRIDGE_PLATFORM_DEFINITIONS)


def _wait_for(fn, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class ContractAdapter:
    """Small fake adapter that exercises the shared BasePlatformAdapter contract."""

    def __init__(self, name: str):
        from aegis.gateway.base import BasePlatformAdapter

        class _Adapter(BasePlatformAdapter):
            pass

        self.adapter = _Adapter()
        self.adapter.name = name
        metadata = platform_metadata(name)
        self.adapter.transport = str(metadata.get("transport") or "fake")
        self.adapter.max_message_length = metadata.get("max_message_length")
        self.adapter.supports_threads = bool(metadata.get("supports_threads"))
        self.adapter.supports_media = bool(metadata.get("supports_media"))
        self.adapter.supports_reactions = bool(metadata.get("supports_reactions"))
        self.adapter.splits_long_messages = bool(metadata.get("splits_long_messages"))
        self.adapter.typed_command_prefix = str(metadata.get("typed_command_prefix") or "/")
        self.sent: list[tuple[str, str, dict]] = []

        def send(chat_id: str, text: str, *, metadata: dict | None = None) -> None:
            self.sent.append((chat_id, text, dict(metadata or {})))

        self.adapter.send = send  # type: ignore[method-assign]


@pytest.mark.parametrize("channel", CONTRACT_CHANNELS)
def test_fake_gateway_adapter_contract_parse_prompts_delivery_and_dead_letters(
    tmp_path,
    monkeypatch,
    channel: str,
):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.gateway.base import MessageEvent
    from aegis.gateway.queue import OutboxQueue

    normalized = normalize_platform_name(channel, default=channel)
    contract = ContractAdapter(channel)
    adapter = contract.adapter
    metadata = adapter.metadata
    assert metadata["id"] == normalized
    assert metadata["transport"]
    assert isinstance(metadata["required_env"], list)
    assert isinstance(metadata["optional_env"], list)

    seen = []
    adapter._init_inbound_queue(lambda ev: seen.append(ev) or f"reply:{ev.text}:{len(ev.attachments)}")
    event = MessageEvent(
        platform=channel.replace("_", "-"),
        chat_id="chat-1",
        text="hello",
        user_id="user-1",
        user_name="Ada",
        thread_id="thread-1",
        message_id="message-1",
        reply_to_message_id="message-0",
        session_key="session-1",
        attachments=[{"type": "image", "path": "/tmp/screenshot.png"}],
        metadata={
            "topic": "ops",
            "channel_id": "channel-1",
            "authorization": "Bearer secret",
            "headers": {"X-Secret": "secret"},
            "raw_payload": {"token": "secret"},
        },
    )

    adapter._submit_inbound(event)
    _wait_for(lambda: bool(contract.sent))

    assert seen[0].platform == normalized
    assert seen[0].attachments == [{"type": "image", "path": "/tmp/screenshot.png"}]
    sent_chat, sent_text, sent_metadata = contract.sent[-1]
    assert sent_chat == "chat-1"
    assert sent_text == "reply:hello:1"
    assert sent_metadata["platform"] == normalized
    assert sent_metadata["thread_id"] == "thread-1"
    assert sent_metadata["message_id"] == "message-1"
    assert sent_metadata["reply_to_message_id"] == "message-0"
    assert sent_metadata["session_key"] == "session-1"
    assert sent_metadata["topic"] == "ops"
    assert sent_metadata["channel_id"] == "channel-1"
    assert "authorization" not in sent_metadata
    assert "headers" not in sent_metadata
    assert "raw_payload" not in sent_metadata

    assert adapter.add_reaction("chat-1", "message-1", "ok") is None
    assert adapter.remove_reaction("chat-1", "message-1", "ok") is None
    allowed, reason = adapter.filter_media_path(str(tmp_path / "missing.png"))
    assert allowed is False
    assert reason == "file not found"

    clarify_answer: dict[str, str] = {}

    def ask_clarify() -> None:
        clarify_answer["value"] = adapter.ask_user(event, "Pick one", ["A", "B"], timeout=2)

    thread = threading.Thread(target=ask_clarify)
    thread.start()
    _wait_for(lambda: len(contract.sent) >= 2)
    clarify_metadata = contract.sent[-1][2]
    assert clarify_metadata["prompt_kind"] == "clarify"
    assert clarify_metadata["prompt_user_id"] == "user-1"
    adapter._submit_inbound(MessageEvent(
        platform=channel,
        chat_id="chat-1",
        text="2",
        user_id="user-1",
        thread_id="thread-1",
        session_key="session-1",
        metadata={"prompt_id": clarify_metadata["prompt_id"]},
    ))
    thread.join(2)
    assert clarify_answer["value"] == "B"

    approval_answer: dict[str, str] = {}

    def ask_approval() -> None:
        approval_answer["value"] = adapter.ask_exec_approval(event, "Allow bash(ls)?", timeout=2)

    thread = threading.Thread(target=ask_approval)
    thread.start()
    _wait_for(lambda: len(contract.sent) >= 3)
    approval_metadata = contract.sent[-1][2]
    assert approval_metadata["prompt_kind"] == "exec_approval"
    adapter._submit_inbound(MessageEvent(
        platform=channel,
        chat_id="chat-1",
        text="deny",
        user_id="user-1",
        thread_id="thread-1",
        session_key="session-1",
        metadata={"prompt_id": approval_metadata["prompt_id"]},
    ))
    thread.join(2)
    assert approval_answer["value"] == "deny"

    queue = OutboxQueue()
    queue.enqueue(
        channel,
        "chat-1",
        "deliver sk-proj-" + ("A" * 32),
        thread_id="thread-1",
        metadata={"auth_token": "secret-value", "safe": "ok"},
    )
    row = queue.due()[0]
    assert row["platform"] == normalized
    queue.mark_failed(row["id"], attempts=4, max_attempts=5)
    dead_letter = queue.dead_letters()[0]
    assert dead_letter["platform"] == normalized
    assert dead_letter["thread_id"] == "thread-1"
    assert "[REDACTED]" in dead_letter["text"]
    assert dead_letter["metadata"] == {"auth_token": "[REDACTED]", "safe": "ok", "thread_id": "thread-1"}
