"""Self-learning engine: forked review, provenance, curator lifecycle, session lineage,
fallback classification, event contract."""

from __future__ import annotations

from conftest import FakeProvider


# --- #2 fallback state machine ---------------------------------------------
def test_fallback_error_classification():
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.providers.fallback import classify_provider_error
    assert classify_provider_error(ProviderHTTPError(429, "")) == "rate_limit"
    assert classify_provider_error(ProviderHTTPError(401, "")) == "auth"
    assert classify_provider_error(ProviderHTTPError(402, "")) == "billing"
    assert classify_provider_error(ProviderHTTPError(503, "")) == "server"
    assert classify_provider_error(ProviderHTTPError(400, "")) == "client"
    assert classify_provider_error(TimeoutError()) == "transient"
    assert classify_provider_error(ValueError("garbage")) == "invalid_response"


def test_fallback_swaps_and_records_trigger():
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class P:
        def __init__(self, name, err=None):
            self.name = name; self.err = err; self.model = "m"
            self.context_length = 1; self.api_mode = None; self.auth = None
        def describe(self): return self.name
        def complete(self, m, tools=None, **k):
            if self.err:
                raise self.err
            return LLMResponse(text=self.name)

    fb = FallbackProvider(P("a", ProviderHTTPError(429, "")), [P("b")])
    assert fb.complete([]).text == "b"
    assert fb.last_trigger == ("a", "rate_limit")


# --- #4 event contract ------------------------------------------------------
def test_event_contract_known_types():
    from aegis.agent.events import EventType, is_known
    assert is_known({"type": EventType.REVIEW_DONE})
    assert is_known({"type": "tool_result"})
    assert not is_known({"type": "made_up_event"})


# --- provenance -------------------------------------------------------------
def test_provenance_origin_pin_protected(tmp_path):
    from aegis import provenance
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    ld = SkillsLoader(Config.load())
    ld.create("agent-one", "by agent", "## x", origin="agent")
    ld.create("user-one", "by me", "## y", origin="user")
    assert provenance.is_agent_created("agent-one")
    assert not provenance.is_agent_created("user-one")
    assert provenance.curatable("agent-one")
    assert not provenance.curatable("user-one")        # user skills protected
    provenance.pin("agent-one")
    assert not provenance.curatable("agent-one")        # pinned bypasses curation
    assert provenance.is_protected("code-review")       # a bundled skill is protected


def test_origin_scope_tags_writes(tmp_path):
    from aegis import provenance
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    ld = SkillsLoader(Config.load())
    with provenance.origin_scope("agent"):
        ld.create("scoped-skill", "via scope", "## x")
    assert provenance.is_agent_created("scoped-skill")


# --- curator lifecycle ------------------------------------------------------
def test_curator_only_prunes_curatable(monkeypatch):
    import aegis.curator as cur
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    ld = SkillsLoader(Config.load())
    ld.create("agent-stale", "agent", "## x", origin="agent")
    ld.create("user-stale", "user", "## y", origin="user")
    # make every skill look ancient
    from pathlib import Path
    monkeypatch.setattr(cur, "_scan", lambda now=None: [
        cur.SkillInfo(name="agent-stale", dir=Path("/x"), age_days=999.0, malformed=""),
        cur.SkillInfo(name="user-stale", dir=Path("/y"), age_days=999.0, malformed=""),
    ])
    candidates = cur.prune(dry_run=True)
    assert "agent-stale" in candidates and "user-stale" not in candidates


# --- session lineage --------------------------------------------------------
def test_session_fork_lineage(tmp_path):
    from aegis.session import Session, SessionStore
    st = SessionStore()
    parent = Session.create("p"); st.save(parent)
    child = st.fork(parent)
    assert child.parent_id == parent.id
    assert child.id in {c["id"] for c in st.children(parent.id)}
    assert st.load(child.id).parent_id == parent.id     # persists across reload


