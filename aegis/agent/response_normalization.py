"""Provider response cleanup at the persistence/display boundary."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Any

from ..redact import redact_secrets
from ..types import LLMResponse, ToolCall

__all__ = [
    "extract_inline_reasoning",
    "normalize_provider_response",
    "sanitize_response_text",
]

logger = logging.getLogger(__name__)

_REASONING_TAG_NAMES = (
    "think",
    "thinking",
    "reasoning",
    "thought",
    "REASONING_SCRATCHPAD",
)
_REASONING_TAG_ALT = "|".join(re.escape(name) for name in _REASONING_TAG_NAMES)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_INLINE_REASONING_RE = re.compile(
    rf"<(?P<tag>{_REASONING_TAG_ALT})\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)

_TOOL_BLOCK_TAGS = (
    "tool_call",
    "tool_calls",
    "tool_result",
    "function_call",
    "function_calls",
)


def _clean_surrogates(value: str) -> str:
    return _SURROGATE_RE.sub("\ufffd", value)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _clean_surrogates(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, dict):
        return {_sanitize_value(key): _sanitize_value(item) for key, item in value.items()}
    return value


def _coerce_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def _load_tool_arguments(raw: str, *, strict: bool = True) -> dict[str, Any] | None:
    try:
        return _coerce_tool_arguments(json.loads(raw, strict=strict))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Escape literal control characters inside JSON string values."""
    out: list[str] = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
        i += 1
    return "".join(out)


def _argument_preview(value: str) -> str:
    return redact_secrets(value[:80])


def _repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> dict[str, Any]:
    """Repair malformed raw tool-call JSON into an argument object.

    OpenAI-compatible providers sometimes emit truncated JSON, trailing commas,
    Python ``None``, or literal control characters. AEGIS' provider parser
    preserves those failures as ``{"__raw__": raw}``; normalize that sentinel
    before execution and persistence so the intended arguments are still usable.
    """
    raw_stripped = raw_args.strip() if isinstance(raw_args, str) else ""
    if not raw_stripped:
        logger.warning("Sanitized empty tool_call arguments for %s", tool_name)
        return {}

    if raw_stripped == "None":
        logger.warning("Sanitized Python-None tool_call arguments for %s", tool_name)
        return {}

    parsed = _load_tool_arguments(raw_stripped, strict=False)
    if parsed is not None:
        reserialized = json.dumps(parsed, separators=(",", ":"))
        if reserialized != raw_stripped:
            logger.warning(
                "Repaired unescaped control chars in tool_call arguments for %s",
                tool_name,
            )
        return parsed

    fixed = raw_stripped
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket

    for _ in range(50):
        if _load_tool_arguments(fixed) is not None:
            break
        if fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
            fixed = fixed[:-1]
        elif fixed.endswith("]") and fixed.count("]") > fixed.count("["):
            fixed = fixed[:-1]
        else:
            break

    parsed = _load_tool_arguments(fixed)
    if parsed is not None:
        logger.warning(
            "Repaired malformed tool_call arguments for %s: %s -> %s",
            tool_name,
            _argument_preview(raw_stripped),
            _argument_preview(fixed),
        )
        return parsed

    escaped = _escape_invalid_chars_in_json_strings(fixed)
    if escaped != fixed:
        parsed = _load_tool_arguments(escaped)
        if parsed is not None:
            logger.warning(
                "Repaired control-char-laced tool_call arguments for %s: %s -> %s",
                tool_name,
                _argument_preview(raw_stripped),
                _argument_preview(escaped),
            )
            return parsed

    logger.warning(
        "Unrepairable tool_call arguments for %s; replaced with empty object (was: %s)",
        tool_name,
        _argument_preview(raw_stripped),
    )
    return {}


def _normalize_tool_call_arguments(arguments: Any, tool_name: str) -> dict[str, Any]:
    arguments = _sanitize_value(arguments)
    if isinstance(arguments, dict):
        raw_args = arguments.get("__raw__")
        if len(arguments) == 1 and isinstance(raw_args, str):
            return _sanitize_value(_repair_tool_call_arguments(raw_args, tool_name))
        return arguments
    return _coerce_tool_arguments(arguments)


def _normalize_tool_calls(tool_calls: list[ToolCall]) -> list[ToolCall]:
    normalized: list[ToolCall] = []
    for call in tool_calls:
        tool_name = call.name if isinstance(call.name, str) else str(call.name or "?")
        normalized.append(
            dataclasses.replace(
                call,
                id=_sanitize_value(call.id),
                name=_sanitize_value(call.name),
                arguments=_normalize_tool_call_arguments(call.arguments, tool_name),
            )
        )
    return normalized


def extract_inline_reasoning(text: str) -> str:
    """Extract closed inline reasoning blocks from provider text."""
    if not text or "<" not in text:
        return ""

    blocks = [
        match.group("body").strip()
        for match in _INLINE_REASONING_RE.finditer(text)
        if match.group("body").strip()
    ]
    return _clean_surrogates("\n\n".join(blocks))


def _strip_tool_xml_blocks(text: str) -> str:
    for tag in _TOOL_BLOCK_TAGS:
        text = re.sub(
            rf"(?m)^[ \t]*<{tag}\b[^>]*>.*?</{tag}>[ \t]*(?:\r?\n)?",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

    return re.sub(
        r"(?:(?<=^)|(?<=[\n\r.!?:]))[ \t]*"
        r"<function\b[^>]*\bname\s*=[^>]*>"
        r"(?:(?:(?!</function>).)*)</function>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )


def sanitize_response_text(text: str, *, redact: bool = True) -> str:
    """Remove private/provider-only markup and unsafe code points from visible text."""
    if not text:
        return ""

    text = _clean_surrogates(text)
    if "<" in text:
        for name in _REASONING_TAG_NAMES:
            text = re.sub(
                rf"(?m)^[ \t]*<{re.escape(name)}\b[^>]*>.*?</{re.escape(name)}>[ \t]*(?:\r?\n)?",
                "",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            text = re.sub(
                rf"<{re.escape(name)}\b[^>]*>.*?</{re.escape(name)}>",
                "",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
        text = _strip_tool_xml_blocks(text)
        text = re.sub(
            rf"(?:^|\n)[ \t]*<(?:{_REASONING_TAG_ALT})\b[^>]*>.*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            rf"</?(?:{_REASONING_TAG_ALT})\b[^>]*>\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"</(?:tool_call|tool_calls|tool_result|function_call|function_calls|function)>\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

    text = text.strip()
    return redact_secrets(text) if redact and text else text


def normalize_provider_response(response: LLMResponse, *, redact: bool = True) -> LLMResponse:
    """Return a cleaned provider response without mutating the original object.

    Existing structured reasoning and Anthropic thinking blocks are preserved.
    Inline reasoning is copied into ``reasoning`` only when the provider did not
    return structured reasoning.
    """
    reasoning = response.reasoning or ""
    if not reasoning:
        reasoning = extract_inline_reasoning(response.text)
    reasoning = _clean_surrogates(reasoning)
    if redact and reasoning:
        reasoning = redact_secrets(reasoning)

    return dataclasses.replace(
        response,
        text=sanitize_response_text(response.text, redact=redact),
        reasoning=reasoning,
        tool_calls=_normalize_tool_calls(response.tool_calls),
        thinking_blocks=list(response.thinking_blocks),
        raw=_sanitize_value(response.raw),
    )
