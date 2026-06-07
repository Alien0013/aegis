"""Shared test fixtures: every test runs against an isolated, throwaway AEGIS_HOME."""

from __future__ import annotations

import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="aegis-test-")
    monkeypatch.setenv("AEGIS_HOME", d)
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
