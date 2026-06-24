"""Cost-accuracy fixes: cache_write is billed + surfaced, Anthropic fresh-input semantics
are honored, and unknown models fall back to the models.dev catalog instead of $0."""

from __future__ import annotations

from aegis import model_meta, usage_log
from aegis.config import Config
from aegis.types import Usage


def test_cache_write_is_counted_and_surfaced():
    cfg = Config.load()
    usage_log.log("anthropic", "claude-sonnet-4-6", Usage(1000, 500, 10_000, 2000))
    r = usage_log.cost_report(30, cfg)
    assert r["cache_read_tokens"] == 10_000
    assert r["cache_write_tokens"] == 2000          # previously dropped entirely
    m = r["by_model"]["claude-sonnet-4-6"]
    assert m["cache_write"] == 2000
    # (1000*3 + 10000*3*0.1 + 2000*3*1.25 + 500*15) / 1e6 = 0.021
    assert abs(m["cost_usd"] - 0.021) < 1e-6


def test_anthropic_input_is_fresh_not_double_discounted():
    e = {"provider": "anthropic", "model": "claude-sonnet-4-6",
         "input": 1000, "cache_read": 10_000, "cache_write": 0, "output": 0}
    # fresh = input (1000), NOT max(0, input - cache_read) = 0
    cost = usage_log._turn_cost(e, 3.0, 15.0, 1.25)
    assert abs(cost - (1000 * 3 + 10_000 * 3 * 0.1) / 1_000_000) < 1e-9


def test_openai_input_includes_cache_read():
    e = {"provider": "openai", "model": "gpt-4o",
         "input": 11_000, "cache_read": 10_000, "cache_write": 0, "output": 0}
    # OpenAI prompt_tokens includes cached, so fresh = 11000 - 10000 = 1000
    cost = usage_log._turn_cost(e, 2.5, 10.0, 1.25)
    assert abs(cost - (1000 * 2.5 + 10_000 * 2.5 * 0.1) / 1_000_000) < 1e-9


def test_cache_write_multiplier_tracks_ttl():
    assert usage_log._cache_write_mult(None) == 1.25
    cfg = Config.load()
    cfg.set("prompt_caching.cache_ttl", "1h")
    assert usage_log._cache_write_mult(cfg) == 2.0


def test_unknown_model_falls_back_to_models_dev(monkeypatch):
    # not in the built-in PRICING table → would have cost $0 before
    assert usage_log._price("acme-frontier-9000", None) == (0.0, 0.0)
    monkeypatch.setattr(model_meta, "pricing", lambda model, provider=None: (4.0, 12.0))
    assert usage_log._price("acme-frontier-9000", None) == (4.0, 12.0)


def test_models_dev_pricing_reads_cached_cost(monkeypatch):
    monkeypatch.setattr(model_meta, "_load_cache",
                        lambda: {"anthropic/claude-x": {"context": 200000,
                                                        "cost": {"input": 3.0, "output": 15.0}}})
    assert model_meta.pricing("claude-x", "anthropic") == (3.0, 15.0)
    assert model_meta.pricing("nope") is None
