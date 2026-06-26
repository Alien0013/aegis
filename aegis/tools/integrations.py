"""Optional Hermes-compatible integration tools.

These tools close exact-name/toolset parity for optional integrations while
keeping unsafe or credential-bound surfaces hidden until configured.  Where the
API contract is small and stable (Home Assistant, basic Discord REST), AEGIS
executes the real API.  Vendor-specific surfaces that require account/session
plugins return explicit setup errors instead of pretending to work.
"""

from __future__ import annotations

import json
import os
import random
import re
from typing import Any

import httpx

from .base import Tool, ToolContext, ToolResult


_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BLOCKED_HA_DOMAINS = frozenset({
    "shell_command",
    "command_line",
    "python_script",
    "pyscript",
    "hassio",
    "rest_command",
})

_STICKERS = [
    {"sticker_id": "ok", "name": "OK", "description": "agree / acknowledge"},
    {"sticker_id": "666", "name": "六六六", "description": "praise / nice move"},
    {"sticker_id": "heart", "name": "比心", "description": "heart / thanks"},
    {"sticker_id": "cool", "name": "酷", "description": "cool / confident"},
    {"sticker_id": "eat-melon", "name": "吃瓜", "description": "watching / curious"},
]


def _env_missing(envs: list[str]) -> list[str]:
    return [env for env in envs if not os.environ.get(env)]


def _json(data: Any) -> ToolResult:
    return ToolResult.ok(json.dumps(data, indent=2, sort_keys=True), data=data)


class OptionalIntegrationTool(Tool):
    """Base class for exact-name optional integrations."""

    required_env: list[str] = []
    setup_hint: str = "Configure the required integration credentials to use this tool."

    def available(self) -> tuple[bool, str]:
        missing = _env_missing(self.required_env)
        if missing:
            return False, "missing required env: " + ", ".join(missing)
        return True, ""

    def _missing_result(self) -> ToolResult | None:
        missing = _env_missing(self.required_env)
        if not missing:
            return None
        return ToolResult.error(
            f"Configure {', '.join(missing)} to use `{self.name}`. {self.setup_hint}"
        )


class DiscordTool(OptionalIntegrationTool):
    name = "discord"
    toolset = "discord"
    groups = ["network"]
    required_env = ["DISCORD_BOT_TOKEN"]
    setup_hint = "Create a Discord bot token and enable the Discord toolset."
    description = (
        "Discord bot integration. Supports basic REST actions when DISCORD_BOT_TOKEN is set: "
        "list_guilds, get_channel, send_message."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list_guilds | get_channel | send_message"},
            "channel_id": {"type": "string"},
            "content": {"type": "string"},
            "message": {"type": "string"},
        },
    }

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}", "Content-Type": "application/json"}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        action = str(args.get("action") or "list_guilds").strip().lower()
        base = "https://discord.com/api/v10"
        try:
            with httpx.Client(timeout=30) as client:
                if action == "list_guilds":
                    resp = client.get(f"{base}/users/@me/guilds", headers=self._headers())
                elif action == "get_channel":
                    channel_id = str(args.get("channel_id") or "").strip()
                    if not channel_id:
                        return ToolResult.error("channel_id is required for get_channel")
                    resp = client.get(f"{base}/channels/{channel_id}", headers=self._headers())
                elif action == "send_message":
                    channel_id = str(args.get("channel_id") or "").strip()
                    content = str(args.get("content") or args.get("message") or "").strip()
                    if not channel_id or not content:
                        return ToolResult.error("channel_id and content/message are required for send_message")
                    resp = client.post(
                        f"{base}/channels/{channel_id}/messages",
                        headers=self._headers(),
                        json={"content": content},
                    )
                else:
                    return ToolResult.error("unsupported discord action; use list_guilds, get_channel, or send_message")
                resp.raise_for_status()
                return _json(resp.json())
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"discord API call failed: {exc}")


