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
    transport = "ntfy_stream"

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
                        attachments = self._attachments_from_event(ev)
                        me = MessageEvent(platform="ntfy", chat_id=self.topic,
                                          text=ev["message"], user_id="ntfy",
                                          message_id=str(ev.get("id") or "") or None,
                                          timestamp=ev.get("time"),
                                          attachments=attachments,
                                          metadata={
                                              "id": ev.get("id"),
                                              "title": ev.get("title"),
                                              "tags": ev.get("tags") or [],
                                              "priority": ev.get("priority"),
                                              "click": ev.get("click"),
                                              "topic": ev.get("topic") or self.topic,
                                          })
                        self._submit_inbound(me)
            except Exception:  # noqa: BLE001 - keep the subscriber alive across drops
                continue

    def _attachments_from_event(self, event: dict) -> list[dict]:
        attachment = event.get("attachment")
        if not isinstance(attachment, dict):
            return []
        url = str(attachment.get("url") or "").strip()
        name = str(attachment.get("name") or attachment.get("filename") or "").strip()
        media_type = str(attachment.get("type") or "").strip()
        return [{
            "id": url or name or "attachment",
            "type": media_type or "file",
            "media_type": media_type,
            "filename": name or "attachment",
            "url": url,
            "size": int(attachment.get("size") or 0),
            "source": "ntfy",
        }]

    def _send_headers(self, metadata: dict | None = None) -> dict:
        headers = dict(self._headers)
        data = metadata or {}
        mapping = {
            "title": "Title",
            "tags": "Tags",
            "priority": "Priority",
            "click": "Click",
            "attach": "Attach",
            "filename": "Filename",
            "actions": "Actions",
        }
        for source, header in mapping.items():
            value = data.get(source)
            if value in (None, "", []):
                continue
            if isinstance(value, (list, tuple, set)):
                value = ",".join(str(item) for item in value if str(item).strip())
            headers[header] = str(value)
        return headers

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        try:
            httpx.post(f"{self.server}/{chat_id}", content=(text or "").encode("utf-8"),
                       headers=self._send_headers(metadata), timeout=30)
        except Exception:  # noqa: BLE001
            pass
