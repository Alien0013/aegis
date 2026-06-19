"""ntfy push-notification channel (ntfy.sh or self-hosted).

Subscribes to a topic, dispatches incoming messages to the agent, and publishes replies back
to the same topic. Dead-simple pub/sub — ideal for alerts and reminders (pairs with the
``send_message`` tool / scheduled tasks).

Config (env): ``NTFY_TOPIC`` (required), ``NTFY_SERVER`` (default https://ntfy.sh),
``NTFY_TOKEN`` (optional bearer for protected topics).
"""

from __future__ import annotations

import hashlib
import json
import os

import httpx

from ..webhook import DeliveryIdCache
from .base import BasePlatformAdapter, Dispatch, MessageEvent

DEFAULT_SERVER = "https://ntfy.sh"


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


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
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(_env_int("NTFY_IDEMPOTENCY_TTL_SECONDS", 3600)),
            max_items=_env_int("NTFY_IDEMPOTENCY_CACHE_MAX", 10000),
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["idempotency"] = {
            "delivery_id_sources": [
                "event.topic + event.id",
                "event.topic + event.time + message hash",
            ],
            "delivery_cache": self._delivery_cache.stats(),
        }
        return data

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
                        if ev.get("event") != "message":
                            continue
                        self._handle_stream_event(ev)
            except Exception:  # noqa: BLE001 - keep the subscriber alive across drops
                continue

    def _delivery_id_from_event(self, event: dict) -> str:
        topic = str(event.get("topic") or self.topic or "").strip()
        event_id = str(event.get("id") or "").strip()
        if topic and event_id:
            return f"ntfy:{topic}:{event_id}"
        timestamp = str(event.get("time") or "").strip()
        message = str(event.get("message") or "").strip()
        if topic and timestamp and message:
            digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
            return f"ntfy:{topic}:{timestamp}:{digest}"
        return ""

    def _message_event_from_event(self, event: dict) -> MessageEvent | None:
        attachments = self._attachments_from_event(event)
        text = str(event.get("message") or "").strip()
        if not text and attachments:
            text = self._attachment_reference_text(attachments)
        if not text and not attachments:
            return None
        topic = str(event.get("topic") or self.topic)
        return MessageEvent(
            platform="ntfy",
            chat_id=topic,
            text=text,
            user_id="ntfy",
            message_id=str(event.get("id") or "") or None,
            timestamp=event.get("time"),
            attachments=attachments,
            metadata={
                "id": event.get("id"),
                "title": event.get("title"),
                "tags": event.get("tags") or [],
                "priority": event.get("priority"),
                "click": event.get("click"),
                "topic": topic,
                "delivery_id": self._delivery_id_from_event(event),
            },
        )

    def _handle_stream_event(self, event: dict) -> MessageEvent | None:
        delivery_id = self._delivery_id_from_event(event)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._delivery_cache.record(delivery_id)
            if not delivery_recorded:
                return None
        try:
            message = self._message_event_from_event(event)
            if message is None:
                if delivery_recorded:
                    self._delivery_cache.discard(delivery_id)
                return None
            self._submit_inbound(message)
            return message
        except Exception:
            if delivery_recorded:
                self._delivery_cache.discard(delivery_id)
            raise

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

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("id") or "attachment").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

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
