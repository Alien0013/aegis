"""Slack channel adapter via Socket Mode (requires `slack_bolt`).

Needs SLACK_BOT_TOKEN (xoxb-…) and SLACK_APP_TOKEN (xapp-…, connections:write).
"""

from __future__ import annotations

import os
import re

from ..platforms import capped_command_menu, chunk_text_by_units, normalize_inbound_command
from ..webhook import DeliveryIdCache
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _csv_set(value: str) -> set[str] | None:
    items = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return items or None


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "all"}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class SlackAdapter(BasePlatformAdapter):
    name = "slack"
    renders_tables = False
    transport = "socket_mode"
    max_message_length = 39000
    supports_threads = True
    supports_media = False
    supports_reactions = True
    typed_command_prefix = "!"

    def __init__(self):
        self.bot_token = os.environ.get("SLACK_BOT_TOKEN")
        self.app_token = os.environ.get("SLACK_APP_TOKEN")
        if not self.bot_token or not self.app_token:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set.")
        self.allow_bots = _env_truthy("SLACK_ALLOW_BOTS")
        self.allowed_users = _csv_set(os.environ.get("SLACK_ALLOWED_USERS", ""))
        self.allowed_channels = _csv_set(os.environ.get("SLACK_ALLOWED_CHANNELS", ""))
        self.ignored_channels = _csv_set(os.environ.get("SLACK_IGNORED_CHANNELS", ""))
        self.allowed_teams = _csv_set(os.environ.get("SLACK_ALLOWED_TEAMS", ""))
        self.bot_user_id = os.environ.get("SLACK_BOT_USER_ID", "").strip()
        self.bot_id = os.environ.get("SLACK_BOT_ID", "").strip()
        self.trigger_mode = os.environ.get("SLACK_TRIGGER_MODE", "all").strip().lower() or "all"
        self.reply_in_thread = _env_truthy("SLACK_REPLY_IN_THREAD")
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(_env_int("SLACK_IDEMPOTENCY_TTL_SECONDS", 3600)),
            max_items=_env_int("SLACK_IDEMPOTENCY_CACHE_MAX", 10000),
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["command_cap"] = 30
        data["supports_slash_commands"] = True
        data["security"] = {
            "allow_bots": self.allow_bots,
            "allowed_users_configured": bool(self.allowed_users),
            "allowed_channels_configured": bool(self.allowed_channels),
            "ignored_channels_configured": bool(self.ignored_channels),
            "allowed_teams_configured": bool(self.allowed_teams),
            "bot_user_id_configured": bool(self.bot_user_id),
            "bot_id_configured": bool(self.bot_id),
            "trigger_mode": self.trigger_mode,
            "reply_in_thread": self.reply_in_thread,
        }
        data["idempotency"] = {
            "delivery_id_sources": [
                "event.event_id",
                "event.client_msg_id",
                "event.channel + event.ts",
            ],
            "delivery_cache": self._delivery_cache.stats(),
        }
        return data

    def command_menu(self, *, max_commands: int = 30) -> list[str]:
        return capped_command_menu(self._extra_commands(), max_commands=max_commands)

    def _extra_commands(self) -> list[str]:
        config = getattr(self, "_config", None)
        if config is None:
            return []
        try:
            return list(config.get("gateway.user_commands", []) or [])
        except Exception:  # noqa: BLE001
            return []

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("slack channel needs `pip install slack_bolt`") from e

        app = App(token=self.bot_token)
        self._app = app
        for command_name in self.command_menu():
            self._register_slash_command(app, command_name)

        @app.event("message")
        def handle_message(event, say):  # noqa: ANN001
            self._handle_message_event(event)

        SocketModeHandler(app, self.app_token).start()

    def _delivery_id_from_event(self, event: dict) -> str:
        event_id = str(event.get("event_id") or event.get("event_ts") or "").strip()
        if event_id:
            return f"event:{event_id}"
        client_msg_id = str(event.get("client_msg_id") or "").strip()
        if client_msg_id:
            return f"client_msg:{client_msg_id}"
        channel = str(event.get("channel") or "").strip()
        ts = str(event.get("ts") or "").strip()
        if channel and ts:
            return f"message:{channel}:{ts}"
        return ""

    def _handle_message_event(self, event: dict) -> MessageEvent | None:
        raw_text = event.get("text", "")
        if not self._event_allowed(event, raw_text):
            return None
        delivery_id = self._delivery_id_from_event(event)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._delivery_cache.record(delivery_id)
            if not delivery_recorded:
                return None
        try:
            attachments = self._attachments_from_event(event)
            text = normalize_inbound_command(self._strip_own_mentions(raw_text), platform="slack")
            if not text.strip() and attachments:
                text = self._attachment_reference_text(attachments)
            thread_id = self._resolve_thread_ts(event)
            ev = MessageEvent(
                platform="slack", chat_id=event["channel"],
                text=text, user_id=event.get("user"),
                thread_id=thread_id,
                message_id=str(event.get("ts") or "") or None,
                timestamp=event.get("ts"),
                attachments=attachments,
                metadata={
                    "team": event.get("team"),
                    "channel_type": event.get("channel_type"),
                    "thread_ts": thread_id,
                    "delivery_id": delivery_id or "",
                },
            )
            self._submit_inbound(ev, raw_text=raw_text)
            return ev
        except Exception:
            if delivery_recorded:
                self._delivery_cache.discard(delivery_id)
            raise

    def _register_slash_command(self, app, command_name: str) -> None:  # noqa: ANN001
        @app.command(command_name)
        def handle_slash_command(ack, command):  # noqa: ANN001
            self._handle_slash_command(command, ack)

    def _handle_slash_command(self, command: dict, ack=None) -> MessageEvent | None:  # noqa: ANN001
        if callable(ack):
            try:
                ack()
            except Exception:  # noqa: BLE001
                pass
        if not self._command_allowed(command):
            return None
        command_name = str(command.get("command") or "").strip() or "/"
        command_text = str(command.get("text") or "").strip()
        raw_text = command_name if not command_text else f"{command_name} {command_text}"
        text = normalize_inbound_command(raw_text, platform="slack")
        ev = MessageEvent(
            platform="slack",
            chat_id=str(command.get("channel_id") or command.get("channel_name") or "unknown"),
            text=text,
            user_id=str(command.get("user_id") or "") or None,
            user_name=str(command.get("user_name") or "") or None,
            message_id=str(command.get("trigger_id") or "") or None,
            metadata={
                "team": command.get("team_id") or command.get("team_domain"),
                "channel_name": command.get("channel_name"),
                "command": command_name,
                "response_url": command.get("response_url"),
                "trigger_id": command.get("trigger_id"),
                "source": "slash_command",
            },
        )
        self._submit_inbound(ev, raw_text=raw_text)
        return ev

    def _channel_values(self, *values: object) -> set[str]:
        return {str(value or "").strip() for value in values if str(value or "").strip()}

    def _channel_allowed(self, channels: set[str]) -> bool:
        if self.ignored_channels and ("*" in self.ignored_channels or channels & self.ignored_channels):
            return False
        return not self.allowed_channels or "*" in self.allowed_channels or bool(channels & self.allowed_channels)

    def _command_allowed(self, command: dict) -> bool:
        user = str(command.get("user_id") or "").strip()
        if self.allowed_users and user not in self.allowed_users:
            return False
        team = str(command.get("team_id") or command.get("team_domain") or "").strip()
        if self.allowed_teams and team not in self.allowed_teams:
            return False
        channels = self._channel_values(command.get("channel_id"), command.get("channel_name"))
        return self._channel_allowed(channels)

    def _resolve_thread_ts(self, event: dict) -> str | None:
        thread_ts = str(event.get("thread_ts") or "").strip()
        ts = str(event.get("ts") or "").strip()
        if thread_ts and thread_ts != ts:
            return thread_ts
        if self.reply_in_thread:
            return thread_ts or ts or None
        return None

    def _event_allowed(self, event: dict, raw_text: str | None = None) -> bool:
        subtype = str(event.get("subtype") or "")
        is_bot = bool(event.get("bot_id"))
        if self._is_self_echo(event):
            return False
        if subtype and subtype not in {"bot_message", "file_share"}:
            return False
        if subtype == "bot_message" and not is_bot:
            return False
        if is_bot and not self.allow_bots:
            return False
        user = str(event.get("user") or "")
        if self.allowed_users and user not in self.allowed_users and not (is_bot and self.allow_bots):
            return False
        team = str(event.get("team") or "")
        if self.allowed_teams and team not in self.allowed_teams:
            return False
        if not self._channel_allowed(self._channel_values(event.get("channel"))):
            return False
        if not self._trigger_allowed(event, raw_text):
            return False
        return True

    def _is_self_echo(self, event: dict) -> bool:
        user = str(event.get("user") or "").strip()
        bot_id = str(event.get("bot_id") or "").strip()
        return bool(
            (self.bot_user_id and user == self.bot_user_id)
            or (self.bot_id and bot_id == self.bot_id)
        )

    def _strip_own_mentions(self, text: str) -> str:
        if not self.bot_user_id:
            return str(text or "")
        pattern = re.compile(rf"<@{re.escape(self.bot_user_id)}(?:\|[^>]+)?>")
        return pattern.sub("", str(text or "")).strip()

    def _trigger_allowed(self, event: dict, raw_text: str | None = None) -> bool:
        channel_type = str(event.get("channel_type") or "").lower()
        if channel_type in {"im", "mpim", "dm"}:
            return True
        mode = self.trigger_mode
        if mode in {"", "all", "always", "true", "1", "yes"}:
            return True
        raw = event.get("text") if raw_text is None else raw_text
        text = str(raw or "")
        stripped = text.lstrip()
        is_command = stripped.startswith(("/", "!"))
        if mode in {"command", "commands"}:
            return is_command
        if mode in {"addressed", "mention", "mentions", "reply", "replies"}:
            if is_command:
                return True
            if self.bot_user_id:
                mention = re.compile(rf"<@{re.escape(self.bot_user_id)}(?:\|[^>]+)?>")
                if mention.search(text):
                    return True
                if str(event.get("parent_user_id") or "") == self.bot_user_id:
                    return True
        return False

    def _attachments_from_event(self, event: dict) -> list[dict]:
        rows: list[dict] = []
        for item in event.get("files") or []:
            if not isinstance(item, dict):
                continue
            mimetype = str(item.get("mimetype") or "").strip()
            filename = str(item.get("name") or item.get("title") or "").strip()
            media_type = mimetype.split("/", 1)[0] if "/" in mimetype else str(item.get("filetype") or "").strip()
            row = {
                "id": str(item.get("id") or "").strip(),
                "type": mimetype or media_type or "file",
                "media_type": mimetype,
                "filename": filename or "file",
                "url": str(item.get("url_private") or item.get("url_private_download") or "").strip(),
                "size": int(item.get("size") or 0),
                "source": "slack",
            }
            for source, target in (("filetype", "filetype"), ("pretty_type", "pretty_type"), ("title", "title")):
                value = str(item.get(source) or "").strip()
                if value:
                    row[target] = value
            rows.append(row)
        return rows

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or attachment.get("filetype") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("title") or attachment.get("id") or "file").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        app = getattr(self, "_app", None)
        client = getattr(app, "client", None)
        if client is None:
            raise RuntimeError("slack client is not started")
        kwargs = {"channel": chat_id}
        thread_ts = (metadata or {}).get("thread_ts") or (metadata or {}).get("thread_id")
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        for chunk in chunk_text_by_units(text, limit=39000):
            client.chat_postMessage(text=chunk, **kwargs)

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        if not reply:
            return
        try:
            from .base import tableify

            for chunk in chunk_text_by_units(tableify(reply), limit=39000):
                kwargs = {"channel": ev.chat_id, "text": chunk}
                if ev.thread_id:
                    kwargs["thread_ts"] = ev.thread_id
                self._app.client.chat_postMessage(**kwargs)
        except Exception:  # noqa: BLE001
            pass

    def _reaction_name(self, reaction: str) -> str:
        value = str(reaction or "").strip()
        aliases = {
            "👍": "+1",
            "👎": "-1",
            "✅": "white_check_mark",
            "❌": "x",
            "👀": "eyes",
            "❤️": "heart",
            "❤": "heart",
            "🚀": "rocket",
        }
        value = aliases.get(value, value)
        return value.strip(":").strip()

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        name = self._reaction_name(reaction)
        if not name or not message_id:
            return
        try:
            self._app.client.reactions_add(channel=chat_id, timestamp=message_id, name=name)
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        name = self._reaction_name(reaction)
        if not name or not message_id:
            return
        try:
            self._app.client.reactions_remove(channel=chat_id, timestamp=message_id, name=name)
        except Exception:  # noqa: BLE001
            pass
