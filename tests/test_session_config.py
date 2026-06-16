"""Session store (persistence, resume, recall) and config (precedence, secrets, workspace)."""

from __future__ import annotations


# --- sessions ---------------------------------------------------------------
def test_session_save_load_resume_variants():
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    st = SessionStore()
    s = Session.create()
    s.title = "my project"
    s.messages = [Message.user("hello"), Message.assistant("hi")]
    st.save(s)
    assert st.load(s.id).messages[0].content == "hello"      # by id
    assert st.load("my project").id == s.id                  # by title
    assert st.load(s.id[:10]).id == s.id                     # by prefix
    assert st.latest().id == s.id


def test_session_list_and_delete():
    from aegis.session import Session, SessionStore
    st = SessionStore()
    ids = []
    for _i in range(3):
        s = Session.create()
        st.save(s)
        ids.append(s.id)
    assert len(st.list()) == 3
    assert st.delete(ids[0]) and len(st.list()) == 2


def test_session_resume_pending_helpers():
    from aegis.session import Session, SessionStore

    st = SessionStore()
    s = Session(id="telegram:c1:u1", title="gateway chat")
    st.save(s)

    assert st.mark_resume_pending(s.id, "SIGTERM") is True
    pending = st.list_resume_pending()
    assert [row["id"] for row in pending] == [s.id]
    assert pending[0]["resume_reason"] == "SIGTERM"
    loaded = st.load(s.id)
    assert loaded.meta["resume_pending"] is True
    assert loaded.meta["last_resume_marked_at"]

    assert st.clear_resume_pending(s.id) is True
    loaded = st.load(s.id)
    assert "resume_pending" not in loaded.meta
    assert st.list_resume_pending() == []
    assert st.clear_resume_pending(s.id) is False


def test_session_search_messages_recall():
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    st = SessionStore()
    s = Session.create()
    s.messages = [Message.user("deploy the kubernetes cluster"), Message.assistant("done")]
    st.save(s)
    hits = st.search_messages("kubernetes")
    assert hits and hits[0]["session"] == s.id and "kubernetes" in hits[0]["snippet"].lower()


def test_session_titles_from_first_message():
    from aegis.session import Session
    s = Session.create()
    s.maybe_title_from("Build me a web scraper")
    assert s.title and s.title != s.id


# --- config -----------------------------------------------------------------
def test_config_set_routes_secret_to_env():
    from aegis.config import Config
    import os
    c = Config.load()
    where = c.set("OPENAI_API_KEY", "sk-xyz")
    assert ".env" in where and os.environ["OPENAI_API_KEY"] == "sk-xyz"


def test_config_set_setting_to_yaml_and_persists():
    from aegis.config import Config
    c = Config.load()
    c.set("agent.max_iterations", 9)
    assert Config.load().get("agent.max_iterations") == 9


def test_config_get_dotted_default():
    from aegis.config import Config
    c = Config.load()
    assert c.get("nope.nope.nope", "fallback") == "fallback"
    assert c.get("tools.exec_mode") == "auto"   # automatic tool approval by default


def test_config_deep_merge_keeps_user_and_defaults():
    from aegis.config import Config, DEFAULT_CONFIG
    c = Config.load()
    # user value preserved, default keys still present
    assert "memory" in c.data and "tools" in c.data
    assert c.get("agent.reasoning_effort") == DEFAULT_CONFIG["agent"]["reasoning_effort"]
    assert c.get("delegation.subagent_auto_approve") is False
    assert c.get("delegation.max_async_children") == 3
    assert c.get("delegation.retain_completed_background_tasks") == 50


def test_workspace_rules_merge(tmp_path):
    from aegis.config import Workspace
    (tmp_path / "AGENTS.md").write_text("use ruff")
    rules = Workspace(cwd=tmp_path).rules()
    assert "use ruff" in rules


def test_workspace_rules_layer_root_and_subdir(tmp_path):
    from aegis.config import Workspace

    (tmp_path / "AGENTS.md").write_text("ROOT RULES")
    sub = tmp_path / "packages" / "foo"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("SUBPKG RULES")

    rules = Workspace(cwd=sub).rules()

    assert "ROOT RULES" in rules
    assert "SUBPKG RULES" in rules
    assert rules.index("ROOT RULES") < rules.index("SUBPKG RULES")


def test_personality_layers_with_soul(tmp_path):
    from aegis.agent.context import ContextBuilder
    from aegis.config import Config, workspace_dir

    workspace = workspace_dir()
    (workspace / "SOUL.md").write_text("core soul voice", encoding="utf-8")
    pdir = workspace / "personalities"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "pilot.md").write_text("pilot surface tone", encoding="utf-8")
    cfg = Config.load()
    cfg.data.setdefault("agent", {})["personality"] = "pilot"

    prompt = ContextBuilder(cfg, cwd=tmp_path).build()

    assert "core soul voice" in prompt
    assert "pilot surface tone" in prompt
    assert prompt.index("core soul voice") < prompt.index("pilot surface tone")


def test_get_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "custom"))
    from aegis import config as cfg
    assert cfg.get_home() == (tmp_path / "custom")
