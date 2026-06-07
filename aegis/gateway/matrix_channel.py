"""Matrix channel adapter via `matrix-nio` (requires `pip install matrix-nio`).

Needs MATRIX_HOMESERVER (e.g. https://matrix.org), MATRIX_USER (full id like
`@bot:matrix.org`) and MATRIX_PASSWORD. Logs in, runs a sync loop, and turns
each inbound room message into a :class:`MessageEvent` for the dispatcher,
posting the reply back into the originating room.
"""

from __future__ import annotations

import asyncio
import os

from .base import BasePlatformAdapter, Dispatch, MessageEvent


class MatrixAdapter(BasePlatformAdapter):
    name = "matrix"

    def __init__(self):
        self.homeserver = os.environ.get("MATRIX_HOMESERVER")
        self.user = os.environ.get("MATRIX_USER")
        self.password = os.environ.get("MATRIX_PASSWORD")
        if not self.homeserver or not self.user or not self.password:
            raise RuntimeError(
                "matrix channel needs MATRIX_HOMESERVER, MATRIX_USER and MATRIX_PASSWORD."
            )
        self._client = None  # set once logged in, used by send()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, dispatch: Dispatch) -> None:
        try:
            from nio import AsyncClient, RoomMessageText
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("matrix channel needs `pip install matrix-nio`") from e

        async def _run() -> None:
            client = AsyncClient(self.homeserver, self.user)
            self._client = client
            self._loop = asyncio.get_event_loop()

            resp = await client.login(self.password)
            if getattr(resp, "access_token", None) is None:
                raise RuntimeError(f"matrix login failed: {resp}")
            # Skip backlog: only react to messages after we start syncing.
            await client.sync(timeout=30000)

            async def on_message(room, event):  # noqa: ANN001
                if event.sender == client.user_id:
                    return  # ignore our own echoes to avoid loops
                ev = MessageEvent(
                    platform="matrix", chat_id=room.room_id,
                    text=event.body, user_id=event.sender,
                    user_name=room.user_name(event.sender),
                )
                reply = await asyncio.get_event_loop().run_in_executor(None, dispatch, ev)
                if reply:
                    await client.room_send(
                        room_id=room.room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": reply},
                    )

            client.add_event_callback(on_message, RoomMessageText)
            try:
                await client.sync_forever(timeout=30000, full_state=True)
            finally:
                await client.close()

        asyncio.run(_run())

    def send(self, chat_id: str, text: str) -> None:
        client, loop = self._client, self._loop
        if client is None or loop is None:
            return
        coro = client.room_send(
            room_id=chat_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
        )
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
