"""Discord channel adapter (requires `discord.py`)."""

from __future__ import annotations

import asyncio
import os

from ..platforms import (
    MAX_DISCORD_APP_COMMANDS,
    chunk_text_by_units,
    discord_application_command_menu,
    normalize_inbound_command,
)
from .base import BasePlatformAdapter, Dispatch, MessageEvent


def _csv_set(value: str) -> set[str] | None:
    items = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return items or None


class DiscordAdapter(BasePlatformAdapter):
    name = "discord"
    renders_tables = False
    transport = "gateway"
    max_message_length = 2000
    supports_threads = True
    supports_media = True
    supports_reactions = True
    typed_command_prefix = "!"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN")
        if not self.token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
        self.allowed = _csv_set(os.environ.get("DISCORD_ALLOWED_USERS", ""))
        self.allowed_roles = _csv_set(os.environ.get("DISCORD_ALLOWED_ROLES", ""))
        self.allowed_guilds = _csv_set(os.environ.get("DISCORD_ALLOWED_GUILDS", ""))
        self.ignored_guilds = _csv_set(os.environ.get("DISCORD_IGNORED_GUILDS", ""))
        self.trigger_mode = os.environ.get("DISCORD_TRIGGER_MODE", "all").strip().lower() or "all"

    @property
    def metadata(self) -> dict:
        data = super().metadata
        data["security"] = {
            "allowed_users_configured": bool(self.allowed),
            "allowed_roles_configured": bool(self.allowed_roles),
            "allowed_guilds_configured": bool(self.allowed_guilds),
            "ignored_guilds_configured": bool(self.ignored_guilds),
            "trigger_mode": self.trigger_mode,
        }
        return data

    def command_menu(self, *, max_commands: int = MAX_DISCORD_APP_COMMANDS) -> list[str]:
        return discord_application_command_menu(max_commands=max_commands)

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        try:
            import discord
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("discord channel needs `pip install discord.py`") from e

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client
        self._loop = None

        @client.event
        async def on_message(message):  # noqa: ANN001
            self._loop = asyncio.get_event_loop()
            if message.author == client.user:
                return
            if not self._message_type_allowed(message):
                return
            if not self._bot_author_allowed(message, client):
                return
            if not self._guild_allowed(message):
                return
            if not self._channel_allowed(message):
                return
            if not self._author_allowed(message):
                return
            if not self._trigger_allowed(message, client):
                return
            reference = getattr(message, "reference", None)
            replied = getattr(reference, "resolved", None) if reference is not None else None
            raw_content = message.content or ""
            content = self._strip_own_mentions(raw_content, client)
            content = normalize_inbound_command(content, platform="discord")
            chat_id, thread_id = self._chat_and_thread_ids(message)
            ev = MessageEvent(
                platform="discord", chat_id=chat_id,
                text=content, user_id=str(message.author.id),
                user_name=str(message.author),
                message_id=str(getattr(message, "id", "") or "") or None,
                reply_to_message_id=str(getattr(replied, "id", "") or "") or None,
                reply_to_text=getattr(replied, "content", None),
                timestamp=getattr(message, "created_at", None),
                thread_id=thread_id,
                attachments=self._attachments_from_message(message),
                metadata={
                    "guild_id": str(getattr(getattr(message, "guild", None), "id", "") or ""),
                    "channel_id": str(getattr(message.channel, "id", "") or ""),
                    "channel_name": str(getattr(message.channel, "name", "") or ""),
                },
            )
            ev._discord_channel = message.channel
            ev._discord_loop = self._loop
            self._submit_inbound(ev, raw_text=raw_content)

        client.run(self.token, log_handler=None)

    def _message_type_allowed(self, message) -> bool:  # noqa: ANN001
        mtype = getattr(message, "type", None)
        if mtype is None:
            return True
        name = str(getattr(mtype, "name", mtype)).lower()
        return name in {"default", "reply", "messagetype.default", "messagetype.reply"}

    def _bot_author_allowed(self, message, client) -> bool:  # noqa: ANN001
        if not getattr(message.author, "bot", False):
            return True
        mode = os.environ.get("DISCORD_ALLOW_BOTS", "none").strip().lower()
        if mode in {"all", "true", "1", "yes"}:
            return True
        if mode == "mentions":
            mentions = getattr(message, "mentions", []) or []
            return bool(getattr(client, "user", None) and client.user in mentions)
        return False

    def _guild_allowed(self, message) -> bool:  # noqa: ANN001
        guild = getattr(message, "guild", None)
        guild_id = str(getattr(guild, "id", "") or "")
        is_dm = not guild_id
        ignored = self.ignored_guilds or set()
        if "*" in ignored or (is_dm and "dm" in ignored) or (guild_id and guild_id in ignored):
            return False
        allowed = self.allowed_guilds or set()
        if not allowed or "*" in allowed:
            return True
        if is_dm:
            return "dm" in allowed
        return guild_id in allowed

    def _channel_allowed(self, message) -> bool:  # noqa: ANN001
        channel_ids = {str(getattr(message.channel, "id", "") or "")}
        parent = getattr(message.channel, "parent", None)
        if parent is not None:
            channel_ids.add(str(getattr(parent, "id", "") or ""))
        channel_ids.discard("")
        ignored = {c.strip() for c in os.environ.get("DISCORD_IGNORED_CHANNELS", "").split(",") if c.strip()}
        if "*" in ignored or channel_ids & ignored:
            return False
        allowed = {c.strip() for c in os.environ.get("DISCORD_ALLOWED_CHANNELS", "").split(",") if c.strip()}
        return not allowed or "*" in allowed or bool(channel_ids & allowed)

    def _author_allowed(self, message) -> bool:  # noqa: ANN001
        if self.allowed and str(message.author.id) in self.allowed:
            return True
        if self.allowed_roles:
            roles = getattr(message.author, "roles", None) or []
            role_ids = {str(getattr(role, "id", "") or "") for role in roles}
            if role_ids & self.allowed_roles:
                return True
        return not self.allowed and not self.allowed_roles

    def _trigger_allowed(self, message, client) -> bool:  # noqa: ANN001
        if not getattr(message, "guild", None):
            return True
        mode = self.trigger_mode
        if mode in {"", "all", "always", "true", "1", "yes"}:
            return True
        content = str(getattr(message, "content", "") or "")
        if mode in {"command", "commands"}:
            return content.lstrip().startswith(("/", "!"))
        if mode in {"addressed", "mention", "mentions", "reply", "replies"}:
            return (
                content.lstrip().startswith(("/", "!"))
                or self._mentions_self(message, client)
                or self._is_reply_to_self(message, client)
            )
        return True

    def _mentions_self(self, message, client) -> bool:  # noqa: ANN001
        user = getattr(client, "user", None)
        if user is not None and user in (getattr(message, "mentions", []) or []):
            return True
        uid = str(getattr(user, "id", "") or "")
        content = str(getattr(message, "content", "") or "")
        return bool(uid and (f"<@{uid}>" in content or f"<@!{uid}>" in content))

    def _is_reply_to_self(self, message, client) -> bool:  # noqa: ANN001
        user = getattr(client, "user", None)
        uid = str(getattr(user, "id", "") or "")
        reference = getattr(message, "reference", None)
        replied = getattr(reference, "resolved", None) if reference is not None else None
        author = getattr(replied, "author", None)
        return bool(uid and str(getattr(author, "id", "") or "") == uid)

    def _strip_own_mentions(self, content: str, client) -> str:  # noqa: ANN001
        user = getattr(client, "user", None)
        uid = str(getattr(user, "id", "") or "")
        if not uid:
            return content
        return content.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "").strip()

    def _attachments_from_message(self, message) -> list[dict]:  # noqa: ANN001
        rows: list[dict] = []
        for attachment in getattr(message, "attachments", []) or []:
            content_type = str(getattr(attachment, "content_type", "") or "").strip()
            filename = str(getattr(attachment, "filename", "") or "").strip()
            media_type = content_type.split("/", 1)[0] if "/" in content_type else content_type
            if not media_type and filename.lower().endswith((".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".aac")):
                media_type = "audio"
            row = {
                "id": str(getattr(attachment, "id", "") or ""),
                "type": content_type or media_type or "file",
                "media_type": content_type,
                "filename": filename,
                "url": str(getattr(attachment, "url", "") or ""),
                "proxy_url": str(getattr(attachment, "proxy_url", "") or ""),
                "size": int(getattr(attachment, "size", 0) or 0),
            }
            description = str(getattr(attachment, "description", "") or "").strip()
            if description:
                row["description"] = description
            rows.append(row)
        return rows

    def _chat_and_thread_ids(self, message) -> tuple[str, str | None]:  # noqa: ANN001
        channel = message.channel
        channel_id = str(getattr(channel, "id", "") or "")
        parent = getattr(channel, "parent", None)
        if parent is not None:
            parent_id = str(getattr(parent, "id", "") or "")
            if parent_id and parent_id != channel_id:
                return parent_id, channel_id
        return channel_id, None

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None:
            return

        async def send_all():
            channel = await self._discord_target_channel(chat_id, metadata)
            for chunk in chunk_text_by_units(text, limit=1900):
                if chunk:
                    await self._discord_send_text(channel, chunk)

        try:
            asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)
        except Exception:  # noqa: BLE001
            pass

    async def _discord_target_channel(self, chat_id: str, metadata: dict | None = None):  # noqa: ANN001
        client = getattr(self, "_client", None)
        if client is None:
            raise RuntimeError("discord client is not started")
        target_id = str((metadata or {}).get("thread_id") or chat_id)
        channel = client.get_channel(int(target_id)) if target_id.isdigit() else None
        if channel is None:
            channel = await client.fetch_channel(int(target_id))
        return channel

    def send_media(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        *,
        metadata: dict | None = None,
        **_kwargs,
    ) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None:
            return

        async def send_all():
            import os

            import discord

            channel = await self._discord_target_channel(chat_id, metadata)
            if not os.path.exists(path):
                prefix = f"{caption}\n" if caption else ""
                await self._discord_send_text(channel, f"{prefix}(file not found: {path})")
                return
            try:
                kwargs = {"file": discord.File(path)}
                if caption:
                    kwargs["content"] = caption
                try:
                    kwargs["allowed_mentions"] = discord.AllowedMentions.none()
                except Exception:  # noqa: BLE001
                    pass
                await channel.send(**kwargs)
            except Exception:  # noqa: BLE001
                prefix = f"{caption}\n" if caption else ""
                await self._discord_send_text(channel, f"{prefix}📎 {path}")

        try:
            asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)
        except Exception:  # noqa: BLE001
            pass

    def _before_dispatch(self, ev: MessageEvent):
        channel = getattr(ev, "_discord_channel", None)
        loop = getattr(ev, "_discord_loop", None)
        if channel is None or loop is None:
            return None
        try:
            cm = channel.typing()
            asyncio.run_coroutine_threadsafe(cm.__aenter__(), loop).result(timeout=10)
            return cm
        except Exception:  # noqa: BLE001
            return None

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        channel = getattr(ev, "_discord_channel", None)
        loop = getattr(ev, "_discord_loop", None)
        if channel is None or loop is None:
            return

        async def send_all():
            import os

            import discord

            from .base import split_media, tableify
            clean, media = split_media(reply)
            clean = tableify(clean)
            for chunk in chunk_text_by_units(clean, limit=1900):
                if chunk:
                    await self._discord_send_text(channel, chunk)
            for path in media:
                try:
                    allowed, reason = self.filter_media_path(path)
                    if not allowed:
                        await self._discord_send_text(channel, f"📎 blocked media path: {reason}")
                    elif os.path.exists(path):
                        await channel.send(file=discord.File(path))
                    else:
                        await channel.send(f"(file not found: {path})")
                except Exception:  # noqa: BLE001
                    await channel.send(f"📎 {path}")

        try:
            if reply:
                asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if state is not None:
                try:
                    asyncio.run_coroutine_threadsafe(state.__aexit__(None, None, None), loop).result(
                        timeout=10
                    )
                except Exception:  # noqa: BLE001
                    pass

    async def _discord_send_text(self, channel, text: str):  # noqa: ANN001
        try:
            import discord

            allowed_mentions = discord.AllowedMentions.none()
            return await channel.send(text, allowed_mentions=allowed_mentions)
        except Exception:  # noqa: BLE001
            return await channel.send(text)

    def add_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None or not message_id or not reaction:
            return

        async def react():
            channel = await self._discord_target_channel(chat_id, None)
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(reaction)

        try:
            asyncio.run_coroutine_threadsafe(react(), loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(self, chat_id: str, message_id: str, reaction: str) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None or not message_id or not reaction:
            return

        async def unreact():
            channel = await self._discord_target_channel(chat_id, None)
            message = await channel.fetch_message(int(message_id))
            clear = getattr(message, "clear_reaction", None)
            if callable(clear):
                await clear(reaction)
                return
            await message.remove_reaction(reaction, client.user)

        try:
            asyncio.run_coroutine_threadsafe(unreact(), loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
