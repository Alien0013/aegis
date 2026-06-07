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
    for i in range(3):
        s = Session.create()
        st.save(s)
        ids.append(s.id)
    assert len(st.list()) == 3
    assert st.delete(ids[0]) and len(st.list()) == 2


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
    assert c.get("tools.exec_mode") == "ask"


def test_config_deep_merge_keeps_user_and_defaults():
    from aegis.config import Config, DEFAULT_CONFIG
    c = Config.load()
    # user value preserved, default keys still present
    assert "memory" in c.data and "tools" in c.data
    assert c.get("agent.reasoning_effort") == DEFAULT_CONFIG["agent"]["reasoning_effort"]


def test_workspace_rules_merge(tmp_path):
    from aegis.config import Workspace
    (tmp_path / "AGENTS.md").write_text("use ruff")
    rules = Workspace(cwd=tmp_path).rules()
    assert "use ruff" in rules


def test_get_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "custom"))
    from aegis import config as cfg
    assert cfg.get_home() == (tmp_path / "custom")
