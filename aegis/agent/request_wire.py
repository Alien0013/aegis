"""Provider request-copy hygiene."""

from __future__ import annotations

import dataclasses
from typing import Any

from ..types import Message

_THINKING_CONTENT_TYPES = {"thinking", "redacted_thinking"}


def _has_visible_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    return True
                continue
            if not isinstance(part, dict):
                if part:
                    return True
                continue
            kind = str(part.get("type") or "")
            if kind in _THINKING_CONTENT_TYPES:
                continue
            if kind == "text":
                text = part.get("text", "")
                if isinstance(text, str):
                    if text.strip():
                        return True
                elif text:
                    return True
                continue
            if kind:
                return True
            if any(part.values()):
                return True
        return False
    return content not in (None, "")


def _has_thinking_payload(message: Message) -> bool:
    reasoning = getattr(message, "reasoning", "")
    if isinstance(reasoning, str):
        if reasoning.strip():
            return True
    elif reasoning:
        return True
    return bool(getattr(message, "thinking_blocks", None))


def is_thinking_only_assistant(message: Message) -> bool:
    """Return True for assistant turns that contain reasoning but no visible output."""
    if getattr(message, "role", "") != "assistant":
        return False
    if getattr(message, "tool_calls", None):
        return False
    if getattr(message, "images", None):
        return False
    if _has_visible_content(getattr(message, "content", "")):
        return False
    return _has_thinking_payload(message)


def _merge_user_content(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        separator = "\n\n" if left and right else ""
        return f"{left}{separator}{right}"
    if left in (None, ""):
        return right
    if right in (None, ""):
        return left
    return f"{left}\n\n{right}"


def _merge_user_messages(left: Message, right: Message) -> Message:
    return dataclasses.replace(
        left,
        content=_merge_user_content(left.content, right.content),
        images=[*list(left.images or []), *list(right.images or [])],
    )


def govern_provider_wire_messages(messages: list[Message]) -> list[Message]:
    """Drop thinking-only assistant turns and merge adjacent users on the wire copy."""
    if not messages:
        return messages

    kept: list[Message] = []
    dropped = False
    for message in messages:
        if is_thinking_only_assistant(message):
            dropped = True
            continue
        kept.append(message)

    if not dropped:
        return messages

    merged: list[Message] = []
    for message in kept:
        if merged and merged[-1].role == "user" and message.role == "user":
            merged[-1] = _merge_user_messages(merged[-1], message)
            continue
        merged.append(message)
    return merged
