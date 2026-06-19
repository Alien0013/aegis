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
    renders_tables = False
    transport = "matrix_sync"
    supports_threads = True

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
        self._init_inbound_queue(dispatch)
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
                thread_id = self._thread_id_from_event(event)
                ev = MessageEvent(
                    platform="matrix", chat_id=room.room_id,
                    text=event.body, user_id=event.sender,
                    user_name=room.user_name(event.sender),
                    thread_id=thread_id,
                    message_id=str(getattr(event, "event_id", "") or "") or None,
                    timestamp=getattr(event, "server_timestamp", None),
                    metadata={
                        "room_id": room.room_id,
                        "event_id": str(getattr(event, "event_id", "") or ""),
                        "thread_id": thread_id,
                    },
                )
                self._submit_inbound(ev)

            client.add_event_callback(on_message, RoomMessageText)
            try:
                await client.sync_forever(timeout=30000, full_state=True)
            finally:
                await client.close()

        asyncio.run(_run())

    def _thread_id_from_event(self, event) -> str | None:  # noqa: ANN001
        source = getattr(event, "source", None)
        content = source.get("content") if isinstance(source, dict) else getattr(event, "content", None)
        relates = content.get("m.relates_to") if isinstance(content, dict) else None
        if not isinstance(relates, dict):
            return None
        rel_type = str(relates.get("rel_type") or "")
        if rel_type != "m.thread":
            return None
        return str(relates.get("event_id") or "").strip() or None

    def _message_content(self, text: str, metadata: dict | None = None) -> dict:
        content = {"msgtype": "m.text", "body": text}
        thread_id = str((metadata or {}).get("thread_id") or "").strip()
        if thread_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_id,
                "is_falling_back": True,
            }
        return content

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        client, loop = self._client, self._loop
        if client is None or loop is None:
            return
        coro = client.room_send(
            room_id=chat_id,
            message_type="m.room.message",
            content=self._message_content(text, metadata),
        )
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
