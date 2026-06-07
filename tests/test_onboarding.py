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
    assert calls == ["openai"]
    text = "\n".join(out)
    assert "Choose authentication method" in text
    assert "OAuth browser login" in text


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
    from aegis.providers.registry import OPENAI_OAUTH

    assert "model.request" not in OPENAI_OAUTH.scopes
    assert "model.request" in OPENAI_OAUTH.required_api_scopes
