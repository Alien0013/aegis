"""MemoryProvider lifecycle hooks, subdirectory hints, .cursorrules, credit telemetry."""

from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    return Config.load()


class RecordingProvider:
    """A memory provider that records every lifecycle hook it receives."""
    name = "recording"

    def __init__(self):
        self.calls = []

    def initialize(self, session_id="", **kw): self.calls.append(("initialize", session_id))
    def system_prompt_block(self): self.calls.append(("system_prompt_block",)); return "PROVIDER MEM"
    def prefetch(self, query, *, session_id=""): self.calls.append(("prefetch", query)); return "FETCHED:" + query[:10]
    def queue_prefetch(self, query, *, session_id=""): self.calls.append(("queue_prefetch",))
    def sync_turn(self, messages): self.calls.append(("sync_turn", len(messages)))
    def tools(self): self.calls.append(("tools",)); return []
    def on_session_end(self, messages): self.calls.append(("on_session_end",))
    def on_pre_compress(self, messages): self.calls.append(("on_pre_compress",)); return "keep this"
    def on_session_switch(self, *, old_session_id, new_session_id, **kw): self.calls.append(("on_session_switch", old_session_id, new_session_id))
    def on_delegation(self, task, result, **kw): self.calls.append(("on_delegation", task))
    def shutdown(self): self.calls.append(("shutdown",))


