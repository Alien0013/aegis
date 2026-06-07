"""Message-list hygiene run before every model call.

Keeps the wire valid across compaction/interrupts:
  * drop orphan tool results (no preceding assistant tool_call)
  * backfill missing tool results for assistant tool_calls (synthetic error)
"""

from __future__ import annotations

from ..types import Message


def _strip_surrogates(s: str) -> str:
    """Remove lone UTF-16 surrogates that crash JSON/UTF-8 serialization."""
    if not s:
        return s
    return "".join(c for c in s if not 0xD800 <= ord(c) <= 0xDFFF)


def normalize(messages: list[Message]) -> list[Message]:
    # Defensive: scrub lone surrogates a model may have emitted (would crash the
    # next request's JSON encode). Mutate in place.
    for m in messages:
        if m.content and any(0xD800 <= ord(c) <= 0xDFFF for c in m.content):
            m.content = _strip_surrogates(m.content)
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
