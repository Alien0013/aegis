"""Redact common secret shapes from text before it is stored or sent to a user/channel.

Single source of truth used by the learning store (don't persist secrets in skills/memory)
and the gateway (don't echo a key the agent happened to read back into a chat platform).
"""

from __future__ import annotations

import re

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}"                                            # OpenAI / Anthropic
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub tokens
    r"|AKIA[0-9A-Z]{16}"                                                  # AWS access key id
    r"|xox[bap]-[A-Za-z0-9-]{10,}"                                        # Slack
    r"|AIza[0-9A-Za-z_\-]{20,}"                                           # Google API key
    r"|\d{8,10}:[A-Za-z0-9_\-]{35}"                                       # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"  # JWT
)


def redact_secrets(text: str) -> str:
    """Replace recognized secret tokens with ``[REDACTED]``."""
    return _SECRET_RE.sub("[REDACTED]", text or "")