def test_manager_fans_out_every_hook(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager
    p = RecordingProvider()
    mm = MemoryManager(config, external=p)

    mm.initialize("sess-1")
    assert mm.prefetch("hello world query") == "FETCHED:hello worl"
    mm.queue_prefetch("q")
    mm.sync_turn([1, 2, 3])
    assert mm.provider_tools() == []
    assert mm.on_pre_compress([]) == "keep this"
    mm.on_session_switch("old", "new")
    mm.on_delegation("do a thing", "did it")
    mm.on_session_end([])
    mm.shutdown()
    # provider system_prompt_block flows through build_context_block
    assert "PROVIDER MEM" in mm.build_context_block()

    kinds = [c[0] for c in p.calls]
    for hook in ("initialize", "prefetch", "queue_prefetch", "sync_turn", "tools",
                 "on_pre_compress", "on_session_switch", "on_delegation",
                 "on_session_end", "shutdown", "system_prompt_block"):
        assert hook in kinds, f"{hook} was never fanned out"


def test_external_prompt_block_is_snapshotted_until_refresh(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager

    class SessionBoundProvider:
        def __init__(self):
            self.block = "before init"
            self.prompt_calls = 0

        def initialize(self, session_id="", **kw):
            self.block = f"session-bound {session_id}"

        def system_prompt_block(self):
            self.prompt_calls += 1
            return self.block

    provider = SessionBoundProvider()
    mm = MemoryManager(config, external=provider)
    mm.initialize("sess-1")
    block = mm.build_context_block()

    assert "session-bound sess-1" in block
    provider.block = "changed live"
    assert "changed live" not in mm.build_context_block()
    assert provider.prompt_calls == 2        # construction + initialize, not every render

    mm.refresh_snapshot()
    assert "changed live" in mm.build_context_block()
    assert provider.prompt_calls == 3


def test_external_prompt_block_refreshes_on_session_switch(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager

    class SessionBoundProvider:
        def __init__(self):
            self.session_id = "before"

        def initialize(self, session_id="", **kw):
            self.session_id = session_id

        def on_session_switch(self, *, old_session_id, new_session_id, **kw):
            self.session_id = new_session_id

        def system_prompt_block(self):
            return f"session-bound {self.session_id}"

    mm = MemoryManager(config, external=SessionBoundProvider())
    mm.initialize("old")
    assert "session-bound old" in mm.build_context_block()

    mm.on_session_switch("old", "new")

    block = mm.build_context_block()
    assert "session-bound new" in block
    assert "session-bound old" not in block


def test_hooks_are_fail_soft(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager

    class Boom:
        def initialize(self, session_id="", **kw): raise RuntimeError("x")
        def prefetch(self, q, *, session_id=""): raise RuntimeError("x")
        def on_pre_compress(self, m): raise RuntimeError("x")
    mm = MemoryManager(config, external=Boom())
    mm.initialize("s")               # must not raise
    assert mm.prefetch("q") == ""    # swallowed -> empty
    assert mm.on_pre_compress([]) == ""


def test_shell_session_lifecycle_hooks_fire(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    config.data["memory"]["enabled"] = False
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    calls = []

    def fake_run_hooks(_config, event, context=None):
        calls.append((event, dict(context or {})))
        return []

    monkeypatch.setattr("aegis.hooks.run_hooks", fake_run_hooks)
    provider = FakeProvider([LLMResponse(text="one"), LLMResponse(text="two")])
    agent = Agent(config=config, provider=provider, session=Session.create(), cwd=tmp_path)

    agent.run("first")
    agent.run("second")
    agent.end_session()

    events = [event for event, _context in calls]
    assert events.count("session_start") == 1
    assert events.count("user_prompt") == 2
    assert events.count("session_stop") == 1
    start = next(context for event, context in calls if event == "session_start")
    stop = next(context for event, context in calls if event == "session_stop")
    assert start["session_id"] == agent.session.id
    assert start["provider"] == "fake"
    assert start["model"] == "fake-model"
    assert start["cwd"] == str(tmp_path)
    assert stop["session_id"] == agent.session.id
    assert stop["message_count"] == len(agent.session.messages)


def test_end_session_kills_task_processes(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    config.data["memory"]["enabled"] = False
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from conftest import FakeProvider

    killed = []
    monkeypatch.setattr(
        "aegis.tools.process_registry.process_registry.kill_all",
        lambda task_id=None: killed.append(task_id) or 0,
    )
    agent = Agent(config=config, provider=FakeProvider(), session=Session.create(), cwd=tmp_path)

    agent.end_session()

    assert killed == [agent.tool_context.task_id]


def test_provider_tools_registered_on_agent(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.tools.base import Tool, ToolResult

    class PMemTool(Tool):
        name = "provider_recall"
        description = "x"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx): return ToolResult.ok("ok")

    class P:
        def tools(self): return [PMemTool()]
    mm = MemoryManager(config, external=P())
    agent = Agent.create(config, memory=mm)
    assert agent.registry.get("provider_recall") is not None   # registered at construction


def test_prefetch_is_wire_only_not_persisted(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session
    from aegis.types import LLMResponse

    class P:
        def __init__(self):
            self.queued = ""
            self.synced = []

        def prefetch(self, q, *, session_id=""): return "RELEVANT PAST FACT"
        def queue_prefetch(self, q, *, session_id=""): self.queued = q
        def sync_turn(self, messages): self.synced = list(messages)

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def __init__(self):
            self.wire_user = ""

        def complete(self, messages, **_kwargs):
            self.wire_user = next(m.content for m in reversed(messages) if m.role == "user")
            return LLMResponse(text="done")

    provider = Provider()
    external = P()
    agent = Agent(config=config, provider=provider, session=Session.create(),
                  memory=MemoryManager(config, external=external), cwd=tmp_path)
    agent.run("what did we decide?")

    assert "<retrieved_memory>" in provider.wire_user
    assert "RELEVANT PAST FACT" in provider.wire_user
    user_message = next(m.content for m in agent.session.messages if m.role == "user")
    assert user_message == "what did we decide?"
    assert external.queued == "what did we decide?"
    assert all("<retrieved_memory>" not in m.content for m in agent.session.messages)
    assert all("<retrieved_memory>" not in m.content for m in external.synced)
    recent = agent.memory.history.recent(2)
    assert all("<retrieved_memory>" not in row["content"] for row in recent)


def test_sync_turn_fires_for_empty_assistant_response(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session
    from aegis.types import LLMResponse

    class P:
        def __init__(self):
            self.synced = []

        def sync_turn(self, messages):
            self.synced.append([(m.role, m.content) for m in messages])

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def complete(self, messages, **_kwargs):
            return LLMResponse(text="")

    external = P()
    agent = Agent(config=config, provider=Provider(), session=Session.create(),
                  memory=MemoryManager(config, external=external), cwd=tmp_path)

    result = agent.run("say nothing")

    assert result.content == ""
    assert len(external.synced) == 1
    assert external.synced[0][-2:] == [("user", "say nothing"), ("assistant", "")]
    assert all(row["role"] != "assistant" for row in agent.memory.history.recent(5))


def test_pre_compress_note_reaches_manual_summary_input(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.agent.loop import compact_now
    from aegis.memory import MemoryManager
    from aegis.session import Session
    from aegis.types import LLMResponse, Message

    class P:
        def on_pre_compress(self, _messages):
            return "MANUAL MEMORY FACT"

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def __init__(self):
            self.summary_input = ""

        def complete(self, messages, **_kwargs):
            self.summary_input = messages[-1].content
            return LLMResponse(text="summary without raw memory note")

    provider = Provider()
    session = Session.create()
    session.messages = [Message.system("s")]
    for i in range(8):
        session.messages.append(Message.user(f"old request {i}"))
        session.messages.append(Message.assistant(f"old answer {i}"))
    agent = Agent(config=config, provider=provider, session=session,
                  memory=MemoryManager(config, external=P()), cwd=tmp_path)

    compact_now(agent, preserve_last=1)

    assert "MANUAL MEMORY FACT" in provider.summary_input
    assert all("MANUAL MEMORY FACT" not in (m.content or "") for m in agent.session.messages)


def test_pre_compress_note_passed_to_context_engine(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent import context_engine as ce
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session
    from aegis.types import LLMResponse, Message

    captured = {}

    class CaptureEngine:
        name = "capture-memory-precompress"

        def __init__(self):
            self.done = False

        def should_compress(self, _messages, _context_length, _overhead_tokens=0):
            return not self.done

        def compress(self, messages, _provider, **kw):
            self.done = True
            captured["pre_compress_context"] = kw.get("pre_compress_context")
            return list(messages)

        def tools(self):
            return []

    class P:
        def on_pre_compress(self, _messages):
            return "AUTOMATIC MEMORY FACT"

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def complete(self, _messages, **_kwargs):
            return LLMResponse(text="done")

    ce.register("capture-memory-precompress", CaptureEngine)
    config.data["agent"]["context_engine"] = "capture-memory-precompress"
    session = Session.create()
    session.messages = [Message.user("old")]
    agent = Agent(config=config, provider=Provider(), session=session,
                  memory=MemoryManager(config, external=P()), cwd=tmp_path)

    agent.run("go")

    assert captured["pre_compress_context"] == "AUTOMATIC MEMORY FACT"
    assert all("AUTOMATIC MEMORY FACT" not in (m.content or "") for m in agent.session.messages)


def test_switch_and_end_fire_hooks(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session
    p = RecordingProvider()
    agent = Agent.create(config, memory=MemoryManager(config, external=p))
    new = Session.create(title="next")
    agent.switch_session(new)
    agent.end_session()
    kinds = [c[0] for c in p.calls]
    assert "on_session_switch" in kinds and "on_session_end" in kinds


def test_terminal_new_ends_old_session_before_switch(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.cli import repl
    from aegis.memory import MemoryManager
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def complete(self, _messages, **_kwargs):
            return LLMResponse(text="done")

    class LifecycleProvider(RecordingProvider):
        def on_session_end(self, messages):
            self.calls.append(("on_session_end", [m.content for m in messages]))

    p = LifecycleProvider()
    old = Session.create(title="old")
    old.messages = [Message.user("old prompt")]
    agent = Agent(config=config, provider=Provider(), session=old,
                  memory=MemoryManager(config, external=p), cwd=tmp_path)

    repl.handle_slash("/new", agent, store=SessionStore())

    kinds = [call[0] for call in p.calls]
    assert kinds.index("on_session_end") < kinds.index("on_session_switch")
    assert ("on_session_end", ["old prompt"]) in p.calls
    assert agent.session.id != old.id


def test_forked_session_rebuilds_memory_provider_prompt_on_first_run(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class SessionBoundProvider:
        def __init__(self):
            self.session_id = ""

        def initialize(self, session_id="", **_kw):
            self.session_id = session_id

        def system_prompt_block(self):
            return f"session-bound {self.session_id}"

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def __init__(self):
            self.system_prompt = ""

        def complete(self, messages, **_kwargs):
            self.system_prompt = messages[0].content
            return LLMResponse(text="done")

    store = SessionStore()
    parent = Session.create("parent")
    parent.messages = [Message.system("old system with session-bound parent")]
    store.save(parent)
    child = store.fork(parent)
    provider = Provider()
    agent = Agent(
        config=config,
        provider=provider,
        session=child,
        memory=MemoryManager(config, external=SessionBoundProvider()),
        cwd=tmp_path,
        store=store,
    )

    agent.run("child turn")

    assert f"session-bound {child.id}" in provider.system_prompt
    assert "session-bound parent" not in provider.system_prompt
    assert "_rebuild_system_prompt" not in agent.session.meta


# --- .cursorrules + subdirectory hints --------------------------------------
def test_cursorrules_recognized(tmp_path):
    from aegis.config import Workspace
    assert ".cursorrules" in Workspace.RULE_FILES
    (tmp_path / ".cursorrules").write_text("CURSOR PROJECT RULES")
    assert "CURSOR PROJECT RULES" in Workspace(cwd=tmp_path).rules()


def test_subdir_hints_inject_on_first_entry(tmp_path):
    from aegis.agent.subdir_hints import SubdirHintTracker
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("PACKAGE-LOCAL RULES")
    t = SubdirHintTracker(tmp_path)
    # first read inside pkg/ -> hint injected
    h1 = t.hints_for("read_file", {"path": "pkg/module.py"})
    assert "PACKAGE-LOCAL RULES" in h1 and "subdir_context" in h1
    # second time -> already seen, no repeat
    assert t.hints_for("read_file", {"path": "pkg/other.py"}) == ""
    # cwd itself is never hinted (loaded at startup)
    assert t.hints_for("read_file", {"path": "top.py"}) == ""


def test_subdir_hints_inject_ancestor_rule_for_deep_first_read(tmp_path):
    from aegis.agent.subdir_hints import SubdirHintTracker
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "AGENTS.md").write_text("PACKAGE-ROOT RULES")
    t = SubdirHintTracker(tmp_path)

    h1 = t.hints_for("read_file", {"path": "pkg/sub/module.py"})

    assert "PACKAGE-ROOT RULES" in h1 and 'dir="pkg"' in h1
    assert t.hints_for("read_file", {"path": "pkg/other.py"}) == ""


def test_subdir_hints_disabled_via_config(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    config.data["agent"]["subdir_hints"] = False
    from aegis.agent.subdir_hints import hints_for_call
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("RULES")

    class FakeAgent:
        pass
    a = FakeAgent(); a.config = config
    assert hints_for_call(a, "read_file", {"path": "pkg/x.py"}, tmp_path) == ""


def test_subdir_hints_reset_on_session_switch(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.agent.subdir_hints import hints_for_call
    from aegis.session import Session
    from conftest import FakeProvider

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("PACKAGE RULES")
    agent = Agent(config=config, provider=FakeProvider(), session=Session.create(), cwd=tmp_path)

    first = hints_for_call(agent, "read_file", {"path": "pkg/a.py"}, tmp_path)
    agent.switch_session(Session.create())
    second = hints_for_call(agent, "read_file", {"path": "pkg/b.py"}, tmp_path)

    assert "PACKAGE RULES" in first
    assert "PACKAGE RULES" in second


# --- credit/balance telemetry -----------------------------------------------
def test_credit_balance_capture():
    from aegis import ratelimit
    ratelimit.record({"x-ratelimit-remaining-credits": "12.50"}, "openrouter")
    bal = ratelimit.balance()
    assert bal.get("provider") == "openrouter" and bal.get("credits left") == "12.50"
    assert "credits left: 12.50" in ratelimit.summary()
