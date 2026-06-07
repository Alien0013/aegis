"""Channel adapter interface and the normalized inbound message event."""

from __future__ import annotations

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
