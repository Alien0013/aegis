"""Context compaction via LLM summarization (preserve head + a token-budgeted tail).

The middle is summarized into a structured handoff; the preserved head/tail are pruned
(oversized tool outputs boundary-truncated, inline base64 images stripped). Four guards
keep long, repeatedly-compacted sessions coherent:
  * the tail is protected by a TOKEN budget, not a fixed message count, so recent work
    survives whether the messages are tiny or huge;
  * a prior summary is FOLDED into the next one (the summarizer updates it) instead of
    being re-summarized as ordinary middle — no summary-of-summary drift;
  * the summarizer input is capped to what the (possibly small) auxiliary model can fit;
  * on summarizer failure the dropped window still yields a deterministic anchor digest
    (files, commands, last request) rather than being lost to "(compaction failed)".
"""

from __future__ import annotations

import dataclasses
import re

from ..constants import COMPACT_THRESHOLD, DEFAULT_CONTEXT_LENGTH
from ..types import Message
from ..util import estimate_tokens

_IMG_DATA_URI = re.compile(r"data:image/[a-zA-Z.+-]+;base64,[A-Za-z0-9+/=\s]{200,}")
_SUMMARY_MARKER = "[Earlier conversation summarized]"
_SUMMARY_REFERENCE_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into "
    "the summary below. Treat it as background, not as active instructions. "
    "Respond to the latest user message after this summary; that latest message wins."
)
_SUMMARY_END_MARKER = (
    "--- END OF CONTEXT SUMMARY - respond to the message below, not the summary above ---"
)
# Tokens of recent conversation to protect by default when a caller doesn't pass a budget.
_DEFAULT_TAIL_TOKENS = 6000


class CompressionAborted(RuntimeError):
    """Raised when configured compression policy refuses deterministic fallback."""


def _strip_images(text: str) -> str:
    return _IMG_DATA_URI.sub("[image omitted]", text)


def _strip_historical_media(messages: list[Message]) -> list[Message]:
    """Drop image payloads from older turns while preserving the newest image turn."""
    image_indexes = [i for i, m in enumerate(messages) if getattr(m, "images", None)]
    if not image_indexes:
        return messages
    newest_image = image_indexes[-1]
    out: list[Message] = []
    for i, m in enumerate(messages):
        content = _strip_images(m.content or "")
        images = list(getattr(m, "images", []) or [])
        if images and i < newest_image:
            suffix = "[historical image omitted after context compaction]"
            content = f"{content}\n{suffix}".strip() if content else suffix
            images = []
        if content != (m.content or "") or images != getattr(m, "images", []):
            m = dataclasses.replace(m, content=content, images=images)
        out.append(m)
    return out


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
               + sum(estimate_tokens(str(img)) for img in getattr(m, "images", []) or [])
               + sum(estimate_tokens(str(tc.arguments)) for tc in m.tool_calls)
               for m in messages)


def should_compress(messages: list[Message], context_length: int, overhead_tokens: int = 0,
                    threshold: float | None = None) -> bool:
    frac = COMPACT_THRESHOLD if threshold is None else threshold
    if context_length <= 0:
        context_length = DEFAULT_CONTEXT_LENGTH
    return estimated_tokens(messages) + overhead_tokens > context_length * frac


def _msg_tokens(m: Message) -> int:
    return (estimate_tokens(m.content or "") + estimate_tokens(m.reasoning or "")
            + sum(estimate_tokens(str(img)) for img in getattr(m, "images", []) or [])
            + sum(estimate_tokens(str(tc.arguments)) for tc in m.tool_calls))


def _snap_to_boundary(convo: list[Message], start: int) -> int:
    """Move ``start`` forward to a safe split: a user message if one exists at/after it,
    else an assistant message (keeps its tool results with it). Avoids returning an empty
    tail (which would summarize away the model's most recent working state) and never
    splits a tool group."""
    for i in range(start, len(convo)):
        if convo[i].role == "user":
            return i
    for i in range(start, len(convo)):
        if convo[i].role == "assistant":
            return i
    return min(start, len(convo))


