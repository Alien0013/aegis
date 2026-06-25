"""Dashboard/desktop chat: a client disconnect (e.g. switching views) must NOT cancel
the in-flight run when dashboard.detach_on_disconnect is on — the run finishes in the
background and persists. Legacy mode (flag off) still cancels on disconnect."""

import asyncio
import threading
import time
from types import SimpleNamespace

from aegis import dashboard_fastapi as dfa
from aegis.config import Config


def _drive_disconnect(monkeypatch, *, detach: bool) -> dict:
    state = {"cancel_called": False}
    finished = threading.Event()

    class FakeAgent:
        def __init__(self):
            self.session = SimpleNamespace(id="sess-detach", meta={})
            self.cancel_event = threading.Event()

        def cancel(self):
            state["cancel_called"] = True
            self.cancel_event.set()

    def fake_stream(body, chat_runner, emit, on_agent=None, cancel_event=None, **kw):
        agent = FakeAgent()
        if on_agent:
            on_agent(agent)
        # Detach mode: never cancelled, so this waits out the grace and returns.
        # Legacy mode: the backend cancels, releasing the wait early.
        agent.cancel_event.wait(0.8)
        finished.set()
        return {"reply": "ok"}

    monkeypatch.setattr(dfa.dash, "_dashboard_chat_stream", fake_stream)

    class FakeRequest:
        async def is_disconnected(self):
            return True            # client navigated away immediately

    cfg = Config.load()
    cfg.data["dashboard"]["detach_on_disconnect"] = detach
    resp = dfa._dashboard_chat_streaming_response(
        {"session_id": "sess-detach"}, lambda *a, **k: None, FakeRequest(), cfg
    )

    async def drain():
        async for _ in resp.body_iterator:
            pass

    asyncio.run(drain())
    finished.wait(2.0)
    time.sleep(0.05)
    return state


def test_detach_keeps_run_alive_on_disconnect(monkeypatch):
    state = _drive_disconnect(monkeypatch, detach=True)
    assert state["cancel_called"] is False


def test_legacy_cancels_run_on_disconnect(monkeypatch):
    state = _drive_disconnect(monkeypatch, detach=False)
    assert state["cancel_called"] is True
