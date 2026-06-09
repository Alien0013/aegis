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


# --- forked review (Hermes Tier-1) ------------------------------------------
def test_forked_review_writes_agent_created_skill(tmp_path):
    from aegis import provenance
    from aegis.agent.agent import Agent
    from aegis.agent import review
    from aegis.config import Config
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


def test_maybe_review_off_by_default_and_guards_recursion(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.agent import review
    from aegis.config import Config
    from aegis.session import Session
    a = Agent(config=Config.load(), provider=FakeProvider(), session=Session.create(), cwd=tmp_path)
    assert review.maybe_review(a, tools_this_turn=99) is False     # off unless learn.background
    a._no_review = True
    a.config.data["learn"]["background"] = True
    assert review.maybe_review(a, tools_this_turn=99) is False     # child never re-forks
