"""Cross-surface /handoff, kanban worker lanes, mixture-of-agents."""

from __future__ import annotations


def test_handoff_set_pop_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import handoff
    assert handoff.pop_handoff("telegram", "123") is None       # nothing pending, no file
    handoff.set_handoff("telegram", "123", "sess_abc")
    assert handoff.pop_handoff("telegram", "999") is None       # other chat untouched
    assert handoff.pop_handoff("telegram", "123") == "sess_abc"
    assert handoff.pop_handoff("telegram", "123") is None       # consumed exactly once


def test_kanban_lane_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.kanban import KanbanStore
    ks = KanbanStore()
    free = ks.create("anyone can take this")
    pinned = ks.create("lane-2 only")
    # pre-assign while ready -> pins the card to lane-2
    with ks._conn() as c:
        c.execute("UPDATE tasks SET assignee='lane-2' WHERE id=?", (pinned.id,))
    t1 = ks.claim_next("lane-1", lane="lane-1")
    assert t1 is not None and t1.id == free.id                  # lane-1 skips the pinned card
    assert ks.claim_next("lane-1", lane="lane-1") is None       # nothing else for lane-1
    t2 = ks.claim_next("lane-2", lane="lane-2")
    assert t2 is not None and t2.id == pinned.id


def test_run_board_parallel_lanes(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import kanban_auto
    from aegis.config import Config
    from aegis.kanban import KanbanStore
    from aegis.agent.agent import Agent
    from aegis.types import Message
    ks = KanbanStore()
    for i in range(4):
        ks.create(f"task {i}")
    monkeypatch.setattr(Agent, "run", lambda self, prompt, on_event=None: Message.assistant("done"))
    done = kanban_auto.run_board(Config.load(), store=ks, workers=2)
    assert len(done) == 4
    assert all(t.status == "done" for t in ks.list())


def test_mixture_tool_fans_and_synthesizes(tmp_path, monkeypatch):
    from aegis.tools.agentic import MixtureTool
    from aegis.tools.base import ToolContext
    from aegis.config import Config

    class FakeProv:
        name, model = "fake", "fake-1"

        def __init__(self, reply):
            self.reply = reply

        def complete(self, messages, tools=None, stream=False, **kw):
            class R:  # minimal response shape
                text = self.reply
            return R()

    calls = []

    def fake_build(config, model=None, name=None):
        calls.append((name, model))
        return FakeProv(f"answer from {model or 'main'}")

    monkeypatch.setattr("aegis.providers.fallback.build_with_fallbacks", fake_build)
    r = MixtureTool().run({"prompt": "Q?", "models": ["m1", "m2"]},
                          ToolContext(cwd=tmp_path, config=Config.load()))
    assert not r.is_error
    assert "# Synthesis" in r.content and "## m1" in r.content and "## m2" in r.content
    assert ("answer from m1" in r.content) and ("answer from m2" in r.content)
    r2 = MixtureTool().run({"prompt": "Q?", "models": ["only-one"]},
                           ToolContext(cwd=tmp_path, config=Config.load()))
    assert r2.is_error
