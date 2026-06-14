"""Global delegation defaults: a distinct subagent model, a parallel-children cap (Hermes name
falling back to the existing knob), and a per-child wall-clock timeout."""

from __future__ import annotations

from aegis.config import Config
from aegis.tools.agentic import (
    _child_config_for_toolsets,
    _child_timeout,
    _delegation_model,
    _subagent_concurrency,
)


def test_delegation_model_read():
    cfg = Config({"delegation": {"provider": "anthropic", "model": "claude-haiku-4-5"}})
    assert _delegation_model(cfg) == ("anthropic", "claude-haiku-4-5")
    assert _delegation_model(Config({})) == ("", "")


def test_child_config_applies_delegation_model():
    cfg = Config({"delegation": {"provider": "anthropic", "model": "claude-haiku-4-5"},
                  "tools": {"toolsets": ["core", "web"]},
                  "model": {"provider": "openai", "default": "gpt-5"}})
    child = _child_config_for_toolsets(cfg, ["web"])
    assert child.get("model.default") == "claude-haiku-4-5"     # subagent runs on the cheap model
    assert child.get("model.provider") == "anthropic"
    assert child.get("tools.toolsets") == ["web"]               # toolset narrowing still works
    assert cfg.get("model.default") == "gpt-5"                  # parent config untouched


def test_child_config_noop_without_toolsets_or_delegation():
    cfg = Config({"model": {"default": "gpt-5"}})
    assert _child_config_for_toolsets(cfg, None) is cfg          # same object, no copy


def test_delegation_model_applies_even_without_toolset_change():
    cfg = Config({"delegation": {"model": "claude-haiku-4-5"}, "model": {"default": "gpt-5"}})
    child = _child_config_for_toolsets(cfg, None)
    assert child is not cfg
    assert child.get("model.default") == "claude-haiku-4-5"


def test_subagent_concurrency_precedence():
    assert _subagent_concurrency(Config({"delegation": {"max_concurrent_children": 8}})) == 8
    assert _subagent_concurrency(Config({"agent": {"subagent_concurrency": 6}})) == 6
    # delegation knob wins over the legacy one
    assert _subagent_concurrency(Config({"delegation": {"max_concurrent_children": 9},
                                         "agent": {"subagent_concurrency": 2}})) == 9
    assert _subagent_concurrency(Config({})) == 4                # default
    assert _subagent_concurrency(Config({"delegation": {"max_concurrent_children": 0}})) == 4


def test_child_timeout_read():
    assert _child_timeout(Config({"delegation": {"child_timeout_seconds": 45}})) == 45.0
    assert _child_timeout(Config({})) == 0.0
    assert _child_timeout(Config({"delegation": {"child_timeout_seconds": "bad"}})) == 0.0
