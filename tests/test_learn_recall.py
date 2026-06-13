"""Learning loop, FTS recall, and fail-closed sandboxing."""

from __future__ import annotations

import json


# --- learning loop ----------------------------------------------------------
class _JSONProvider:
    context_length = 200_000
    def complete(self, messages, **k):
        from aegis.types import LLMResponse
        return LLMResponse(text='{"memories": ["project uses pnpm; my key is sk-abcdefghijklmnopqrstuvwx"],'
                                ' "skills": [{"name": "deploy-x", "description": "deploy. use to ship.",'
                                ' "body": "## Procedure\\n1. go"}]}')


def test_learn_review_redacts_and_extracts(monkeypatch):
    import aegis.providers.registry as reg
    monkeypatch.setattr(reg, "build_provider", lambda *a, **k: _JSONProvider())
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    from aegis import learn

    s = Session.create()
    s.messages = [Message.user("ship the app"), Message.assistant("deployed")]
    SessionStore().save(s)

    found = learn.review_session(Config.load(), s.id)
    assert len(found) == 2
    mem = next(c for c in found if c["type"] == "memory")
    assert "sk-abcdefghijklmnopqrstuvwx" not in mem["payload"]  # secret redacted
    assert "[REDACTED]" in mem["payload"]


def test_learn_apply_promotes(monkeypatch):
    import aegis.providers.registry as reg
    monkeypatch.setattr(reg, "build_provider", lambda *a, **k: _JSONProvider())
    from aegis.config import Config
    from aegis.memory import MemoryStore
    from aegis.session import Session, SessionStore
    from aegis.skills import SkillsLoader
    from aegis.types import Message
    from aegis import learn

    cfg = Config.load()
    s = Session.create()
    s.messages = [Message.user("ship"), Message.assistant("ok")]
    SessionStore().save(s)
    found = learn.review_session(cfg, s.id)

    skill_cand = next(c for c in found if c["type"] == "skill")
    learn.apply_candidate(skill_cand["id"], cfg)
    assert "deploy-x" in SkillsLoader(cfg).discover()

    mem_cand = next(c for c in found if c["type"] == "memory")
    learn.apply_candidate(mem_cand["id"], cfg)
    assert "pnpm" in MemoryStore().raw("memory")
    assert not learn.list_candidates("pending")  # both resolved


def test_learn_reject():
    from aegis import learn
    learn._save([{"id": "cand_1", "type": "memory", "payload": "x", "status": "pending",
                  "session": "s", "created_at": ""}])
    learn.reject_candidate("cand_1")
    assert learn.list_candidates("pending") == []


# --- FTS recall -------------------------------------------------------------
def test_fts_or_like_recall():
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    st = SessionStore()
    s = Session.create()
    s.messages = [Message.user("deploy to kubernetes production cluster"), Message.assistant("done")]
    st.save(s)
    hits = st.search_messages("kubernetes")
    assert hits and hits[0]["session"] == s.id


def test_session_search_tool_browse_discover_read_and_scroll():
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.recall import SessionSearchTool
    from aegis.types import Message

    store = SessionStore()
    previous = Session.create(title="parser launch notes")
    previous.messages = [
        Message.user("what did we decide about the parser bug?"),
        Message.assistant("We fixed the parser bug, shipped v2, and kept the fallback parser."),
        Message.user("also remember the kubernetes rollout plan"),
    ]
    store.save(previous)
    current = Session.create(title="current chat")
    store.save(current)

    tool = SessionSearchTool()
    ctx = ToolContext(session=current)

    browse = json.loads(tool.run({}, ctx).content)
    assert browse["mode"] == "browse"
    assert any(row["session_id"] == previous.id for row in browse["results"])

    discover = json.loads(tool.run({"query": "what did we decide about parser bug"}, ctx).content)
    assert discover["mode"] == "discover"
    assert discover["results"][0]["session_id"] == previous.id
    assert discover["results"][0]["match_message_id"] == 0

    read = json.loads(tool.run({"session_id": previous.id[:14]}, ctx).content)
    assert read["mode"] == "read"
    assert read["session_id"] == previous.id
    assert read["messages"][1]["content"].startswith("We fixed the parser bug")

    scroll = json.loads(tool.run({"session_id": previous.id, "around_message_id": 1, "window": 1}, ctx).content)
    assert scroll["mode"] == "scroll"
    assert [m["id"] for m in scroll["messages"]] == [0, 1, 2]
    assert scroll["messages"][1]["anchor"] is True


def test_system_prompt_guides_prior_session_recall(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session

    agent = Agent(config=Config.load(), provider=_JSONProvider(), session=Session.create(), cwd=tmp_path)
    agent.ensure_system_prompt()
    prompt = agent.session.messages[0].content
    assert "call `session_search` before answering" in prompt


def test_session_summary_stored():
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    st = SessionStore()
    s = Session.create()
    s.messages = [Message.user("hi"), Message.assistant("hello")]
    st.save(s)

    class P:
        def complete(self, *a, **k):
            from aegis.types import LLMResponse
            return LLMResponse(text="User greeted; agent replied.")
    summary = st.summarize(s.id, P())
    assert "greeted" in summary
    assert st.load(s.id).meta.get("summary") == summary


# --- fail-closed sandbox ----------------------------------------------------
def test_sandbox_fail_closed(tmp_path):
    from aegis.config import Config
    from aegis.tools.backends import _degraded
    cfg = Config.load()
    out, code = _degraded(cfg, "docker down", "echo hi", str(tmp_path), 10)
    assert code == 126 and "efus" in out          # refuses to run locally
    cfg.data["tools"]["allow_local_fallback"] = True
    out2, code2 = _degraded(cfg, "docker down", "echo yo", str(tmp_path), 10)
    assert code2 == 0 and "yo" in out2
