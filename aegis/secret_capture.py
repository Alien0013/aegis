"""Safe local secret capture helpers.

The model should request a secret by name only. The UI/terminal captures the
value out-of-band, stores it in ~/.aegis/.env, and returns only status metadata.
"""

from __future__ import annotations

import getpass
import re
import sys
from typing import Any

from . import config as cfg
from .config import set_env_var
from .redact import redact_secrets

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def validate_secret_key(key: str) -> str:
    key = str(key or "").strip()
    if not _ENV_KEY_RE.match(key):
        raise ValueError("secret key must be an uppercase env var name like TELEGRAM_BOT_TOKEN")
    return key


def store_secret_value(key: str, value: str) -> dict[str, Any]:
    key = validate_secret_key(key)
    value = str(value or "")
    if not value:
        return {
            "success": True,
            "skipped": True,
            "stored_as": key,
            "env_path": str(cfg.env_path()),
            "message": "Secret setup was skipped.",
        }
    set_env_var(key, value)
    return {
        "success": True,
        "skipped": False,
        "stored_as": key,
        "env_path": str(cfg.env_path()),
        "message": "Secret stored securely. The secret value was not exposed to the model.",
    }


def capture_secret_interactive(
    key: str,
    prompt: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prompt locally with hidden input, then store the value in ~/.aegis/.env."""
    key = validate_secret_key(key)
    label = redact_secrets((prompt or f"Enter {key}").strip())
    try:
        value = getpass.getpass(f"{label} (hidden, empty Enter to skip): ")
    except (EOFError, KeyboardInterrupt):
        value = ""
        print("", file=sys.stderr)
    result = store_secret_value(key, value)
    if result.get("skipped"):
        print(f"  secret entry skipped for {key}", file=sys.stderr)
    else:
        print(f"  stored secret in {cfg.env_path()} as {key}", file=sys.stderr)
    return result
