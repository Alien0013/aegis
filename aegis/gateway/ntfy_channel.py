"""ntfy push-notification channel (ntfy.sh or self-hosted).

Subscribes to a topic, dispatches incoming messages to the agent, and publishes replies back
to the same topic. Dead-simple pub/sub — ideal for alerts and reminders (pairs with the
``send_message`` tool / scheduled tasks).

Config (env): ``NTFY_TOPIC`` (required), ``NTFY_SERVER`` (default https://ntfy.sh),
``NTFY_TOKEN`` (optional bearer for protected topics).
"""

from __future__ import annotations

import json
import os

import httpx

from .base import BasePlatformAdapter, Dispatch, MessageEvent

DEFAULT_SERVER = "https://ntfy.sh"


class NtfyAdapter(BasePlatformAdapter):
    name = "ntfy"
    renders_tables = False

    def __init__(self, topic: str | None = None, server: str | None = None):
        self.topic = topic or os.environ.get("NTFY_TOPIC")
        if not self.topic:
            raise RuntimeError("NTFY_TOPIC is not set.")
        self.server = (server or os.environ.get("NTFY_SERVER") or DEFAULT_SERVER).rstrip("/")
        token = os.environ.get("NTFY_TOKEN")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        url = f"{self.server}/{self.topic}/json"
        # read=None keeps the long-lived subscription stream open between messages.
        timeout = httpx.Timeout(connect=15.0, read=None, write=30.0, pool=15.0)
        while True:
            try:
                with httpx.Client(timeout=timeout) as c, c.stream("GET", url, headers=self._headers) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("event") != "message" or not ev.get("message"):
                            continue
                        me = MessageEvent(platform="ntfy", chat_id=self.topic,
                                          text=ev["message"], user_id="ntfy",
                                          timestamp=ev.get("time"))
                        self._submit_inbound(me)
            except Exception:  # noqa: BLE001 - keep the subscriber alive across drops
                continue

    def send(self, chat_id: str, text: str) -> None:
        try:
            httpx.post(f"{self.server}/{chat_id}", content=(text or "").encode("utf-8"),
                       headers=self._headers, timeout=30)
        except Exception:  # noqa: BLE001
            pass