class DiscordAdminTool(DiscordTool):
    name = "discord_admin"
    toolset = "discord_admin"
    description = (
        "Discord admin/moderation integration. Supports get_member, kick_member, and ban_member "
        "when DISCORD_BOT_TOKEN has the needed Discord permissions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "get_member | kick_member | ban_member"},
            "guild_id": {"type": "string"},
            "user_id": {"type": "string"},
            "reason": {"type": "string"},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        action = str(args.get("action") or "get_member").strip().lower()
        guild_id = str(args.get("guild_id") or "").strip()
        user_id = str(args.get("user_id") or "").strip()
        if not guild_id or not user_id:
            return ToolResult.error("guild_id and user_id are required")
        base = "https://discord.com/api/v10"
        headers = self._headers()
        if args.get("reason"):
            headers["X-Audit-Log-Reason"] = str(args["reason"])
        try:
            with httpx.Client(timeout=30) as client:
                if action == "get_member":
                    resp = client.get(f"{base}/guilds/{guild_id}/members/{user_id}", headers=headers)
                elif action == "kick_member":
                    resp = client.delete(f"{base}/guilds/{guild_id}/members/{user_id}", headers=headers)
                elif action == "ban_member":
                    resp = client.put(f"{base}/guilds/{guild_id}/bans/{user_id}", headers=headers, json={})
                else:
                    return ToolResult.error("unsupported discord_admin action; use get_member, kick_member, or ban_member")
                if resp.status_code == 204:
                    return _json({"success": True, "action": action, "guild_id": guild_id, "user_id": user_id})
                resp.raise_for_status()
                return _json(resp.json())
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"discord_admin API call failed: {exc}")


class HomeAssistantTool(OptionalIntegrationTool):
    toolset = "homeassistant"
    groups = ["network"]
    required_env = ["HASS_TOKEN"]
    setup_hint = "Set HASS_TOKEN and optionally HASS_URL (default http://homeassistant.local:8123)."

    def _base_url(self) -> str:
        return os.environ.get("HASS_URL", "http://homeassistant.local:8123").rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ['HASS_TOKEN']}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json_payload: Any = None) -> Any:
        with httpx.Client(timeout=30) as client:
            resp = client.request(method, f"{self._base_url()}{path}", headers=self._headers(), json=json_payload)
            resp.raise_for_status()
            return resp.json() if resp.content else {"success": True}


class HAListEntitiesTool(HomeAssistantTool):
    name = "ha_list_entities"
    description = "List Home Assistant entities, optionally filtering by domain or area/friendly name."
    parameters = {
        "type": "object",
        "properties": {"domain": {"type": "string"}, "area": {"type": "string"}},
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        try:
            states = self._request("GET", "/api/states")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"failed to list Home Assistant entities: {exc}")
        domain = str(args.get("domain") or "").strip()
        area = str(args.get("area") or "").strip().lower()
        entities = []
        for item in states:
            entity_id = str(item.get("entity_id") or "")
            attrs = item.get("attributes") or {}
            friendly = str(attrs.get("friendly_name") or "")
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            if area and area not in friendly.lower() and area not in str(attrs.get("area") or "").lower():
                continue
            entities.append({"entity_id": entity_id, "state": item.get("state"), "friendly_name": friendly})
        return _json({"count": len(entities), "entities": entities})


class HAGetStateTool(HomeAssistantTool):
    name = "ha_get_state"
    description = "Get detailed Home Assistant state for one entity_id."
    parameters = {
        "type": "object",
        "properties": {"entity_id": {"type": "string"}},
        "required": ["entity_id"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        entity_id = str(args.get("entity_id") or "").strip()
        if not _ENTITY_ID_RE.match(entity_id):
            return ToolResult.error(f"invalid entity_id format: {entity_id}")
        try:
            return _json(self._request("GET", f"/api/states/{entity_id}"))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"failed to get Home Assistant state: {exc}")


class HAListServicesTool(HomeAssistantTool):
    name = "ha_list_services"
    description = "List Home Assistant services/actions, optionally filtering by domain."
    parameters = {"type": "object", "properties": {"domain": {"type": "string"}}}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        domain = str(args.get("domain") or "").strip()
        try:
            services = self._request("GET", "/api/services")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"failed to list Home Assistant services: {exc}")
        if domain:
            services = [item for item in services if item.get("domain") == domain]
        return _json({"count": len(services), "domains": services})


