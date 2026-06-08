"""Context compaction via LLM summarization (preserve first N + last M turns).

Beyond summarizing the dropped middle, the preserved head/tail are pruned: oversized
tool outputs are boundary-truncated and inline base64 images are stripped, so a few
huge tool dumps in recent turns can't blow the window on their own (à la Hermes
ContextCompressor)."""

from __future__ import annotations

import re

from ..constants import COMPACT_THRESHOLD
from ..types import Message
from ..util import estimate_tokens

_IMG_DATA_URI = re.compile(r"data:image/[a-zA-Z.+-]+;base64,[A-Za-z0-9+/=\s]{200,}")


def _strip_images(text: str) -> str:
    return _IMG_DATA_URI.sub("[image omitted]", text)


def _prune_tool_output(text: str, max_tokens: int) -> str:
    """Strip inline images then boundary-truncate to a token budget."""
    text = _strip_images(text)
    if estimate_tokens(text) <= max_tokens:
        return text
    limit = max_tokens * 4
    head = text[:limit]
    cut = max(head.rfind("\n"), head.rfind(". "))
    if cut > limit // 2:
        head = head[:cut + 1]
    return head.rstrip() + " …[truncated]"


def _prune_messages(messages: list[Message], max_tool_tokens: int) -> list[Message]:
    """Return copies of any tool messages whose output exceeds the budget, pruned."""
    out: list[Message] = []
    for m in messages:
        if m.role == "tool" and m.content and estimate_tokens(m.content) > max_tool_tokens:
            pruned = _prune_tool_output(m.content, max_tool_tokens)
            if pruned != m.content:
                m = Message.tool(m.tool_call_id, m.name, pruned)
        out.append(m)
    return out


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
             preserve_last: int = 20, max_tool_tokens: int = 600) -> list[Message]:
    system_msgs = [m for m in messages if m.role == "system"]
    convo = [m for m in messages if m.role != "system"]
    tail_start = _split_at_user_boundary(convo, preserve_last)
    if tail_start <= preserve_first:
        # Nothing meaningful to summarize, but still prune oversized tool dumps.
        return system_msgs + _prune_messages(convo, max_tool_tokens)

    # Prune oversized tool outputs / images in the kept turns before summarizing the rest.
    head = _prune_messages(convo[:preserve_first], max_tool_tokens)
    middle = convo[preserve_first:tail_start]
    tail = _prune_messages(convo[tail_start:], max_tool_tokens)
    if not middle:
        return system_msgs + head + tail

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
