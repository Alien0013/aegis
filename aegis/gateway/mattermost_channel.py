"""Mattermost channel adapter.

Inbound uses a local HTTP endpoint suitable for Mattermost outgoing webhooks or
slash-command integrations. Outbound uses the Mattermost REST posts API.
"""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import httpx

from ..platforms import chunk_text_by_units, normalize_inbound_command
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class MattermostAdapter(BasePlatformAdapter):
    name = "mattermost"
    renders_tables = False
    transport = "http_webhook"
    max_message_length = 16000
    supports_threads = True
    supports_media = False
    typed_command_prefix = "!"

    def __init__(self):
        self.base_url = os.environ.get("MATTERMOST_URL", "").rstrip("/")
        self.token = os.environ.get("MATTERMOST_BOT_TOKEN", "")
        if not self.base_url or not self.token:
            raise RuntimeError("MATTERMOST_URL and MATTERMOST_BOT_TOKEN must be set.")
        self.port = _env_int("MATTERMOST_CHANNEL_PORT", 18791)
        self.webhook_secret = (
            os.environ.get("MATTERMOST_WEBHOOK_SECRET")
            or os.environ.get("MATTERMOST_OUTGOING_TOKEN")
            or ""
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data.update({
            "port": self.port,
            "security": {
                "webhook_secret_configured": bool(self.webhook_secret),
                "auth_type": "bearer",
            },
        })
        return data

    def _root_id(self, chat_id: str, metadata: dict | None = None) -> str:
        source = metadata or {}
        raw = source.get("root_id") or source.get("thread_id") or source.get("post_id")
        root_id = str(raw or "").strip()
        if not root_id or root_id.lower() in {"none", "null", "undefined"}:
            return ""
        if root_id == str(chat_id or "").strip():
            return ""
        return root_id

    def _event_from_body(self, body: dict) -> MessageEvent:
        raw_text = str(body.get("text") or body.get("message") or "")
        text = normalize_inbound_command(raw_text, platform="mattermost")
        channel_id = str(body.get("channel_id") or body.get("channel") or "")
        post_id = str(body.get("post_id") or body.get("id") or body.get("message_id") or "")
        root_id = str(body.get("root_id") or body.get("thread_id") or "") or post_id or None
        return MessageEvent(
            platform="mattermost",
            chat_id=channel_id,
            text=text,
            user_id=str(body.get("user_id") or "") or None,
            user_name=str(body.get("user_name") or body.get("username") or "") or None,
            thread_id=root_id,
            message_id=post_id or None,
            timestamp=body.get("create_at") or body.get("timestamp"),
            metadata={
                "team_id": body.get("team_id"),
                "channel_name": body.get("channel_name"),
                "post_id": post_id,
                "root_id": root_id or "",
            },
        )

    def _verify_webhook(self, headers, body: dict) -> bool:
        if not self.webhook_secret:
            return True
        supplied = (
            headers.get("X-Secret")
            or headers.get("X-Mattermost-Token")
            or body.get("token")
            or ""
        )
        return hmac.compare_digest(str(supplied or ""), self.webhook_secret)

    def _parse_body(self, raw: bytes, content_type: str) -> dict:
        if "application/json" in content_type:
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: ANN001
                pass

            def _json(self, code: int, obj: dict) -> None:
                payload = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):  # noqa: N802
                try:
                    size = int(self.headers.get("content-length", "0") or "0")
                except (TypeError, ValueError):
                    return self._json(400, {"error": "invalid content-length"})
                if size < 0 or size > 1_000_000:
                    return self._json(413 if size > 1_000_000 else 400, {"error": "payload too large"})
                raw = self.rfile.read(size) if size else b"{}"
                try:
                    body = adapter._parse_body(raw, self.headers.get("content-type", ""))
                except Exception:  # noqa: BLE001
                    return self._json(400, {"error": "invalid body"})
                if not adapter._verify_webhook(self.headers, body):
                    return self._json(401, {"error": "invalid webhook token"})
                ev = adapter._event_from_body(body)
                reply = adapter._submit_inbound(ev, wait=True) or ""
                return self._json(200, {"text": reply, "response_type": "comment"})

        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        print(f"  - mattermost channel listening on :{self.port}/in")
        httpd.serve_forever()

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        root_id = self._root_id(chat_id, metadata)
        with httpx.Client(timeout=30) as client:
            for chunk in chunk_text_by_units(text, limit=16000):
                payload = {"channel_id": chat_id, "message": chunk}
                if root_id:
                    payload["root_id"] = root_id
                response = client.post(f"{self.base_url}/api/v4/posts", headers=headers, json=payload)
                response.raise_for_status()
