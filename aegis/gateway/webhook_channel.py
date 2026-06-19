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

import httpx

from ..platforms import chunk_text_by_units, normalize_platform_name
from ..webhook import DeliveryIdCache, FixedWindowRateLimiter, _env_truthy, _is_loopback_host, verify_signature
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _channel_env(prefix: str, suffix: str, default: str = "") -> str:
    key = f"{prefix}_{suffix}"
    return os.environ.get(key, default) or default


def _channel_env_int(prefix: str, suffix: str, default: int) -> int:
    try:
        value = int(_channel_env(prefix, suffix, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _channel_env_truthy(prefix: str, suffix: str) -> bool:
    return _env_truthy(f"{prefix}_{suffix}")


def _max_channel_webhook_bytes() -> int:
    try:
        value = int(os.environ.get("WEBHOOK_CHANNEL_MAX_BYTES", "10000000") or "10000000")
    except (TypeError, ValueError):
        return 10_000_000
    return value if value > 0 else 10_000_000


MAX_CHANNEL_WEBHOOK_BYTES = _max_channel_webhook_bytes()
_WHATSAPP_BROADCAST_CHATS = {"status@broadcast"}
_WHATSAPP_BROADCAST_SUFFIXES = ("@broadcast", "@newsletter")


def _dig(source: dict, *path: str):
    cur = source
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _string_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _first_string(source: dict, paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        value = _dig(source, *path)
        text = _string_value(value)
        if text:
            return text
        if isinstance(value, dict):
            nested = _first_string(value, (
                ("text",),
                ("body",),
                ("content",),
                ("conversation",),
                ("caption",),
            ))
            if nested:
                return nested
    return ""


def _is_whatsapp_broadcast_chat(chat_id: str) -> bool:
    value = str(chat_id or "").strip().lower()
    if not value:
        return False
    return value in _WHATSAPP_BROADCAST_CHATS or value.endswith(_WHATSAPP_BROADCAST_SUFFIXES)


class WebhookChannel(BasePlatformAdapter):
    name = "webhook"
    transport = "http"
    supports_threads = True
    supports_media = False
    supports_reactions = True
    max_message_length = 16000

    def __init__(
        self,
        *,
        name: str | None = None,
        default_platform: str | None = None,
        env_prefix: str = "WEBHOOK_CHANNEL",
        default_port: int = 18790,
        transport: str | None = None,
    ):
        if name:
            self.name = name
        if transport:
            self.transport = transport
        self.default_platform = default_platform or self.name
        self.env_prefix = env_prefix
        self.port = _channel_env_int(env_prefix, "PORT", default_port)
        self.secret = _channel_env(env_prefix, "SECRET")
        self.outbound_url = _channel_env(env_prefix, "OUTBOUND_URL")
        self.outbound_secret = _channel_env(env_prefix, "OUTBOUND_SECRET", self.secret)
        self.outbound_max_chars = _channel_env_int(env_prefix, "OUTBOUND_MAX_CHARS", self.max_message_length)
        self.max_body_bytes = _channel_env_int(env_prefix, "MAX_BYTES", MAX_CHANNEL_WEBHOOK_BYTES)
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(_channel_env(env_prefix, "IDEMPOTENCY_TTL_SECONDS", "3600") or "3600"),
            max_items=_channel_env_int(env_prefix, "IDEMPOTENCY_CACHE_MAX", 10000),
        )
        self._rate_limiter = FixedWindowRateLimiter(
            limit=_channel_env_int(env_prefix, "RATE_LIMIT_PER_MINUTE", 60),
            window_seconds=60,
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data.update({
            "port": self.port,
            "security": {
                "secret_configured": bool(self.secret),
                "loopback_unsigned_allowed": True,
                "insecure_env_override": self._insecure_no_auth(),
                "env_prefix": self.env_prefix,
                "max_body_bytes": self.max_body_bytes,
                "outbound_configured": bool(self.outbound_url),
                "outbound_secret_configured": bool(self.outbound_secret),
                "signature_schemes": [
                    "X-Secret",
                    "X-Hub-Signature-256",
                    "X-Webhook-Signature",
                    "svix-signature",
                    "X-Gitlab-Token",
                ],
            },
            "idempotency": {
                "delivery_id_sources": [
                    "X-GitHub-Delivery",
                    "svix-id",
                    "X-Request-ID",
                    "X-Request-Id",
                    "Idempotency-Key",
                    "body.delivery_id",
                    "body.event_id",
                    "body.message_id",
                    "body.key.id",
                    "body.id",
                ],
                "delivery_cache": self._delivery_cache.stats(),
            },
            "rate_limiter": self._rate_limiter.stats(),
        })
        return data

    def _insecure_no_auth(self) -> bool:
        return (
            _channel_env_truthy(self.env_prefix, "INSECURE_NO_AUTH")
            or _env_truthy("WEBHOOK_CHANNEL_INSECURE_NO_AUTH")
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
        for path in (("key", "id"), ("message", "key", "id"), ("data", "key", "id")):
            value = _first_string(body, (path,))
            if value:
                return f"body:{'.'.join(path)}:{value}"
        return ""

    def _event_from_body(self, body: dict) -> MessageEvent:
        raw_platform = _string_value(body.get("platform", self.default_platform)) or self.default_platform
        platform = normalize_platform_name(
            raw_platform,
            default=self.default_platform,
        )
        metadata = dict(body.get("metadata")) if isinstance(body.get("metadata"), dict) else {}
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        chat_id = _first_string(body, (
            ("chat_id",),
            ("chatId",),
            ("channel_id",),
            ("channel",),
            ("room_id",),
            ("room",),
            ("remote_jid",),
            ("remoteJid",),
            ("data", "chat_id"),
            ("data", "chatId"),
            ("data", "remote_jid"),
            ("data", "remoteJid"),
            ("key", "remoteJid"),
            ("key", "remote_jid"),
            ("message", "key", "remoteJid"),
            ("message", "key", "remote_jid"),
            ("data", "key", "remoteJid"),
            ("data", "key", "remote_jid"),
            ("jid",),
            ("from",),
            ("source",),
        )) or "unknown"
        text = _first_string(body, (
            ("text",),
            ("body",),
            ("content",),
            ("caption",),
            ("conversation",),
            ("message",),
            ("message", "text"),
            ("message", "body"),
            ("message", "content"),
            ("message", "conversation"),
            ("message", "caption"),
            ("message", "extendedTextMessage", "text"),
            ("message", "imageMessage", "caption"),
            ("message", "videoMessage", "caption"),
            ("data", "text"),
            ("data", "body"),
            ("data", "message"),
            ("data", "message", "text"),
            ("data", "message", "conversation"),
            ("data", "message", "extendedTextMessage", "text"),
            ("data", "message", "imageMessage", "caption"),
            ("data", "message", "videoMessage", "caption"),
        ))
        user_id = _first_string(body, (
            ("user_id",),
            ("userId",),
            ("sender_id",),
            ("senderId",),
            ("participant",),
            ("key", "participant"),
            ("message", "key", "participant"),
            ("data", "sender_id"),
            ("data", "senderId"),
            ("data", "participant"),
            ("data", "key", "participant"),
            ("author",),
            ("sender",),
            ("sender", "id"),
            ("sender", "jid"),
            ("from_user",),
        ))
        user_name = _first_string(body, (
            ("user_name",),
            ("userName",),
            ("username",),
            ("pushName",),
            ("sender_name",),
            ("senderName",),
            ("sender", "name"),
            ("sender", "username"),
        ))
        thread_id = _first_string(body, (
            ("thread_id",),
            ("threadId",),
            ("thread_ts",),
            ("root_id",),
            ("topic",),
        ))
        message_id = _first_string(body, (
            ("message_id",),
            ("messageId",),
            ("event_id",),
            ("eventId",),
            ("id",),
            ("key", "id"),
            ("message", "key", "id"),
            ("message", "id"),
            ("data", "id"),
            ("data", "key", "id"),
            ("data", "message", "id"),
        ))
        reply_to_message_id = _first_string(body, (
            ("reply_to_message_id",),
            ("replyToMessageId",),
            ("message", "extendedTextMessage", "contextInfo", "stanzaId"),
            ("message", "imageMessage", "contextInfo", "stanzaId"),
            ("message", "videoMessage", "contextInfo", "stanzaId"),
            ("message", "audioMessage", "contextInfo", "stanzaId"),
            ("data", "message", "extendedTextMessage", "contextInfo", "stanzaId"),
            ("data", "message", "imageMessage", "contextInfo", "stanzaId"),
            ("data", "message", "videoMessage", "contextInfo", "stanzaId"),
            ("data", "message", "audioMessage", "contextInfo", "stanzaId"),
            ("contextInfo", "stanzaId"),
        ))
        reply_to_text = _first_string(body, (
            ("reply_to_text",),
            ("replyToText",),
            ("message", "extendedTextMessage", "contextInfo", "quotedMessage", "conversation"),
            ("message", "extendedTextMessage", "contextInfo", "quotedMessage", "extendedTextMessage", "text"),
            ("message", "extendedTextMessage", "contextInfo", "quotedMessage", "imageMessage", "caption"),
            ("message", "extendedTextMessage", "contextInfo", "quotedMessage", "videoMessage", "caption"),
            ("message", "imageMessage", "contextInfo", "quotedMessage", "conversation"),
            ("message", "videoMessage", "contextInfo", "quotedMessage", "conversation"),
            ("data", "message", "extendedTextMessage", "contextInfo", "quotedMessage", "conversation"),
            ("data", "message", "extendedTextMessage", "contextInfo", "quotedMessage", "extendedTextMessage", "text"),
            ("data", "message", "extendedTextMessage", "contextInfo", "quotedMessage", "imageMessage", "caption"),
            ("data", "message", "extendedTextMessage", "contextInfo", "quotedMessage", "videoMessage", "caption"),
            ("data", "message", "imageMessage", "contextInfo", "quotedMessage", "conversation"),
            ("data", "message", "videoMessage", "contextInfo", "quotedMessage", "conversation"),
            ("contextInfo", "quotedMessage", "conversation"),
            ("contextInfo", "quotedMessage", "extendedTextMessage", "text"),
        ))
        if not metadata:
            metadata = self._salvage_bridge_metadata(
                body,
                platform=platform,
                raw_platform=raw_platform,
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
            )
        return MessageEvent(
            platform=platform,
            chat_id=chat_id,
            text=text,
            user_id=user_id or None,
            user_name=user_name or None,
            thread_id=thread_id or None,
            message_id=message_id or None,
            reply_to_message_id=reply_to_message_id or None,
            reply_to_text=reply_to_text or None,
            timestamp=body.get("timestamp") or body.get("ts"),
            attachments=attachments,
            metadata=metadata,
        )

    def _salvage_bridge_metadata(
        self,
        body: dict,
        *,
        platform: str,
        raw_platform: str,
        chat_id: str,
        user_id: str,
        message_id: str,
    ) -> dict:
        metadata: dict[str, object] = {}
        if raw_platform and raw_platform != platform:
            metadata["bridge_platform"] = raw_platform
            metadata["normalized_platform"] = platform
        if platform != "whatsapp":
            return metadata
        remote_jid = _first_string(body, (
            ("remote_jid",),
            ("remoteJid",),
            ("key", "remoteJid"),
            ("key", "remote_jid"),
            ("message", "key", "remoteJid"),
            ("data", "key", "remoteJid"),
            ("jid",),
        )) or chat_id
        participant = _first_string(body, (
            ("participant",),
            ("key", "participant"),
            ("message", "key", "participant"),
            ("data", "key", "participant"),
        )) or user_id
        key_id = _first_string(body, (
            ("key", "id"),
            ("message", "key", "id"),
            ("data", "key", "id"),
        )) or message_id
        if remote_jid:
            metadata["remote_jid"] = remote_jid
            if remote_jid.endswith("@g.us") or remote_jid.endswith("-g.us"):
                metadata["group_jid"] = remote_jid
                metadata["is_group"] = True
        if participant:
            metadata["participant"] = participant
        if key_id:
            metadata["message_key_id"] = key_id
        return metadata

    def _handle_inbound_payload(self, headers, body: dict) -> tuple[int, dict]:
        delivery_id = self._delivery_id(headers, body)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._delivery_cache.record(delivery_id)
            if not delivery_recorded:
                return 200, {"reply": "", "duplicate": True}
        try:
            ev = self._event_from_body(body)
            if ev.platform == "whatsapp" and _is_whatsapp_broadcast_chat(ev.chat_id):
                return 200, {"reply": "", "ignored": True, "reason": "whatsapp_broadcast_chat"}
            reply = self._submit_inbound(ev, wait=True) or ""
        except Exception as exc:  # noqa: BLE001
            if delivery_recorded:
                self._delivery_cache.discard(delivery_id)
            return 500, {"reply": "", "error": f"dispatch failed: {type(exc).__name__}: {exc}"}
        return 200, {"reply": reply}

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
                if n < 0 or n > adapter.max_body_bytes:
                    self.send_response(413 if n > adapter.max_body_bytes else 400)
                    self.end_headers()
                    return
                raw_body = self.rfile.read(n) if n else b"{}"
                client_host = str((self.client_address or ("",))[0] or "")
                if not adapter._rate_limiter.allow(client_host):
                    self.send_response(429)
                    self.end_headers()
                    return
                insecure = adapter._insecure_no_auth()
                if not secret and not (insecure or _is_loopback_host(client_host)):
                    self.send_response(401)
                    self.end_headers()
                    return
                if secret and self.headers.get("X-Secret") != secret and not verify_signature(secret, raw_body, self.headers):
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
                status, payload = adapter._handle_inbound_payload(self.headers, body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        print(f"  ▸ webhook channel listening on :{self.port}/in")
        httpd.serve_forever()

    def _outbound_payload(self, chat_id: str, text: str, metadata: dict) -> dict:
        platform = normalize_platform_name(
            metadata.get("platform") or metadata.get("normalized_platform") or self.default_platform,
            default=self.default_platform,
        )
        payload = {
            "platform": platform,
            "chat_id": str(chat_id),
            "text": text,
            "metadata": metadata,
        }
        for source, target in (
            ("thread_id", "thread_id"),
            ("thread_ts", "thread_id"),
            ("root_id", "thread_id"),
            ("topic", "thread_id"),
            ("remote_jid", "remote_jid"),
            ("group_jid", "group_jid"),
            ("participant", "participant"),
            ("message_key_id", "reply_to_message_id"),
            ("reply_to_message_id", "reply_to_message_id"),
            ("message_id", "reply_to_message_id"),
        ):
            value = _string_value(metadata.get(source))
            if value and target not in payload:
                payload[target] = value
        return payload

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        """Optionally deliver replies to an external bridge endpoint.

        Inline webhook replies still use the HTTP response from ``start``. This outbound
        path is for async gateway replies, cron delivery, and custom bridges that need a
        separate send endpoint.
        """
        if not self.outbound_url:
            return
        metadata = dict(metadata or {})
        headers = {"Content-Type": "application/json"}
        if self.outbound_secret:
            headers["X-Secret"] = self.outbound_secret
        with httpx.Client(timeout=30) as client:
            for chunk in chunk_text_by_units(text or "", limit=self.outbound_max_chars):
                payload = self._outbound_payload(chat_id, chunk, metadata)
                response = client.post(self.outbound_url, headers=headers, json=payload)
                response.raise_for_status()

    def _post_outbound_event(self, payload: dict) -> None:
        if not self.outbound_url:
            return
        headers = {"Content-Type": "application/json"}
        if self.outbound_secret:
            headers["X-Secret"] = self.outbound_secret
        with httpx.Client(timeout=30) as client:
            response = client.post(self.outbound_url, headers=headers, json=payload)
            response.raise_for_status()

    def _reaction_payload(self, action: str, chat_id: str, message_id: str, reaction: str) -> dict:
        platform = normalize_platform_name(self.default_platform, default=self.default_platform)
        return {
            "platform": platform,
            "chat_id": str(chat_id),
            "type": "reaction",
            "action": action,
            "message_id": str(message_id),
            "reaction": str(reaction),
        }

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:
        if not chat_id or not message_id or not reaction:
            return
        try:
            self._post_outbound_event(self._reaction_payload("add", chat_id, message_id, reaction))
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:
        if not chat_id or not message_id or not reaction:
            return
        try:
            self._post_outbound_event(self._reaction_payload("remove", chat_id, message_id, reaction))
        except Exception:  # noqa: BLE001
            pass