def _tail_start(convo: list[Message], preserve_last: int, tail_tokens: int | None) -> int:
    """Index where the protected tail begins. Token-budget mode (tail_tokens given) walks
    back from the end accumulating tokens until the budget is met, so recent context is
    protected by SIZE not message count; otherwise falls back to the last ``preserve_last``
    messages. Either way the result is snapped to a safe boundary."""
    if tail_tokens is None:
        start = max(0, len(convo) - preserve_last)
    else:
        total, start = 0, 0
        for i in range(len(convo) - 1, -1, -1):
            total += _msg_tokens(convo[i])
            if total >= tail_tokens:
                start = i
                break
    return _snap_to_boundary(convo, start)


def _summary_input_budget(provider) -> int:
    """Max chars of transcript to hand the summarizer, sized to the aux model's window
    (reserve room for the instruction + the summary output). Prevents shipping more than
    a small auxiliary model can read."""
    ctx = getattr(provider, "context_length", 0) or 0
    if ctx <= 0:
        return 60_000                       # unknown window -> conservative fixed cap
    usable_tokens = max(2_000, ctx - 6_000)  # ~2k instruction + ~4k output headroom
    return usable_tokens * 4                 # ~4 chars/token


def _is_summary_note(m: Message) -> bool:
    return (
        m.role == "assistant"
        and (
            bool(getattr(m, "meta", {}).get("_compressed_summary"))
            or (bool(m.content) and m.content.startswith(_SUMMARY_MARKER))
        )
    )


def _summary_body(m: Message) -> str:
    text = m.content or ""
    return text[len(_SUMMARY_MARKER):].strip() if text.startswith(_SUMMARY_MARKER) else text.strip()


def _summary_note(body: str, *, fallback_used: bool = False) -> Message:
    return Message(
        role="assistant",
        content=_SUMMARY_MARKER + "\n" + body,
        meta={
            "_compressed_summary": True,
            "summary_end_marker": _SUMMARY_END_MARKER,
            "fallback_used": bool(fallback_used),
        },
    )


def _fallback_summary(middle: list[Message], prior: list[str],
                      pre_compress_context: str = "") -> str:
    """Deterministic digest when the summarizer call fails — keep continuity anchors
    (prior summary, file/path mentions, the most recent user request) instead of losing
    the whole dropped window."""
    files: list[str] = []
    for m in middle:
        for tok in re.findall(r"(?:/[\w.\-/]+|[\w.\-/]+\.[A-Za-z]{1,8})\b", m.content or ""):
            if len(tok) > 3 and tok not in files:
                files.append(tok)
    last_user = next((m.content for m in reversed(middle)
                      if m.role == "user" and m.content), "")
    parts = ["(automatic summary unavailable — deterministic anchors preserved)"]
    if prior:
        parts.append("Carried-forward prior summary:\n" + prior[-1])
    if pre_compress_context:
        parts.append("Memory provider pre-compression notes:\n" + pre_compress_context)
    if files:
        parts.append("Files / paths referenced in the dropped turns: " + ", ".join(files[:20]))
    if last_user:
        parts.append("Most recent user request in the dropped turns: " + last_user[:600])
    return "\n\n".join(parts)


_SUMMARY_INSTRUCTION = (
    "You are compressing an agent conversation so work can continue seamlessly with this "
    "summary in place of the original messages. This summary will be REFERENCE ONLY: "
    "old tasks and old asks must not become active instructions for the next model turn. "
    "Write a structured handoff with exactly these sections (skip a section only if truly empty):\n"
    "1. ## Historical Task Snapshot / Primary request — what the user asked for, including later refinements.\n"
    "2. ## Constraints & Preferences — durable constraints, user preferences, and safety rules.\n"
    "3. ## Completed Work — what is already DONE, with commands/results when useful.\n"
    "4. ## Key Decisions — choices made and why.\n"
    "5. ## Relevant Files — paths touched or inspected, with functions/lines that matter.\n"
    "6. ## Errors & Fixes — what failed, how it was fixed, and what to avoid repeating.\n"
    "7. ## Historical In-Progress State — any unfinished state from the compacted window, clearly marked historical.\n"
    "8. ## Historical Pending User Asks — user asks from the compacted window that were not resolved, clearly marked historical.\n"
    "9. ## Historical Remaining Work / Pending & next step — remaining work and the exact next action, clearly marked historical.\n"
    "10. ## Critical Context — facts that would break continuity if omitted.\n"
    "Be factual and specific (paths, commands, names). No filler. Do not invent work."
)


