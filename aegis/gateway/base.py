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
    renders_tables: bool = True   # chat surfaces (Telegram/Discord/…) set False -> tables rewritten

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
        if clean and not self.renders_tables:
            clean = tableify(clean)             # pipe tables don't render on chat surfaces
        if clean:
            self.send(chat_id, clean)
        for path in media:
            try:
                self.send_media(chat_id, path)
            except Exception:  # noqa: BLE001
                self.send(chat_id, f"📎 {path}")


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
