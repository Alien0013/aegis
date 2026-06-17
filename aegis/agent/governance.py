"""Message-list hygiene run before every model call.

Keeps the wire valid across compaction/interrupts:
  * strip interrupted assistant->tool blocks before replay
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


def _is_interrupted_tool_result(content: str) -> bool:
    if not isinstance(content, str):
        return False
    lowered = content.lower()
    if "[command interrupted]" in lowered or "[interrupted by user]" in lowered:
        return True
    if "exit_code" in lowered and ("130" in lowered or "-1" in lowered):
        return "interrupt" in lowered
    return False


def _strip_interrupted_tool_blocks(messages: list[Message]) -> list[Message]:
    """Remove persisted interrupted tool-call blocks before provider replay."""
    if not messages:
        return messages
    out: list[Message] = []
    i = 0
    total = len(messages)
    while i < total:
        m = messages[i]
        if m.role == "assistant" and m.tool_calls:
            call_ids = {tc.id for tc in m.tool_calls}
            j = i + 1
            tool_results: list[Message] = []
            while (
                j < total
                and messages[j].role == "tool"
                and messages[j].tool_call_id in call_ids
            ):
                tool_results.append(messages[j])
                j += 1
            if tool_results and any(_is_interrupted_tool_result(t.content) for t in tool_results):
                i = j
                continue
        if m.role == "tool" and _is_interrupted_tool_result(m.content):
            i += 1
            continue
        out.append(m)
        i += 1
    return out


def normalize(messages: list[Message]) -> list[Message]:
    # Defensive: scrub lone surrogates a model may have emitted in any field that
    # can reach provider wire JSON. Mutate in place so ids stay paired.
    for m in messages:
        _sanitize_message(m)
    messages = _strip_interrupted_tool_blocks(messages)
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
