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

from .base import BasePlatformAdapter, Dispatch, MessageEvent


class WebhookChannel(BasePlatformAdapter):
    name = "webhook"

    def __init__(self):
        self.port = int(os.environ.get("WEBHOOK_CHANNEL_PORT", "18790"))
        self.secret = os.environ.get("WEBHOOK_CHANNEL_SECRET")

    def start(self, dispatch: Dispatch) -> None:
        secret = self.secret
        adapter = self
        self._init_inbound_queue(dispatch)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):  # noqa: N802
                if secret and self.headers.get("X-Secret") != secret:
                    self.send_response(401)
                    self.end_headers()
                    return
                n = int(self.headers.get("content-length", 0))
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                ev = MessageEvent(
                    platform=body.get("platform", "webhook"),
                    chat_id=str(body.get("chat_id", "unknown")),
                    text=body.get("text", ""),
                    user_id=str(body.get("user_id")) if body.get("user_id") else None,
                )
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
