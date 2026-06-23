from __future__ import annotations

import os


def test_onboarding_rejects_key_as_provider(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "sk-proj-oops",# provider prompt: should be rejected as a provider
        "1",           # OpenAI
        "2",           # API key auth
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        secret_func=lambda _prompt: "sk-test",
        output_func=out.append,
    )

    assert rc == 0
    assert Config.load().get("model.provider") == "openai"
    assert "looks like an API key" in "\n".join(out)
    assert "sk-test" == os.environ["OPENAI_API_KEY"]
    from aegis import config as cfg_paths

    workspace = cfg_paths.workspace_dir()
    assert (workspace / "SOUL.md").exists()
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "README.md").exists()
    # No workspace/USER.md — the profile lives only in memories/USER.md.
    assert not (workspace / "USER.md").exists()


def test_onboarding_can_select_codex_login(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.setattr("aegis.onboarding._ensure_codex_cli_login", lambda *_args: True)
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # ChatGPT/Codex auth
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 0
    assert Config.load().get("model.provider") == "codex"
    text = "\n".join(out)
    assert "Choose authentication method" in text
    assert "ChatGPT subscription via Codex login" in text
    assert "Auth:            codex" in text


def test_onboarding_codex_login_failure_aborts_setup(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("aegis.onboarding._ensure_codex_cli_login", lambda *_args: False)
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # ChatGPT/Codex auth
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        secret_func=lambda _prompt: "sk-fallback",
        output_func=out.append,
    )

    assert rc == 1
    assert "OPENAI_API_KEY" not in os.environ
    text = "\n".join(out)
    assert "ChatGPT subscription setup did not finish" in text
    assert "Select model" not in text
    assert Config.load().get("model.provider") == "anthropic"


def test_onboarding_codex_login_failure_does_not_probe(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.setattr("aegis.onboarding._ensure_codex_cli_login", lambda *_args: False)

    def fail_probe(*_args):
        raise AssertionError("probe should not run without usable credentials")

    monkeypatch.setattr("aegis.onboarding._probe_model", fail_probe)
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # ChatGPT/Codex auth
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=True,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 1
    text = "\n".join(out)
    assert "ChatGPT subscription setup did not finish" in text


def test_ensure_codex_cli_login_can_install_missing_cli(monkeypatch):
    from aegis.onboarding import _ensure_codex_cli_login

    installed = {"done": False}

    def fake_which(name: str):
        if name == "npm":
            return "/bin/npm"
        if name == "codex" and installed["done"]:
            return "/bin/codex"
        return None

    def fake_run(cmd, **_kwargs):
        class Result:
            returncode = 0
            stdout = "Logged in using ChatGPT"
            stderr = ""

        if cmd[:3] == ["/bin/npm", "install", "-g"]:
            installed["done"] = True
        return Result()

    monkeypatch.setattr("aegis.onboarding.shutil.which", fake_which)
    monkeypatch.setattr("aegis.onboarding.subprocess.run", fake_run)
    answers = iter(["y"])
    out: list[str] = []

    assert _ensure_codex_cli_login(lambda _prompt: next(answers), out.append)
    assert "Codex CLI installed" in "\n".join(out)


def test_onboarding_terminal_menu_uses_selector_markers(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "3",           # skip credentials
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 0
    text = "\n".join(out)
    assert "  ❯ OpenAI" in text
    assert "  ⬡ Telegram" in text
    assert "Tools & skills" in text
    assert "model-visible tools:" in text
    assert "skills available:" in text
    assert "OpenAI (GPT-4o / GPT-5 API) (1)" not in text


def test_onboarding_accepts_partial_provider_label(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",                      # security notice
        "OpenAI / Codex",         # natural label from the displayed option
        "3",                      # skip credentials
        "",                       # model default
        "",                       # exec mode default
        "6",                      # skip web setup
        "",                       # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 0
    assert Config.load().get("model.provider") == "openai"
    assert "unknown choice" not in "\n".join(out)


def test_noninteractive_onboarding_json_configures_defaults(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--provider",
        "ollama",
        "--auth",
        "local",
        "--model",
        "llama3.1",
        "--web",
        "skip",
        "--toolsets",
        "core,mcp",
        "--channels",
        "telegram",
        "--exec-mode",
        "auto",
        "--no-services",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["model"]["provider"] == "ollama"
    assert data["model"]["auth"] == "local"
    assert data["web_search"] == "skip"
    assert data["integrations"] == ["telegram"]
    assert data["surface"]["tools_enabled"] > 0
    cfg = Config.load()
    assert cfg.get("model.provider") == "ollama"
    assert cfg.get("tools.exec_mode") == "auto"


def test_config_setup_runs_noninteractive_onboarding(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "config",
        "setup",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--provider",
        "openai",
        "--auth",
        "skip",
        "--model",
        "gpt-5.5",
        "--web",
        "skip",
        "--toolsets",
        "core",
        "--exec-mode",
        "ask",
        "--no-services",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["model"]["provider"] == "openai"
    assert data["model"]["model"] == "gpt-5.5"
    assert data["web_search"] == "skip"
    cfg = Config.load()
    assert cfg.get("model.provider") == "openai"
    assert cfg.get("model.default") == "gpt-5.5"


def test_setup_section_tools_noninteractive_json(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "tools",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--toolsets",
        "core,mcp",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["object"] == "aegis.setup.section"
    assert data["section"] == "tools"
    assert data["surface"]["toolsets"] == ["core", "mcp"]
    assert Config.load().get("tools.toolsets") == ["core", "mcp"]


def test_setup_section_tools_noninteractive_can_select_skills(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "tools",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--toolsets",
        "core",
        "--skills",
        "web-research,summarize",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["surface"]["toolsets"] == ["core"]
    assert data["surface"]["skills_allowlist"] == ["web-research", "summarize"]
    assert Config.load().get("skills.allowlist") == ["web-research", "summarize"]


def test_setup_section_tools_interactive_flags_select_surface(capsys):
    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "tools",
        "--toolsets",
        "core,mcp",
        "--skills",
        "web-research,summarize",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "installer-selected toolsets: core, mcp" in out
    assert "installer-selected skills: 2 selected" in out
    assert Config.load().get("tools.toolsets") == ["core", "mcp"]
    assert Config.load().get("skills.allowlist") == ["web-research", "summarize"]


def test_setup_section_gateway_noninteractive_json(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "gateway",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--channels",
        "telegram,discord",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["section"] == "gateway"
    assert data["integrations"] == ["telegram", "discord"]
    assert data["gateway"]["channels"] == ["telegram", "discord"]
    assert Config.load().get("gateway.channels") == ["telegram", "discord"]


def test_config_setup_section_terminal_noninteractive_json(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "config",
        "setup",
        "terminal",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--exec-mode",
        "smart",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["section"] == "terminal"
    assert data["terminal"]["exec_mode"] == "smart"
    assert Config.load().get("tools.exec_mode") == "smart"


def test_setup_section_model_noninteractive_json(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "model",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--provider",
        "openai",
        "--auth",
        "skip",
        "--model",
        "gpt-5.5",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["section"] == "model"
    assert data["model"]["provider"] == "openai"
    assert data["model"]["model"] == "gpt-5.5"
    assert Config.load().get("model.provider") == "openai"
    assert Config.load().get("model.default") == "gpt-5.5"


def test_setup_section_noninteractive_requires_risk_ack(capsys):
    from aegis.cli.main import main

    rc = main(["setup", "tools", "--non-interactive", "--json"])

    assert rc == 2
    assert "accept-risk" in capsys.readouterr().out


def test_noninteractive_onboarding_requires_risk_ack(capsys):
    from aegis.cli.main import main

    rc = main(["setup", "--non-interactive", "--json"])

    assert rc == 2
    out = capsys.readouterr().out
    assert "accept-risk" in out


def test_noninteractive_provider_uses_provider_default_model(capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    rc = main([
        "setup",
        "--noninteractive",
        "--accept-risk",
        "--json",
        "--provider",
        "openai",
        "--auth",
        "skip",
        "--no-services",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["model"]["provider"] == "openai"
    # uses the provider's registry default (kept current)
    from aegis.providers.registry import get_spec
    default = get_spec("openai").default_model
    assert data["model"]["model"] == default
    assert Config.load().get("model.default") == default


def test_noninteractive_codex_auth_uses_stateless_provider(monkeypatch, tmp_path, capsys):
    import json

    from aegis.cli.main import main
    from aegis.config import Config

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "token", "account_id": "acct"}})
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    rc = main([
        "setup",
        "--noninteractive",
        "--accept-risk",
        "--json",
        "--provider",
        "openai",
        "--auth",
        "codex",
        "--no-services",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["model"]["provider"] == "codex"
    assert data["model"]["auth"] == "codex"
    assert Config.load().get("model.provider") == "codex"


def test_noninteractive_api_key_requires_env(monkeypatch, capsys):
    from aegis.cli.main import main

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = main([
        "setup",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--provider",
        "openai",
        "--auth",
        "api-key",
    ])

    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_noninteractive_rejects_unknown_toolset(capsys):
    from aegis.cli.main import main

    rc = main([
        "setup",
        "--non-interactive",
        "--accept-risk",
        "--json",
        "--toolsets",
        "core,hovercraft",
    ])

    assert rc == 2
    assert "hovercraft" in capsys.readouterr().out


def test_setup_json_requires_noninteractive(capsys):
    from aegis.cli.main import main

    rc = main(["setup", "--json"])

    assert rc == 1
    assert "--json requires --non-interactive" in capsys.readouterr().err


def test_dialogs_are_opt_in(monkeypatch):
    from aegis.onboarding import _can_use_dialogs

    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("AEGIS_ONBOARD_DIALOGS", raising=False)
    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr("sys.stdout", Tty())

    assert not _can_use_dialogs(input, print)


def test_dialogs_can_be_enabled(monkeypatch):
    from aegis.onboarding import _can_use_dialogs

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("AEGIS_ONBOARD_DIALOGS", "1")
    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr("sys.stdout", Tty())

    assert _can_use_dialogs(input, print)


def test_gateway_setup_validates_telegram_token_and_home_channel(monkeypatch):
    import os

    from aegis.config import Config
    from aegis.onboarding import OnboardingState, _configure_channels

    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS", "TELEGRAM_HOME_CHANNEL"):
        monkeypatch.delenv(key, raising=False)
    cfg = Config.load()
    answers = iter([
        "telegram",
        " 7 , @ada ",
        "y",
    ])
    secrets = iter([
        "bad-token",
        "123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ123456",
    ])
    out: list[str] = []
    state = OnboardingState()

    _configure_channels(
        cfg,
        state,
        advanced=False,
        input_func=lambda _prompt: next(answers),
        secret_func=lambda _prompt: next(secrets),
        out=out.append,
    )

    assert "Invalid Telegram token format" in "\n".join(out)
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ123456"
    assert os.environ["TELEGRAM_ALLOWED_USERS"] == "7,@ada"
    assert os.environ["TELEGRAM_HOME_CHANNEL"] == "7"
    assert state.channels == ["telegram"]
    assert Config.load().get("gateway.channels") == ["telegram"]


def test_gateway_setup_collects_advanced_discord_and_slack_fields(monkeypatch):
    import os

    from aegis.config import Config
    from aegis.onboarding import OnboardingState, _configure_channels

    for key in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_ALLOWED_USERS",
        "DISCORD_ALLOWED_CHANNELS",
        "DISCORD_HOME_CHANNEL",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_ALLOWED_USERS",
        "SLACK_ALLOWED_CHANNELS",
        "SLACK_HOME_CHANNEL",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = Config.load()
    answers = iter([
        "discord,slack",
        "U1, U2",
        "C1",
        "y",
        "U9",
        "",
        "n",
        "C-home",
    ])
    secrets = iter(["discord-token", "xoxb-token", "xapp-token"])
    state = OnboardingState()

    _configure_channels(
        cfg,
        state,
        advanced=True,
        input_func=lambda _prompt: next(answers),
        secret_func=lambda _prompt: next(secrets),
        out=lambda _msg: None,
    )

    assert state.channels == ["discord", "slack"]
    assert Config.load().get("gateway.channels") == ["discord", "slack"]
    assert os.environ["DISCORD_BOT_TOKEN"] == "discord-token"
    assert os.environ["DISCORD_ALLOWED_USERS"] == "U1,U2"
    assert os.environ["DISCORD_ALLOWED_CHANNELS"] == "C1"
    assert os.environ["DISCORD_HOME_CHANNEL"] == "C1"
    assert os.environ["SLACK_BOT_TOKEN"] == "xoxb-token"
    assert os.environ["SLACK_APP_TOKEN"] == "xapp-token"
    assert os.environ["SLACK_ALLOWED_USERS"] == "U9"
    assert os.environ["SLACK_HOME_CHANNEL"] == "C-home"


def test_onboarding_picks_free_dashboard_port(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import OnboardingState, _configure_dashboard

    cfg = Config.load()
    cfg.data["server"]["dashboard_port"] = 9119
    monkeypatch.setattr(
        "aegis.daemon.port_available",
        lambda _host, port: port == 9121,
    )
    out: list[str] = []
    state = OnboardingState()

    _configure_dashboard(cfg, state, out.append)

    assert cfg.get("server.dashboard_port") == 9121
    assert "using 9121" in "\n".join(out)
    assert ":9121/" in state.dashboard_url


def test_onboarding_existing_config_can_keep(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    cfg.set("model.provider", "ollama")
    answers = iter([
        "y",      # security notice
        "keep",   # existing config review
    ])
    out: list[str] = []

    rc = run_onboarding(
        Config.load(),
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 0
    assert Config.load().get("model.provider") == "ollama"
    assert "keeping existing setup" in "\n".join(out)


def test_onboarding_can_select_a_provider_model(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "3",           # skip credentials
        "1",           # provider-default model option
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=lambda _line: None,
    )

    from aegis.providers.registry import get_spec
    assert rc == 0
    assert Config.load().get("model.default") == get_spec("openai").default_model


def test_onboarding_can_select_qwen_wave3_provider(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "qwen",        # Wave 3 provider option
        "skip",        # skip API key for now
        "",            # model default
        "",            # exec mode default
        "6",           # skip web setup
        "",            # no messaging integrations
    ])
    out: list[str] = []

    rc = run_onboarding(
        cfg,
        quick=True,
        probe=False,
        services=False,
        input_func=lambda _prompt: next(answers),
        output_func=out.append,
    )

    assert rc == 0
    assert Config.load().get("model.provider") == "qwen"
    assert Config.load().get("model.default") == "qwen-max"
    text = "\n".join(out)
    assert "Qwen" in text
    assert "API key (QWEN_API_KEY)" in text


def test_openai_oauth_login_scope_avoids_auth_page_rejection():
    from aegis.providers.registry import OPENAI_CODEX_OAUTH, OPENAI_OAUTH

    assert "model.request" not in OPENAI_OAUTH.scopes
    assert "model.request" in OPENAI_OAUTH.required_api_scopes
    assert OPENAI_CODEX_OAUTH.required_api_scopes == []
