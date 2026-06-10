"""Context compaction via LLM summarization (preserve first N + last M turns).

Beyond summarizing the dropped middle, the preserved head/tail are pruned: oversized
tool outputs are boundary-truncated and inline base64 images are stripped, so a few
huge tool dumps in recent turns can't blow the window on their own."""

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


def _split_at_safe_boundary(convo: list[Message], preserve_last: int) -> int:
    """Tail-start index at a safe boundary: a user message when one exists in the
    window, else an assistant message (keeps its tool results with it). Long
    agentic turns often have a single user message at the top — without the
    assistant fallback the tail came back EMPTY and the model's most recent
    working state was summarized away mid-task."""
    start = max(0, len(convo) - preserve_last)
    for i in range(start, len(convo)):
        if convo[i].role == "user":
            return i
    for i in range(start, len(convo)):
        if convo[i].role == "assistant":
            return i
    return min(start, len(convo))


def compress(messages: list[Message], provider, *, preserve_first: int = 3,
             preserve_last: int = 20, max_tool_tokens: int = 600) -> list[Message]:
    system_msgs = [m for m in messages if m.role == "system"]
    convo = [m for m in messages if m.role != "system"]
    tail_start = _split_at_safe_boundary(convo, preserve_last)
    # Extend the head cut past any tool results so a tool group is never split —
    # an assistant with tool_calls whose results were summarized away is a wire error.
    cut = preserve_first
    while cut < tail_start and convo[cut].role == "tool":
        cut += 1
    if tail_start <= cut:
        # Nothing meaningful to summarize, but still prune oversized tool dumps.
        return system_msgs + _prune_messages(convo, max_tool_tokens)

    # Prune oversized tool outputs / images in the kept turns before summarizing the rest.
    head = _prune_messages(convo[:cut], max_tool_tokens)
    middle = convo[cut:tail_start]
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
                    "You are compressing an agent conversation so work can continue seamlessly "
                    "with this summary in place of the original messages. Write a structured "
                    "handoff with exactly these sections (skip a section only if truly empty):\n"
                    "1. Primary request — what the user asked for, including later refinements.\n"
                    "2. Key decisions & constraints — choices made and rules to respect.\n"
                    "3. Files & code — every file touched, with the specific functions/lines "
                    "that matter and any snippets still needed.\n"
                    "4. Errors & fixes — what failed, how it was fixed, what to avoid repeating.\n"
                    "5. Completed work — what is already DONE (so it isn't redone).\n"
                    "6. Pending & next step — what remains, and the exact next action.\n"
                    "Be factual and specific (paths, commands, names). No filler."
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
