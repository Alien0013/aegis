"""Built-in channel adapters: CLI (local testing) and Telegram (long-poll)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

from .. import config as cfg
from ..platforms import (
    MAX_TELEGRAM_COMMANDS,
    capped_command_menu,
    chunk_text_by_units,
    normalize_inbound_command,
    normalize_platform_name,
    utf16_units,
)
from ..webhook import DeliveryIdCache, FixedWindowRateLimiter
from .base import BasePlatformAdapter, Dispatch, MessageEvent
from .idempotency import PersistentDeliveryIdStore


def _csv_set(value: str) -> set[str] | None:
    items = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return items or None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"", "auto"}:
        return default
    return value in {"1", "true", "yes", "on", "all"}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


_TELEGRAM_ALLOWED_UPDATES = json.dumps([
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
])

_COMMAND_DESCRIPTIONS = {
    "help": "Show available AEGIS commands",
    "whoami": "Show your gateway identity",
    "status": "Show current agent status",
    "stop": "Stop the active turn",
    "new": "Start a fresh session",
    "reset": "Reset the current session",
    "model": "Show or change the model",
    "provider": "Show or change the provider",
    "reasoning": "Set reasoning effort",
    "fast": "Switch to faster settings",
    "busy": "Change busy handling",
    "compress": "Compress session context",
    "goal": "Set or inspect the active goal",
    "subgoal": "Set or inspect a subgoal",
    "steer": "Steer the active turn",
}


class CLIChannel(BasePlatformAdapter):
    """Reads stdin lines and prints replies. Lets you exercise the gateway locally."""

    name = "cli"

    def start(self, dispatch: Dispatch) -> None:
        print("[cli channel] type messages; Ctrl+D to quit.")
        for line in sys.stdin:
            text = line.strip()
            if not text:
                continue
            ev = MessageEvent(platform="cli", chat_id="local", text=text, user_id="local")
            reply = dispatch(ev)
            if reply:
                self.deliver("local", reply)

    def send(self, chat_id: str, text: str) -> None:
        print(f"\naegis> {text}\n")


class TelegramAdapter(BasePlatformAdapter):
    """Long-polls the Telegram Bot API. Needs TELEGRAM_BOT_TOKEN.

    Optional TELEGRAM_ALLOWED_USERS (comma-separated user ids) restricts access.
    """

    name = "telegram"
    renders_tables = False
    transport = "long_poll"
    max_message_length = 4096
    splits_long_messages = True
    supports_threads = True
    supports_media = True
    supports_reactions = True
    supports_interactive_prompts = True

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
        self.allowed = _csv_set(os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
        self.allowed_chats = _csv_set(os.environ.get("TELEGRAM_ALLOWED_CHATS", ""))
        self.ignored_chats = _csv_set(os.environ.get("TELEGRAM_IGNORED_CHATS", ""))
        self.allowed_chat_types = _csv_set(os.environ.get("TELEGRAM_ALLOWED_CHAT_TYPES", ""))
        self.group_trigger_mode = os.environ.get("TELEGRAM_GROUP_TRIGGER_MODE", "all").strip().lower() or "all"
        self.bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or None
        self.bot_id = os.environ.get("TELEGRAM_BOT_ID", "").strip() or None
        self.auto_discover_bot = _env_bool("TELEGRAM_AUTO_DISCOVER_BOT", True)
        self.register_commands = _env_bool("TELEGRAM_REGISTER_COMMANDS", True)
        self.command_scope_chat_id = os.environ.get("TELEGRAM_COMMAND_SCOPE_CHAT_ID", "").strip()
        self.command_language_code = os.environ.get("TELEGRAM_COMMAND_LANGUAGE_CODE", "").strip()
        self.callback_ttl_seconds = _env_int("TELEGRAM_CALLBACK_TTL_SECONDS", 3600)
        self.idempotency_ttl_seconds = _env_int("TELEGRAM_IDEMPOTENCY_TTL_SECONDS", 3600)
        self.idempotency_cache_max = _env_int("TELEGRAM_IDEMPOTENCY_CACHE_MAX", 10000)
        self.rate_limit_per_minute = _env_int("TELEGRAM_RATE_LIMIT_PER_MINUTE", 60)
        self._delivery_cache = DeliveryIdCache(
            ttl_seconds=float(self.idempotency_ttl_seconds),
            max_items=self.idempotency_cache_max,
        )
        self.persist_idempotency = _env_bool("TELEGRAM_IDEMPOTENCY_PERSIST", True)
        store_path = os.environ.get("TELEGRAM_IDEMPOTENCY_STORE_PATH", "").strip()
        self._delivery_store = PersistentDeliveryIdStore(
            cfg.sub("gateway", "telegram_delivery_ids.json") if not store_path else Path(store_path).expanduser(),
            ttl_seconds=float(self.idempotency_ttl_seconds),
            max_items=self.idempotency_cache_max,
        )
        self._rate_limiter = FixedWindowRateLimiter(limit=self.rate_limit_per_minute, window_seconds=60)
        self._callback_payloads: dict[str, str] = {}
        self._callback_payload_meta: dict[str, dict] = {}
        self._base = f"https://api.telegram.org/bot{self.token}"

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["security"] = {
            "auth_type": "bot_token",
            "allowed_users_configured": bool(self.allowed),
            "allowed_chats_configured": bool(self.allowed_chats),
            "ignored_chats_configured": bool(self.ignored_chats),
            "allowed_chat_types": sorted(self.allowed_chat_types or []),
            "group_trigger_mode": self.group_trigger_mode,
            "bot_username_configured": bool(self.bot_username),
            "bot_id_configured": bool(self.bot_id),
            "auto_discover_bot": self.auto_discover_bot,
            "register_commands": self.register_commands,
            "command_scope_chat_id_configured": bool(self.command_scope_chat_id),
            "command_language_code_configured": bool(self.command_language_code),
            "callback_ttl_env": "TELEGRAM_CALLBACK_TTL_SECONDS",
            "callback_ttl_seconds": self.callback_ttl_seconds,
            "idempotency_env": [
                "TELEGRAM_IDEMPOTENCY_TTL_SECONDS",
                "TELEGRAM_IDEMPOTENCY_CACHE_MAX",
                "TELEGRAM_IDEMPOTENCY_PERSIST",
                "TELEGRAM_IDEMPOTENCY_STORE_PATH",
            ],
            "rate_limit_env": "TELEGRAM_RATE_LIMIT_PER_MINUTE",
        }
        data["idempotency"] = {
            "delivery_id_sources": [
                "update.update_id",
                "callback_query.id",
                "message.chat.id + message.message_id",
            ],
            "delivery_cache": self._delivery_cache.stats(),
            "persistent": self.persist_idempotency,
            "delivery_store": self._delivery_store.stats() if self.persist_idempotency else {},
        }
        data["rate_limiter"] = self._rate_limiter.stats()
        return data

    def command_menu(self, *, max_commands: int = MAX_TELEGRAM_COMMANDS) -> list[str]:
        return capped_command_menu(self._extra_commands(), max_commands=max_commands)

    def _extra_commands(self) -> list[str]:
        config = getattr(self, "_config", None)
        if config is None:
            return []
        try:
            return list(config.get("gateway.user_commands", []) or [])
        except Exception:  # noqa: BLE001
            return []

    def _api(self, method: str, **params):
        with httpx.Client(timeout=70) as c:
            r = c.get(f"{self._base}/{method}", params=params)
            r.raise_for_status()
            return r.json()

    def start(self, dispatch: Dispatch) -> None:
        # Poll on this thread; run each turn on a per-chat worker so the poller keeps reading —
        # that's what lets a 'stop' message interrupt a run already in progress.
        self._init_inbound_queue(dispatch)
        self._startup_sync()
        offset = 0
        while True:
            try:
                data = self._api(
                    "getUpdates",
                    offset=offset,
                    timeout=60,
                    allowed_updates=_TELEGRAM_ALLOWED_UPDATES,
                )
            except Exception:  # noqa: BLE001 - keep the poller alive
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                self._handle_update(upd)

    def _handle_update(self, update: dict) -> bool:
        delivery_id = self._delivery_id_from_update(update)
        if delivery_id and not self._record_delivery_id(delivery_id):
            return True
        try:
            if self._handle_callback_update(update):
                return True
            msg, source = self._message_from_update(update)
            if not msg:
                return False
            attachments = self._attachments_from_message(msg)
            raw_text = self._raw_message_text(msg)
            if not raw_text and not attachments:
                return False
            user_id, username = self._author_from_message(msg)
            if not self._author_allowed(user_id, username):
                self.send(str(msg["chat"]["id"]), "⛔ not authorized.")
                return True
            chat_id = str(msg["chat"]["id"])
            if not self._rate_limiter.allow(self._rate_limit_key(chat_id, user_id)):
                self.send(chat_id, "⏳ rate limit exceeded.")
                return True
            reply_to = msg.get("reply_to_message") or {}
            normalized_text = normalize_inbound_command(
                raw_text,
                platform="telegram",
                bot_username=self.bot_username,
            )
            if not self._message_allowed(msg, normalized_text):
                return True
            event_text = self._event_text(msg, normalized_text, attachments=attachments)
            thread_id = self._message_thread_id(msg)
            ev = MessageEvent(
                platform="telegram",
                chat_id=chat_id,
                text=event_text,   # prefix sender in groups; commands/DMs untouched
                user_id=user_id,
                user_name=username,
                thread_id=thread_id,
                message_id=str(msg.get("message_id") or "") or None,
                reply_to_message_id=str(reply_to.get("message_id") or "") or None,
                reply_to_text=reply_to.get("text") or reply_to.get("caption"),
                timestamp=msg.get("date"),
                attachments=attachments,
                metadata={
                    "chat_type": msg.get("chat", {}).get("type"),
                    "message_thread_id": thread_id,
                    "source": source,
                    "delivery_id": delivery_id or "",
                },
            )
            self._submit_inbound(ev, raw_text=raw_text)
            return True
        except Exception:
            if delivery_id:
                self._discard_delivery_id(delivery_id)
            raise

    def _record_delivery_id(self, delivery_id: str) -> bool:
        delivery_id = str(delivery_id or "").strip()
        if not delivery_id:
            return True
        if not self._delivery_cache.record(delivery_id):
            return False
        if self.persist_idempotency and not self._delivery_store.record(delivery_id):
            self._delivery_cache.discard(delivery_id)
            return False
        return True

    def _discard_delivery_id(self, delivery_id: str) -> None:
        delivery_id = str(delivery_id or "").strip()
        if not delivery_id:
            return
        self._delivery_cache.discard(delivery_id)
        if self.persist_idempotency:
            self._delivery_store.discard(delivery_id)

    def _delivery_id_from_update(self, update: dict) -> str:
        update_id = str(update.get("update_id") or "").strip()
        if update_id:
            return f"update:{update_id}"
        query = update.get("callback_query")
        if isinstance(query, dict):
            query_id = str(query.get("id") or "").strip()
            if query_id:
                return f"callback_query:{query_id}"
            inline_id = str(query.get("inline_message_id") or "").strip()
            if inline_id:
                return f"callback_inline:{inline_id}:{query.get('data') or ''}"
        msg, source = self._message_from_update(update)
        if isinstance(msg, dict):
            chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
            chat_id = str(chat.get("id") or "").strip()
            message_id = str(msg.get("message_id") or "").strip()
            if chat_id and message_id:
                return f"{source}:{chat_id}:{message_id}"
        return ""

    def _startup_sync(self) -> None:
        if self.auto_discover_bot:
            self._refresh_bot_identity()
        if self.register_commands:
            self._register_command_menu()

    def _refresh_bot_identity(self) -> None:
        if self.bot_username and self.bot_id:
            return
        try:
            result = self._api("getMe").get("result") or {}
        except Exception:  # noqa: BLE001 - identity discovery should not kill long-poll
            return
        username = str(result.get("username") or "").strip().lstrip("@")
        bot_id = str(result.get("id") or "").strip()
        if username and not self.bot_username:
            self.bot_username = username
        if bot_id and not self.bot_id:
            self.bot_id = bot_id

    def _register_command_menu(self) -> None:
        commands = []
        for command in self.command_menu():
            name = str(command or "").strip().lstrip("/")
            if not name or not re.fullmatch(r"[a-z0-9_]{1,32}", name):
                continue
            commands.append({
                "command": name,
                "description": _COMMAND_DESCRIPTIONS.get(name, f"Run /{name}"),
            })
        if not commands:
            return
        params = {"commands": json.dumps(commands)}
        if self.command_scope_chat_id:
            params["scope"] = json.dumps({"type": "chat", "chat_id": self.command_scope_chat_id})
        if self.command_language_code:
            params["language_code"] = self.command_language_code
        try:
            self._api("setMyCommands", **params)
        except Exception:  # noqa: BLE001 - command publishing is best-effort
            pass

    def _message_from_update(self, update: dict) -> tuple[dict | None, str]:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            msg = update.get(key)
            if isinstance(msg, dict):
                return msg, key
        return None, ""

    def _author_from_message(self, msg: dict) -> tuple[str, str | None]:
        author = msg.get("from")
        if not isinstance(author, dict):
            author = msg.get("sender_chat") if isinstance(msg.get("sender_chat"), dict) else {}
        user_id = str(author.get("id") or "").strip()
        username = str(author.get("username") or msg.get("author_signature") or "").strip() or None
        return user_id, username

    def _handle_callback_update(self, update: dict) -> bool:
        query = update.get("callback_query")
        if not isinstance(query, dict):
            return False
        raw_data = str(query.get("data") or "").strip()
        data, callback_meta = self._resolve_callback_payload(raw_data)
        if not data:
            kwargs = {"text": "Prompt expired", "show_alert": True} if raw_data.startswith("aegis:") else {}
            self._answer_callback_query(str(query.get("id") or ""), **kwargs)
            return True
        user = query.get("from") if isinstance(query.get("from"), dict) else {}
        user_id = str(user.get("id") or "").strip()
        username = str(user.get("username") or "").strip() or None
        message = query.get("message") if isinstance(query.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or query.get("chat_instance") or "").strip()
        if not chat_id:
            self._answer_callback_query(str(query.get("id") or ""))
            return True
        if not self._author_allowed(user_id, username):
            self._answer_callback_query(str(query.get("id") or ""), text="Not authorized", show_alert=True)
            return True
        if not self._rate_limiter.allow(self._rate_limit_key(chat_id, user_id)):
            self._answer_callback_query(str(query.get("id") or ""), text="Rate limit exceeded", show_alert=True)
            return True
        normalized = normalize_inbound_command(data, platform="telegram", bot_username=self.bot_username)
        if message and not self._message_allowed(message, normalized):
            self._answer_callback_query(str(query.get("id") or ""))
            return True
        thread_id = self._message_thread_id(message)
        ev = MessageEvent(
            platform="telegram",
            chat_id=chat_id,
            text=normalized,
            user_id=user_id or None,
            user_name=username,
            thread_id=thread_id,
            message_id=str(message.get("message_id") or "") or None,
            timestamp=message.get("date") or query.get("date"),
            metadata={
                "callback_query_id": str(query.get("id") or ""),
                "chat_type": chat.get("type"),
                "message_thread_id": thread_id,
                "source": "callback_query",
                "command": normalized if normalized.startswith("/") else "",
                "callback_action_id": callback_meta.get("action_id", ""),
                "callback_kind": callback_meta.get("kind", ""),
                "prompt_id": callback_meta.get("prompt_id", ""),
                "prompt_kind": callback_meta.get("prompt_kind", callback_meta.get("kind", "")),
                "callback_cached": bool(callback_meta.get("cached")),
            },
        )
        self._answer_callback_query(str(query.get("id") or ""))
        self._submit_inbound(ev, raw_text=data)
        return True

    def _rate_limit_key(self, chat_id: str, user_id: str | None = None) -> str:
        chat = str(chat_id or "").strip() or "unknown"
        user = str(user_id or "").strip()
        return f"{chat}:{user}" if user else chat

    def _answer_callback_query(self, callback_query_id: str, *, text: str = "", show_alert: bool = False) -> None:
        if not callback_query_id:
            return
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        if show_alert:
            params["show_alert"] = "true"
        try:
            self._api("answerCallbackQuery", **params)
        except Exception:  # noqa: BLE001
            pass

    def _callback_data_for(self, value: str, *, prefix: str = "c", metadata: dict | None = None) -> str:
        self._prune_callback_payloads()
        text = str(value or "").strip()
        prompt_id = str((metadata or {}).get("prompt_id") or "").strip()
        digest_source = f"{prefix}\0{prompt_id}\0{text}"
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:20]
        key = f"aegis:{prefix}:{digest}"
        now = time.time()
        self._callback_payloads[key] = text
        self._callback_payload_meta[key] = {
            "action_id": key,
            "kind": prefix,
            "prompt_id": prompt_id,
            "prompt_kind": str((metadata or {}).get("prompt_kind") or prefix).strip(),
            "cached": True,
            "value_chars": len(text),
            "created_at": now,
        }
        self._prune_callback_payloads(now=now)
        return key

    def _prune_callback_payloads(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        ttl = int(getattr(self, "callback_ttl_seconds", 3600) or 0)
        if ttl > 0:
            for key, meta in list(self._callback_payload_meta.items()):
                try:
                    created_at = float((meta or {}).get("created_at", now) or now)
                except (TypeError, ValueError):
                    created_at = now
                if now - created_at > ttl:
                    self._callback_payloads.pop(key, None)
                    self._callback_payload_meta.pop(key, None)
        while len(self._callback_payloads) > 1024:
            stale = next(iter(self._callback_payloads))
            self._callback_payloads.pop(stale, None)
            self._callback_payload_meta.pop(stale, None)

    def _resolve_callback_data(self, data: str) -> str:
        return self._resolve_callback_payload(data)[0]

    def _resolve_callback_payload(self, data: str) -> tuple[str, dict]:
        self._prune_callback_payloads()
        raw = str(data or "").strip()
        if raw in self._callback_payloads:
            return self._callback_payloads.get(raw, ""), dict(self._callback_payload_meta.get(raw) or {})
        if raw.startswith("aegis:"):
            return "", {"action_id": raw, "cached": False}
        return raw, {}

    def _message_thread_id(self, msg: dict) -> str | None:
        thread_id = str(msg.get("message_thread_id") or "").strip()
        if not thread_id:
            return None
        if not bool(msg.get("is_topic_message")):
            return None
        return thread_id or None

    def _raw_message_text(self, msg: dict) -> str:
        return str(msg.get("text") or msg.get("caption") or "")

    def _strip_own_addressing(self, text: str) -> str:
        pattern = self._bot_mention_pattern()
        if pattern is None:
            return str(text or "").strip()
        return pattern.sub("", str(text or "")).strip()

    def _event_text(self, msg: dict, normalized_text: str, *, attachments: list[dict] | None = None) -> str:
        text = normalized_text
        if not text.strip() and attachments:
            text = self._attachment_reference_text(attachments)
        if text.lstrip().startswith("/"):
            return text
        return _with_group_context({**msg, "text": self._strip_own_addressing(text)})

    def _attachments_from_message(self, msg: dict) -> list[dict]:
        rows: list[dict] = []
        for kind, default_type, default_filename in (
            ("voice", "audio/ogg", "voice.ogg"),
            ("audio", "audio", "audio"),
            ("document", "document", "document"),
            ("animation", "video/mp4", "animation.mp4"),
            ("video_note", "video/mp4", "video_note.mp4"),
            ("sticker", "image/webp", "sticker.webp"),
        ):
            payload = msg.get(kind)
            if isinstance(payload, dict):
                row = self._telegram_file_attachment(
                    kind,
                    payload,
                    default_type=default_type,
                    default_filename=default_filename,
                )
                if row:
                    rows.append(row)
        photos = [p for p in (msg.get("photo") or []) if isinstance(p, dict)]
        if photos:
            photo = max(
                photos,
                key=lambda p: self._safe_int(p.get("file_size"))
                or self._safe_int(p.get("width")) * self._safe_int(p.get("height")),
            )
            row = self._telegram_file_attachment(
                "photo",
                photo,
                default_type="image/jpeg",
                default_filename="photo.jpg",
            )
            if row:
                rows.append(row)
        video = msg.get("video")
        if isinstance(video, dict):
            row = self._telegram_file_attachment(
                "video",
                video,
                default_type="video/mp4",
                default_filename="video.mp4",
            )
            if row:
                rows.append(row)
        rows.extend(self._structured_attachments_from_message(msg))
        return rows

    def _telegram_file_attachment(
        self,
        kind: str,
        payload: dict,
        *,
        default_type: str,
        default_filename: str,
    ) -> dict | None:
        file_id = str(payload.get("file_id") or "").strip()
        if not file_id:
            return None
        content_type = str(payload.get("mime_type") or default_type or "").strip()
        filename = str(payload.get("file_name") or default_filename or "").strip()
        row: dict = {
            "id": file_id,
            "type": content_type or kind,
            "media_type": content_type,
            "filename": filename,
            "size": self._safe_int(payload.get("file_size")),
            "source": "telegram",
            "kind": kind,
            "file_id": file_id,
        }
        file_unique_id = str(payload.get("file_unique_id") or "").strip()
        if file_unique_id:
            row["file_unique_id"] = file_unique_id
        for key in ("duration", "width", "height", "length"):
            value = self._safe_int(payload.get(key))
            if value:
                row[key] = value
        for key in ("emoji", "set_name", "custom_emoji_id", "performer", "title"):
            value = str(payload.get(key) or "").strip()
            if value:
                row[key] = value
        for key in ("is_animated", "is_video"):
            if key in payload:
                row[key] = bool(payload.get(key))
        return row

    def _structured_attachments_from_message(self, msg: dict) -> list[dict]:
        rows: list[dict] = []
        contact = msg.get("contact")
        if isinstance(contact, dict):
            first = str(contact.get("first_name") or "").strip()
            last = str(contact.get("last_name") or "").strip()
            name = " ".join(part for part in (first, last) if part).strip()
            phone = str(contact.get("phone_number") or "").strip()
            row = {
                "id": str(contact.get("user_id") or phone or name or "contact"),
                "type": "contact",
                "media_type": "contact",
                "filename": name or phone or "contact",
                "source": "telegram",
                "kind": "contact",
            }
            for key in ("phone_number", "first_name", "last_name", "user_id", "vcard"):
                value = contact.get(key)
                if value not in (None, ""):
                    row[key] = value
            rows.append(row)

        location = msg.get("location")
        if isinstance(location, dict):
            row = self._telegram_location_attachment(location, kind="location")
            if row:
                rows.append(row)

        venue = msg.get("venue")
        if isinstance(venue, dict):
            loc = venue.get("location") if isinstance(venue.get("location"), dict) else {}
            row = self._telegram_location_attachment(loc, kind="venue")
            if row:
                title = str(venue.get("title") or "").strip()
                address = str(venue.get("address") or "").strip()
                row["id"] = str(venue.get("foursquare_id") or venue.get("google_place_id") or row["id"])
                row["filename"] = title or address or row["filename"]
                for key in (
                    "title",
                    "address",
                    "foursquare_id",
                    "foursquare_type",
                    "google_place_id",
                    "google_place_type",
                ):
                    value = venue.get(key)
                    if value not in (None, ""):
                        row[key] = value
                rows.append(row)

        poll = msg.get("poll")
        if isinstance(poll, dict):
            question = str(poll.get("question") or "").strip()
            options = []
            for item in poll.get("options") or []:
                if not isinstance(item, dict):
                    continue
                option = {"text": str(item.get("text") or "").strip()}
                if "voter_count" in item:
                    option["voter_count"] = self._safe_int(item.get("voter_count"))
                options.append(option)
            row = {
                "id": str(poll.get("id") or question or "poll"),
                "type": "poll",
                "media_type": "poll",
                "filename": question or "poll",
                "source": "telegram",
                "kind": "poll",
                "question": question,
                "options": options,
            }
            for source, target in (
                ("total_voter_count", "total_voter_count"),
                ("is_closed", "is_closed"),
                ("is_anonymous", "is_anonymous"),
                ("type", "poll_type"),
                ("allows_multiple_answers", "allows_multiple_answers"),
                ("correct_option_id", "correct_option_id"),
                ("explanation", "explanation"),
            ):
                if source in poll:
                    row[target] = poll.get(source)
            rows.append(row)

        return rows

    def _telegram_location_attachment(self, location: dict, *, kind: str) -> dict | None:
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if latitude in (None, "") or longitude in (None, ""):
            return None
        row = {
            "id": f"{latitude},{longitude}",
            "type": kind,
            "media_type": kind,
            "filename": kind,
            "source": "telegram",
            "kind": kind,
            "latitude": latitude,
            "longitude": longitude,
        }
        for key in ("horizontal_accuracy", "live_period", "heading", "proximity_alert_radius"):
            value = location.get(key)
            if value not in (None, ""):
                row[key] = value
        return row

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("kind") or "file").strip()
            name = str(
                attachment.get("filename")
                or attachment.get("file_id")
                or attachment.get("id")
                or "file"
            ).strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

    def _safe_int(self, value) -> int:  # noqa: ANN001
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _author_allowed(self, user_id: str, username: str | None = None) -> bool:
        if not self.allowed:
            return True
        names = {str(user_id)}
        if username:
            names.update({str(username), f"@{username}"})
        return bool(names & self.allowed)

    def _message_allowed(self, msg: dict, normalized_text: str | None = None) -> bool:
        chat = msg.get("chat", {}) or {}
        chat_id = str(chat.get("id", "") or "")
        if self.ignored_chats and ("*" in self.ignored_chats or chat_id in self.ignored_chats):
            return False
        if self.allowed_chats and "*" not in self.allowed_chats and chat_id not in self.allowed_chats:
            return False
        chat_type = str(chat.get("type", "") or "")
        if self.allowed_chat_types and chat_type not in self.allowed_chat_types:
            return False
        if chat_type not in {"group", "supergroup"}:
            return True
        mode = self.group_trigger_mode
        if mode in {"", "all", "always", "true", "1", "yes"}:
            return True
        raw_text = self._raw_message_text(msg)
        text = normalized_text if normalized_text is not None else raw_text
        if mode in {"command", "commands"}:
            return text.lstrip().startswith("/")
        if mode in {"addressed", "mention", "mentions", "reply", "replies"}:
            return (
                text.lstrip().startswith("/")
                or self._mentions_bot(msg, raw_text)
                or self._is_reply_to_bot(msg.get("reply_to_message") or {})
            )
        return True

    def _bot_mention_pattern(self):
        if not self.bot_username:
            return None
        return re.compile(
            rf"(?<![A-Za-z0-9_])@{re.escape(self.bot_username)}"
            r"(?![A-Za-z0-9_]|\.[A-Za-z0-9]|-)",
            re.IGNORECASE,
        )

    def _entity_text(self, text: str, entity: dict) -> str:
        try:
            offset = int(entity.get("offset") or 0)
            length = int(entity.get("length") or 0)
        except (TypeError, ValueError):
            return ""
        if length <= 0:
            return ""
        encoded = str(text or "").encode("utf-16-le")
        start = max(0, offset * 2)
        end = max(start, start + length * 2)
        try:
            return encoded[start:end].decode("utf-16-le")
        except UnicodeDecodeError:
            return ""

    def _mentions_bot(self, msg: dict, text: str) -> bool:
        if not self.bot_username:
            return False
        expected = self.bot_username.lower().lstrip("@")
        for key in ("entities", "caption_entities"):
            entities = msg.get(key)
            if not isinstance(entities, list):
                continue
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("type") or "")
                if entity_type == "mention":
                    mention = self._entity_text(text, entity).strip().lstrip("@").lower()
                    if mention == expected:
                        return True
                elif entity_type == "text_mention" and self.bot_id:
                    user = entity.get("user") if isinstance(entity.get("user"), dict) else {}
                    if str(user.get("id") or "").strip() == str(self.bot_id):
                        return True
        pattern = self._bot_mention_pattern()
        return bool(pattern and pattern.search(str(text or "")))

    def _is_reply_to_bot(self, reply_to: dict) -> bool:
        if not isinstance(reply_to, dict):
            return False
        author = reply_to.get("from") or {}
        if self.bot_id and str(author.get("id", "") or "") == self.bot_id:
            return True
        username = str(author.get("username", "") or "").strip().lstrip("@").lower()
        return bool(self.bot_username and username == self.bot_username.lower())

    def _before_dispatch(self, ev: MessageEvent):
        metadata = self._reply_metadata(ev)
        self._call_with_metadata(self._typing, ev.chat_id, metadata=metadata)
        return {
            "status_id": self._call_with_metadata(
                self._send_status,
                ev.chat_id,
                "🤔 working…",
                metadata=metadata,
            ),
            "metadata": metadata,
        }

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:
        self._finish(ev.chat_id, state, reply)

    def _reply_metadata(self, ev: MessageEvent) -> dict:
        metadata = dict(ev.metadata or {})
        if ev.thread_id and not metadata.get("message_thread_id"):
            metadata["message_thread_id"] = ev.thread_id
        if ev.message_id:
            metadata.setdefault("message_id", ev.message_id)
            metadata["reply_to_message_id"] = ev.message_id
        elif ev.reply_to_message_id:
            metadata.setdefault("reply_to_message_id", ev.reply_to_message_id)
        return metadata

    def _thread_params(self, metadata: dict | None = None) -> dict[str, str]:
        thread_id = (metadata or {}).get("message_thread_id") or (metadata or {}).get("thread_id")
        return {"message_thread_id": str(thread_id)} if thread_id else {}

    def _reply_params(self, metadata: dict | None = None) -> dict[str, str]:
        reply_to = (metadata or {}).get("reply_to_message_id") or (metadata or {}).get("message_id")
        if not reply_to:
            return {}
        return {
            "reply_to_message_id": str(reply_to),
            "allow_sending_without_reply": "true",
        }

    def _send_params(self, metadata: dict | None = None, *, reply: bool = True) -> dict[str, str]:
        params = self._thread_params(metadata)
        if reply:
            params.update(self._reply_params(metadata))
        return params

    def _api_with_thread_fallback(self, method: str, **params):
        try:
            return self._api(method, **params)
        except Exception as exc:  # noqa: BLE001
            if "message_thread_id" not in params or not self._telegram_thread_not_found(exc):
                raise
            retry = {key: value for key, value in params.items() if key != "message_thread_id"}
            return self._api(method, **retry)

    def _telegram_thread_not_found(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                text = f"{text} {response.text}".lower()
            except Exception:  # noqa: BLE001
                pass
            try:
                body = response.json()
                if isinstance(body, dict):
                    text = f"{text} {body.get('description') or ''} {body.get('message') or ''}".lower()
            except Exception:  # noqa: BLE001
                pass
        return "message thread not found" in text or "thread not found" in text

    def _call_with_metadata(self, fn, *args, metadata: dict | None = None):
        import inspect

        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "metadata" in params:
            return fn(*args, metadata=metadata)
        return fn(*args)

    def _typing(self, chat_id: str, *, metadata: dict | None = None) -> None:
        try:
            self._api_with_thread_fallback("sendChatAction", chat_id=chat_id, action="typing", **self._thread_params(metadata))
        except Exception:  # noqa: BLE001 — a missing indicator must never block the reply
            pass

    def _send_status(self, chat_id: str, text: str, *, metadata: dict | None = None) -> int | None:
        try:
            return self._api_with_thread_fallback(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                **self._send_params(metadata),
            ).get("result", {}).get("message_id")
        except Exception:  # noqa: BLE001
            return None

    def _edit(self, chat_id: str, message_id: int, text: str) -> bool:
        try:
            self._api("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _delete(self, chat_id: str, message_id: int) -> None:
        try:
            self._api("deleteMessage", chat_id=chat_id, message_id=message_id)
        except Exception:  # noqa: BLE001
            pass

    def _finish(self, chat_id: str, state, reply: str) -> None:
        """Turn the status bubble into the answer: edit it in place for a short single-message
        text reply; otherwise drop the bubble and deliver normally (chunking/media/tables)."""
        from .base import split_media, tableify
        if isinstance(state, dict):
            status_id = state.get("status_id")
            metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else None
        else:
            status_id = state
            metadata = None
        if not reply:
            if status_id:
                self._delete(chat_id, status_id)
            return
        clean, media = split_media(reply)
        clean = tableify(clean)                       # Telegram can't render pipe tables
        if status_id and clean and not media and len(clean) <= 4000:
            if self._edit(chat_id, status_id, clean):
                return                                # edited in place — no extra bubble
        if status_id:
            self._delete(chat_id, status_id)
        self.deliver(chat_id, reply, metadata=metadata)

    def send_media(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        *,
        metadata: dict | None = None,  # noqa: ARG002
        **_kwargs,
    ) -> None:
        import os
        if not os.path.exists(path):
            self.send(chat_id, f"(file not found: {path})", metadata=metadata)
            return
        ext = os.path.splitext(path)[1].lower()
        method, field = ("sendPhoto", "photo") if ext in (".png", ".jpg", ".jpeg", ".webp") \
            else ("sendVoice", "voice") if ext == ".ogg" \
            else ("sendVideo", "video") if ext in (".mp4", ".mov") \
            else ("sendDocument", "document")
        try:
            with open(path, "rb") as fh, httpx.Client(timeout=120) as c:
                data = {"chat_id": chat_id, "caption": caption}
                if metadata:
                    data.update(self._send_params(metadata))
                try:
                    r = c.post(f"{self._base}/{method}", data=data, files={field: fh})
                    r.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    if "message_thread_id" not in data or not self._telegram_thread_not_found(exc):
                        raise
                    fh.seek(0)
                    retry_data = {key: value for key, value in data.items() if key != "message_thread_id"}
                    r = c.post(f"{self._base}/{method}", data=retry_data, files={field: fh})
                    r.raise_for_status()
        except Exception:  # noqa: BLE001 — fall back to a path note
            self.send(chat_id, f"📎 file ready: {path}", metadata=metadata)

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        # Telegram caps messages at 4096 UTF-16 code units; leave a small margin.
        params = self._send_params(metadata)
        for chunk in chunk_text_by_units(text, limit=4000, len_fn=utf16_units):
            self._api_with_thread_fallback("sendMessage", chat_id=chat_id, text=chunk, **params)

    def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: list[str] | None = None,
        *,
        metadata: dict | None = None,
    ) -> None:
        rendered = str(question or "").strip()
        rows = []
        for i, choice in enumerate(choices or [], 1):
            text = str(choice or "").strip()
            if not text:
                continue
            rendered += f"\n  {i}. {text}"
            rows.append([{
                "text": text,
                "callback_data": self._callback_data_for(text, prefix="clarify", metadata=metadata),
            }])
        params = self._send_params(metadata)
        if rows:
            params["reply_markup"] = json.dumps({"inline_keyboard": rows}, ensure_ascii=False)
        try:
            self._api_with_thread_fallback("sendMessage", chat_id=chat_id, text=rendered, **params)
        except Exception:  # noqa: BLE001
            BasePlatformAdapter.send_clarify(self, chat_id, question, choices or [], metadata=metadata)

    def send_exec_approval(
        self,
        chat_id: str,
        prompt: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        rows = [[
            {
                "text": "Approve",
                "callback_data": self._callback_data_for("approve", prefix="approval", metadata=metadata),
            },
            {
                "text": "Always",
                "callback_data": self._callback_data_for("always", prefix="approval", metadata=metadata),
            },
            {
                "text": "Deny",
                "callback_data": self._callback_data_for("deny", prefix="approval", metadata=metadata),
            },
        ]]
        params = {
            **self._send_params(metadata),
            "reply_markup": json.dumps({"inline_keyboard": rows}, ensure_ascii=False),
        }
        try:
            self._api_with_thread_fallback("sendMessage", chat_id=chat_id, text=prompt, **params)
        except Exception:  # noqa: BLE001
            BasePlatformAdapter.send_exec_approval(self, chat_id, prompt, metadata=metadata)

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:
        emoji = str(reaction or "").strip()
        if not chat_id or not message_id or not emoji:
            return
        payload = json.dumps([{"type": "emoji", "emoji": emoji}], ensure_ascii=False)
        try:
            self._api("setMessageReaction", chat_id=chat_id, message_id=message_id, reaction=payload)
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        if not chat_id or not message_id:
            return
        try:
            self._api("setMessageReaction", chat_id=chat_id, message_id=message_id, reaction="[]")
        except Exception:  # noqa: BLE001
            pass


def _with_group_context(msg: dict) -> str:
    """In a group/supergroup, prefix the sender so the agent knows who is speaking (Telegram
    delivers every member's messages to the bot). DMs are returned unchanged."""
    text = msg.get("text", "")
    if msg.get("chat", {}).get("type") in ("group", "supergroup"):
        who = _telegram_sender_label(msg)
        return f"[{who}]: {text}"
    return text


def _telegram_sender_label(msg: dict) -> str:
    sender_chat = msg.get("sender_chat") if isinstance(msg.get("sender_chat"), dict) else {}
    signature = str(msg.get("author_signature") or "").strip()
    if sender_chat:
        return (
            str(sender_chat.get("username") or "").strip().lstrip("@")
            or str(sender_chat.get("title") or "").strip()
            or str(sender_chat.get("id") or "").strip()
            or signature
            or "sender_chat"
        )
    frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    if frm:
        name = str(frm.get("username") or "").strip().lstrip("@")
        if name:
            return name
        parts = [
            str(frm.get("first_name") or "").strip(),
            str(frm.get("last_name") or "").strip(),
        ]
        full_name = " ".join(part for part in parts if part)
        return full_name or str(frm.get("id") or "user")
    return signature or "user"


def build_adapter(name: str) -> BasePlatformAdapter:
    name = normalize_platform_name(name, default=str(name or "").strip().lower())
    if name == "cli":
        return CLIChannel()
    if name == "telegram":
        return TelegramAdapter()
    if name == "discord":
        from .discord_channel import DiscordAdapter
        return DiscordAdapter()
    if name == "slack":
        from .slack_channel import SlackAdapter
        return SlackAdapter()
    if name == "mattermost":
        from .mattermost_channel import MattermostAdapter
        return MattermostAdapter()
    if name == "signal":
        from .signal_channel import SignalAdapter
        return SignalAdapter()
    if name == "matrix":
        from .matrix_channel import MatrixAdapter
        return MatrixAdapter()
    if name == "email":
        from .email_channel import EmailAdapter
        return EmailAdapter()
    if name == "webhook":
        from .webhook_channel import WebhookChannel
        return WebhookChannel()
    if name == "whatsapp":
        from .webhook_channel import WebhookChannel
        return WebhookChannel(
            name="whatsapp",
            default_platform="whatsapp",
            env_prefix="WHATSAPP_CHANNEL",
            default_port=18792,
            transport="http_bridge",
        )
    if name == "ntfy":
        from .ntfy_channel import NtfyAdapter
        return NtfyAdapter()
    try:
        from ..plugins import load_plugins
        api = load_plugins(quiet=True)
        factory = api.channels.get(name)
        if factory:
            adapter = factory() if callable(factory) else factory
            if not isinstance(adapter, BasePlatformAdapter):
                # Keep duck-typed plugin channels usable while giving them the
                # same delivery helpers if they subclass BasePlatformAdapter.
                if not (hasattr(adapter, "start") and hasattr(adapter, "send")):
                    raise TypeError("plugin channel must expose start(dispatch) and send(chat_id, text)")
            return adapter
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Plugin channel '{name}' failed to load: {exc}") from exc
    raise ValueError(f"Unknown channel '{name}'. Available: cli, telegram, discord, slack, "
                     "signal, matrix, email, webhook, whatsapp, mattermost, ntfy, or a plugin channel.")
