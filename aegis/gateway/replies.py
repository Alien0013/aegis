"""Final-reply shaping for chat surfaces.

Chat platforms are someone's phone inbox — they should get a short, safe message, not a raw
HTTP body or an empty bubble. Two transforms, applied to the agent's reply before delivery:
  * provider-error envelopes  -> a friendly one-liner by category (auth / rate-limit / generic)
  * empty/None responses      -> a clear "nothing came back, try again" message
"""

from __future__ import annotations

import re

_AUTH_RE = re.compile(r"\b(unauthorized|invalid api key|incorrect api key|401|authentication failed)\b",
                      re.IGNORECASE)
_RATE_RE = re.compile(r"\b(rate[\s-]?limit|429|too many requests)\b", re.IGNORECASE)
# A provider-error *envelope* starts with one of these markers (optionally behind punctuation).
_SHAPE_RE = re.compile(
    r"^\s*\W*\s*("
    r"\[provider error\]|api (?:call )?failed|provider authentication failed|non-retryable"
    r"|rate limited after \d+|error code\s*:|http\s*\d{3}\b|incorrect api key|invalid api key"
    r")",
    re.IGNORECASE,
)


def looks_like_provider_error(text: str) -> bool:
    """True only for short error *envelopes* — not prose that mentions an HTTP code."""
    body = (text or "").strip()
    if not body or len(body) > 400 or body.count("\n") > 4:
        return False
    return bool(_SHAPE_RE.search(body))


def friendly_error(text: str) -> str:
    if _AUTH_RE.search(text):
        return ("⚠️ Provider authentication failed — check the configured API key/credentials "
                "(raw details are in the gateway logs).")
    if _RATE_RE.search(text):
        return "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again."
    return ("⚠️ The model provider failed after retries. The raw error is in the gateway logs; "
            "try again shortly.")


def shape_reply(text: str, *, api_calls: int = 0) -> str:
    """Apply both transforms. Normal assistant prose passes through unchanged."""
    if text and text.strip():
        return friendly_error(text) if looks_like_provider_error(text) else text
    if api_calls > 0:
        return ("⚠️ I finished processing but didn't produce a reply — this is usually transient, "
                "please send your message again.")
    return "(no response)"
