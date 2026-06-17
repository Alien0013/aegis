"""Shared platform adapter helpers."""

from .helpers import (
    MAX_DISCORD_APP_COMMANDS,
    MAX_TELEGRAM_COMMANDS,
    PLATFORM_METADATA,
    capped_command_menu,
    chunk_text_by_units,
    discord_application_command_menu,
    known_gateway_commands,
    normalize_inbound_command,
    normalize_platform_name,
    platform_metadata,
    utf16_units,
)

__all__ = [
    "MAX_DISCORD_APP_COMMANDS",
    "MAX_TELEGRAM_COMMANDS",
    "PLATFORM_METADATA",
    "capped_command_menu",
    "chunk_text_by_units",
    "discord_application_command_menu",
    "known_gateway_commands",
    "normalize_inbound_command",
    "normalize_platform_name",
    "platform_metadata",
    "utf16_units",
]
