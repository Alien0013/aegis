from __future__ import annotations


def test_onboarding_rejects_key_as_provider(monkeypatch):
    from aegis.config import Config
    from aegis.onboarding import run_onboarding

    cfg = Config.load()
    answers = iter([
        "y",           # security notice
        "sk-proj-oops",# provider prompt: should be rejected as a provider
        "1",           # OpenAI
        "",            # model default
        "y",           # configure OPENAI_API_KEY
        "",            # exec mode default
        "6",           # skip web setup
        "n",           # no Telegram
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
    assert "sk-test" == __import__("os").environ["OPENAI_API_KEY"]
