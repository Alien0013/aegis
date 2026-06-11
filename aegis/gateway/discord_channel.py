"""Discord channel adapter (requires `discord.py`)."""

from __future__ import annotations

import asyncio
import os

from .base import BasePlatformAdapter, Dispatch, MessageEvent


class DiscordAdapter(BasePlatformAdapter):
    name = "discord"
    renders_tables = False

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN")
        if not self.token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
        allowed = os.environ.get("DISCORD_ALLOWED_USERS", "").strip()
        self.allowed = {u.strip() for u in allowed.split(",") if u.strip()} if allowed else None

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
            if self.allowed and str(message.author.id) not in self.allowed:
                return
            ev = MessageEvent(
                platform="discord", chat_id=str(message.channel.id),
                text=message.content, user_id=str(message.author.id),
                user_name=str(message.author),
            )
            setattr(ev, "_discord_channel", message.channel)
            setattr(ev, "_discord_loop", self._loop)
            self._submit_inbound(ev, raw_text=message.content)

        client.run(self.token, log_handler=None)

    def send(self, chat_id: str, text: str) -> None:  # replies happen inline
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
            for i in range(0, len(clean), 1900):
                chunk = clean[i:i + 1900]
                if chunk:
                    await channel.send(chunk)
            for path in media:
                try:
                    if os.path.exists(path):
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
