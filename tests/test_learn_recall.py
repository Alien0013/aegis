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

    row_id_scroll = json.loads(tool.run({
        "session_id": previous.id,
        "around_message_row_id": read["messages"][1]["message_row_id"],
        "window": 1,
    }, ctx).content)
    assert row_id_scroll["mode"] == "scroll"
    assert row_id_scroll["around_message_id"] == 1
    assert row_id_scroll["messages"][1]["anchor"] is True


def test_session_search_rebinds_row_anchor_to_child_lineage():
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.recall import SessionSearchTool
    from aegis.types import Message

    store = SessionStore()
    parent = Session.create(title="compressed parent")
    parent.messages = [Message.user("root setup")]
    store.save(parent)
    child = Session.create(title="compressed child", parent_id=parent.id)
    child.messages = [
        Message.user("summary breadcrumb"),
        Message.assistant("the child owns the detailed anchor"),
        Message.user("tail work"),
    ]
    store.save(child)

    tool = SessionSearchTool()
    parent_read = json.loads(tool.run({"session_id": parent.id}, ToolContext()).content)
    child_read = json.loads(tool.run({"session_id": child.id}, ToolContext()).content)
    child_row_id = child_read["messages"][1]["message_row_id"]
    assert child_row_id not in {m["id"] for m in parent_read["messages"]}

    rebound = json.loads(tool.run({
        "session_id": parent.id,
        "around_message_row_id": child_row_id,
        "window": 1,
    }, ToolContext()).content)
    assert rebound["success"] is True
    assert rebound["session_id"] == child.id
    assert rebound["rebound_from_session_id"] == parent.id
    assert rebound["around_message_id"] == 1
    assert rebound["messages"][1]["anchor"] is True

    hermes_style = json.loads(tool.run({
        "session_id": parent.id,
        "around_message_id": child_row_id,
        "window": 1,
    }, ToolContext()).content)
    assert hermes_style["session_id"] == child.id
    assert hermes_style["around_message_id"] == 1

    active_reject = json.loads(tool.run({
        "session_id": parent.id,
        "around_message_row_id": child_row_id,
        "window": 1,
    }, ToolContext(session=child)).content)
    assert active_reject["success"] is False
    assert "current session lineage" in active_reject["error"]


def test_session_browse_projects_compression_tip_not_branch():
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.recall import SessionSearchTool
    from aegis.types import Message

    store = SessionStore()
    compressed_root = Session.create(title="long task")
    compressed_root.messages = [Message.user("old opening")]
    compressed_root.meta["end_reason"] = "compression"
    store.save(compressed_root)
    compression_child = Session.create(title="long task #2", parent_id=compressed_root.id)
    compression_child.messages = [Message.user("live continuation preview")]
    compression_child.meta["creator_kind"] = "compression"
    compression_child.meta["parent_end_reason"] = "compression"
    store.save(compression_child)

    branch_root = Session.create(title="branch root")
    branch_root.messages = [Message.user("branch root preview")]
    store.save(branch_root)
    branch = Session.create(title="manual branch", parent_id=branch_root.id)
    branch.messages = [Message.user("manual branch preview")]
    branch.meta["branch_reason"] = "manual_branch"
    store.save(branch)

    browsed = json.loads(SessionSearchTool().run({"limit": 10}, ToolContext()).content)
    ids = [row["session_id"] for row in browsed["results"]]

    assert compression_child.id in ids
    assert compressed_root.id not in ids
    compression_row = next(row for row in browsed["results"] if row["session_id"] == compression_child.id)
    assert compression_row["lineage_root_id"] == compressed_root.id
    assert compression_row["preview"] == "live continuation preview"
    assert branch_root.id in ids
    assert branch.id not in ids


def test_session_title_resolution_prefers_latest_continuation_tip():
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.recall import SessionSearchTool
    from aegis.types import Message

    store = SessionStore()
    root = Session.create(title="parser project")
    root.messages = [Message.user("old parser opening")]
    root.meta["end_reason"] = "compression"
    store.save(root)
    child = Session.create(title="parser project #2", parent_id=root.id)
    child.messages = [Message.user("latest parser continuation")]
    child.meta["creator_kind"] = "compression"
    child.meta["parent_end_reason"] = "compression"
    store.save(child)

    exact = store.load(root.id)
    assert exact and exact.id == root.id
    assert store.resolve_title_to_tip("parser project").id == child.id
    assert store.resolve_title_to_tip("parser project #2").id == child.id

    tool = SessionSearchTool()
    read = json.loads(tool.run({"session_id": "parser project"}, ToolContext()).content)
    assert read["success"] is True
    assert read["session_id"] == child.id
    assert read["messages"][0]["content"] == "latest parser continuation"


def test_session_title_resolution_escapes_sql_wildcards():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    exact = Session.create(title="test_project")
    exact.messages = [Message.user("exact underscore")]
    store.save(exact)
    unrelated = Session.create(title="testXproject #2")
    unrelated.messages = [Message.user("wildcard should not match")]
    store.save(unrelated)

    resolved = store.resolve_title_to_tip("test_project")
    assert resolved and resolved.id == exact.id


def test_session_search_can_read_explicit_profile():
    from aegis import config as cfg
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.recall import SessionSearchTool
    from aegis.types import Message

    default_store = SessionStore()
    default = Session.create(title="default launch notes")
    default.messages = [Message.user("default-only launch note")]
    default_store.save(default)

    work_store = SessionStore(profile="work")
    work = Session.create(title="work parser notes")
    work.messages = [Message.user("work-only xylophonemark note"), Message.assistant("work profile answer")]
    work_store.save(work)

    tool = SessionSearchTool()
    ctx = ToolContext(session=default)
    default_result = json.loads(tool.run({"query": "xylophonemark"}, ctx).content)
    work_result = json.loads(tool.run({"query": "xylophonemark", "profile": "work"}, ctx).content)
    read_work = json.loads(tool.run({"session_id": work.id[:12], "profile": "work"}, ctx).content)
    read_work_bare = json.loads(tool.run({"session_id": work.id[:12]}, ctx).content)
    read_work_link = json.loads(tool.run({"session_id": f"@session:work/{work.id[:12]}"}, ctx).content)
    cfg.set_profile("work")
    try:
        read_default_link = json.loads(
            tool.run({"session_id": f"@session:default/{default.id[:12]}"}, ctx).content
        )
    finally:
        cfg.set_profile(None)

    assert default_result["results"] == []
    assert work_result["profile"] == "work"
    assert work_result["results"][0]["session_id"] == work.id
    assert work_result["results"][0]["profile"] == "work"
    assert work_result["results"][0]["match_message_row_id"]
    assert read_work["session_meta"]["profile"] == "work"
    assert read_work["messages"][0]["message_row_id"]
    assert read_work_bare["profile"] == "work"
    assert read_work_bare["session_id"] == work.id
    assert read_work_link["profile"] == "work"
    assert read_work_link["session_id"] == work.id
    assert read_default_link["profile"] == ""
    assert read_default_link["session_id"] == default.id


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
