"""Slack channel adapter via Socket Mode (requires `slack_bolt`).

Needs SLACK_BOT_TOKEN (xoxb-…) and SLACK_APP_TOKEN (xapp-…, connections:write).
"""

from __future__ import annotations

import os

from ..platforms import chunk_text_by_units, normalize_inbound_command
from .base import BasePlatformAdapter, Dispatch, MessageEvent


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
            if event.get("subtype") or event.get("bot_id"):
                return  # ignore bot/system messages to avoid loops
            raw_text = event.get("text", "")
            text = normalize_inbound_command(raw_text, platform="slack")
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
