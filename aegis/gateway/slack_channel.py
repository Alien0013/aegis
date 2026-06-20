"""Slack channel adapter via Socket Mode (requires `slack_bolt`).

Needs SLACK_BOT_TOKEN (xoxb-…) and SLACK_APP_TOKEN (xapp-…, connections:write).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import config as cfg
from ..platforms import capped_command_menu, chunk_text_by_units, normalize_inbound_command
from ..webhook import DeliveryIdCache
from .base import BasePlatformAdapter, Dispatch, MessageEvent
from .idempotency import PersistentDeliveryIdStore


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


class SlackAdapter(BasePlatformAdapter):
    name = "slack"
    renders_tables = False
    transport = "socket_mode"
    max_message_length = 39000
    supports_threads = True
    supports_media = True
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
        self.idempotency_ttl_seconds = _env_int("SLACK_IDEMPOTENCY_TTL_SECONDS", 3600)
        self.idempotency_cache_max = _env_int("SLACK_IDEMPOTENCY_CACHE_MAX", 10000)
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(self.idempotency_ttl_seconds),
            max_items=self.idempotency_cache_max,
        )
        self.persist_idempotency = _env_bool("SLACK_IDEMPOTENCY_PERSIST", True)
        store_path = os.environ.get("SLACK_IDEMPOTENCY_STORE_PATH", "").strip()
        self._delivery_store = PersistentDeliveryIdStore(
            cfg.sub("gateway", "slack_delivery_ids.json") if not store_path else Path(store_path).expanduser(),
            ttl_seconds=float(self.idempotency_ttl_seconds),
            max_items=self.idempotency_cache_max,
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["command_cap"] = 30
        data["supports_slash_commands"] = True
        data["supports_interactive_prompts"] = True
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
            "idempotency_env": [
                "SLACK_IDEMPOTENCY_TTL_SECONDS",
                "SLACK_IDEMPOTENCY_CACHE_MAX",
                "SLACK_IDEMPOTENCY_PERSIST",
                "SLACK_IDEMPOTENCY_STORE_PATH",
            ],
        }
        data["idempotency"] = {
            "delivery_id_sources": [
                "event.event_id",
                "event.client_msg_id",
                "event.channel + event.ts",
                "slash.trigger_id",
                "block.trigger_id",
                "block.container.message_ts + action.action_ts",
            ],
            "delivery_cache": self._delivery_cache.stats(),
            "persistent": self.persist_idempotency,
            "delivery_store": self._delivery_store.stats() if self.persist_idempotency else {},
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
        self._register_block_action(app, "aegis_clarify")
        self._register_block_action(app, "aegis_exec_approval")

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
            delivery_recorded = self._record_delivery_id(delivery_id)
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
                self._discard_delivery_id(delivery_id)
            raise

    def _register_slash_command(self, app, command_name: str) -> None:  # noqa: ANN001
        @app.command(command_name)
        def handle_slash_command(ack, command):  # noqa: ANN001
            self._handle_slash_command(command, ack)

    def _register_block_action(self, app, action_id: str) -> None:  # noqa: ANN001
        @app.action(action_id)
        def handle_block_action(ack, body, action):  # noqa: ANN001
            self._handle_block_action(body, action=action, ack=ack)

    def _handle_slash_command(self, command: dict, ack=None) -> MessageEvent | None:  # noqa: ANN001
        if callable(ack):
            try:
                ack()
            except Exception:  # noqa: BLE001
                pass
        if not self._command_allowed(command):
            return None
        delivery_id = self._delivery_id_from_slash_command(command)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._record_delivery_id(delivery_id)
            if not delivery_recorded:
                return None
        command_name = str(command.get("command") or "").strip() or "/"
        command_text = str(command.get("text") or "").strip()
        raw_text = command_name if not command_text else f"{command_name} {command_text}"
        text = normalize_inbound_command(raw_text, platform="slack")
        try:
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
                    "delivery_id": delivery_id or "",
                },
            )
            self._submit_inbound(ev, raw_text=raw_text)
            return ev
        except Exception:
            if delivery_recorded:
                self._discard_delivery_id(delivery_id)
            raise

    def _delivery_id_from_slash_command(self, command: dict) -> str:
        trigger_id = str(command.get("trigger_id") or "").strip()
        if trigger_id:
            return f"slash:trigger:{trigger_id}"
        response_url = str(command.get("response_url") or "").strip()
        if response_url:
            return f"slash:response_url:{response_url}"
        team = str(command.get("team_id") or command.get("team_domain") or "").strip()
        channel = str(command.get("channel_id") or command.get("channel_name") or "").strip()
        user = str(command.get("user_id") or "").strip()
        command_name = str(command.get("command") or "").strip()
        text = str(command.get("text") or "").strip()
        if team or channel or user or command_name or text:
            return f"slash:fallback:{team}:{channel}:{user}:{command_name}:{text}"
        return ""

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

    def _handle_block_action(self, body: dict, *, action: dict | None = None, ack=None) -> MessageEvent | None:  # noqa: ANN001
        if callable(ack):
            try:
                ack()
            except Exception:  # noqa: BLE001
                pass
        payload = body if isinstance(body, dict) else {}
        selected = action if isinstance(action, dict) else {}
        if not selected:
            actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
            selected = actions[0] if actions and isinstance(actions[0], dict) else {}
        action_id = str(selected.get("action_id") or "").strip()
        if action_id not in {"aegis_clarify", "aegis_exec_approval"}:
            return None
        text, prompt_meta = self._decode_prompt_button_value(selected.get("value"))
        if not text:
            return None
        channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        channel_id = str(channel.get("id") or payload.get("channel_id") or "").strip()
        channel_name = str(channel.get("name") or payload.get("channel_name") or "").strip()
        user_id = str(user.get("id") or payload.get("user_id") or "").strip()
        user_name = str(user.get("username") or user.get("name") or payload.get("user_name") or "").strip()
        team_id = str(team.get("id") or payload.get("team_id") or "").strip()
        if not self._command_allowed({
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
        }):
            return None
        delivery_id = self._delivery_id_from_block_action(payload, selected)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._record_delivery_id(delivery_id)
            if not delivery_recorded:
                return None
        thread_id = str(message.get("thread_ts") or container.get("thread_ts") or "").strip() or None
        message_id = str(
            container.get("message_ts")
            or message.get("ts")
            or selected.get("action_ts")
            or ""
        ).strip() or None
        try:
            ev = MessageEvent(
                platform="slack",
                chat_id=channel_id or channel_name or "unknown",
                text=normalize_inbound_command(text, platform="slack"),
                user_id=user_id or None,
                user_name=user_name or None,
                thread_id=thread_id,
                message_id=message_id,
                timestamp=selected.get("action_ts") or message.get("ts"),
                metadata={
                    "team": team_id,
                    "channel_name": channel_name,
                    "thread_ts": thread_id,
                    "action_id": action_id,
                    "prompt_id": prompt_meta.get("prompt_id", ""),
                    "prompt_kind": prompt_meta.get("prompt_kind", ""),
                    "source": "block_action",
                    "response_url": payload.get("response_url"),
                    "delivery_id": delivery_id or "",
                },
            )
            self._submit_inbound(ev, raw_text=text)
            return ev
        except Exception:
            if delivery_recorded:
                self._discard_delivery_id(delivery_id)
            raise

    def _record_delivery_id(self, delivery_id: str) -> bool:
        delivery_id = str(delivery_id or "").strip()
        if not delivery_id:
            return True
        if not self._delivery_cache.record(delivery_id):
            return False
        if self.persist_idempotency and not self._delivery_store.record(delivery_id):
            self._delivery_cache.discard(delivery_id)
            return False
        return True

    def _discard_delivery_id(self, delivery_id: str) -> None:
        delivery_id = str(delivery_id or "").strip()
        if not delivery_id:
            return
        self._delivery_cache.discard(delivery_id)
        if self.persist_idempotency:
            self._delivery_store.discard(delivery_id)

    def _delivery_id_from_block_action(self, payload: dict, action: dict) -> str:
        trigger_id = str(payload.get("trigger_id") or "").strip()
        if trigger_id:
            return f"block:trigger:{trigger_id}"
        container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        channel_id = str(channel.get("id") or payload.get("channel_id") or "").strip()
        user_id = str(user.get("id") or payload.get("user_id") or "").strip()
        message_ts = str(container.get("message_ts") or message.get("ts") or "").strip()
        action_ts = str(action.get("action_ts") or "").strip()
        action_id = str(action.get("action_id") or "").strip()
        value = str(action.get("value") or "").strip()
        if message_ts or action_ts or action_id or value:
            return f"block:fallback:{channel_id}:{user_id}:{message_ts}:{action_ts}:{action_id}:{value}"
        return ""

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

    def send_media(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        *,
        metadata: dict | None = None,
        **_kwargs,
    ) -> None:
        app = getattr(self, "_app", None)
        client = getattr(app, "client", None)
        if client is None:
            raise RuntimeError("slack client is not started")
        media_path = Path(path).expanduser()
        if not media_path.exists():
            prefix = f"{caption}\n" if caption else ""
            self.send(chat_id, f"{prefix}(file not found: {path})", metadata=metadata)
            return
        thread_kwargs = self._thread_kwargs(metadata)
        title = media_path.name
        upload_v2 = getattr(client, "files_upload_v2", None)
        if callable(upload_v2):
            upload_v2(
                channel=chat_id,
                file=str(media_path),
                title=title,
                initial_comment=caption or None,
                **thread_kwargs,
            )
            return
        upload = getattr(client, "files_upload", None)
        if not callable(upload):
            raise RuntimeError("slack client cannot upload files")
        upload(
            channels=chat_id,
            file=str(media_path),
            filename=title,
            title=title,
            initial_comment=caption or None,
            **thread_kwargs,
        )

    def _thread_kwargs(self, metadata: dict | None = None) -> dict:
        thread_ts = (metadata or {}).get("thread_ts") or (metadata or {}).get("thread_id")
        return {"thread_ts": thread_ts} if thread_ts else {}

    def _encode_prompt_button_value(self, value: str, metadata: dict | None = None) -> str:
        text = str(value or "")
        prompt_id = str((metadata or {}).get("prompt_id") or "").strip()
        if not prompt_id:
            return text[:1900]
        payload = json.dumps(
            {
                "v": text,
                "pid": prompt_id,
                "k": str((metadata or {}).get("prompt_kind") or "").strip(),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if len(payload) <= 1900:
            return payload
        trimmed = {"v": text[:1500], "pid": prompt_id, "k": str((metadata or {}).get("prompt_kind") or "").strip()}
        return json.dumps(trimmed, separators=(",", ":"), ensure_ascii=False)[:1900]

    def _decode_prompt_button_value(self, value: object) -> tuple[str, dict[str, str]]:
        text = str(value or "").strip()
        if not text.startswith("{"):
            return text, {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text, {}
        if not isinstance(payload, dict):
            return text, {}
        return str(payload.get("v") or "").strip(), {
            "prompt_id": str(payload.get("pid") or "").strip(),
            "prompt_kind": str(payload.get("k") or "").strip(),
        }

    def _button(
        self,
        label: str,
        value: str,
        action_id: str,
        *,
        style: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        button = {
            "type": "button",
            "text": {"type": "plain_text", "text": str(label or value)[:75] or "Choose"},
            "value": self._encode_prompt_button_value(value, metadata),
            "action_id": action_id,
        }
        if style:
            button["style"] = style
        return button

    def _post_blocks(self, chat_id: str, text: str, blocks: list[dict], *, metadata: dict | None = None) -> None:
        app = getattr(self, "_app", None)
        client = getattr(app, "client", None)
        if client is None:
            raise RuntimeError("slack client is not started")
        client.chat_postMessage(
            channel=chat_id,
            text=text,
            blocks=blocks,
            **self._thread_kwargs(metadata),
        )

    def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: list[str] | None = None,
        *,
        metadata: dict | None = None,
    ) -> None:
        choice_values = [str(choice).strip() for choice in (choices or []) if str(choice).strip()]
        if not choice_values:
            return super().send_clarify(chat_id, question, choices or [], metadata=metadata)
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": str(question or "").strip() or "Choose one:"}},
            {
                "type": "actions",
                "elements": [
                    self._button(choice, choice, "aegis_clarify", metadata=metadata)
                    for choice in choice_values[:25]
                ],
            },
        ]
        try:
            self._post_blocks(chat_id, str(question or "").strip() or "Choose one:", blocks, metadata=metadata)
        except Exception:  # noqa: BLE001
            super().send_clarify(chat_id, question, choices or [], metadata=metadata)

    def send_exec_approval(
        self,
        chat_id: str,
        prompt: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        text = str(prompt or "").strip() or "Approve this action?"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    self._button("Approve", "approve", "aegis_exec_approval", style="primary", metadata=metadata),
                    self._button("Always", "always", "aegis_exec_approval", metadata=metadata),
                    self._button("Deny", "deny", "aegis_exec_approval", style="danger", metadata=metadata),
                ],
            },
        ]
        try:
            self._post_blocks(chat_id, text, blocks, metadata=metadata)
        except Exception:  # noqa: BLE001
            super().send_exec_approval(chat_id, prompt, metadata=metadata)

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        super()._deliver_reply(ev, reply, state)

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
