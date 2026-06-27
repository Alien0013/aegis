"""Domain-grouped dashboard router modules (split out of dashboard_fastapi.create_app)."""

from __future__ import annotations


def register_all(app, config, chat_runner) -> None:
    """Register every dashboard route group onto ``app`` in the original order."""
    from . import (
        appearance,
        static_auth,
        config_profiles,
        skills_plugins,
        tools_mcp,
        sessions,
        cron_jobs,
        gateway_messaging,
        file_browser,
        misc,
        fallback,
    )
    for mod in (
        static_auth,
        config_profiles,
        appearance,
        skills_plugins,
        tools_mcp,
        sessions,
        cron_jobs,
        gateway_messaging,
        file_browser,
        misc,
        fallback,
    ):
        mod.register(app, config, chat_runner)
