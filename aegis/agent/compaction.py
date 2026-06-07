"""Context compaction via LLM summarization (preserve first N + last M turns)."""

from __future__ import annotations

from ..constants import COMPACT_THRESHOLD
from ..types import Message
from ..util import estimate_tokens


def estimated_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens(m.content or "") + estimate_tokens(m.reasoning or "")
               + sum(estimate_tokens(str(tc.arguments)) for tc in m.tool_calls)
               for m in messages)


def should_compress(messages: list[Message], context_length: int, overhead_tokens: int = 0) -> bool:
    return estimated_tokens(messages) + overhead_tokens > context_length * COMPACT_THRESHOLD


def _split_at_user_boundary(convo: list[Message], preserve_last: int) -> int:
    """Return an index for the tail start that lands on a user message."""
    start = max(0, len(convo) - preserve_last)
    while start < len(convo) and convo[start].role != "user":
        start += 1
    return min(start, len(convo))


def compress(messages: list[Message], provider, *, preserve_first: int = 3,
             preserve_last: int = 20) -> list[Message]:
    system_msgs = [m for m in messages if m.role == "system"]
    convo = [m for m in messages if m.role != "system"]
    tail_start = _split_at_user_boundary(convo, preserve_last)
    if tail_start <= preserve_first:
        return messages  # nothing meaningful to compress

    head = convo[:preserve_first]
    middle = convo[preserve_first:tail_start]
    tail = convo[tail_start:]
    if not middle:
        return messages

    transcript = "\n".join(
        f"{m.role}: {m.content}" + (f" [tools: {[tc.name for tc in m.tool_calls]}]" if m.tool_calls else "")
        for m in middle
    )
    try:
        resp = provider.complete(
            [
                Message.system(
                    "Summarize the following conversation excerpt. Preserve decisions, file "
                    "paths, commands, results, and open threads. Be terse and factual."
                ),
                Message.user(transcript[:60_000]),
            ],
            tools=None,
            stream=False,
        )
        summary = resp.text.strip() or "(summary unavailable)"
    except Exception as e:  # noqa: BLE001
        summary = f"(compaction failed: {e}; older turns dropped)"

    note = Message.assistant("[Earlier conversation summarized]\n" + summary)
    return system_msgs + head + [note] + tail