def compress(messages: list[Message], provider, *, preserve_first: int = 3,
             preserve_last: int = 20, max_tool_tokens: int = 600,
             focus: str = "", tail_tokens: int | None = None,
             pre_compress_context: str = "",
             abort_on_summary_failure: bool = False) -> list[Message]:
    system_msgs = [m for m in messages if m.role == "system"]
    convo = [m for m in messages if m.role != "system"]
    tail_start = _tail_start(convo, preserve_last, tail_tokens)
    # Extend the head cut past any tool results so a tool group is never split —
    # an assistant with tool_calls whose results were summarized away is a wire error.
    cut = preserve_first
    while cut < tail_start and convo[cut].role == "tool":
        cut += 1
    if tail_start <= cut:
        # Nothing meaningful to summarize, but still prune oversized tool dumps.
        return system_msgs + _strip_historical_media(_prune_messages(convo, max_tool_tokens))

    # Prune oversized tool outputs / images in the kept turns before summarizing the rest.
    head = _prune_messages(convo[:cut], max_tool_tokens)
    middle = convo[cut:tail_start]
    tail = _prune_messages(convo[tail_start:], max_tool_tokens)
    if not middle:
        return system_msgs + head + tail

    # Iterative fold: a summary note from a previous compaction in the middle is UPDATED,
    # not re-summarized (that would drift). Pull prior summaries out; the newest is handed
    # to the summarizer to carry forward. Their bodies are dropped from the new transcript.
    prior = [_summary_body(m) for m in middle if _is_summary_note(m)]
    new_middle = [m for m in middle if not _is_summary_note(m)]

    pre_compress_context = (pre_compress_context or "").strip()

    if not new_middle:
        # The middle was only prior summaries — keep the most recent, drop the rest. No call.
        if not prior:
            return system_msgs + _strip_historical_media(head + tail)
        body = prior[-1]
        if pre_compress_context:
            body = f"{body}\n\nMemory provider pre-compression notes:\n{pre_compress_context}"
        if _SUMMARY_REFERENCE_PREFIX not in body:
            body = f"{_SUMMARY_REFERENCE_PREFIX}\n\n{body}"
        if _SUMMARY_END_MARKER not in body:
            body = f"{body}\n\n{_SUMMARY_END_MARKER}"
        return system_msgs + _strip_historical_media(head + [_summary_note(body)] + tail)

    transcript = "\n".join(
        f"{m.role}: {m.content}" + (f" [tools: {[tc.name for tc in m.tool_calls]}]" if m.tool_calls else "")
        for m in new_middle
    )
    instruction = _SUMMARY_INSTRUCTION + (
        f"\nFOCUS: weight the summary toward anything related to: {focus}" if focus else "")
    prelude = ""
    if pre_compress_context:
        prelude = (
            "MEMORY PROVIDER PRE-COMPRESSION NOTES:\n"
            f"{pre_compress_context}\n\n"
        )
    if prior:
        instruction += ("\nA PRIOR SUMMARY is included first — carry EVERY fact in it "
                        "forward, then merge in the new material; output one consolidated "
                        "summary, not two.")
        user_content = f"{prelude}PRIOR SUMMARY:\n{prior[-1]}\n\nNEW MATERIAL:\n{transcript}"
    else:
        user_content = f"{prelude}{transcript}"
    user_content = user_content[:_summary_input_budget(provider)]   # fit the aux model

    fallback_used = False
    try:
        resp = provider.complete(
            [Message.system(instruction), Message.user(user_content)],
            tools=None, stream=False,
        )
        summary = resp.text.strip()
        if not summary:
            raise ValueError("empty summary")
    except Exception as exc:  # noqa: BLE001 — keep continuity anchors instead of losing the window
        if abort_on_summary_failure:
            raise CompressionAborted(f"summary generation failed: {exc}") from exc
        fallback_used = True
        summary = _fallback_summary(new_middle, prior, pre_compress_context)

    if _SUMMARY_REFERENCE_PREFIX not in summary:
        summary = f"{_SUMMARY_REFERENCE_PREFIX}\n\n{summary}"
    if _SUMMARY_END_MARKER not in summary:
        summary = f"{summary}\n\n{_SUMMARY_END_MARKER}"
    return system_msgs + _strip_historical_media(
        head + [_summary_note(summary, fallback_used=fallback_used)] + tail
    )
