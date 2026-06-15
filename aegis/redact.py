"""Redact common secret shapes from text before it is stored or sent to a user/channel.

Single source of truth used by the learning store (don't persist secrets in skills/memory)
and the gateway (don't echo a key the agent happened to read back into a chat platform).
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}"                                            # OpenAI / Anthropic
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|ghu_[A-Za-z0-9]{20,}"
    r"|ghs_[A-Za-z0-9]{20,}|ghr_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub tokens
    r"|AKIA[0-9A-Z]{16}"                                                  # AWS access key id
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"                                      # Slack
    r"|AIza[0-9A-Za-z_\-]{20,}"                                           # Google API key
    r"|pplx-[A-Za-z0-9]{10,}|fal_[A-Za-z0-9_-]{10,}|fc-[A-Za-z0-9]{10,}"
    r"|bb_live_[A-Za-z0-9_-]{10,}|sk_live_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}"
    r"|SG\.[A-Za-z0-9_-]{10,}|hf_[A-Za-z0-9]{10,}|r8_[A-Za-z0-9]{10,}"
    r"|npm_[A-Za-z0-9]{10,}|pypi-[A-Za-z0-9_-]{10,}|tvly-[A-Za-z0-9]{10,}"
    r"|exa_[A-Za-z0-9]{10,}|gsk_[A-Za-z0-9]{10,}|xai-[A-Za-z0-9]{30,}"
    r"|mem0_[A-Za-z0-9]{10,}|brv_[A-Za-z0-9]{10,}|hsk-[A-Za-z0-9]{10,}"
    r"|\d{8,10}:[A-Za-z0-9_\-]{35}"                                       # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"  # JWT
)
_ENV_ASSIGN_RE = re.compile(
    r"\b([A-Z0-9_]{0,50}(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
    r"[A-Z0-9_]{0,50})\s*=\s*(['\"]?)(\S+)\2",
)
_JSON_FIELD_RE = re.compile(
    r'("(?:api_?key|token|secret|password|access_token|refresh_token|auth_token|bearer|'
    r'private_key|secret_value|raw_secret|secret_input|key_material)")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*(?:Bearer|Basic|Bot)\s+)(\S+)", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)
_URL_USERINFO_RE = re.compile(r"((?:https?|wss?|ftp)://[^/\s:@]+:)([^/\s@]+)(@)")
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|client_secret|"
    r"password|auth|jwt|session|secret|key|code|signature|x-amz-signature)=)([^&#\s]+)",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Replace recognized secret tokens with ``[REDACTED]``."""
    value = text or ""
    value = _PRIVATE_KEY_RE.sub("[REDACTED]", value)
    value = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", value)
    value = _ENV_ASSIGN_RE.sub(r"\1=\2[REDACTED]\2", value)
    value = _JSON_FIELD_RE.sub(r'\1: "[REDACTED]"', value)
    value = _DB_CONNSTR_RE.sub(r"\1[REDACTED]\3", value)
    value = _URL_USERINFO_RE.sub(r"\1[REDACTED]\3", value)
    value = _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", value)
    return _SECRET_RE.sub("[REDACTED]", value)


_SECRET_KEY_RE = re.compile(r"(^api[_-]?key$|token|secret|password|credential|auth|bearer|value)", re.IGNORECASE)


def redact_secret_values(value: Any) -> Any:
    """Return a copy of value with secret-shaped strings and secret-named fields masked."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() != "key" and _SECRET_KEY_RE.search(key_text):
                out[key] = "[REDACTED]" if item not in (None, "") else item
            else:
                out[key] = redact_secret_values(item)
        return out
    if isinstance(value, list):
        return [redact_secret_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secret_values(item) for item in value)
    return value
