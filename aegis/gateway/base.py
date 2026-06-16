"""Channel adapter interface and the normalized inbound message event."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# A dispatcher takes a normalized event and returns the agent's reply text.
Dispatch = Callable[["MessageEvent"], str]


@dataclass
class MessageEvent:
    platform: str
    chat_id: str
    text: str
    user_id: str | None = None
    user_name: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    reply_to_text: str | None = None
    session_key: str | None = None
    internal: bool = False
    attachments: list[dict] = field(default_factory=list)


class BasePlatformAdapter:
    """Subclasses implement a blocking ``start`` loop and ``send``."""

    name: str = "base"
    renders_tables: bool = True   # chat surfaces (Telegram/Discord/…) set False -> tables rewritten

    def start(self, dispatch: Dispatch) -> None:  # pragma: no cover - interface
        """Block, receiving messages and calling ``dispatch(event)``; send replies."""
        raise NotImplementedError

    def send(self, chat_id: str, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def _init_inbound_queue(self, dispatch: Dispatch) -> None:
        self._dispatch = dispatch
        self._queues: dict[str, list[MessageEvent]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._active: set[str] = set()
        self._qlock = threading.Lock()

    def _ensure_inbound_queue(self, dispatch: Dispatch | None = None) -> None:
        if dispatch is not None or not hasattr(self, "_dispatch"):
            self._dispatch = dispatch or (lambda _ev: "")
        if not hasattr(self, "_queues"):
            self._queues = {}
        if not hasattr(self, "_workers"):
            self._workers = {}
        if not hasattr(self, "_active"):
            self._active = set()
        if not hasattr(self, "_qlock"):
            self._qlock = threading.Lock()
        if not hasattr(self, "_clarify_waiters"):
            self._clarify_waiters = {}

    def _conversation_key(self, ev: MessageEvent) -> str:
        cb = getattr(self, "_conversation_key_cb", None)
        if cb is not None:
            try:
                key = cb(ev)
                if key:
                    return str(key)
            except Exception:  # noqa: BLE001
                pass
        return ev.chat_id

    def _submit_inbound(
        self,
        ev: MessageEvent,
        *,
        wait: bool = False,
        raw_text: str | None = None,
    ) -> str | None:
        self._ensure_inbound_queue(getattr(self, "_dispatch", None))
        if self._resolve_clarify_waiter(ev):
            return ""
        if self._handle_inbound_control(ev, raw_text=raw_text):
            return ""
        done: threading.Event | None = None
        if wait:
            done = threading.Event()
            ev._reply_event = done
            ev._reply_inline = True
        self._enqueue(ev)
        if done is None:
            return None
        done.wait()
        return str(getattr(ev, "_reply_text", "") or "")

    def _handle_inbound_control(self, ev: MessageEvent, *, raw_text: str | None = None) -> bool:
        self._ensure_inbound_queue()
        text = raw_text if raw_text is not None else ev.text
        if getattr(ev, "internal", False):
            return False
        key = self._conversation_key(ev)
        with self._qlock:
            worker = self._workers.get(key)
            busy = bool(worker and worker.is_alive() and key in self._active)
        if not busy:
            return False
        if is_control_reset(text):
            cb = getattr(self, "_interrupt_cb", None)
            if cb and cb(ev):
                ev._bypass_busy_mode = True
                self._deliver_reply(ev, "🛑 stopping current turn; reset queued.", None)
            return False
        if is_control_interrupt(text):
            cb = getattr(self, "_interrupt_cb", None)
            if cb and cb(ev):
                self._deliver_reply(ev, "🛑 stopped.", None)
                return True
        if (text or "").startswith("/steer "):
            scb = getattr(self, "_steer_cb", None)
            guidance = (text or "")[len("/steer "):].strip()
            if scb and scb(ev, guidance):
                self._deliver_reply(ev, "🧭 steering noted.", None)
                return True
        return False

    def _enqueue(self, ev: MessageEvent) -> None:
        self._ensure_inbound_queue()
        key = self._conversation_key(ev)
        if not hasattr(ev, "_queued_at"):
            ev._queued_at = time.monotonic()
        with self._qlock:
            worker = self._workers.get(key)
            busy = bool(worker and worker.is_alive() and key in self._active)
        if busy and not getattr(ev, "internal", False) and not getattr(ev, "_bypass_busy_mode", False):
            handled, note = self._apply_busy_mode(ev)
            if note:
                self._deliver_reply(ev, note, None)
            if handled:
                done = getattr(ev, "_reply_event", None)
                if done is not None:
                    ev._reply_text = ""
                    done.set()
                return
        with self._qlock:
            self._queues.setdefault(key, []).append(ev)
            worker = self._workers.get(key)
            if not (worker and worker.is_alive()):
                worker = threading.Thread(target=self._drain, args=(key,), daemon=True)
                self._workers[key] = worker
                worker.start()

    def _apply_busy_mode(self, ev: MessageEvent) -> tuple[bool, str]:
        config = getattr(self, "_config", None)
        mode = str(config.get("gateway.busy_mode", "queue")) if config else "queue"
        handled = False
        applied = "queue"
        if mode == "steer":
            scb = getattr(self, "_steer_cb", None)
            if scb and scb(ev, ev.text):
                handled, applied = True, "steer"
        elif mode == "interrupt":
            cb = getattr(self, "_interrupt_cb", None)
            if cb and cb(ev):
                applied = "interrupt"
        note = ""
        if config is not None:
            from ..firstrun import BUSY_FLAG, busy_hint, is_seen, mark_seen
            if not is_seen(config, BUSY_FLAG):
                mark_seen(config, BUSY_FLAG)
                note = busy_hint(applied)
        return handled, note

    def _drain(self, key: str) -> None:
        self._ensure_inbound_queue()
        while True:
            with self._qlock:
                queue = self._queues.get(key) or []
                ev = queue.pop(0) if queue else None
                if ev is None:
                    if self._workers.get(key) is threading.current_thread():
                        self._workers.pop(key, None)
                    return
            state = self._before_dispatch(ev)
            run_id = self._record_delivery_start(ev)
            status = "ok"
            error = ""
            started = time.monotonic()
            with self._qlock:
                self._active.add(key)
            try:
                reply = self._dispatch(ev)
            except Exception as exc:  # noqa: BLE001
                reply = f"⚠ dispatch failed: {type(exc).__name__}: {exc}"
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._qlock:
                    self._active.discard(key)
            ev._reply_text = reply or ""
            if not getattr(ev, "_reply_inline", False):
                try:
                    self._deliver_reply(ev, reply, state)
                except Exception as exc:  # noqa: BLE001
                    status = "error"
                    error = f"deliver {type(exc).__name__}: {exc}"
            done = getattr(ev, "_reply_event", None)
            if done is not None:
                done.set()
            self._record_delivery_finish(
                run_id,
                status=status,
                reply=reply or "",
                error=error,
                dispatch_ms=int((time.monotonic() - started) * 1000),
                inline=bool(getattr(ev, "_reply_inline", False)),
            )

    def _before_dispatch(self, ev: MessageEvent):  # noqa: ANN001
        return None

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        if reply:
            self.deliver(ev.chat_id, reply)

    def _record_delivery_start(self, ev: MessageEvent) -> str:
        try:
            from ..runs import RunStore

            queued_at = float(getattr(ev, "_queued_at", time.monotonic()) or time.monotonic())
            run = RunStore().start(
                surface="gateway",
                kind="delivery",
                title=f"{ev.platform}:{ev.chat_id}",
                session_id=self._conversation_key(ev),
                prompt=ev.text,
                data={
                    "platform": ev.platform,
                    "chat_id": ev.chat_id,
                    "user_id": ev.user_id or "",
                    "user_name": ev.user_name or "",
                    "thread_id": ev.thread_id or "",
                    "message_id": ev.message_id or "",
                    "reply_to_message_id": ev.reply_to_message_id or "",
                    "has_reply_context": bool(ev.reply_to_text),
                    "session_key": ev.session_key or "",
                    "internal": bool(ev.internal),
                    "attachment_count": len(ev.attachments or []),
                    "queue_wait_ms": int((time.monotonic() - queued_at) * 1000),
                    "inline_reply": bool(getattr(ev, "_reply_inline", False)),
                },
            )
            return str(run.get("id") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _record_delivery_finish(
        self,
        run_id: str,
        *,
        status: str,
        reply: str,
        error: str,
        dispatch_ms: int,
        inline: bool,
    ) -> None:
        if not run_id:
            return
        try:
            from ..runs import RunStore

            RunStore().finish(
                run_id,
                status=status,
                result=reply,
                error=error,
                data={
                    "dispatch_ms": dispatch_ms,
                    "reply_chars": len(reply or ""),
                    "inline_reply": inline,
                    "delivery_status": status,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        """Send a file as a native attachment. Default: mention it as text (adapters that
        support native uploads — Telegram, Discord — override this)."""
        import os
        if os.path.exists(path):
            self.send(chat_id, (caption + "\n" if caption else "") + f"📎 file ready: {path}")
        else:
            self.send(chat_id, f"(file not found: {path})")

    def send_image(self, chat_id: str, path: str, caption: str = "") -> None:
        self.send_media(chat_id, path, caption)

    def send_video(self, chat_id: str, path: str, caption: str = "") -> None:
        self.send_media(chat_id, path, caption)

    def send_voice(self, chat_id: str, path: str, caption: str = "") -> None:
        self.send_media(chat_id, path, caption)

    def send_document(self, chat_id: str, path: str, caption: str = "") -> None:
        self.send_media(chat_id, path, caption)

    def send_clarify(self, chat_id: str, question: str, choices: list[str] | None = None) -> None:
        rendered = question.strip()
        for i, choice in enumerate(choices or [], 1):
            rendered += f"\n  {i}. {choice}"
        self.send(chat_id, rendered)

    def send_exec_approval(self, chat_id: str, prompt: str) -> None:
        self.send(chat_id, prompt)

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        return None

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:  # noqa: ARG002
        return None

    def filter_media_path(self, path: str) -> tuple[bool, str]:
        import os
        if not path:
            return False, "empty media path"
        if not os.path.exists(path):
            return False, "file not found"
        try:
            from ..tools.file_safety import read_denial
            reason = read_denial(path)
            if reason:
                return False, reason
        except Exception:  # noqa: BLE001
            pass
        return True, ""

    def ask_user(
        self,
        ev: MessageEvent,
        question: str,
        choices: list[str] | None = None,
        *,
        timeout: float = 3600,
    ) -> str:
        import threading

        self._ensure_inbound_queue()
        key = self._conversation_key(ev)
        done = threading.Event()
        waiter = {"event": done, "answer": ""}
        with self._qlock:
            self._clarify_waiters.setdefault(key, []).append(waiter)
        try:
            self.send_clarify(ev.chat_id, question, choices or [])
            done.wait(max(0.1, float(timeout or 0)))
            return str(waiter.get("answer") or "")
        finally:
            with self._qlock:
                waiters = self._clarify_waiters.get(key, [])
                if waiter in waiters:
                    waiters.remove(waiter)
                if not waiters:
                    self._clarify_waiters.pop(key, None)

    def ask_exec_approval(
        self,
        ev: MessageEvent,
        prompt: str,
        *,
        timeout: float = 3600,
    ) -> str:
        import threading

        self._ensure_inbound_queue()
        key = self._conversation_key(ev)
        done = threading.Event()
        waiter = {"event": done, "answer": ""}
        with self._qlock:
            self._clarify_waiters.setdefault(key, []).append(waiter)
        try:
            rendered = (prompt or "").strip()
            if rendered:
                rendered += "\n"
            rendered += "Reply approve, always, or deny."
            self.send_exec_approval(ev.chat_id, rendered)
            done.wait(max(0.1, float(timeout or 0)))
            return str(waiter.get("answer") or "")
        finally:
            with self._qlock:
                waiters = self._clarify_waiters.get(key, [])
                if waiter in waiters:
                    waiters.remove(waiter)
                if not waiters:
                    self._clarify_waiters.pop(key, None)

    def _resolve_clarify_waiter(self, ev: MessageEvent) -> bool:
        self._ensure_inbound_queue()
        if getattr(ev, "internal", False):
            return False
        key = self._conversation_key(ev)
        with self._qlock:
            waiters = self._clarify_waiters.get(key) or []
            waiter = waiters.pop(0) if waiters else None
            if not waiters:
                self._clarify_waiters.pop(key, None)
        if waiter is None:
            return False
        waiter["answer"] = ev.text or ""
        waiter["event"].set()
        return True

    def deliver(self, chat_id: str, text: str) -> None:
        """Send a reply, extracting any ``MEDIA:/abs/path`` lines and sending each as a native
        attachment. Adapters should call this (not ``send``) to deliver agent replies."""
        clean, media = split_media(text)
        if clean and not self.renders_tables:
            clean = tableify(clean)             # pipe tables don't render on chat surfaces
        if clean:
            self.send(chat_id, clean)
        for path in media:
            try:
                allowed, reason = self.filter_media_path(path)
                if not allowed:
                    self.send(chat_id, f"📎 blocked media path: {reason}")
                    continue
                self.send_media(chat_id, path)
            except Exception:  # noqa: BLE001
                self.send(chat_id, f"📎 {path}")


_CONTROL_RE = re.compile(r"^\s*/?(stop|cancel|abort|halt)\s*!?\s*$", re.IGNORECASE)
_RESET_RE = re.compile(r"^\s*/?(new|reset)\s*!?\s*$", re.IGNORECASE)


def is_control_interrupt(text: str) -> bool:
    """True for a bare 'stop'/'cancel'/'abort'/'halt' (optionally '/stop') — used to cancel a
    run in progress rather than start a new turn."""
    return bool(_CONTROL_RE.match(text or ""))


def is_control_reset(text: str) -> bool:
    """True for a bare '/new' or '/reset' command while a run is active."""
    return bool(_RESET_RE.match(text or ""))


_MEDIA_RE = re.compile(r"^[ \t]*MEDIA:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def tableify(text: str) -> str:
    """Rewrite markdown pipe-tables into bullet groups for surfaces that can't render them
    (Telegram, WhatsApp, Signal, Slack, Discord). Each data row becomes a '• col: val — …' line."""
    if "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (_TABLE_ROW.match(lines[i]) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1])
                and "-" in lines[i + 1]):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                pairs = [f"{h}: {c}" for h, c in zip(header, cells, strict=False) if c]
                out.append("• " + " — ".join(pairs))
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def split_media(text: str) -> tuple[str, list[str]]:
    """Split a reply into (clean_text, [file_paths]) by extracting ``MEDIA:/path`` lines."""
    paths = [m.strip() for m in _MEDIA_RE.findall(text or "")]
    clean = _MEDIA_RE.sub("", text or "").strip()
    return clean, paths
