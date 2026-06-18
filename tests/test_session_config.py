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


def test_session_read_and_scroll_include_runtime_metadata():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    st = SessionStore()
    s = Session(id="telegram:c1:u1", title="gateway chat")
    s.meta.update({
        "surface": "gateway",
        "gateway": {"platform": "telegram", "chat_id": "c1", "user_id": "u1"},
        "platform": "telegram",
        "chat_id": "c1",
        "runtime_controls": {"provider": "openrouter", "model": "gpt-5.5"},
        "runtime": {"busy_mode": "steer"},
        "last_trace_id": "trace_session",
        "last_run_id": "run_session",
        "last_turn_id": "turn_session",
        "response_state": {"previous_response_id": "resp_session"},
        "resume_pending": True,
        "resume_reason": "planned_stop",
    })
    s.messages = [Message.user("hello"), Message.assistant("hi")]
    st.save(s)

    read_meta = st.read_session(s.id)["session_meta"]
    scroll_meta = st.messages_around(s.id, 1)["session_meta"]

    assert read_meta["platform"] == "telegram"
    assert read_meta["gateway"] == {"platform": "telegram", "chat_id": "c1", "user_id": "u1"}
    assert read_meta["chat_id"] == "c1"
    assert read_meta["runtime_controls"] == {"provider": "openrouter", "model": "gpt-5.5"}
    assert read_meta["runtime"] == {"busy_mode": "steer"}
    assert read_meta["last_trace_id"] == "trace_session"
    assert read_meta["last_run_id"] == "run_session"
    assert read_meta["last_turn_id"] == "turn_session"
    assert read_meta["response_state"]["previous_response_id"] == "resp_session"
    assert read_meta["resume_pending"] is True
    assert read_meta["resume_reason"] == "planned_stop"
    assert scroll_meta["runtime_controls"]["model"] == "gpt-5.5"
    assert scroll_meta["gateway"]["user_id"] == "u1"
    assert scroll_meta["response_state"]["previous_response_id"] == "resp_session"


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


def test_config_set_dotted_secret_path_stays_in_yaml():
    from aegis import config as cfg
    from aegis.config import Config

    c = Config.load()
    where = c.set("server.api_key", "dev-secret")

    assert where == "config.yaml (server.api_key)"
    assert Config.load().get("server.api_key") == "dev-secret"
    assert not cfg.env_path().exists() or "SERVER.API_KEY" not in cfg.env_path().read_text(encoding="utf-8")


def test_config_set_indexed_list_path_preserves_siblings():
    from aegis import config as cfg
    from aegis.config import Config

    cfg.config_path().write_text(
        "custom_providers:\n"
        "- name: provider-a\n"
        "  env_var: OLD_A_KEY\n"
        "  base_url: https://a.example.test/v1\n"
        "- name: provider-b\n"
        "  env_var: OLD_B_KEY\n"
        "  base_url: https://b.example.test/v1\n",
        encoding="utf-8",
    )

    where = Config.load().set("custom_providers.0.env_var", "NEW_A_KEY")
    reloaded = Config.load()
    providers = reloaded.get("custom_providers")

    assert where == "config.yaml (custom_providers.0.env_var)"
    assert reloaded.get("custom_providers.0.env_var") == "NEW_A_KEY"
    assert providers == [
        {
            "name": "provider-a",
            "env_var": "NEW_A_KEY",
            "base_url": "https://a.example.test/v1",
        },
        {
            "name": "provider-b",
            "env_var": "OLD_B_KEY",
            "base_url": "https://b.example.test/v1",
        },
    ]


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
    assert c.get("webhook.idempotency_ttl_seconds") == 3600
    assert c.get("webhook.idempotency_cache_max") == 10000
    assert c.get("server.stale_run_health_seconds") == 21600
    assert c.get("server.stale_resume_pending_health_seconds") == 86400


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


def test_workspace_context_files_truncate_and_warn(tmp_path):
    from aegis.config import Workspace, drain_context_file_warnings

    body = "A" * 80 + "middle-marker" + "Z" * 80
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")

    rules = Workspace(cwd=tmp_path, context_file_max_chars=50).rules()
    warnings = drain_context_file_warnings()

    assert "truncated project:AGENTS.md" in rules
    assert "middle-marker" not in rules
    assert len(warnings) == 1
    assert "context_file_max_chars" in warnings[0]
    assert "AGENTS.md" in warnings[0]


def test_context_file_max_chars_prefers_workspace_alias():
    from aegis.config import Config, context_file_max_chars

    cfg = Config({
        "context_file_max_chars": 100,
        "workspace": {"context_file_max_chars": 64},
    })

    assert context_file_max_chars(cfg) == 64


def test_workspace_context_file_warnings_are_context_local(tmp_path):
    from contextvars import copy_context

    from aegis.config import Workspace, drain_context_file_warnings

    (tmp_path / "AGENTS.md").write_text("R" * 120, encoding="utf-8")

    def build_and_drain():
        Workspace(cwd=tmp_path, context_file_max_chars=40).rules()
        return drain_context_file_warnings()

    warnings = copy_context().run(build_and_drain)

    assert warnings
    assert drain_context_file_warnings() == []


def test_agent_records_context_file_truncation_warnings(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session

    class DummyProvider:
        name = "dummy"
        model = "dummy-model"
        context_length = 200_000
        auth = None

    (tmp_path / "AGENTS.md").write_text("P" * 140, encoding="utf-8")
    cfg = Config.load()
    cfg.data["context_file_max_chars"] = 50
    agent = Agent(config=cfg, provider=DummyProvider(), session=Session.create(), cwd=tmp_path)

    agent.ensure_system_prompt()

    warnings = agent.session.meta.get("context_file_warnings")
    assert warnings and "context_file_max_chars" in warnings[0]
    assert "truncated project:AGENTS.md" in agent.session.messages[0].content


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
