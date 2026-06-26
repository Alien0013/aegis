"""Domain-grouped dashboard router modules (split out of dashboard_fastapi.create_app)."""

from __future__ import annotations


def register_all(app, config, chat_runner) -> None:
    """Register every dashboard route group onto ``app`` in the original order."""
    from . import (static_auth, config_profiles, skills_plugins, tools_mcp, sessions, cron_jobs, gateway_messaging, misc, fallback)
    for mod in (static_auth, config_profiles, skills_plugins, tools_mcp, sessions, cron_jobs, gateway_messaging, misc, fallback):
        mod.register(app, config, chat_runner)
