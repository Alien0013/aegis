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


def test_onboarding_can_select_oauth(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    calls: list[str] = []
    monkeypatch.setattr(
        "aegis.onboarding._oauth_login",
        lambda provider, _spec, _out: calls.append(provider) or True,
    )
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # OAuth auth
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
    assert calls == ["openai-codex"]
    assert Config.load().get("model.provider") == "openai-codex"
    text = "\n".join(out)
    assert "Choose authentication method" in text
    assert "ChatGPT / Codex OAuth" in text


def test_onboarding_oauth_missing_scope_falls_back_to_api_key(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.setattr("aegis.onboarding._oauth_login", lambda *_args: False)
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # OAuth auth
        "y",           # configure API key fallback
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
    assert os.environ["OPENAI_API_KEY"] == "sk-fallback"
    text = "\n".join(out)
    assert "Use an API key if OAuth is unavailable for this provider." in text
    assert "Auth:            api_key" in text
    assert Config.load().get("model.provider") == "openai"


def test_onboarding_oauth_missing_scope_can_skip_without_probe(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    monkeypatch.setattr("aegis.onboarding._oauth_login", lambda *_args: False)

    def fail_probe(*_args):
        raise AssertionError("probe should not run without usable credentials")

    monkeypatch.setattr("aegis.onboarding._probe_model", fail_probe)
    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "1",           # OpenAI
        "1",           # OAuth auth
        "n",           # do not configure API key fallback
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
    assert "OpenAI (GPT-4o / GPT-5 API) (1)" not in text


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
