from __future__ import annotations

import json

from aegis import usage_log
from aegis.types import Usage


def _entries(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_usage_summary_derives_prompt_and_total_with_cache_fields():
    anthropic = usage_log.usage_summary(
        "anthropic", "claude-sonnet-4-6", Usage(1000, 500, cache_read=200, cache_write=300)
    )
    assert anthropic["input_tokens"] == 1000
    assert anthropic["output_tokens"] == 500
    assert anthropic["cache_read"] == 200
    assert anthropic["cache_write"] == 300
    assert anthropic["fresh_input_tokens"] == 1000
    assert anthropic["prompt_tokens"] == 1500
    assert anthropic["total_tokens"] == 2000

    openai = usage_log.usage_summary(
        "openai", "gpt-4o", Usage(1200, 50, cache_read=200, cache_write=25)
    )
    assert openai["fresh_input_tokens"] == 1000
    assert openai["prompt_tokens"] == 1225
    assert openai["total_tokens"] == 1275


def test_log_writes_usage_summary_and_estimated_cost_evidence(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)

    usage_log.log(
        "anthropic",
        "claude-sonnet-4-6",
        Usage(1000, 500, cache_read=200, cache_write=300),
        session_id="sess_stage_w",
        turn_id="turn_stage_w",
        trace_id="trace_stage_w",
        run_id="run_stage_w",
    )

    entry = _entries(path)[0]
    assert entry["input"] == 1000
    assert entry["output"] == 500
    assert entry["cache_read"] == 200
    assert entry["cache_write"] == 300
    assert entry["prompt_tokens"] == 1500
    assert entry["total_tokens"] == 2000
    assert entry["cost_status"] == "estimated"
    assert entry["cost_source"] == "official_docs_snapshot"
    assert entry["pricing_source"] == "official_docs_snapshot"
    assert entry["cost_label"].startswith("~$")
    assert entry["estimated_cost_usd"] > 0
    assert entry["session_id"] == "sess_stage_w"
    assert entry["turn_id"] == "turn_stage_w"
    assert entry["trace_id"] == "trace_stage_w"
    assert entry["run_id"] == "run_stage_w"


def test_provider_reported_cost_wins_and_is_labeled_actual(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)
    actual = Usage(999_999, 999_999)
    actual.cost = 0.0123

    usage_log.log("openrouter", "stage-w-unknown-model", actual)
    entry = _entries(path)[0]
    report = usage_log.cost_report(30)

    assert entry["cost"] == 0.0123
    assert "estimated_cost_usd" not in entry
    assert entry["cost_status"] == "actual"
    assert entry["cost_source"] == "provider_generation_api"
    assert entry["pricing_source"] == "provider_generation_api"
    assert entry["cost_label"] == "$0.0123"
    assert report["total_cost_usd"] == 0.0123
    assert report["cost_status"] == "actual"
    assert report["cost_source"] == "provider_generation_api"
    assert report["by_model"]["stage-w-unknown-model"]["cost_label"] == "$0.0123"


def test_cost_report_rolls_up_usage_and_mixed_cost_evidence(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)
    usage_log.log(
        "anthropic", "claude-sonnet-4-6", Usage(1000, 500, cache_read=200, cache_write=300)
    )
    actual = Usage(100, 50, cache_read=10, cache_write=5)
    actual.cost = 0.02
    usage_log.log("openrouter", "stage-w-billed-model", actual)

    report = usage_log.cost_report(30)

    assert report["calls"] == 2
    assert report["input_tokens"] == 1100
    assert report["output_tokens"] == 550
    assert report["cache_read_tokens"] == 210
    assert report["cache_write_tokens"] == 305
    assert report["prompt_tokens"] == 1605
    assert report["total_tokens"] == 2155
    assert report["cost_status"] == "mixed"
    assert report["cost_label"].startswith("~$")
    assert report["cost_source"] == "official_docs_snapshot+provider_generation_api"
    assert report["pricing_source"] == "official_docs_snapshot+provider_generation_api"

    model = report["by_model"]["claude-sonnet-4-6"]
    assert model["input"] == model["input_tokens"] == 1000
    assert model["output"] == model["output_tokens"] == 500
    assert model["cache_read"] == 200
    assert model["cache_write"] == 300
    assert model["prompt_tokens"] == 1500
    assert model["total_tokens"] == 2000
    assert model["cost_status"] == "estimated"
    assert model["pricing_source"] == "official_docs_snapshot"


def test_daily_series_includes_usage_and_cost_evidence(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)
    usage_log.log(
        "anthropic", "claude-sonnet-4-6", Usage(1000, 500, cache_read=200, cache_write=300)
    )

    row = usage_log.daily_series(1)[0]

    assert row["calls"] == 1
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["cache_read_tokens"] == 200
    assert row["cache_write_tokens"] == 300
    assert row["prompt_tokens"] == 1500
    assert row["total_tokens"] == 2000
    assert row["cost_status"] == "estimated"
    assert row["cost_source"] == "official_docs_snapshot"
    assert row["pricing_source"] == "official_docs_snapshot"
    assert row["cost_label"].startswith("~$")


def test_unknown_pricing_is_explicitly_unknown(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)
    monkeypatch.setattr(usage_log, "_price_source", lambda _model, _config=None: "none")
    monkeypatch.setattr(usage_log, "_price", lambda _model, _config=None: (0.0, 0.0))
    monkeypatch.setattr(usage_log, "_extra_rates", lambda _model, _config=None: {})

    usage_log.log("custom", "stage-w-unpriced-model", Usage(100, 20))

    entry = _entries(path)[0]
    report = usage_log.cost_report(30)
    assert "estimated_cost_usd" not in entry
    assert entry["cost_status"] == "unknown"
    assert entry["cost_source"] == "none"
    assert entry["pricing_source"] == "none"
    assert entry["cost_label"] == "n/a"
    assert report["cost_status"] == "unknown"
    assert report["cost_source"] == "none"
    assert report["pricing_source"] == "none"
    assert report["cost_label"] == "n/a"


def test_loop_trace_cost_uses_usage_log_cache_write_math():
    from aegis.agent.loop import _usage_cost_usd
    from aegis.config import Config

    usage = Usage(1000, 500, cache_read=200, cache_write=300)
    cfg = Config.load()
    expected = usage_log._turn_cost(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
        },
        *usage_log._price("claude-sonnet-4-6", cfg),
        usage_log._cache_write_mult(cfg),
        usage_log._extra_rates("claude-sonnet-4-6", cfg),
    )

    cost = _usage_cost_usd("claude-sonnet-4-6", usage, cfg, provider="anthropic")

    assert cost == round(expected, 6)
    assert cost > 0.00996   # stale loop estimator ignored cache writes here


def test_agent_usage_log_is_joinable_to_turn_trace_and_run(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_log, "_path", lambda: path)
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    provider = FakeProvider([
        LLMResponse(text="joinable", usage=Usage(1000, 500, cache_read=200, cache_write=300))
    ])
    agent = Agent(config=cfg, provider=provider, session=Session.create("joinable"), cwd=tmp_path)
    agent._surface_run_id = "run_stage_w_joinable"

    result = agent.run("write a joinable usage record")

    assert result.content == "joinable"
    entry = _entries(path)[0]
    assert entry["session_id"] == agent.session.id
    assert entry["turn_id"] == agent.session.meta["last_turn_id"]
    assert entry["trace_id"] == agent.session.meta["last_trace_id"]
    assert entry["run_id"] == "run_stage_w_joinable"
    assert entry["cache_write"] == 300
    cost = agent.session.meta["usage"]["last_turn_cost"]
    assert cost["cache_write"] == 300
    assert agent.session.meta["usage"]["last_turn_cost_status"] == cost["cost_status"]
