"""Built-in channel adapters: CLI (local testing) and Telegram (long-poll)."""

from __future__ import annotations

import os
import sys

import httpx

from .base import BasePlatformAdapter, Dispatch, MessageEvent


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

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
        allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
        self.allowed = {u.strip() for u in allowed.split(",") if u.strip()} if allowed else None
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _api(self, method: str, **params):
        with httpx.Client(timeout=70) as c:
            r = c.get(f"{self._base}/{method}", params=params)
            r.raise_for_status()
            return r.json()

    def start(self, dispatch: Dispatch) -> None:
        offset = 0
        while True:
            try:
                data = self._api("getUpdates", offset=offset, timeout=60)
            except Exception:  # noqa: BLE001 - keep the poller alive
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                user_id = str(msg["from"]["id"])
                username = msg["from"].get("username")
                names = {user_id}
                if username:
                    names.update({username, f"@{username}"})
                if self.allowed and not (names & self.allowed):
                    self.send(str(msg["chat"]["id"]), "⛔ not authorized.")
                    continue
                ev = MessageEvent(
                    platform="telegram",
                    chat_id=str(msg["chat"]["id"]),
                    text=msg["text"],
                    user_id=user_id,
                    user_name=username,
                )
                reply = dispatch(ev)
                if reply:
                    self.deliver(ev.chat_id, reply)

    def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        import os
        if not os.path.exists(path):
            self.send(chat_id, f"(file not found: {path})")
            return
        ext = os.path.splitext(path)[1].lower()
        method, field = ("sendPhoto", "photo") if ext in (".png", ".jpg", ".jpeg", ".webp") \
            else ("sendVoice", "voice") if ext == ".ogg" \
            else ("sendVideo", "video") if ext in (".mp4", ".mov") \
            else ("sendDocument", "document")
        try:
            with open(path, "rb") as fh, httpx.Client(timeout=120) as c:
                r = c.post(f"{self._base}/{method}", data={"chat_id": chat_id, "caption": caption},
                           files={field: fh})
                r.raise_for_status()
        except Exception:  # noqa: BLE001 — fall back to a path note
            self.send(chat_id, f"📎 file ready: {path}")

    def send(self, chat_id: str, text: str) -> None:
        # Telegram caps messages at 4096 chars.
        for i in range(0, len(text) or 1, 4000):
            chunk = text[i:i + 4000] or "(empty)"
            try:
                self._api("sendMessage", chat_id=chat_id, text=chunk)
            except Exception:  # noqa: BLE001
                pass


def build_adapter(name: str) -> BasePlatformAdapter:
    name = name.lower()
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
    if name == "ntfy":
        from .ntfy_channel import NtfyAdapter
        return NtfyAdapter()
    raise ValueError(f"Unknown channel '{name}'. Available: cli, telegram, discord, slack, "
                     "signal, matrix, email, webhook, ntfy.")
