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
    assert (workspace / "USER.md").exists()
    assert (workspace / "README.md").exists()


def test_onboarding_can_select_oauth(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.setattr(
        "aegis.onboarding._ensure_codex_cli_login",
        lambda _input_func, _out: True,
    )
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
    assert "ChatGPT subscription via Codex CLI" in text
    assert "Auth:            codex" in text


def test_onboarding_codex_login_failure_does_not_fall_back_to_api_key(monkeypatch):
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

    assert rc == 0
    assert "OPENAI_API_KEY" not in os.environ
    text = "\n".join(out)
    assert "Auth:            skipped" in text
    assert Config.load().get("model.provider") == "codex"


def test_onboarding_oauth_missing_scope_can_skip_without_probe(monkeypatch):
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

    assert rc == 0
    text = "\n".join(out)
    assert "Skipping model connection test until usable credentials are configured." in text
    assert "Auth:            skipped" in text


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
    assert "CONFIGURING TOOLS & SKILLS" in text
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
    assert data["model"]["model"] == "gpt-4o"
    assert Config.load().get("model.default") == "gpt-4o"


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
        "2",           # GPT-5.2 model option
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

    assert rc == 0
    assert Config.load().get("model.default") == "gpt-5.2"


def test_openai_oauth_login_scope_avoids_auth_page_rejection():
    from aegis.providers.registry import OPENAI_CODEX_OAUTH, OPENAI_OAUTH

    assert "model.request" not in OPENAI_OAUTH.scopes
    assert "model.request" in OPENAI_OAUTH.required_api_scopes
    assert OPENAI_CODEX_OAUTH.required_api_scopes == []
