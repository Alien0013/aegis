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
from ..webhook import DeliveryIdCache, FixedWindowRateLimiter, _env_truthy, _is_loopback_host
from .base import BasePlatformAdapter, Dispatch, MessageEvent

_NULL_THREAD_IDS = {"", "none", "null", "undefined"}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    if name not in os.environ:
        return default
    value = os.environ.get(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _list_of_strings(value) -> list[str]:  # noqa: ANN001
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _string_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _first_string(*values) -> str:  # noqa: ANN001
    for value in values:
        text = _string_value(value)
        if text:
            return text
    return ""


class MattermostAdapter(BasePlatformAdapter):
    name = "mattermost"
    renders_tables = False
    transport = "http_webhook"
    max_message_length = 16000
    supports_threads = True
    supports_media = False
    supports_reactions = True
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
        self.action_url = os.environ.get("MATTERMOST_ACTION_URL", "").strip()
        self.bot_user_id = os.environ.get("MATTERMOST_BOT_USER_ID", "").strip()
        self.allow_unsigned_loopback = _env_bool("MATTERMOST_ALLOW_UNSIGNED_LOOPBACK", True)
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(_env_int("MATTERMOST_IDEMPOTENCY_TTL_SECONDS", 3600)),
            max_items=_env_int("MATTERMOST_IDEMPOTENCY_CACHE_MAX", 10000),
        )
        self._rate_limiter = FixedWindowRateLimiter(
            limit=_env_int("MATTERMOST_RATE_LIMIT_PER_MINUTE", 60),
            window_seconds=60,
        )

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data.update({
            "port": self.port,
            "security": {
                "webhook_secret_configured": bool(self.webhook_secret),
                "action_url_configured": bool(self.action_url),
                "auth_type": "bearer",
                "loopback_unsigned_allowed": self.allow_unsigned_loopback,
                "insecure_env_override": _env_truthy("MATTERMOST_INSECURE_NO_AUTH"),
            },
            "supports_interactive_prompts": True,
            "idempotency": {
                "delivery_id_sources": [
                    "X-Request-ID",
                    "X-Request-Id",
                    "Idempotency-Key",
                    "body.post_id",
                    "body.id",
                    "body.message_id",
                ],
                "delivery_cache": self._delivery_cache.stats(),
            },
            "rate_limiter": self._rate_limiter.stats(),
        })
        return data

    def _root_id(self, chat_id: str, metadata: dict | None = None) -> str:
        source = metadata or {}
        raw = source.get("root_id") or source.get("thread_id") or source.get("parent_id")
        root_id = str(raw or "").strip()
        if root_id.lower() in _NULL_THREAD_IDS:
            return ""
        if root_id == str(chat_id or "").strip():
            return ""
        current_post_id = str(source.get("post_id") or source.get("message_id") or source.get("id") or "").strip()
        if current_post_id and root_id == current_post_id:
            return ""
        return root_id

    def _resolve_root_id(self, client: httpx.Client, chat_id: str, metadata: dict | None = None) -> str:
        root_id = self._root_id(chat_id, metadata)
        if not root_id:
            return ""
        try:
            response = client.get(f"{self.base_url}/api/v4/posts/{root_id}", headers={"Authorization": f"Bearer {self.token}"})
            response.raise_for_status()
            post = response.json()
        except Exception:  # noqa: BLE001
            return root_id
        if not isinstance(post, dict):
            return root_id
        resolved = str(post.get("root_id") or "").strip()
        if resolved.lower() in _NULL_THREAD_IDS:
            return root_id
        return resolved or root_id

    def _broken_root_response(self, exc: httpx.HTTPStatusError) -> bool:
        status = getattr(exc.response, "status_code", 0)
        if status not in {400, 404}:
            return False
        try:
            text = exc.response.text
        except Exception:  # noqa: BLE001
            text = ""
        lowered = str(text or "").lower()
        if not lowered:
            return True
        return "root" in lowered or "thread" in lowered or "post" in lowered

    def _emoji_name(self, reaction: str) -> str:
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
        return aliases.get(value, value).strip(":").strip()

    def _resolve_bot_user_id(self, client: httpx.Client) -> str:
        if self.bot_user_id:
            return self.bot_user_id
        try:
            response = client.get(f"{self.base_url}/api/v4/users/me", headers={"Authorization": f"Bearer {self.token}"})
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                self.bot_user_id = str(payload.get("id") or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        return self.bot_user_id

    def _interactive_text_from_body(self, body: dict) -> str:
        context = _dict_value(body.get("context"))
        selected = _dict_value(body.get("selected_option") or context.get("selected_option"))
        action = _dict_value(body.get("action"))
        event_type = _string_value(body.get("type") or body.get("event_type") or body.get("kind")).lower()
        if event_type and event_type not in {
            "action",
            "interactive",
            "button",
            "clarify_response",
            "approval_response",
            "exec_approval_response",
        }:
            event_type = ""
        if not (event_type or context or selected or action or body.get("action_id")):
            return ""
        return _first_string(
            body.get("value"),
            body.get("choice"),
            body.get("answer"),
            body.get("selected"),
            context.get("value"),
            context.get("choice"),
            context.get("answer"),
            context.get("action"),
            selected.get("value"),
            selected.get("text"),
            selected.get("name"),
            action.get("value"),
            action.get("text"),
        )

    def _event_from_body(self, body: dict) -> MessageEvent:
        interactive_text = self._interactive_text_from_body(body)
        body_text = interactive_text or str(body.get("text") or body.get("message") or "")
        command_name = str(body.get("command") or "").strip()
        raw_text = f"{command_name} {body_text}".strip() if command_name else body_text
        text = normalize_inbound_command(raw_text, platform="mattermost")
        attachments = self._attachments_from_body(body)
        if not text.strip() and attachments:
            text = self._attachment_reference_text(attachments)
        channel_id = str(body.get("channel_id") or body.get("channel") or "")
        post_id = str(body.get("post_id") or body.get("id") or body.get("message_id") or "")
        root_id = str(body.get("root_id") or body.get("thread_id") or body.get("parent_id") or "").strip()
        if root_id.lower() in _NULL_THREAD_IDS:
            root_id = ""
        if root_id and post_id and root_id == post_id:
            root_id = ""
        metadata = {
            "team_id": body.get("team_id"),
            "channel_name": body.get("channel_name"),
            "post_id": post_id,
            "root_id": root_id or "",
        }
        if command_name:
            metadata["command"] = command_name
            metadata["source"] = "slash_command"
            if body.get("response_url"):
                metadata["response_url"] = body.get("response_url")
            if body.get("trigger_id"):
                metadata["trigger_id"] = body.get("trigger_id")
        if interactive_text:
            metadata["source"] = "interactive_action"
            metadata["action_id"] = _string_value(body.get("action_id") or body.get("callback_id"))
            context = _dict_value(body.get("context"))
            if context.get("type"):
                metadata["action_type"] = _string_value(context.get("type"))
        return MessageEvent(
            platform="mattermost",
            chat_id=channel_id,
            text=text,
            user_id=str(body.get("user_id") or "") or None,
            user_name=str(body.get("user_name") or body.get("username") or "") or None,
            thread_id=root_id or None,
            message_id=post_id or None,
            timestamp=body.get("create_at") or body.get("timestamp"),
            attachments=attachments,
            metadata=metadata,
        )

    def _attachments_from_body(self, body: dict) -> list[dict]:
        rows: list[dict] = []
        for file_id in _list_of_strings(body.get("file_ids") or body.get("fileIds")):
            text = str(file_id or "").strip()
            if text:
                rows.append({
                    "id": text,
                    "type": "file",
                    "filename": text,
                    "source": "mattermost",
                })
        candidates = []
        for key in ("files", "attachments"):
            value = body.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        props = body.get("props")
        if isinstance(props, dict) and isinstance(props.get("attachments"), list):
            candidates.extend(props.get("attachments") or [])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("id") or item.get("file_id") or item.get("fileId") or "").strip()
            filename = str(item.get("name") or item.get("filename") or item.get("title") or "").strip()
            mimetype = str(item.get("mime_type") or item.get("mimeType") or item.get("content_type") or "").strip()
            media_type = mimetype.split("/", 1)[0] if "/" in mimetype else str(item.get("type") or "").strip()
            row = {
                "id": file_id,
                "type": mimetype or media_type or "file",
                "media_type": mimetype,
                "filename": filename or file_id or "file",
                "url": str(item.get("url") or item.get("link") or "").strip(),
                "source": "mattermost",
            }
            try:
                row["size"] = int(item.get("size") or 0)
            except (TypeError, ValueError):
                row["size"] = 0
            rows.append(row)
        return rows

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("id") or "file").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

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

    def _auth_allowed(self, headers, body: dict, client_host: str) -> bool:
        if self.webhook_secret:
            return self._verify_webhook(headers, body)
        return _env_truthy("MATTERMOST_INSECURE_NO_AUTH") or (
            self.allow_unsigned_loopback and _is_loopback_host(client_host)
        )

    def _delivery_id(self, headers, body: dict) -> str:
        for name in ("X-Request-ID", "X-Request-Id", "Idempotency-Key"):
            value = str(headers.get(name, "") or "").strip()
            if value:
                return f"{name.lower()}:{value}"
        for key in ("post_id", "id", "message_id"):
            value = str(body.get(key, "") or "").strip()
            if value:
                return f"body:{key}:{value}"
        return ""

    def _is_self_echo(self, body: dict) -> bool:
        if not self.bot_user_id:
            return False
        user_id = str(body.get("user_id") or body.get("userId") or "").strip()
        return bool(user_id and user_id == self.bot_user_id)

    def _handle_inbound_payload(self, headers, body: dict, *, client_host: str = "127.0.0.1") -> tuple[int, dict]:
        if not self._rate_limiter.allow(str(client_host or "")):
            return 429, {"error": "rate limit exceeded"}
        if not self._auth_allowed(headers, body, client_host):
            return 401, {"error": "invalid webhook token"}
        if self._is_self_echo(body):
            return 200, {"text": "", "response_type": "comment", "ignored": True, "reason": "bot_self_echo"}
        delivery_id = self._delivery_id(headers, body)
        delivery_recorded = False
        if delivery_id:
            delivery_recorded = self._delivery_cache.record(delivery_id)
            if not delivery_recorded:
                return 200, {"text": "", "response_type": "comment", "duplicate": True}
        try:
            ev = self._event_from_body(body)
            reply = self._submit_inbound(ev, wait=True) or ""
        except Exception as exc:  # noqa: BLE001
            if delivery_recorded:
                self._delivery_cache.discard(delivery_id)
            return 500, {"error": f"dispatch failed: {type(exc).__name__}: {exc}"}
        return 200, {"text": reply, "response_type": "comment"}

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
                client_host = str((self.client_address or ("",))[0] or "")
                status, payload = adapter._handle_inbound_payload(self.headers, body, client_host=client_host)
                return self._json(status, payload)

        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        print(f"  - mattermost channel listening on :{self.port}/in")
        httpd.serve_forever()

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(timeout=30) as client:
            root_id = self._resolve_root_id(client, chat_id, metadata)
            for chunk in chunk_text_by_units(text, limit=16000):
                self._post_message(client, chat_id, {"message": chunk}, root_id=root_id, headers=headers)

    def _post_message(
        self,
        client: httpx.Client,
        chat_id: str,
        payload: dict,
        *,
        root_id: str = "",
        headers: dict | None = None,
    ) -> None:
        headers = headers or {"Authorization": f"Bearer {self.token}"}
        post_payload = {"channel_id": chat_id, **payload}
        if root_id:
            post_payload["root_id"] = root_id
        response = client.post(f"{self.base_url}/api/v4/posts", headers=headers, json=post_payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if not root_id or not self._broken_root_response(exc):
                raise
            fallback = {"channel_id": chat_id, **payload}
            retry = client.post(f"{self.base_url}/api/v4/posts", headers=headers, json=fallback)
            retry.raise_for_status()

    def _interactive_action(self, *, name: str, value: str, kind: str) -> dict:
        return {
            "id": f"aegis_{kind}",
            "name": str(name or value)[:75] or "Choose",
            "integration": {
                "url": self.action_url,
                "context": {
                    "source": "aegis",
                    "type": kind,
                    "value": str(value or ""),
                },
            },
        }

    def _post_interactive_prompt(
        self,
        chat_id: str,
        text: str,
        actions: list[dict],
        *,
        metadata: dict | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(timeout=30) as client:
            root_id = self._resolve_root_id(client, chat_id, metadata)
            self._post_message(
                client,
                chat_id,
                {
                    "message": text,
                    "props": {
                        "attachments": [{
                            "text": text,
                            "actions": actions,
                        }],
                    },
                },
                root_id=root_id,
                headers=headers,
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
        if not self.action_url or not choice_values:
            return super().send_clarify(chat_id, question, choices or [], metadata=metadata)
        actions = [
            self._interactive_action(name=choice, value=choice, kind="clarify")
            for choice in choice_values[:5]
        ]
        try:
            self._post_interactive_prompt(
                chat_id,
                str(question or "").strip() or "Choose one:",
                actions,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            super().send_clarify(chat_id, question, choices or [], metadata=metadata)

    def send_exec_approval(
        self,
        chat_id: str,
        prompt: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        if not self.action_url:
            return super().send_exec_approval(chat_id, prompt, metadata=metadata)
        actions = [
            self._interactive_action(name="Approve", value="approve", kind="exec_approval"),
            self._interactive_action(name="Always", value="always", kind="exec_approval"),
            self._interactive_action(name="Deny", value="deny", kind="exec_approval"),
        ]
        try:
            self._post_interactive_prompt(
                chat_id,
                str(prompt or "").strip() or "Approve this action?",
                actions,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            super().send_exec_approval(chat_id, prompt, metadata=metadata)

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        post_id = str(message_id or "").strip()
        emoji = self._emoji_name(reaction)
        if not post_id or not emoji:
            return
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            with httpx.Client(timeout=30) as client:
                user_id = self._resolve_bot_user_id(client)
                if not user_id:
                    return
                response = client.post(
                    f"{self.base_url}/api/v4/reactions",
                    headers=headers,
                    json={"user_id": user_id, "post_id": post_id, "emoji_name": emoji},
                )
                response.raise_for_status()
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        post_id = str(message_id or "").strip()
        emoji = self._emoji_name(reaction)
        if not post_id or not emoji:
            return
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            with httpx.Client(timeout=30) as client:
                user_id = self._resolve_bot_user_id(client)
                if not user_id:
                    return
                response = client.delete(
                    f"{self.base_url}/api/v4/users/{user_id}/posts/{post_id}/reactions/{emoji}",
                    headers=headers,
                )
                response.raise_for_status()
        except Exception:  # noqa: BLE001
            pass
