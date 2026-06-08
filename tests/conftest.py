"""Shared test fixtures: every test runs against an isolated, throwaway AEGIS_HOME."""

from __future__ import annotations

import tempfile

import pytest


# Provider/auth env that must never leak into a test run (hermetic parity with CI).
_LEAKY_ENV = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "OPENROUTER_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
    "MISTRAL_API_KEY", "TOGETHER_API_KEY", "GOOGLE_OAUTH_CLIENT_SECRET",
    "AEGIS_ONBOARD_DIALOGS", "AEGIS_PROFILE",
)


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="aegis-test-")
    monkeypatch.setenv("AEGIS_HOME", d)
    monkeypatch.setenv("TZ", "UTC")          # deterministic timestamps
    for name in _LEAKY_ENV:                  # never read a real credential in tests
        monkeypatch.delenv(name, raising=False)
    from aegis import config as cfg
    cfg.set_profile(None)
    yield d


class FakeProvider:
    """A scripted provider: yields the queued responses, then a final 'done.'."""

    context_length = 200_000
    name = "fake"
    model = "fake-model"
    api_mode = None
    auth = None

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = 0
        self.last_reasoning = None

    def describe(self):
        return "fake"

    def complete(self, messages, tools=None, stream=False, on_delta=None, model=None,
                 max_tokens=None, reasoning="off"):
        from aegis.types import LLMResponse
        self.calls += 1
        self.last_reasoning = reasoning
        if self.script:
            return self.script.pop(0)
        return LLMResponse(text="done.")
