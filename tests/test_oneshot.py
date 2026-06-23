from __future__ import annotations


def test_run_oneshot_uses_template_and_strips_fence(monkeypatch):
    from aegis.agent.oneshot import run_oneshot
    from aegis.config import Config
    from aegis.types import LLMResponse

    seen = {}

    class Provider:
        def complete(self, messages, **kwargs):
            seen["messages"] = messages
            seen["kwargs"] = kwargs
            return LLMResponse(text="```text\nfix(api): add managed cron fire\n```")

    monkeypatch.setattr("aegis.providers.registry.build_aux_provider", lambda *a, **k: Provider())

    text = run_oneshot(
        config=Config.load(),
        template="commit_message",
        variables={"diff": "diff --git a/a b/a"},
        max_tokens=77,
    )

    assert text == "fix(api): add managed cron fire"
    assert seen["messages"][0].role == "system"
    assert "git commit messages" in seen["messages"][0].content
    assert "Diff to describe" in seen["messages"][1].content
    assert seen["kwargs"]["max_tokens"] == 77


def test_run_oneshot_requires_prompt():
    import pytest
    from aegis.agent.oneshot import run_oneshot
    from aegis.config import Config

    with pytest.raises(ValueError):
        run_oneshot(config=Config.load())
