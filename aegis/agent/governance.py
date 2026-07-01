"""Message-list hygiene run before every model call.

Keeps the wire valid across compaction/interrupts:
  * strip interrupted assistant->tool blocks before replay
  * drop orphan tool results (no preceding assistant tool_call)
  * backfill missing tool results for assistant tool_calls (synthetic error)
  * close interrupted tool-result tails before a follow-on user turn
"""

from __future__ import annotations

import re
from typing import Any

from ..types import Message

# Reasoning models (deepseek-r1, qwen, some OpenRouter routes) sometimes inline their
# chain-of-thought as <think>…</think> in the reply content instead of a separate field.
# Strip closed reasoning blocks so they never reach the user.
_THINK_RE = re.compile(
    r"<(think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_VALID_ROLES = {"system", "user", "assistant", "tool"}
_UNKNOWN_TOOL_NAME = "unknown_tool"
_INTERRUPTED_TOOL_RESULT = "[no result: interrupted]"
_INTERRUPTED_ASSISTANT_CLOSE = "Operation interrupted."


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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_tool_name(value: Any) -> str:
    return _clean_text(value) or _UNKNOWN_TOOL_NAME


def _normalize_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def _fresh_tool_call_id(used_ids: set[str], message_index: int, call_index: int) -> str:
    base = f"call_{message_index}_{call_index}"
    candidate = base
    suffix = 1
    while not candidate or candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _pop_pending_call(
    pending: dict[str, list[tuple[str, str]]],
    original_id: str,
    tool_name: Any,
) -> tuple[str, str] | None:
    calls = pending.get(original_id)
    if not calls:
        return None

    name = _clean_text(tool_name)
    if name:
        for idx, (_new_id, pending_name) in enumerate(calls):
            if pending_name == name:
                match = calls.pop(idx)
                if not calls:
                    pending.pop(original_id, None)
                return match

    match = calls.pop(0)
    if not calls:
        pending.pop(original_id, None)
    return match


def _drop_invalid_roles(messages: list[Message]) -> list[Message]:
    return [m for m in messages if m.role in _VALID_ROLES]


def _normalize_tool_calls(messages: list[Message]) -> None:
    """Repair persisted assistant tool-call envelopes and matching result ids."""
    used_ids: set[str] = set()
    pending: dict[str, list[tuple[str, str]]] = {}

    for message_index, m in enumerate(messages):
        if m.role == "assistant":
            pending.clear()
            for call_index, tc in enumerate(m.tool_calls):
                original_id = _clean_text(tc.id)
                name = _normalize_tool_name(tc.name)
                next_id = original_id
                if not next_id or next_id in used_ids:
                    next_id = _fresh_tool_call_id(used_ids, message_index, call_index)
                tc.id = next_id
                tc.name = name
                tc.arguments = _normalize_tool_arguments(tc.arguments)
                used_ids.add(next_id)
                pending.setdefault(original_id, []).append((next_id, name))
        elif m.role == "tool":
            original_id = _clean_text(m.tool_call_id)
            match = _pop_pending_call(pending, original_id, m.name)
            if match is not None:
                next_id, call_name = match
                m.tool_call_id = next_id
                m.name = _clean_text(m.name) or call_name
            else:
                m.tool_call_id = original_id or m.tool_call_id
                m.name = _normalize_tool_name(m.name)
        else:
            pending.clear()


def _merge_adjacent_user_messages(messages: list[Message]) -> list[Message]:
    merged: list[Message] = []
    for m in messages:
        if merged and m.role == "user" and merged[-1].role == "user":
            prev = merged[-1]
            prev_content = prev.content if isinstance(prev.content, str) else str(prev.content)
            next_content = m.content if isinstance(m.content, str) else str(m.content)
            if prev_content and next_content:
                prev.content = f"{prev_content}\n\n{next_content}"
            else:
                prev.content = prev_content or next_content
            prev.images.extend(m.images)
            continue
        merged.append(m)
    return merged


def _close_tool_result_before_user(messages: list[Message]) -> list[Message]:
    """Close interrupted tool-result tails before the next real user message.

    A live tool loop legitimately ends on a tool result while the next provider
    call asks the model to process it. Once a user message follows that tool
    result, the previous turn was interrupted or repaired; strict providers do
    not accept the raw ``tool -> user`` transition.
    """
    if not messages:
        return messages

    closed: list[Message] = []
    for idx, m in enumerate(messages):
        closed.append(m)
        next_message = messages[idx + 1] if idx + 1 < len(messages) else None
        if m.role == "tool" and next_message is not None and next_message.role == "user":
            closed.append(Message.assistant(_INTERRUPTED_ASSISTANT_CLOSE))
    return closed


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
    messages = _drop_invalid_roles(messages)
    _normalize_tool_calls(messages)
    messages = _strip_interrupted_tool_blocks(messages)
    # Pass 1: keep only the active assistant->tool result group. A user/system/
    # assistant boundary closes the group; later tool messages with an old id are
    # orphans and must not ride to providers.
    pending_results: dict[str, str] = {}
    pass1: list[Message] = []

    def flush_missing_results() -> None:
        nonlocal pending_results
        for call_id, name in list(pending_results.items()):
            pass1.append(Message.tool(call_id, name, _INTERRUPTED_TOOL_RESULT))
        pending_results = {}

    for m in messages:
        if m.role == "assistant":
            if pending_results:
                flush_missing_results()
            pass1.append(m)
            pending_results = {tc.id: tc.name for tc in m.tool_calls if tc.id}
        elif m.role == "tool":
            if m.tool_call_id in pending_results:
                pass1.append(m)
                pending_results.pop(m.tool_call_id, None)
            # else: orphan -> drop
        else:
            if pending_results:
                flush_missing_results()
            pass1.append(m)
    if pending_results:
        flush_missing_results()

    # Pass 2: backfill missing results so every tool_call is answered.
    result_ids = {m.tool_call_id for m in pass1 if m.role == "tool"}
    out: list[Message] = []
    for m in pass1:
        out.append(m)
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                if tc.id not in result_ids:
                    out.append(Message.tool(tc.id, tc.name, _INTERRUPTED_TOOL_RESULT))
                    result_ids.add(tc.id)
    return _merge_adjacent_user_messages(_close_tool_result_before_user(out))
