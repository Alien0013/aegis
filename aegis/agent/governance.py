"""Message-list hygiene run before every model call.

Keeps the wire valid across compaction/interrupts:
  * drop orphan tool results (no preceding assistant tool_call)
  * backfill missing tool results for assistant tool_calls (synthetic error)
"""

from __future__ import annotations

import re

from ..types import Message

# Reasoning models (deepseek-r1, qwen, some OpenRouter routes) sometimes inline their
# chain-of-thought as <think>…</think> in the reply content instead of a separate field.
# Strip closed reasoning blocks so they never reach the user.
_THINK_RE = re.compile(
    r"<(think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> style reasoning blocks a model inlined into its reply."""
    if not text or "<" not in text:
        return text
    return _THINK_RE.sub("", text).strip()


def _strip_surrogates(s: str) -> str:
    """Replace lone UTF-16 surrogates that crash JSON/UTF-8 serialization."""
    if not s:
        return s
    return _SURROGATE_RE.sub("\ufffd", s)


def _sanitize_value(value):
    if isinstance(value, str):
        return _strip_surrogates(value)
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_value(v) for k, v in value.items()}
    return value


def _sanitize_message(m: Message) -> None:
    m.content = _sanitize_value(m.content)
    m.tool_call_id = _sanitize_value(m.tool_call_id)
    m.name = _sanitize_value(m.name)
    m.reasoning = _sanitize_value(m.reasoning)
    m.thinking_blocks = _sanitize_value(m.thinking_blocks)
    m.images = _sanitize_value(m.images)
    for tc in m.tool_calls:
        tc.id = _sanitize_value(tc.id)
        tc.name = _sanitize_value(tc.name)
        tc.arguments = _sanitize_value(tc.arguments)


def normalize(messages: list[Message]) -> list[Message]:
    # Defensive: scrub lone surrogates a model may have emitted in any field that
    # can reach provider wire JSON. Mutate in place so ids stay paired.
    for m in messages:
        _sanitize_message(m)
    # Pass 1: drop tool results whose call id was never requested before them.
    seen_call_ids: set[str] = set()
    pass1: list[Message] = []
    for m in messages:
        if m.role == "assistant":
            seen_call_ids.update(tc.id for tc in m.tool_calls)
            pass1.append(m)
        elif m.role == "tool":
            if m.tool_call_id in seen_call_ids:
                pass1.append(m)
            # else: orphan -> drop
        else:
            pass1.append(m)

    # Pass 2: backfill missing results so every tool_call is answered.
    result_ids = {m.tool_call_id for m in pass1 if m.role == "tool"}
    out: list[Message] = []
    for m in pass1:
        out.append(m)
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                if tc.id not in result_ids:
                    out.append(Message.tool(tc.id, tc.name, "[no result: interrupted]"))
                    result_ids.add(tc.id)
    return out
