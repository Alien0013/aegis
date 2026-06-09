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
        try:
            import discord
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("discord channel needs `pip install discord.py`") from e

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_message(message):  # noqa: ANN001
            if message.author == client.user:
                return
            if self.allowed and str(message.author.id) not in self.allowed:
                return
            ev = MessageEvent(
                platform="discord", chat_id=str(message.channel.id),
                text=message.content, user_id=str(message.author.id),
                user_name=str(message.author),
            )
            loop = asyncio.get_event_loop()
            async with message.channel.typing():       # show "typing…" while the agent works
                reply = await loop.run_in_executor(None, dispatch, ev)
            if reply:
                import os

                import discord

                from .base import split_media, tableify
                clean, media = split_media(reply)
                clean = tableify(clean)               # Discord doesn't render pipe tables
                for i in range(0, len(clean), 1900):
                    chunk = clean[i:i + 1900]
                    if chunk:
                        await message.channel.send(chunk)
                for path in media:                       # native file attachments
                    try:
                        if os.path.exists(path):
                            await message.channel.send(file=discord.File(path))
                        else:
                            await message.channel.send(f"(file not found: {path})")
                    except Exception:  # noqa: BLE001
                        await message.channel.send(f"📎 {path}")

        client.run(self.token, log_handler=None)

    def send(self, chat_id: str, text: str) -> None:  # replies happen inline
        pass
