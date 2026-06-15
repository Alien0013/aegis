"""Cross-session recall tool: browse and search past conversations."""

from __future__ import annotations

import json

from .. import config as cfg
from .base import Tool, ToolContext, ToolResult


def _session_ref(raw: str) -> tuple[bool, str, str]:
    """Return (has_profile, profile, session_id) for session references."""
    ref = (raw or "").strip()
    if ref.startswith("@session:"):
        ref = ref[len("@session:"):]
    if "/" in ref:
        profile, _, sid = ref.partition("/")
        profile = profile.strip()
        sid = sid.strip()
        if profile and sid:
            return True, cfg.profile_name(profile), sid
    return False, "", ref


class SessionSearchTool(Tool):
    name = "session_search"
    description = (
        "Browse, search, read, or scroll PAST conversations/sessions. Use whenever the "
        "user references previous chats, last session, memory, or earlier work instead "
        "of asking them to repeat context."
    )
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query. Omit to browse recent sessions.",
            },
            "session_id": {
                "type": "string",
                "description": "Session id, id prefix, or exact title to read/scroll.",
            },
            "around_message_id": {
                "type": "integer",
                "description": "Message id to center when scrolling within a session.",
            },
            "around_message_row_id": {
                "type": "integer",
                "description": "Stable SQLite message_row_id to center when scrolling within a session.",
            },
            "window": {
                "type": "integer",
                "description": "Messages before/after around_message_id (default 5, max 20).",
            },
            "head": {
                "type": "integer",
                "description": "Initial messages to include when reading a session (default 20).",
            },
            "tail": {
                "type": "integer",
                "description": "Final messages to include when reading a long session (default 10).",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions/results (browse default 10, search default 3).",
            },
            "sort": {
                "type": "string",
                "enum": ["rank", "newest", "oldest"],
                "description": "Sort search results by rank or timestamp.",
            },
            "role_filter": {
                "type": "array",
                "items": {"type": "string", "enum": ["user", "assistant", "tool"]},
                "description": "Restrict search matches by message role.",
            },
            "profile": {
                "type": "string",
                "description": "Optional AEGIS config profile to search (default searches the active profile).",
            },
        },
    }

    def _session_lookup(self, store, args, session_id: str, current_session_id: str | None) -> dict:
        if args.get("around_message_row_id") is not None:
            return store.messages_around(
                session_id,
                args.get("around_message_row_id"),
                window=int(args.get("window", 5)),
                current_session_id=current_session_id,
                anchor_is_row_id=True,
            )
        if args.get("around_message_id") is not None:
            return store.messages_around(
                session_id,
                args.get("around_message_id"),
                window=int(args.get("window", 5)),
                current_session_id=current_session_id,
            )
        return store.read_session(
            session_id,
            head=int(args.get("head", 20)),
            tail=int(args.get("tail", 10)),
        )

    def _session_not_found(self, data: dict) -> bool:
        return not data.get("success", True) and "not found" in str(data.get("error", "")).lower()

    def _lookup_across_profiles(
        self,
        *,
        args,
        session_id: str,
        primary_profile: str,
        current_session_id: str | None,
    ) -> dict | None:
        from .. import config as cfg
        from ..session import SessionStore

        seen: set[str] = {primary_profile}
        profiles = [primary_profile]
        active = cfg.current_profile()
        if active not in seen:
            profiles.append(active)
            seen.add(active)
        for profile in cfg.available_profiles():
            if profile not in seen:
                profiles.append(profile)
                seen.add(profile)

        for profile in profiles:
            if profile == primary_profile:
                continue
            db = cfg.profile_home(profile) / "state.db"
            if not db.exists():
                continue
            try:
                candidate = SessionStore(profile=profile, read_only=True)
                data = self._session_lookup(candidate, args, session_id, None)
            except Exception:  # noqa: BLE001
                continue
            if data.get("success", True):
                data["profile"] = profile
                data.setdefault("located_profile", profile)
                if profile != primary_profile:
                    data["message"] = (
                        f"Found session in profile {profile or 'default'} "
                        f"after it was not found in {primary_profile or 'default'}."
                    )
                return data
            if not self._session_not_found(data):
                data["profile"] = profile
                return data
        return None

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..session import SessionStore

        raw_requested_profile = str(args.get("profile") or "").strip()
        requested_profile = raw_requested_profile
        raw_session_id = str(args.get("session_id") or "").strip()
        try:
            has_ref_profile, ref_profile, session_id = _session_ref(raw_session_id)
            requested_profile = cfg.profile_name(requested_profile) if requested_profile else ""
        except ValueError as e:
            data = {"success": False, "mode": "recall", "error": str(e), "profile": requested_profile}
            content = json.dumps(data, ensure_ascii=False, indent=2)
            return ToolResult(content=content, is_error=True, display="recall: error", data=data)
        if has_ref_profile:
            if requested_profile and requested_profile != ref_profile:
                data = {
                    "success": False,
                    "mode": "recall",
                    "error": (
                        f"session_id points at profile {ref_profile or 'default'}, "
                        f"but profile argument was {requested_profile or 'default'}"
                    ),
                    "profile": requested_profile,
                }
                content = json.dumps(data, ensure_ascii=False, indent=2)
                return ToolResult(content=content, is_error=True, display="recall: error", data=data)
            requested_profile = ref_profile
        explicit_profile = bool(raw_requested_profile) or has_ref_profile
        try:
            store = SessionStore(profile=requested_profile) if explicit_profile else SessionStore()
        except ValueError as e:
            data = {"success": False, "mode": "recall", "error": str(e), "profile": requested_profile}
            content = json.dumps(data, ensure_ascii=False, indent=2)
            return ToolResult(content=content, is_error=True, display="recall: error", data=data)
        current_session_id = getattr(getattr(ctx, "session", None), "id", None)
        if explicit_profile:
            current_session_id = None
        query = str(args.get("query") or "").strip()

        if session_id:
            data = self._session_lookup(store, args, session_id, current_session_id)
            if self._session_not_found(data):
                found = self._lookup_across_profiles(
                    args=args,
                    session_id=session_id,
                    primary_profile=requested_profile if explicit_profile else store.profile or "",
                    current_session_id=current_session_id,
                )
                if found is not None:
                    data = found
        elif query:
            role_filter = args.get("role_filter")
            if isinstance(role_filter, str):
                role_filter = [r.strip() for r in role_filter.split(",") if r.strip()]
            data = store.discover_sessions(
                query,
                limit=int(args.get("limit", 3)),
                role_filter=role_filter,
                sort=args.get("sort"),
                current_session_id=current_session_id,
            )
        else:
            data = store.browse_sessions(
                limit=int(args.get("limit", 10)),
                current_session_id=current_session_id,
            )

        data.setdefault("profile", requested_profile if explicit_profile else store.profile or "")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        mode = data.get("mode", "recall")
        count = data.get("count", len(data.get("messages", [])))
        display = f"recall {mode}: {count}"
        if not data.get("success", True):
            return ToolResult(content=content, is_error=True, display=f"recall {mode}: error", data=data)
        return ToolResult.ok(content, display=display, data=data)


def recall_tools() -> list[Tool]:
    return [SessionSearchTool()]
