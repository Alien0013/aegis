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

_ARGUMENT_COMMANDS = {
    "model",
    "provider",
    "reasoning",
    "fast",
    "busy",
    "compress",
    "goal",
    "subgoal",
    "steer",
}


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
    supports_interactive_prompts = True
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
        data["supports_interactive_prompts"] = True
        data["security"] = {
            "allowed_users_configured": bool(self.allowed),
            "allowed_roles_configured": bool(self.allowed_roles),
            "allowed_guilds_configured": bool(self.allowed_guilds),
            "ignored_guilds_configured": bool(self.ignored_guilds),
            "trigger_mode": self.trigger_mode,
        }
        return data

    def command_menu(self, *, max_commands: int = MAX_DISCORD_APP_COMMANDS) -> list[str]:
        return discord_application_command_menu(self._extra_commands(), max_commands=max_commands)

    def _extra_commands(self) -> list[str]:
        config = getattr(self, "_config", None)
        if config is None:
            return []
        try:
            return list(config.get("gateway.user_commands", []) or [])
        except Exception:  # noqa: BLE001
            return []

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
        command_tree = self._build_command_tree(discord, client)

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
            attachments = self._attachments_from_message(message)
            if not content.strip() and attachments:
                content = self._attachment_reference_text(attachments)
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
                attachments=attachments,
                metadata={
                    "guild_id": str(getattr(getattr(message, "guild", None), "id", "") or ""),
                    "channel_id": str(getattr(message.channel, "id", "") or ""),
                    "channel_name": str(getattr(message.channel, "name", "") or ""),
                },
            )
            ev._discord_channel = message.channel
            ev._discord_loop = self._loop
            self._submit_inbound(ev, raw_text=raw_content)

        @client.event
        async def on_ready():  # noqa: ANN001
            if command_tree is None:
                return
            try:
                await command_tree.sync()
            except Exception:  # noqa: BLE001 - slash command sync should not kill the gateway
                pass

        client.run(self.token, log_handler=None)

    def _build_command_tree(self, discord, client):  # noqa: ANN001
        try:
            tree = discord.app_commands.CommandTree(client)
        except Exception:  # noqa: BLE001
            return None
        for command_name in self.command_menu():
            name = command_name.lstrip("/")

            def make_callback(slug: str):
                if slug in _ARGUMENT_COMMANDS:
                    async def callback(interaction, args: str = ""):  # noqa: ANN001
                        await self._handle_app_command(interaction, slug, args=args)
                else:
                    async def callback(interaction):  # noqa: ANN001
                        await self._handle_app_command(interaction, slug)

                callback.__name__ = f"aegis_{slug.replace('-', '_')}"
                describe = getattr(getattr(discord, "app_commands", None), "describe", None)
                if slug in _ARGUMENT_COMMANDS and callable(describe):
                    try:
                        callback = describe(args="Optional text for the AEGIS command.")(callback)
                    except Exception:  # noqa: BLE001
                        pass
                return callback

            try:
                tree.command(name=name, description=f"AEGIS {name}")(make_callback(name))
            except Exception:  # noqa: BLE001
                continue
        self._command_tree = tree
        return tree

    async def _handle_app_command(
        self,
        interaction,
        command_name: str,
        *,
        args: str = "",
    ) -> MessageEvent | None:  # noqa: ANN001
        self._loop = asyncio.get_event_loop()
        if not self._interaction_allowed(interaction):
            await self._deny_interaction(interaction)
            return None
        response = getattr(interaction, "response", None)
        defer = getattr(response, "defer", None)
        if callable(defer):
            try:
                await defer(thinking=True)
            except TypeError:
                await defer()
            except Exception:  # noqa: BLE001
                pass
        channel = getattr(interaction, "channel", None)
        chat_id, thread_id = self._chat_and_thread_ids_from_channel(channel)
        user = getattr(interaction, "user", None)
        guild = getattr(interaction, "guild", None)
        command = f"/{str(command_name or '').lstrip('/')}"
        arg_text = str(args or self._interaction_argument_text(interaction) or "").strip()
        raw_text = f"{command} {arg_text}".strip()
        text = normalize_inbound_command(raw_text, platform="discord")
        ev = MessageEvent(
            platform="discord",
            chat_id=chat_id,
            text=text,
            user_id=str(getattr(user, "id", "") or "") or None,
            user_name=str(user or "") or None,
            message_id=str(getattr(interaction, "id", "") or "") or None,
            timestamp=getattr(interaction, "created_at", None),
            thread_id=thread_id,
            metadata={
                "guild_id": str(getattr(guild, "id", "") or ""),
                "channel_id": str(getattr(channel, "id", "") or ""),
                "channel_name": str(getattr(channel, "name", "") or ""),
                "command": command,
                "args": arg_text,
                "source": "app_command",
            },
        )
        ev._discord_channel = channel
        ev._discord_interaction = interaction
        ev._discord_loop = self._loop
        self._submit_inbound(ev, raw_text=ev.text)
        return ev

    def _interaction_argument_text(self, interaction) -> str:  # noqa: ANN001
        namespace = getattr(interaction, "namespace", None)
        for name in ("args", "arg", "text", "value", "query"):
            value = getattr(namespace, name, None) if namespace is not None else None
            if value not in (None, ""):
                return str(value)
        data = getattr(interaction, "data", None)
        options = data.get("options") if isinstance(data, dict) else None
        if isinstance(options, list):
            for option in options:
                if not isinstance(option, dict):
                    continue
                name = str(option.get("name") or "").strip().lower()
                if name in {"args", "arg", "text", "value", "query"}:
                    value = option.get("value")
                    if value not in (None, ""):
                        return str(value)
        return ""

    async def _deny_interaction(self, interaction) -> None:  # noqa: ANN001
        response = getattr(interaction, "response", None)
        send_message = getattr(response, "send_message", None)
        if not callable(send_message):
            return
        try:
            await send_message("Not authorized.", ephemeral=True)
        except TypeError:
            try:
                await send_message("Not authorized.")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    async def _ack_interaction(self, interaction) -> None:  # noqa: ANN001
        response = getattr(interaction, "response", None)
        defer = getattr(response, "defer", None)
        if not callable(defer):
            return
        try:
            await defer()
        except TypeError:
            try:
                await defer(thinking=False)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    async def _handle_component_interaction(
        self,
        interaction,
        value: str,
        *,
        action_id: str,
        action_type: str,
    ) -> MessageEvent | None:  # noqa: ANN001
        self._loop = asyncio.get_event_loop()
        if not self._interaction_allowed(interaction):
            await self._deny_interaction(interaction)
            return None
        await self._ack_interaction(interaction)
        channel = getattr(interaction, "channel", None)
        chat_id, thread_id = self._chat_and_thread_ids_from_channel(channel)
        user = getattr(interaction, "user", None)
        guild = getattr(interaction, "guild", None)
        text = normalize_inbound_command(str(value or ""), platform="discord")
        ev = MessageEvent(
            platform="discord",
            chat_id=chat_id,
            text=text,
            user_id=str(getattr(user, "id", "") or "") or None,
            user_name=str(user or "") or None,
            message_id=str(getattr(interaction, "id", "") or "") or None,
            timestamp=getattr(interaction, "created_at", None),
            thread_id=thread_id,
            metadata={
                "guild_id": str(getattr(guild, "id", "") or ""),
                "channel_id": str(getattr(channel, "id", "") or ""),
                "channel_name": str(getattr(channel, "name", "") or ""),
                "action_id": action_id,
                "action_type": action_type,
                "source": "component_interaction",
            },
        )
        ev._discord_channel = channel
        ev._discord_interaction = interaction
        ev._discord_loop = self._loop
        self._submit_inbound(ev, raw_text=str(value or ""))
        return ev

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
        return self._guild_id_allowed(guild_id)

    def _guild_id_allowed(self, guild_id: str) -> bool:
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
        return self._channel_ids_allowed(self._channel_ids(getattr(message, "channel", None)))

    def _channel_ids(self, channel) -> set[str]:  # noqa: ANN001
        channel_ids = {str(getattr(channel, "id", "") or "")}
        parent = getattr(channel, "parent", None)
        if parent is not None:
            channel_ids.add(str(getattr(parent, "id", "") or ""))
        channel_ids.discard("")
        return channel_ids

    def _channel_ids_allowed(self, channel_ids: set[str]) -> bool:
        ignored = {c.strip() for c in os.environ.get("DISCORD_IGNORED_CHANNELS", "").split(",") if c.strip()}
        if "*" in ignored or channel_ids & ignored:
            return False
        allowed = {c.strip() for c in os.environ.get("DISCORD_ALLOWED_CHANNELS", "").split(",") if c.strip()}
        return not allowed or "*" in allowed or bool(channel_ids & allowed)

    def _author_allowed(self, message) -> bool:  # noqa: ANN001
        return self._author_identity_allowed(getattr(message, "author", None))

    def _author_identity_allowed(self, user) -> bool:  # noqa: ANN001
        if self.allowed and str(getattr(user, "id", "") or "") in self.allowed:
            return True
        if self.allowed_roles:
            roles = getattr(user, "roles", None) or []
            role_ids = {str(getattr(role, "id", "") or "") for role in roles}
            if role_ids & self.allowed_roles:
                return True
        return not self.allowed and not self.allowed_roles

    def _interaction_allowed(self, interaction) -> bool:  # noqa: ANN001
        guild = getattr(interaction, "guild", None)
        guild_id = str(getattr(guild, "id", "") or "")
        if not self._guild_id_allowed(guild_id):
            return False
        if not self._channel_ids_allowed(self._channel_ids(getattr(interaction, "channel", None))):
            return False
        user = getattr(interaction, "user", None)
        if getattr(user, "bot", False):
            mode = os.environ.get("DISCORD_ALLOW_BOTS", "none").strip().lower()
            if mode not in {"all", "true", "1", "yes"}:
                return False
        return self._author_identity_allowed(user)

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

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("id") or "file").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

    def _chat_and_thread_ids(self, message) -> tuple[str, str | None]:  # noqa: ANN001
        return self._chat_and_thread_ids_from_channel(message.channel)

    def _chat_and_thread_ids_from_channel(self, channel) -> tuple[str, str | None]:  # noqa: ANN001
        channel_id = str(getattr(channel, "id", "") or "")
        parent = getattr(channel, "parent", None)
        if parent is not None and self._is_thread_channel(channel):
            parent_id = str(getattr(parent, "id", "") or "")
            if parent_id and parent_id != channel_id:
                return parent_id, channel_id
        return channel_id, None

    def _is_thread_channel(self, channel) -> bool:  # noqa: ANN001
        cls_name = channel.__class__.__name__.lower()
        type_value = getattr(channel, "type", None)
        type_name = str(getattr(type_value, "name", type_value) or "").lower()
        if "thread" in cls_name or "thread" in type_name:
            return True
        return bool(
            getattr(channel, "parent_id", None)
            and hasattr(channel, "owner_id")
            and hasattr(channel, "message_count")
        )

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None:
            raise RuntimeError("discord client is not started")

        async def send_all():
            channel = await self._discord_target_channel(chat_id, metadata)
            for chunk in chunk_text_by_units(text, limit=1900):
                if chunk:
                    await self._discord_send_text(channel, chunk)

        asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)

    async def _discord_target_channel(self, chat_id: str, metadata: dict | None = None):  # noqa: ANN001
        client = getattr(self, "_client", None)
        if client is None:
            raise RuntimeError("discord client is not started")
        target_id = str((metadata or {}).get("thread_id") or chat_id)
        try:
            return await self._fetch_discord_channel(target_id)
        except Exception as exc:  # noqa: BLE001
            if target_id == str(chat_id or ""):
                raise
            try:
                return await self._fetch_discord_channel(str(chat_id or ""))
            except Exception:  # noqa: BLE001
                raise exc

    async def _fetch_discord_channel(self, channel_id: str):  # noqa: ANN001
        client = getattr(self, "_client", None)
        if client is None:
            raise RuntimeError("discord client is not started")
        if not str(channel_id or "").strip():
            raise RuntimeError("discord channel id is empty")
        channel_id = str(channel_id or "").strip()
        channel = client.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if channel is None:
            channel = await client.fetch_channel(int(channel_id))
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
            raise RuntimeError("discord client is not started")

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

        asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)

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
        interaction = getattr(ev, "_discord_interaction", None)
        loop = getattr(ev, "_discord_loop", None)
        if (channel is None and interaction is None) or loop is None:
            return

        async def send_all():
            import os

            import discord

            from .base import split_media, tableify
            clean, media = split_media(reply)
            clean = tableify(clean)
            for chunk in chunk_text_by_units(clean, limit=1900):
                if chunk:
                    if interaction is not None:
                        await self._discord_send_interaction(interaction, chunk)
                    else:
                        await self._discord_send_text(channel, chunk)
            for path in media:
                try:
                    allowed, reason = self.filter_media_path(path)
                    if not allowed:
                        text = f"📎 blocked media path: {reason}"
                        if interaction is not None:
                            await self._discord_send_interaction(interaction, text)
                        else:
                            await self._discord_send_text(channel, text)
                    elif os.path.exists(path):
                        if interaction is not None:
                            await self._discord_send_interaction(interaction, file=discord.File(path))
                        else:
                            await channel.send(file=discord.File(path))
                    else:
                        text = f"(file not found: {path})"
                        if interaction is not None:
                            await self._discord_send_interaction(interaction, text)
                        else:
                            await channel.send(text)
                except Exception:  # noqa: BLE001
                    text = f"📎 {path}"
                    if interaction is not None:
                        await self._discord_send_interaction(interaction, text)
                    else:
                        await channel.send(text)

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

    async def _discord_send_interaction(self, interaction, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        try:
            import discord

            kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        except Exception:  # noqa: BLE001
            pass
        followup = getattr(interaction, "followup", None)
        send = getattr(followup, "send", None)
        if callable(send):
            try:
                return await send(*args, **kwargs)
            except TypeError:
                kwargs.pop("allowed_mentions", None)
                return await send(*args, **kwargs)
        edit = getattr(interaction, "edit_original_response", None)
        if callable(edit):
            if args and "content" not in kwargs:
                kwargs["content"] = args[0]
                args = args[1:]
            try:
                return await edit(*args, **kwargs)
            except TypeError:
                kwargs.pop("allowed_mentions", None)
                return await edit(*args, **kwargs)
        channel = getattr(interaction, "channel", None)
        if channel is not None and args:
            return await self._discord_send_text(channel, str(args[0]))
        if channel is not None and kwargs.get("content"):
            return await self._discord_send_text(channel, str(kwargs.get("content") or ""))
        return None

    def _button_style(self, discord, style: str):  # noqa: ANN001
        styles = getattr(discord, "ButtonStyle", None)
        if styles is None:
            return style
        return getattr(styles, style, style)

    def _build_prompt_view(self, discord, entries: list[tuple[str, str, str]], action_type: str):  # noqa: ANN001
        view = discord.ui.View(timeout=None)
        for index, (label, value, style) in enumerate(entries[:5]):
            action_id = f"aegis_{action_type}_{index}"
            button = discord.ui.Button(
                label=str(label or value)[:80] or "Choose",
                style=self._button_style(discord, style),
                custom_id=action_id,
            )

            async def callback(interaction, *, answer=str(value or ""), aid=action_id):  # noqa: ANN001
                await self._handle_component_interaction(
                    interaction,
                    answer,
                    action_id=aid,
                    action_type=action_type,
                )

            button.callback = callback
            view.add_item(button)
        return view

    def _send_prompt_view(
        self,
        chat_id: str,
        text: str,
        entries: list[tuple[str, str, str]],
        action_type: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None:
            raise RuntimeError("discord client is not started")

        async def send_all():
            import discord

            channel = await self._discord_target_channel(chat_id, metadata)
            view = self._build_prompt_view(discord, entries, action_type)
            kwargs = {"view": view}
            try:
                kwargs["allowed_mentions"] = discord.AllowedMentions.none()
            except Exception:  # noqa: BLE001
                pass
            await channel.send(text, **kwargs)

        asyncio.run_coroutine_threadsafe(send_all(), loop).result(timeout=60)

    def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: list[str] | None = None,
        *,
        metadata: dict | None = None,
    ) -> None:
        choice_values = [str(choice).strip() for choice in (choices or []) if str(choice).strip()]
        if not choice_values:
            return super().send_clarify(chat_id, question, choices or [], metadata=metadata)
        try:
            self._send_prompt_view(
                chat_id,
                str(question or "").strip() or "Choose one:",
                [(choice, choice, "secondary") for choice in choice_values],
                "clarify",
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            super().send_clarify(chat_id, question, choices or [], metadata=metadata)

    def send_exec_approval(
        self,
        chat_id: str,
        prompt: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        try:
            self._send_prompt_view(
                chat_id,
                str(prompt or "").strip() or "Approve this action?",
                [
                    ("Approve", "approve", "success"),
                    ("Always", "always", "primary"),
                    ("Deny", "deny", "danger"),
                ],
                "exec_approval",
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            super().send_exec_approval(chat_id, prompt, metadata=metadata)

    def add_reaction(
        self,
        chat_id: str,
        message_id: str,
        reaction: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None or not message_id or not reaction:
            return

        async def react():
            channel = await self._discord_target_channel(chat_id, metadata)
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(reaction)

        try:
            asyncio.run_coroutine_threadsafe(react(), loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass

    def remove_reaction(
        self,
        chat_id: str,
        message_id: str,
        reaction: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        client = getattr(self, "_client", None)
        loop = getattr(self, "_loop", None)
        if client is None or loop is None or not message_id or not reaction:
            return

        async def unreact():
            channel = await self._discord_target_channel(chat_id, metadata)
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