# --- forked review ---------------------------------------------------------
def test_forked_review_writes_agent_created_skill(tmp_path):
    from aegis import provenance
    from aegis.agent.agent import Agent
    from aegis.agent import review
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.session import Session
    from aegis.types import LLMResponse, Message, ToolCall

    class SkillWriter:
        context_length = 200_000; name = "f"; model = "m"; api_mode = None; auth = None
        def __init__(self): self.n = 0
        def describe(self): return "f"
        def complete(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return LLMResponse(text="", tool_calls=[ToolCall("c1", "skill", {
                    "action": "create", "name": "deploy-flow",
                    "description": "how to deploy safely", "body": "## When\n..."})])
            return LLMResponse(text="saved")

    cfg = Config.load(); cfg.data["tools"]["exec_mode"] = "full"
    a = Agent(config=cfg, provider=SkillWriter(), session=Session.create())
    a.session.messages = [Message.user("deploy it"), Message.assistant("done")]
    actions = review.run_review(a, "skill")
    assert actions and "deploy-flow" in a.skills.discover()
    assert provenance.is_agent_created("deploy-flow")
    run = next(row for row in RunStore().list(surface="review", limit=5)
               if row["data"].get("review_kind") == "skill")
    assert run["data"]["provider"] == "f"
    assert run["data"]["model"] == "m"


def test_forked_review_restores_parent_memory_provider_session(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.agent import review
    from aegis.config import Config
    from aegis.memory import MemoryManager
    from aegis.session import Session
    from aegis.types import LLMResponse, Message

    class ExternalMemory:
        def __init__(self):
            self.current = ""
            self.prefetch_sessions = []
            self.switches = []

        def initialize(self, session_id="", **_kw):
            self.current = session_id

        def on_session_switch(self, *, old_session_id, new_session_id, **_kw):
            self.switches.append((old_session_id, new_session_id))
            self.current = new_session_id

        def prefetch(self, query, *, session_id=""):
            self.prefetch_sessions.append(session_id)
            return ""

        def system_prompt_block(self):
            return f"bound {self.current}"

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = True
    parent_session = Session.create("parent")
    parent_session.messages = [Message.user("remember this"), Message.assistant("noted")]
    external = ExternalMemory()
    agent = Agent(
        config=cfg,
        provider=FakeProvider([LLMResponse(text="nothing to save")]),
        session=parent_session,
        memory=MemoryManager(cfg, external=external),
        cwd=tmp_path,
    )

    review.run_review(agent, "memory")
    agent.memory.prefetch("after review")

    assert external.current == parent_session.id
    assert external.prefetch_sessions[-1] == parent_session.id
    assert external.switches[-1][1] == parent_session.id
    assert external.switches[-1][0] != parent_session.id


def test_compaction_splits_into_child_session(tmp_path, monkeypatch):
    """When the window fills, roll into a child session (parent kept, lineage chained)."""
    from aegis.agent import compaction
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    # deterministic: over the threshold on the first check, under it after compaction
    calls = {"n": 0}

    def fake_should(messages, ctx, overhead=0):
        calls["n"] += 1
        return calls["n"] == 1
    monkeypatch.setattr(compaction, "should_compress", fake_should)

    class Dual:
        # small window so the token-budgeted tail (a fraction of it) leaves a middle to compress
        context_length = 2000; name = "f"; model = "m"; api_mode = None; auth = None
        def describe(self): return "f"
        def complete(self, messages, tools=None, **k):
            if tools is None:
                return LLMResponse(text="SUMMARY of earlier turns")
            return LLMResponse(text="final")        # compaction already split before this call

    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = "full"
    cfg.data["learn"]["background"] = False
    store = SessionStore()
    s = Session.create("My Task")
    # substantial messages so the conversation exceeds the protected tail budget
    body = "this is turn content with enough words to carry real token weight here "
    s.messages = [Message.system("sys")] + [
        (Message.user(f"u{i} {body}") if i % 2 == 0 else Message.assistant(f"a{i} {body}"))
        for i in range(40)]
    store.save(s)
    parent_id = s.id
    a = Agent(config=cfg, provider=Dual(), session=s, store=store, cwd=tmp_path)
    a.run("continue")
    assert a.session.id != parent_id                       # rolled into a child
    assert a.session.parent_id == parent_id                # lineage chained
    assert a.session.title == "My Task (2)"                # auto-numbered
    assert len(a.session.messages) < len(store.load(parent_id).messages)  # child is compressed
    assert store.load(parent_id) is not None               # parent preserved full
    assert a.session.id in {c["id"] for c in store.children(parent_id)}


def test_store_lock_prevents_lost_updates():
    """Concurrent writers to the shared memory store must not clobber each other (the
    background-review thread + the foreground agent share it)."""
    import threading
    from aegis.memory import MemoryStore
    m = MemoryStore()

    def writer(start):
        for i in range(start, start + 40):
            m.add("memory", f"fact-{i}")

    threads = [threading.Thread(target=writer, args=(b,)) for b in (0, 1000, 2000, 3000)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(m.entries("memory")) == 160          # all 4x40 survived, none lost


def test_lineage_title_numbering():
    from aegis.agent.loop import _next_in_lineage
    assert _next_in_lineage("Task") == "Task (2)"
    assert _next_in_lineage("Task (2)") == "Task (3)"
    assert _next_in_lineage("") == "session (2)"


def test_maybe_review_off_by_default_and_guards_recursion(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.agent import review
    from aegis.config import Config
    from aegis.session import Session
    a = Agent(config=Config.load(), provider=FakeProvider(), session=Session.create(), cwd=tmp_path)
    a.config.data["learn"]["background"] = False
    assert review.maybe_review(a, tools_this_turn=99) is False     # off when disabled
    a._no_review = True
    a.config.data["learn"]["background"] = True
    assert review.maybe_review(a, tools_this_turn=99) is False     # child never re-forks even when on


# --- learning autonomy: memory auto-applies, skills gated unless opted in ----
def test_maybe_review_skill_autonomy(monkeypatch):
    """review.maybe_review (the live path): memory honors auto_apply, skills honor
    auto_apply_skills independently."""
    import time
    import aegis.agent.review as review
    routed = []
    monkeypatch.setattr(review, "run_review", lambda agent, kind, on_event=None: routed.append(("write", kind)))
    monkeypatch.setattr(review, "_propose_only", lambda agent, kind: routed.append(("queue", kind)))

    class Cfg:
        def __init__(self, **k): self.k = {"learn.background": True, "learn.memory_every": 1,
                                           "learn.skill_every_iters": 1, **k}
        def get(self, key, d=None): return self.k.get(key, d)

    class Agent:
        def __init__(self, **k):
            self.config = Cfg(**k)
            self.provider = object()
            self._no_review = False
            self.session = type("S", (), {"meta": {}})()
            self.tool_context = type("T", (), {"emit": None})()

    # defaults: auto_apply True (memory writes), auto_apply_skills False (skill queues)
    review.maybe_review(Agent(**{"learn.auto_apply": True, "learn.auto_apply_skills": False}), tools_this_turn=5)
    time.sleep(0.2)
    assert ("write", "memory") in routed and ("queue", "skill") in routed
    routed.clear()
    # auto_apply_skills True -> skill also writes
    review.maybe_review(Agent(**{"learn.auto_apply": True, "learn.auto_apply_skills": True}), tools_this_turn=5)
    time.sleep(0.2)
    assert ("write", "memory") in routed and ("write", "skill") in routed
