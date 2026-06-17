"""Small cross-platform helpers used by gateway adapters.

These are intentionally narrow. The gateway adapters stay blocking and small,
while this module keeps Hermes-style normalization rules in one place.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Callable


MAX_TELEGRAM_COMMANDS = 30
MAX_DISCORD_APP_COMMANDS = 100

PLATFORM_ALIASES = {
    "tg": "telegram",
    "telegram-bot": "telegram",
    "telegram_bot": "telegram",
    "dc": "discord",
    "discordapp": "discord",
    "sl": "slack",
    "mm": "mattermost",
    "mattermost-webhook": "mattermost",
    "mattermost_webhook": "mattermost",
    "signal-cli": "signal",
    "matrix-nio": "matrix",
    "mail": "email",
    "e-mail": "email",
    "ntfy.sh": "ntfy",
    "hook": "webhook",
    "hooks": "webhook",
    "webhooks": "webhook",
    "wa": "whatsapp",
    "whatsapp-web": "whatsapp",
    "whatsapp_web": "whatsapp",
    "whatsapp-web.js": "whatsapp",
    "baileys": "whatsapp",
}


PLATFORM_METADATA: dict[str, dict[str, Any]] = {
    "telegram": {
        "display_name": "Telegram",
        "transport": "long_poll",
        "required_env": ["TELEGRAM_BOT_TOKEN"],
        "optional_env": ["TELEGRAM_ALLOWED_USERS", "TELEGRAM_BOT_USERNAME"],
        "max_message_length": 4096,
        "message_length_units": "utf16",
        "supports_threads": True,
        "supports_media": True,
        "typed_command_prefix": "/",
        "command_cap": MAX_TELEGRAM_COMMANDS,
    },
    "discord": {
        "display_name": "Discord",
        "transport": "gateway",
        "required_env": ["DISCORD_BOT_TOKEN"],
        "optional_env": [
            "DISCORD_ALLOWED_USERS",
            "DISCORD_ALLOWED_ROLES",
            "DISCORD_ALLOW_BOTS",
            "DISCORD_ALLOWED_CHANNELS",
            "DISCORD_IGNORED_CHANNELS",
        ],
        "max_message_length": 2000,
        "message_length_units": "codepoints",
        "supports_threads": True,
        "supports_media": True,
        "typed_command_prefix": "!",
        "command_cap": MAX_DISCORD_APP_COMMANDS,
        "slash_command_cap": MAX_DISCORD_APP_COMMANDS,
    },
    "slack": {
        "display_name": "Slack",
        "transport": "socket_mode",
        "required_env": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
        "optional_env": ["SLACK_ALLOW_BOTS"],
        "max_message_length": 39000,
        "message_length_units": "codepoints",
        "supports_threads": True,
        "supports_media": False,
        "typed_command_prefix": "!",
    },
    "mattermost": {
        "display_name": "Mattermost",
        "transport": "http_webhook",
        "required_env": ["MATTERMOST_URL", "MATTERMOST_BOT_TOKEN"],
        "optional_env": [
            "MATTERMOST_WEBHOOK_SECRET",
            "MATTERMOST_OUTGOING_TOKEN",
            "MATTERMOST_CHANNEL_PORT",
        ],
        "max_message_length": 16000,
        "message_length_units": "codepoints",
        "supports_threads": True,
        "supports_media": False,
        "typed_command_prefix": "!",
    },
    "signal": {
        "display_name": "Signal",
        "transport": "signal_cli",
        "required_env": ["SIGNAL_CLI_ACCOUNT"],
        "optional_env": ["SIGNAL_ALLOWED_USERS", "SIGNAL_CLI_BIN"],
        "max_message_length": None,
        "message_length_units": "codepoints",
        "supports_threads": False,
        "supports_media": False,
        "typed_command_prefix": "/",
    },
    "matrix": {
        "display_name": "Matrix",
        "transport": "matrix_sync",
        "required_env": ["MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD"],
        "optional_env": [],
        "max_message_length": None,
        "message_length_units": "codepoints",
        "supports_threads": False,
        "supports_media": False,
        "typed_command_prefix": "/",
    },
    "email": {
        "display_name": "Email",
        "transport": "imap_smtp",
        "required_env": ["EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_ADDRESS", "EMAIL_PASSWORD"],
        "optional_env": ["EMAIL_IMAP_PORT", "EMAIL_SMTP_PORT", "EMAIL_POLL"],
        "max_message_length": None,
        "message_length_units": "codepoints",
        "supports_threads": True,
        "supports_media": False,
        "typed_command_prefix": "/",
    },
    "ntfy": {
        "display_name": "ntfy",
        "transport": "ntfy_stream",
        "required_env": ["NTFY_TOPIC"],
        "optional_env": ["NTFY_SERVER", "NTFY_TOKEN"],
        "max_message_length": None,
        "message_length_units": "codepoints",
        "supports_threads": False,
        "supports_media": False,
        "typed_command_prefix": "/",
    },
    "webhook": {
        "display_name": "Webhook",
        "transport": "http",
        "required_env": [],
        "optional_env": [
            "WEBHOOK_CHANNEL_SECRET",
            "WEBHOOK_CHANNEL_PORT",
            "WEBHOOK_CHANNEL_MAX_BYTES",
        ],
        "max_message_length": None,
        "message_length_units": "codepoints",
        "supports_threads": False,
        "supports_media": False,
        "typed_command_prefix": "/",
    },
}


_SAFE_PLATFORM_RE = re.compile(r"[^a-z0-9_.-]+")
_COMMAND_TOKEN_RE = re.compile(r"^([!/])([A-Za-z][A-Za-z0-9_-]*)(?:@([A-Za-z0-9_]{2,64}))?(.*)$", re.S)


_BASE_GATEWAY_COMMANDS = (
    "help",
    "whoami",
    "status",
    "stop",
    "new",
    "reset",
    "model",
    "provider",
    "reasoning",
    "fast",
    "busy",
    "compress",
    "goal",
    "subgoal",
    "steer",
)


def normalize_platform_name(value: Any, *, default: str = "webhook") -> str:
    """Return a lowercase, alias-normalized platform id safe for session keys."""

    raw = str(value or "").strip().lower()
    if not raw:
        return default
    raw = PLATFORM_ALIASES.get(raw, raw)
    normalized = _SAFE_PLATFORM_RE.sub("-", raw).strip("._-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized or default


def platform_metadata(platform: Any) -> dict[str, Any]:
    """Return copied metadata for a platform, including its canonical id."""

    name = normalize_platform_name(platform, default="")
    metadata = deepcopy(PLATFORM_METADATA.get(name, {}))
    metadata.setdefault("id", name)
    return metadata


def known_gateway_commands(extra_commands: Iterable[Any] | None = None) -> set[str]:
    """Return known gateway command names without leading slash."""

    names = {item.lower() for item in _BASE_GATEWAY_COMMANDS}
    for item in extra_commands or ():
        token = str(item or "").strip().lower().lstrip("/")
        if token:
            names.add(token.split(None, 1)[0])
    return names


def capped_command_menu(
    extra_commands: Iterable[Any] | None = None,
    *,
    max_commands: int = 30,
) -> list[str]:
    """Return slash command names capped for platform command menus.

    Telegram caps BotCommand lists at 100, but Hermes deliberately keeps a
    smaller 30-command budget per scope so growth never silently overflows.
    AEGIS has fewer commands today; this helper keeps the same bounded behavior.
    """

    cap = max(1, int(max_commands or 30))
    ordered = list(_BASE_GATEWAY_COMMANDS)
    ordered.extend(str(c or "").strip().lstrip("/") for c in extra_commands or ())
    out: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        name = str(raw or "").strip().lower().lstrip("/").split(None, 1)[0]
        if not name or name in seen:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
            continue
        out.append("/" + name)
        seen.add(name)
        if len(out) >= cap:
            break
    return out


def discord_application_command_menu(
    extra_commands: Iterable[Any] | None = None,
    *,
    max_commands: int = MAX_DISCORD_APP_COMMANDS,
) -> list[str]:
    """Return a slash-command menu bounded by Discord's app-command hard cap."""

    requested = max(1, int(max_commands or MAX_DISCORD_APP_COMMANDS))
    return capped_command_menu(
        extra_commands,
        max_commands=min(requested, MAX_DISCORD_APP_COMMANDS),
    )


