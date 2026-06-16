from __future__ import annotations


def test_display_resolver_uses_platform_override():
    from aegis.display_config import resolve_display_setting

    config = {
        "display": {
            "memory_notifications": "off",
            "platforms": {
                "telegram": {"memory_notifications": "verbose"},
            },
        }
    }

    assert resolve_display_setting(config, "telegram", "memory_notifications") == "verbose"
    assert resolve_display_setting(config, "slack", "memory_notifications") == "off"


def test_display_resolver_normalizes_aliases_and_invalid_values():
    from aegis.display_config import normalize_platform_display_overrides, resolve_display_setting

    overrides = normalize_platform_display_overrides({
        "Telegram": {
            "tool_progress_style": "SEPARATE",
            "memory_notifications": False,
            "unknown": "ignored",
        },
        "": {"memory_notifications": "verbose"},
        "slack": {"tool_progress_grouping": "noisy"},
    })

    assert overrides == {
        "telegram": {
            "tool_progress_grouping": "separate",
            "memory_notifications": "off",
        },
        "slack": {"tool_progress_grouping": "accumulate"},
    }
    assert resolve_display_setting({"display": {"platforms": overrides}}, "telegram", "tool_progress_grouping") == "separate"
