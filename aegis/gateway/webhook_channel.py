"""Generic inbound webhook channel.

Lets any external bridge POST messages into AEGIS — e.g. a self-hosted WhatsApp
(Baileys/whatsapp-web.js) bridge, an SMS gateway, or a custom integration.

Bridge contract:
    POST http://<host>:18790/in
    Headers: X-Secret: <WEBHOOK_CHANNEL_SECRET>  (optional)
    Body: {"chat_id": "...", "text": "...", "user_id": "...", "platform": "whatsapp"}
    Response: {"reply": "<agent reply>"}
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..platforms import normalize_platform_name
from ..webhook import DeliveryIdCache, verify_signature
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _max_channel_webhook_bytes() -> int:
    try:
        value = int(os.environ.get("WEBHOOK_CHANNEL_MAX_BYTES", "10000000") or "10000000")
    except (TypeError, ValueError):
        return 10_000_000
    return value if value > 0 else 10_000_000


MAX_CHANNEL_WEBHOOK_BYTES = _max_channel_webhook_bytes()


class WebhookChannel(BasePlatformAdapter):
    name = "webhook"
    transport = "http"
    supports_threads = False
    supports_media = False

    def __init__(self):
        self.port = int(os.environ.get("WEBHOOK_CHANNEL_PORT", "18790"))
        self.secret = os.environ.get("WEBHOOK_CHANNEL_SECRET")
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(os.environ.get("WEBHOOK_CHANNEL_IDEMPOTENCY_TTL_SECONDS", "3600") or "3600"),
            max_items=int(os.environ.get("WEBHOOK_CHANNEL_IDEMPOTENCY_CACHE_MAX", "10000") or "10000"),
        )

    def _delivery_id(self, headers, body: dict) -> str:
        for name in ("X-GitHub-Delivery", "svix-id", "X-Request-ID", "X-Request-Id", "Idempotency-Key"):
            value = str(headers.get(name, "") or "").strip()
            if value:
                return f"{name.lower()}:{value}"
        for key in ("delivery_id", "event_id", "message_id", "id"):
            value = str(body.get(key, "") or "").strip()
            if value:
                return f"body:{key}:{value}"
        return ""

    def _event_from_body(self, body: dict) -> MessageEvent:
        platform = normalize_platform_name(body.get("platform", "webhook"), default="webhook")
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        return MessageEvent(
            platform=platform,
            chat_id=str(body.get("chat_id", "unknown")),
            text=str(body.get("text", "")),
            user_id=str(body.get("user_id")) if body.get("user_id") else None,
            user_name=str(body.get("user_name")) if body.get("user_name") else None,
            thread_id=str(body.get("thread_id")) if body.get("thread_id") else None,
            message_id=str(body.get("message_id") or body.get("event_id") or body.get("id") or "") or None,
            reply_to_message_id=str(body.get("reply_to_message_id") or "") or None,
            reply_to_text=str(body.get("reply_to_text") or "") or None,
            timestamp=body.get("timestamp") or body.get("ts"),
            attachments=attachments,
            metadata=metadata,
        )

    def start(self, dispatch: Dispatch) -> None:
        secret = self.secret
        adapter = self
        self._init_inbound_queue(dispatch)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):  # noqa: N802
                try:
                    n = int(self.headers.get("content-length", 0) or 0)
                except (TypeError, ValueError):
                    self.send_response(400)
                    self.end_headers()
                    return
                if n < 0 or n > MAX_CHANNEL_WEBHOOK_BYTES:
                    self.send_response(413 if n > MAX_CHANNEL_WEBHOOK_BYTES else 400)
                    self.end_headers()
                    return
                raw_body = self.rfile.read(n) if n else b"{}"
                if secret and self.headers.get("X-Secret") != secret:
                    if not verify_signature(secret, raw_body, self.headers):
                        self.send_response(401)
                        self.end_headers()
                        return
                try:
                    body = json.loads(raw_body or b"{}")
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                if not isinstance(body, dict):
                    self.send_response(400)
                    self.end_headers()
                    return
                delivery_id = adapter._delivery_id(self.headers, body)
                if delivery_id and not adapter._delivery_cache.record(delivery_id):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"reply": "", "duplicate": True}).encode())
                    return
                ev = adapter._event_from_body(body)
                reply = adapter._submit_inbound(ev, wait=True) or ""
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"reply": reply}).encode())

        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        print(f"  ▸ webhook channel listening on :{self.port}/in")
        httpd.serve_forever()

    def send(self, chat_id: str, text: str) -> None:  # replies are returned inline
        pass
