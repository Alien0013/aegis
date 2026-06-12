"""Background-completion wakeups: queued notes reach the next agent turn."""

from __future__ import annotations


def test_add_and_drain_roundtrip():
    from aegis.agent import wakeups
    wakeups.drain_wakeups()                       # start clean
    wakeups.add_wakeup("process", "proc_1 exited (code 0): sleep 1", "done")
    wakeups.add_wakeup("subagent", "bg_2: research task", "found 3 results")
    notes = wakeups.drain_wakeups()
    assert [n["source"] for n in notes] == ["process", "subagent"]
    assert wakeups.drain_wakeups() == []          # drained exactly once


def test_drain_wakeups_can_filter_by_source():
    from aegis.agent import wakeups
    wakeups.drain_wakeups()
    wakeups.add_wakeup("process", "proc", "done")
    wakeups.add_wakeup("subagent", "agent", "done")

    notes = wakeups.drain_wakeups(source="process")

    assert [n["source"] for n in notes] == ["process"]
    assert [n["source"] for n in wakeups.drain_wakeups()] == ["subagent"]


def test_wakeups_can_filter_by_session_key():
    from aegis.agent import wakeups
    wakeups.drain_wakeups()
    wakeups.add_wakeup("process", "proc-a", "done-a", session_key="sess-a")
    wakeups.add_wakeup("process", "proc-b", "done-b", session_key="sess-b")
    wakeups.add_wakeup("subagent", "legacy", "done-global")

    block = wakeups.wakeup_block(session_key="sess-a")

    assert "proc-a" in block
    assert "legacy" in block
    assert "proc-b" not in block
    assert [n["title"] for n in wakeups.drain_wakeups()] == ["proc-b"]


def test_wakeup_block_format_and_cap():
    from aegis.agent import wakeups
    wakeups.drain_wakeups()
    assert wakeups.wakeup_block() == ""           # empty queue -> no block
    for i in range(15):
        wakeups.add_wakeup("process", f"p{i}", "x" * 5000)
    block = wakeups.wakeup_block()
    assert block.startswith("<background_completions>")
    assert "untrusted" in block                   # injection defense framing
    assert block.count("[process]") == 10         # capped per turn
    assert "x" * 2001 not in block                # per-note payload cap


def test_process_completion_queues_wakeup(tmp_path):
    import time
    from aegis.agent import wakeups
    from aegis.tools.process import ProcessTool
    from aegis.tools.base import ToolContext
    wakeups.drain_wakeups()
    r = ProcessTool().run({"action": "start", "command": "echo hello-wakeup"},
                          ToolContext(cwd=tmp_path))
    assert not r.is_error and "notified" in r.content
    for _ in range(50):                            # watcher fires shortly after exit
        notes = wakeups.drain_wakeups()
        if notes:
            break
        time.sleep(0.1)
    assert notes and notes[0]["source"] == "process"
    assert "hello-wakeup" in notes[0]["text"]


def test_agent_run_folds_wakeups(monkeypatch):
    from aegis.agent import wakeups
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import Message
    wakeups.drain_wakeups()
    wakeups.add_wakeup("process", "proc_9 exited (code 0)", "build ok")
    agent = Agent.create(Config.load(), session=Session.create())
    seen = {}

    def fake_conversation(a, on_event=None):
        seen["user"] = a.session.messages[-1].content
        return Message.assistant("ok")

    monkeypatch.setattr("aegis.agent.agent.run_conversation", fake_conversation)
    agent.run("continue please")
    assert "<background_completions>" in seen["user"]
    assert "proc_9" in seen["user"] and "continue please" in seen["user"]


def test_agent_run_keeps_other_session_wakeups_queued(monkeypatch):
    from aegis.agent import wakeups
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import Message
    wakeups.drain_wakeups()
    session = Session(id="sess-a", title="a")
    wakeups.add_wakeup("process", "proc-a", "build ok", session_key="sess-a")
    wakeups.add_wakeup("process", "proc-b", "wrong session", session_key="sess-b")
    agent = Agent.create(Config.load(), session=session)
    seen = {}

    def fake_conversation(a, on_event=None):
        seen["user"] = a.session.messages[-1].content
        return Message.assistant("ok")

    monkeypatch.setattr("aegis.agent.agent.run_conversation", fake_conversation)
    agent.run("continue please")

    assert "proc-a" in seen["user"]
    assert "proc-b" not in seen["user"]
    assert [n["title"] for n in wakeups.drain_wakeups()] == ["proc-b"]


def test_agent_run_can_skip_wakeups_once(monkeypatch):
    from aegis.agent import wakeups
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import Message
    wakeups.drain_wakeups()
    wakeups.add_wakeup("process", "proc_10 exited", "done")
    agent = Agent.create(Config.load(), session=Session.create())
    seen = {}

    def fake_conversation(a, on_event=None):
        seen["user"] = a.session.messages[-1].content
        return Message.assistant("ok")

    monkeypatch.setattr("aegis.agent.agent.run_conversation", fake_conversation)
    agent._skip_wakeups_once = True
    agent.run("synthetic process event")

    assert "<background_completions>" not in seen["user"]
    assert "synthetic process event" in seen["user"]
    assert wakeups.drain_wakeups()[0]["source"] == "process"
