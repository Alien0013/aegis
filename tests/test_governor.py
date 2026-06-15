"""Cost & latency governor: complexity classification, downshift routing, budget caps."""

import pytest

from aegis import governor as g
from aegis.config import Config


@pytest.mark.parametrize("prompt,expected", [
    ("rename foo to bar", "simple"),
    ("fix the typo in the README", "simple"),
    ("what is the capital of France", "simple"),
    ("refactor the auth module to use dependency injection", "hard"),
    ("why is this test flaky? investigate the root cause", "hard"),
    ("design a migration plan for the database", "hard"),
    ("optimize this hot loop for concurrency safety", "hard"),
    ("", "simple"),
])
def test_classify_complexity(prompt, expected):
    assert g.classify_complexity(prompt) == expected


def test_long_or_code_prompts_are_hard():
    assert g.classify_complexity("x" * 300) == "hard"
    assert g.classify_complexity("do this\n```\ncode\n```") == "hard"
    assert g.classify_complexity("line1\nline2\nline3\nline4\nline5") == "hard"


def test_downshift_off_by_default():
    cfg = Config.load()
    assert g.downshift_model("rename a var", cfg) == ""        # auto_downshift off


def test_downshift_routes_simple_turns_when_enabled():
    cfg = Config.load()
    cfg.set("budget.auto_downshift", True)
    cfg.set("budget.cheap_model", "claude-haiku-4-5")
    assert g.downshift_model("rename a var", cfg) == "claude-haiku-4-5"   # simple -> cheap
    assert g.downshift_model("refactor the architecture", cfg) == ""     # hard -> keep strong


def test_downshift_needs_cheap_model_set():
    cfg = Config.load()
    cfg.set("budget.auto_downshift", True)
    assert g.downshift_model("rename a var", cfg) == ""        # no cheap_model configured


def test_budget_status_disabled_by_default():
    st = g.budget_status(Config.load())
    assert st.enabled is False and st.enforce == "off" and st.over is False


def test_budget_status_over_daily_cap(monkeypatch):
    cfg = Config.load()
    cfg.set("budget.enabled", True)
    cfg.set("budget.daily_usd", 1.0)
    cfg.set("budget.enforce", "block")
    monkeypatch.setattr(g, "spend_window", lambda config, days=1: 2.5)
    st = g.budget_status(cfg)
    assert st.over_daily is True and st.over is True and st.should_block is True
    assert "blocked" in st.warning


def test_budget_status_session_cap():
    cfg = Config.load()
    cfg.set("budget.enabled", True)
    cfg.set("budget.session_usd", 0.50)
    st = g.budget_status(cfg, session_spend=0.75)
    assert st.over_session is True and st.over is True


def test_near_cap_warning(monkeypatch):
    cfg = Config.load()
    cfg.set("budget.enabled", True)
    cfg.set("budget.daily_usd", 10.0)
    monkeypatch.setattr(g, "spend_window", lambda config, days=1: 8.5)
    st = g.budget_status(cfg)
    assert st.over is False and "85%" in st.warning


def test_block_only_when_enforce_block(monkeypatch):
    cfg = Config.load()
    cfg.set("budget.enabled", True)
    cfg.set("budget.daily_usd", 1.0)
    cfg.set("budget.enforce", "warn")
    monkeypatch.setattr(g, "spend_window", lambda config, days=1: 5.0)
    st = g.budget_status(cfg)
    assert st.over is True and st.should_block is False        # warn != block
