"""Live-run reattach: a session-scoped frame channel lets a client that returns
mid-run replay the backlog then follow the live stream (/api/chat/attach)."""

import asyncio
import json

from aegis.dashboard_fastapi import (
    _LIVE_SENTINEL,
    _LiveRun,
    _dashboard_chat_attach_response,
    _drop_live_run,
    _get_live_run,
    _register_live_run,
)


class _Req:
    def __init__(self, disconnected=False):
        self._d = disconnected

    async def is_disconnected(self):
        return self._d


def _drain(resp):
    async def go():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
        return out
    chunks = asyncio.run(go())
    return [json.loads(c.decode().split("data: ", 1)[1]) for c in chunks]


def test_live_run_backlog_then_stream_then_finish():
    live = _LiveRun(prompt="hi")
    live.publish({"type": "start"})
    live.publish({"type": "event", "event": {"type": "assistant_delta", "text": "a"}})
    sub, backlog, done = live.subscribe()
    assert [f["type"] for f in backlog] == ["start", "event"]
    assert done is False
    live.publish({"type": "final", "reply": "done"})
    assert sub.get_nowait()["type"] == "final"      # live event reaches the subscriber
    live.finish()
    assert sub.get_nowait() is _LIVE_SENTINEL


def test_live_run_registry_roundtrip():
    live = _LiveRun()
    _register_live_run("sX", live)
    assert _get_live_run("sX") is live
    _drop_live_run("sX", live)
    assert _get_live_run("sX") is None


def test_attach_emits_no_active_run_when_nothing_live():
    frames = _drain(_dashboard_chat_attach_response("missing-session", _Req(disconnected=True)))
    assert frames[0]["type"] == "no_active_run"


def test_attach_replays_resume_prompt_and_backlog():
    live = _LiveRun(prompt="P")
    live.publish({"type": "start", "session_id": "sA"})
    live.publish({"type": "event", "event": {"type": "assistant_delta", "text": "hello"}})
    _register_live_run("sA", live)
    live.finish()                                    # run already done -> stream ends after backlog
    try:
        frames = _drain(_dashboard_chat_attach_response("sA", _Req()))
    finally:
        _drop_live_run("sA", live)
    assert frames[0]["type"] == "resume" and frames[0]["prompt"] == "P"
    types = [f["type"] for f in frames]
    assert "start" in types and "event" in types
