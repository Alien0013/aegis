"""Built-in channel adapters: CLI (local testing) and Telegram (long-poll)."""

from __future__ import annotations

import os
import sys
import threading

import httpx

from .base import BasePlatformAdapter, Dispatch, MessageEvent, is_control_interrupt


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
        # Poll on this thread; run each turn on a per-chat worker so the poller keeps reading —
        # that's what lets a 'stop' message interrupt a run already in progress.
        self._dispatch = dispatch
        self._queues: dict[str, list[MessageEvent]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._qlock = threading.Lock()
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
                    text=_with_group_context(msg),   # prefix sender in groups; DMs untouched
                    user_id=user_id,
                    user_name=username,
                )
                # A bare 'stop' while this chat has a turn running cancels it (no new turn).
                if is_control_interrupt(msg["text"]):
                    cb = getattr(self, "_interrupt_cb", None)
                    w = self._workers.get(ev.chat_id)
                    if cb and w and w.is_alive() and cb(ev):
                        self.send(ev.chat_id, "🛑 stopped.")
                        continue
                self._enqueue(ev)

    def _enqueue(self, ev: MessageEvent) -> None:
        with self._qlock:
            self._queues.setdefault(ev.chat_id, []).append(ev)
            w = self._workers.get(ev.chat_id)
            if not (w and w.is_alive()):     # one worker per chat -> turns stay ordered
                w = threading.Thread(target=self._drain, args=(ev.chat_id,), daemon=True)
                self._workers[ev.chat_id] = w
                w.start()

    def _drain(self, chat_id: str) -> None:
        while True:
            with self._qlock:
                q = self._queues.get(chat_id) or []
                ev = q.pop(0) if q else None
                if ev is None:
                    return
            self._typing(chat_id)
            status_id = self._send_status(chat_id, "🤔 working…")
            reply = self._dispatch(ev)
            self._finish(chat_id, status_id, reply)

    def _typing(self, chat_id: str) -> None:
        try:
            self._api("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001 — a missing indicator must never block the reply
            pass

    def _send_status(self, chat_id: str, text: str) -> int | None:
        try:
            return self._api("sendMessage", chat_id=chat_id, text=text).get("result", {}).get("message_id")
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

    def _finish(self, chat_id: str, status_id: int | None, reply: str) -> None:
        """Turn the status bubble into the answer: edit it in place for a short single-message
        text reply; otherwise drop the bubble and deliver normally (chunking/media/tables)."""
        from .base import split_media, tableify
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
        self.deliver(chat_id, reply)

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


def _with_group_context(msg: dict) -> str:
    """In a group/supergroup, prefix the sender so the agent knows who is speaking (Telegram
    delivers every member's messages to the bot). DMs are returned unchanged. Hermes parity."""
    text = msg.get("text", "")
    if msg.get("chat", {}).get("type") in ("group", "supergroup"):
        frm = msg.get("from", {})
        who = frm.get("username") or frm.get("first_name") or str(frm.get("id", "user"))
        return f"[{who}]: {text}"
    return text


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