def normalize_inbound_command(
    text: str,
    *,
    platform: Any,
    bot_username: str | None = None,
    known_commands: Iterable[str] | None = None,
) -> str:
    """Normalize platform-specific command spellings before dispatch.

    Supported cases:
      - Telegram bot-menu forms like ``/status@aegis_bot`` become ``/status``.
      - Slack/Discord/Mattermost thread-friendly ``!stop`` aliases become ``/stop`` when
        the command is known.
    """

    if not text:
        return text or ""
    platform_name = normalize_platform_name(platform, default="")
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    if not stripped:
        return text

    match = _COMMAND_TOKEN_RE.match(stripped)
    if not match:
        return text

    prefix, name, suffix, rest = match.groups()
    known = {c.lower().lstrip("/") for c in (known_commands or known_gateway_commands())}
    command_name = name.lower()
    if known and command_name not in known:
        return text

    if prefix == "!":
        if platform_name not in {"slack", "discord", "mattermost"}:
            return text
    elif suffix:
        expected = (bot_username or "").strip().lstrip("@").lower()
        if platform_name == "telegram" and not expected:
            return text
        if expected and suffix.lower() != expected:
            return text

    return f"{leading}/{name}{rest}"


def utf16_units(text: str) -> int:
    """Count UTF-16 code units, matching Telegram's message limit."""

    return len((text or "").encode("utf-16-le")) // 2


def chunk_text_by_units(
    text: str,
    *,
    limit: int,
    len_fn: Callable[[str], int] = len,
) -> list[str]:
    """Split text into chunks where each chunk satisfies ``len_fn(chunk) <= limit``."""

    raw = text or ""
    max_units = max(1, int(limit or 1))
    if len_fn(raw) <= max_units:
        return [raw or "(empty)"]

    chunks: list[str] = []
    start = 0
    while start < len(raw):
        lo = start + 1
        hi = len(raw)
        best = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = raw[start:mid]
            if len_fn(candidate) <= max_units:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        chunks.append(raw[start:best])
        start = best
    return chunks or ["(empty)"]
