"""Slack channel adapter via Socket Mode (requires `slack_bolt`).

Needs SLACK_BOT_TOKEN (xoxb-…) and SLACK_APP_TOKEN (xapp-…, connections:write).
"""

from __future__ import annotations

import os
import re

from ..platforms import chunk_text_by_units, normalize_inbound_command
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _csv_set(value: str) -> set[str] | None:
    items = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return items or None


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "all"}


class SlackAdapter(BasePlatformAdapter):
    name = "slack"
    renders_tables = False
    transport = "socket_mode"
    max_message_length = 39000
    supports_threads = True
    supports_media = False
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
        self.trigger_mode = os.environ.get("SLACK_TRIGGER_MODE", "all").strip().lower() or "all"

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["security"] = {
            "allow_bots": self.allow_bots,
            "allowed_users_configured": bool(self.allowed_users),
            "allowed_channels_configured": bool(self.allowed_channels),
            "ignored_channels_configured": bool(self.ignored_channels),
            "allowed_teams_configured": bool(self.allowed_teams),
            "bot_user_id_configured": bool(self.bot_user_id),
            "trigger_mode": self.trigger_mode,
        }
        return data

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("slack channel needs `pip install slack_bolt`") from e

        app = App(token=self.bot_token)
        self._app = app

        @app.event("message")
        def handle_message(event, say):  # noqa: ANN001
            raw_text = event.get("text", "")
            if not self._event_allowed(event, raw_text):
                return
            text = normalize_inbound_command(self._strip_own_mentions(raw_text), platform="slack")
            thread_id = event.get("thread_ts") or event.get("ts")
            ev = MessageEvent(
                platform="slack", chat_id=event["channel"],
                text=text, user_id=event.get("user"),
                thread_id=thread_id,
                message_id=str(event.get("ts") or "") or None,
                timestamp=event.get("ts"),
                metadata={
                    "team": event.get("team"),
                    "channel_type": event.get("channel_type"),
                },
            )
            self._submit_inbound(ev, raw_text=raw_text)

        SocketModeHandler(app, self.app_token).start()

    def _event_allowed(self, event: dict, raw_text: str | None = None) -> bool:
        subtype = str(event.get("subtype") or "")
        is_bot = bool(event.get("bot_id"))
        if subtype and subtype != "bot_message":
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
        channel = str(event.get("channel") or "")
        if self.ignored_channels and ("*" in self.ignored_channels or channel in self.ignored_channels):
            return False
        if self.allowed_channels and "*" not in self.allowed_channels and channel not in self.allowed_channels:
            return False
        if not self._trigger_allowed(event, raw_text):
            return False
        return True

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

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        try:
            kwargs = {"channel": chat_id}
            thread_ts = (metadata or {}).get("thread_ts") or (metadata or {}).get("thread_id")
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            for chunk in chunk_text_by_units(text, limit=39000):
                self._app.client.chat_postMessage(text=chunk, **kwargs)
        except Exception:  # noqa: BLE001
            pass

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        if not reply:
            return
        try:
            from .base import tableify

            for chunk in chunk_text_by_units(tableify(reply), limit=39000):
                self._app.client.chat_postMessage(
                    channel=ev.chat_id,
                    text=chunk,
                    thread_ts=ev.thread_id,
                )
        except Exception:  # noqa: BLE001
            pass
