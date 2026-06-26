"""Hermes-compatible optional integration tools."""

from __future__ import annotations

import json
from types import SimpleNamespace


OPTIONAL_TOOLS = {
    "discord": ("discord", ["DISCORD_BOT_TOKEN"]),
    "discord_admin": ("discord_admin", ["DISCORD_BOT_TOKEN"]),
    "feishu_doc_read": ("feishu_doc", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    "feishu_drive_list_comments": ("feishu_drive", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    "feishu_drive_list_comment_replies": ("feishu_drive", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    "feishu_drive_reply_comment": ("feishu_drive", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    "feishu_drive_add_comment": ("feishu_drive", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    "ha_list_entities": ("homeassistant", ["HASS_TOKEN"]),
    "ha_get_state": ("homeassistant", ["HASS_TOKEN"]),
    "ha_list_services": ("homeassistant", ["HASS_TOKEN"]),
    "ha_call_service": ("homeassistant", ["HASS_TOKEN"]),
    "video_generate": ("video_gen", []),
    "yb_query_group_info": ("yuanbao", ["YUANBAO_COOKIE"]),
    "yb_query_group_members": ("yuanbao", ["YUANBAO_COOKIE"]),
    "yb_send_dm": ("yuanbao", ["YUANBAO_COOKIE"]),
    "yb_search_sticker": ("yuanbao", []),
    "yb_send_sticker": ("yuanbao", ["YUANBAO_COOKIE"]),
}


def test_optional_hermes_integration_tools_are_registered(monkeypatch):
    for env in {item for _toolset, envs in OPTIONAL_TOOLS.values() for item in envs}:
        monkeypatch.delenv(env, raising=False)

    from aegis.tools.registry import default_registry

    tools = {tool.name: tool for tool in default_registry(include_plugins=False).all()}

    for name, (toolset, envs) in OPTIONAL_TOOLS.items():
        assert name in tools
        assert tools[name].toolset == toolset
        for env in envs:
            assert env in tools[name].metadata()["required_env"]


def test_optional_integration_tools_return_setup_errors_without_credentials(monkeypatch, tmp_path):
    for env in {item for _toolset, envs in OPTIONAL_TOOLS.values() for item in envs}:
        monkeypatch.delenv(env, raising=False)

    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry

    cfg = Config.load()
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=SimpleNamespace(config=cfg))
    tools = {tool.name: tool for tool in default_registry(include_plugins=False).all()}

    for name in OPTIONAL_TOOLS:
        if name in {"yb_search_sticker", "video_generate"}:
            continue
        result = tools[name].run({}, ctx)
        assert result.is_error, name
        assert "configure" in result.content.lower() or "set" in result.content.lower()


def test_homeassistant_tool_validates_service_names_before_network(monkeypatch, tmp_path):
    monkeypatch.setenv("HASS_TOKEN", "token")

    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry

    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    tool = {tool.name: tool for tool in default_registry(include_plugins=False).all()}["ha_call_service"]

    result = tool.run({"domain": "shell_command", "service": "turn_on"}, ctx)
    assert result.is_error
    assert "blocked" in result.content.lower()

    result = tool.run({"domain": "light/../api", "service": "turn_on"}, ctx)
    assert result.is_error
    assert "invalid" in result.content.lower()


def test_video_generate_requires_prompt_before_backend_lookup(tmp_path):
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry

    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    tool = {tool.name: tool for tool in default_registry(include_plugins=False).all()}["video_generate"]

    result = tool.run({}, ctx)
    assert result.is_error
    assert "prompt" in result.content.lower()

    missing_backend = tool.run({"prompt": "a slow cinematic dolly shot"}, ctx)
    assert missing_backend.is_error
    assert "video generation" in missing_backend.content.lower()


def test_yuanbao_search_sticker_has_local_fallback_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("YUANBAO_COOKIE", raising=False)

    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry

    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    tool = {tool.name: tool for tool in default_registry(include_plugins=False).all()}["yb_search_sticker"]

    result = tool.run({"query": "ok", "limit": 3}, ctx)
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["stickers"]
