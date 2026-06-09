"""Channel adapter interface and the normalized inbound message event."""

from __future__ import annotations

import re
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
    attachments: list[dict] = field(default_factory=list)


class BasePlatformAdapter:
    """Subclasses implement a blocking ``start`` loop and ``send``."""

    name: str = "base"

    def start(self, dispatch: Dispatch) -> None:  # pragma: no cover - interface
        """Block, receiving messages and calling ``dispatch(event)``; send replies."""
        raise NotImplementedError

    def send(self, chat_id: str, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def send_media(self, chat_id: str, path: str, caption: str = "") -> None:
        """Send a file as a native attachment. Default: mention it as text (adapters that
        support native uploads — Telegram, Discord — override this)."""
        import os
        if os.path.exists(path):
            self.send(chat_id, (caption + "\n" if caption else "") + f"📎 file ready: {path}")
        else:
            self.send(chat_id, f"(file not found: {path})")

    def deliver(self, chat_id: str, text: str) -> None:
        """Send a reply, extracting any ``MEDIA:/abs/path`` lines and sending each as a native
        attachment. Adapters should call this (not ``send``) to deliver agent replies."""
        clean, media = split_media(text)
        if clean:
            self.send(chat_id, clean)
        for path in media:
            try:
                self.send_media(chat_id, path)
            except Exception:  # noqa: BLE001
                self.send(chat_id, f"📎 {path}")


_MEDIA_RE = re.compile(r"^[ \t]*MEDIA:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


def split_media(text: str) -> tuple[str, list[str]]:
    """Split a reply into (clean_text, [file_paths]) by extracting ``MEDIA:/path`` lines."""
    paths = [m.strip() for m in _MEDIA_RE.findall(text or "")]
    clean = _MEDIA_RE.sub("", text or "").strip()
    return clean, paths
