def test_skill_preprocessing_supports_aegis_and_hermes_template_vars(tmp_path):
    from aegis.skill_preprocessing import preprocess_skill_content

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    content = (
        "${AEGIS_SKILL_DIR}\n"
        "${HERMES_SKILL_DIR}\n"
        "${AEGIS_SESSION_ID}\n"
        "${HERMES_SESSION_ID}\n"
    )

    out = preprocess_skill_content(
        content,
        skill_dir,
        session_id="sess-123",
        skills_cfg={"template_vars": True},
    )

    assert out.splitlines() == [
        str(skill_dir),
        str(skill_dir),
        "sess-123",
        "sess-123",
    ]


def test_skill_preprocessing_respects_template_and_inline_shell_config(tmp_path):
    from aegis.skill_preprocessing import preprocess_skill_content

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "note.txt").write_text("from skill cwd\n", encoding="utf-8")

    disabled = preprocess_skill_content(
        "${HERMES_SKILL_DIR} !`cat note.txt`",
        skill_dir,
        session_id="sess-123",
        skills_cfg={"template_vars": False, "inline_shell": False},
    )
    enabled = preprocess_skill_content(
        "${HERMES_SKILL_DIR} !`cat note.txt`",
        skill_dir,
        session_id="sess-123",
        skills_cfg={"template_vars": True, "inline_shell": True},
    )

    assert disabled == "${HERMES_SKILL_DIR} !`cat note.txt`"
    assert enabled == f"{skill_dir} from skill cwd"


def test_agent_skill_activation_passes_session_id_to_preprocessor(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def describe(self):
            return "fake"

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    skill_dir = tmp_path / "skills" / "session-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: session-helper\n"
        "description: Use for session template testing.\n"
        "---\n"
        "dir=${HERMES_SKILL_DIR}\n"
        "session=${HERMES_SESSION_ID}\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["include_bundled"] = False
    session = Session.create()

    agent = Agent(config=cfg, provider=Provider(), session=session, cwd=tmp_path)
    block = agent.skills.activate("session-helper") or ""

    assert f"dir={skill_dir}" in block
    assert f"session={session.id}" in block


def test_slash_skill_activation_preprocesses_aegis_template_vars(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.skills import SKILL_USER_TASK_MARKER, SkillsLoader

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "slash-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: slash-helper\n"
        "description: Use for slash template testing.\n"
        "---\n"
        "dir=${AEGIS_SKILL_DIR}\n"
        "session=${AEGIS_SESSION_ID}\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data["skills"]["include_bundled"] = False
    loader = SkillsLoader(cfg, cwd=workspace, session_id="sess-slash")

    block, loaded = loader.invocation_from_slash("/slash-helper ship it") or ("", [])

    assert loaded == ["slash-helper"]
    assert f"dir={skill_dir}" in block
    assert "session=sess-slash" in block
    assert SKILL_USER_TASK_MARKER in block
    assert "ship it" in block


def test_preload_block_preprocesses_inline_shell_only_when_enabled(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.skills import SkillsLoader

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "shell-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "note.txt").write_text("from preload cwd\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: shell-helper\n"
        "description: Use for preload shell testing.\n"
        "---\n"
        "result=!`cat note.txt`\n",
        encoding="utf-8",
    )
    disabled_cfg = Config.load()
    disabled_cfg.data["skills"]["include_bundled"] = False
    disabled_cfg.data["skills"]["inline_shell"] = False
    enabled_cfg = Config.load()
    enabled_cfg.data["skills"]["include_bundled"] = False
    enabled_cfg.data["skills"]["inline_shell"] = True

    disabled_block, disabled_loaded, _ = SkillsLoader(
        disabled_cfg,
        cwd=workspace,
        session_id="sess-preload",
    ).preload_block(["shell-helper"])
    enabled_block, enabled_loaded, _ = SkillsLoader(
        enabled_cfg,
        cwd=workspace,
        session_id="sess-preload",
    ).preload_block(["shell-helper"])

    assert disabled_loaded == ["shell-helper"]
    assert enabled_loaded == ["shell-helper"]
    assert "result=!`cat note.txt`" in disabled_block
    assert "result=from preload cwd" in enabled_block