class HACallServiceTool(HomeAssistantTool):
    name = "ha_call_service"
    description = "Call a Home Assistant service such as light.turn_on after safety validation."
    parameters = {
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "service": {"type": "string"},
            "entity_id": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["domain", "service"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        domain = str(args.get("domain") or "").strip()
        service = str(args.get("service") or "").strip()
        if not _SERVICE_NAME_RE.match(domain):
            return ToolResult.error(f"invalid domain format: {domain!r}")
        if not _SERVICE_NAME_RE.match(service):
            return ToolResult.error(f"invalid service format: {service!r}")
        if domain in _BLOCKED_HA_DOMAINS:
            return ToolResult.error(
                f"service domain {domain!r} is blocked for safety; blocked domains: "
                + ", ".join(sorted(_BLOCKED_HA_DOMAINS))
            )
        payload = dict(args.get("data") or {})
        entity_id = str(args.get("entity_id") or "").strip()
        if entity_id:
            if not _ENTITY_ID_RE.match(entity_id):
                return ToolResult.error(f"invalid entity_id format: {entity_id}")
            payload["entity_id"] = entity_id
        try:
            return _json(self._request("POST", f"/api/services/{domain}/{service}", json_payload=payload))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"failed to call Home Assistant service {domain}.{service}: {exc}")


class FeishuTool(OptionalIntegrationTool):
    groups = ["network"]
    required_env = ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]
    setup_hint = "Set FEISHU_APP_ID and FEISHU_APP_SECRET or install a richer Feishu plugin."

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        return ToolResult.error(
            f"{self.name} is registered for Hermes-compatible Feishu parity, but this AEGIS build "
            "does not yet include the full Feishu REST adapter. Install/enable a Feishu plugin "
            "or continue the Feishu parity slice."
        )


class FeishuDocReadTool(FeishuTool):
    name = "feishu_doc_read"
    toolset = "feishu_doc"
    description = "Read a Feishu/Lark document by document token."
    parameters = {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]}


class FeishuDriveCommentTool(FeishuTool):
    toolset = "feishu_drive"
    parameters = {
        "type": "object",
        "properties": {
            "file_token": {"type": "string"},
            "comment_id": {"type": "string"},
            "reply_id": {"type": "string"},
            "content": {"type": "string"},
        },
    }


class FeishuDriveListCommentsTool(FeishuDriveCommentTool):
    name = "feishu_drive_list_comments"
    description = "List comments for a Feishu/Lark Drive file."


class FeishuDriveListCommentRepliesTool(FeishuDriveCommentTool):
    name = "feishu_drive_list_comment_replies"
    description = "List replies for a Feishu/Lark Drive comment."


class FeishuDriveReplyCommentTool(FeishuDriveCommentTool):
    name = "feishu_drive_reply_comment"
    description = "Reply to a Feishu/Lark Drive comment."


class FeishuDriveAddCommentTool(FeishuDriveCommentTool):
    name = "feishu_drive_add_comment"
    description = "Add a comment to a Feishu/Lark Drive file."


class VideoGenerateTool(Tool):
    name = "video_generate"
    toolset = "video_gen"
    groups = ["network"]
    description = (
        "Generate a video from a text prompt or animate an image using the configured video "
        "generation backend. AEGIS currently supports FAL-compatible endpoints when FAL_KEY "
        "is set; richer provider plugins can override this tool later."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "image_url": {"type": "string"},
            "duration": {"type": "integer"},
            "aspect_ratio": {"type": "string"},
            "resolution": {"type": "string"},
            "model": {"type": "string"},
        },
        "required": ["prompt"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult.error("prompt is required for video generation")
        key = os.environ.get("FAL_KEY")
        if not key:
            return ToolResult.error(
                "Configure FAL_KEY or a video generation plugin to use video_generate. "
                "Video generation backends are intentionally user-configured."
            )
        model = str(args.get("model") or "fal-ai/veo3/fast").strip()
        payload = {k: v for k, v in {
            "prompt": prompt,
            "image_url": args.get("image_url"),
            "duration": args.get("duration"),
            "aspect_ratio": args.get("aspect_ratio"),
            "resolution": args.get("resolution"),
        }.items() if v not in (None, "")}
        try:
            with httpx.Client(timeout=600) as client:
                resp = client.post(f"https://fal.run/{model}", headers={"Authorization": f"Key {key}"}, json=payload)
                resp.raise_for_status()
                return _json(resp.json())
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"video generation failed: {exc}")


