"""Display preference resolution shared by dashboard and gateway surfaces."""

from __future__ import annotations

from typing import Any

from .platforms import normalize_platform_name


DISPLAY_DEFAULTS: dict[str, Any] = {
    "reasoning": "summary",
    "status_footer": True,
    "tool_progress": "compact",
    "tool_progress_grouping": "accumulate",
    "memory_notifications": "on",
}

DISPLAY_CHOICES: dict[str, set[str]] = {
    "reasoning": {"summary", "live", "off"},
    "tool_progress": {"compact", "detailed"},
    "tool_progress_grouping": {"accumulate", "separate"},
    "memory_notifications": {"off", "on", "verbose"},
}

_BOOL_SETTINGS = {"status_footer"}
_ALIASES = {
    "tool_progress_style": "tool_progress_grouping",
}


def display_setting_key(setting: str) -> str:
    key = str(setting or "").strip()
    if key.startswith("display."):
        key = key[len("display."):]
    return _ALIASES.get(key, key)


def platform_key(platform: Any) -> str:
    return normalize_platform_name(platform, default="")


def normalize_display_setting(setting: str, value: Any, default: Any = None) -> Any:
    key = display_setting_key(setting)
    fallback = DISPLAY_DEFAULTS.get(key, default)
    if key in _BOOL_SETTINGS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in {"true", "1", "yes", "on"}:
                return True
            if raw in {"false", "0", "no", "off"}:
                return False
        return bool(fallback)
    choices = DISPLAY_CHOICES.get(key)
    if choices is not None:
        if key == "memory_notifications" and isinstance(value, bool):
            value = "on" if value else "off"
        raw = str(value or "").strip().lower()
        return raw if raw in choices else fallback
    return value if value is not None else fallback


def resolve_display_setting(config: Any, platform: Any, setting: str, default: Any = None) -> Any:
    """Resolve a display setting with Hermes-style per-platform overrides."""
    key = display_setting_key(setting)
    fallback = DISPLAY_DEFAULTS.get(key, default)
    data = getattr(config, "data", config)
    display = data.get("display") if isinstance(data, dict) else {}
    if not isinstance(display, dict):
        return fallback
    pkey = platform_key(platform)
    platforms = display.get("platforms")
    if pkey and isinstance(platforms, dict):
        platform_cfg = platforms.get(pkey)
        if not isinstance(platform_cfg, dict):
            for raw_platform, raw_cfg in platforms.items():
                if platform_key(raw_platform) == pkey and isinstance(raw_cfg, dict):
                    platform_cfg = raw_cfg
                    break
        if isinstance(platform_cfg, dict) and key in platform_cfg:
            return normalize_display_setting(key, platform_cfg.get(key), fallback)
    return normalize_display_setting(key, display.get(key, fallback), fallback)


def normalize_platform_display_overrides(overrides: Any) -> dict[str, dict[str, Any]]:
    """Normalize dashboard/API-provided display.platforms overrides."""
    if not isinstance(overrides, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_platform, raw_values in overrides.items():
        pkey = platform_key(raw_platform)
        if not pkey or not isinstance(raw_values, dict):
            continue
        values: dict[str, Any] = {}
        for raw_key, raw_value in raw_values.items():
            key = display_setting_key(str(raw_key))
            if key not in DISPLAY_DEFAULTS:
                continue
            values[key] = normalize_display_setting(key, raw_value)
        if values:
            out[pkey] = values
    return out
