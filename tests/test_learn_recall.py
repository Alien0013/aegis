"""Learning loop, FTS recall, and fail-closed sandboxing."""

from __future__ import annotations


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