class YuanbaoTool(OptionalIntegrationTool):
    toolset = "yuanbao"
    groups = ["network"]
    required_env = ["YUANBAO_COOKIE"]
    setup_hint = "Set YUANBAO_COOKIE or enable the Yuanbao gateway/plugin."
    parameters = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        missing = self._missing_result()
        if missing:
            return missing
        return ToolResult.error(
            f"{self.name} requires the Yuanbao gateway/plugin adapter; credentials are present but "
            "the full adapter is not bundled in this AEGIS build yet."
        )


class YBQueryGroupInfoTool(YuanbaoTool):
    name = "yb_query_group_info"
    description = "Query Yuanbao group/Pai info."
    parameters = {"type": "object", "properties": {"group_code": {"type": "string"}}, "required": ["group_code"]}


class YBQueryGroupMembersTool(YuanbaoTool):
    name = "yb_query_group_members"
    description = "Query Yuanbao group/Pai members for lookup or @mentions."
    parameters = {
        "type": "object",
        "properties": {
            "group_code": {"type": "string"},
            "action": {"type": "string"},
            "name": {"type": "string"},
            "mention": {"type": "boolean"},
        },
        "required": ["group_code", "action"],
    }


class YBSendDMTool(YuanbaoTool):
    name = "yb_send_dm"
    description = "Send a Yuanbao direct/private message via the Yuanbao gateway/plugin."
    parameters = {
        "type": "object",
        "properties": {
            "group_code": {"type": "string"},
            "name": {"type": "string"},
            "message": {"type": "string"},
            "user_id": {"type": "string"},
        },
    }


class YBSearchStickerTool(Tool):
    name = "yb_search_sticker"
    toolset = "yuanbao"
    description = "Search the built-in Yuanbao sticker catalogue by keyword. Works locally without credentials."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        query = str(args.get("query") or "").strip().lower()
        limit = max(1, min(int(args.get("limit") or 10), 50))
        matches = [item for item in _STICKERS if not query or query in item["name"].lower() or query in item["description"].lower() or query in item["sticker_id"].lower()]
        if not matches:
            matches = random.sample(_STICKERS, k=min(limit, len(_STICKERS)))
        return _json({"stickers": matches[:limit], "count": min(len(matches), limit)})


class YBSendStickerTool(YuanbaoTool):
    name = "yb_send_sticker"
    description = "Send a real Yuanbao/TIM sticker via the Yuanbao gateway/plugin."
    parameters = {
        "type": "object",
        "properties": {
            "sticker": {"type": "string"},
            "chat_id": {"type": "string"},
            "reply_to": {"type": "string"},
        },
    }


def integration_tools() -> list[Tool]:
    return [
        DiscordTool(),
        DiscordAdminTool(),
        FeishuDocReadTool(),
        FeishuDriveListCommentsTool(),
        FeishuDriveListCommentRepliesTool(),
        FeishuDriveReplyCommentTool(),
        FeishuDriveAddCommentTool(),
        HAListEntitiesTool(),
        HAGetStateTool(),
        HAListServicesTool(),
        HACallServiceTool(),
        VideoGenerateTool(),
        YBQueryGroupInfoTool(),
        YBQueryGroupMembersTool(),
        YBSendDMTool(),
        YBSearchStickerTool(),
        YBSendStickerTool(),
    ]
