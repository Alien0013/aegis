"""Per-turn token-usage logging + cost estimation with a pricing table.

Each agent turn appends a line to ~/.aegis/usage.jsonl; `aegis cost` aggregates it
into per-model spend, cache savings, and an estimated total. Prices are approximate
USD per 1M tokens and easily edited / overridden via config `pricing`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import config as cfg
from .util import append_line, now_iso, read_text

# Approximate USD per 1M tokens (input, output). Prefix-matched on model id.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable": (10.0, 50.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "o3": (2.0, 8.0),
    "o4-mini": (1.10, 4.40),
    "gpt-5": (1.25, 10.0),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-pro": (1.25, 5.0),
    "deepseek": (0.27, 1.10),
    "grok": (2.0, 10.0),
    "mistral-large": (2.0, 6.0),
    "llama": (0.20, 0.20),
    "qwen": (0.40, 1.20),
}


def _path():
    return cfg.sub("usage.jsonl")


def log(provider: str, model: str, usage) -> None:
    """Append one turn's token usage. ``usage`` is a types.Usage."""
    if not usage or (usage.input_tokens == 0 and usage.output_tokens == 0):
        return
    try:
        append_line(_path(), json.dumps({
            "ts": now_iso(), "provider": provider, "model": model,
            "input": usage.input_tokens, "output": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read", 0),
            "cache_write": getattr(usage, "cache_write", 0)}))
    except Exception:  # noqa: BLE001
        pass


def _price(model: str, config=None) -> tuple[float, float]:
    overrides = (config.get("pricing", {}) if config else None) or {}
    table = {**PRICING, **overrides}
    m = (model or "").lower()
    for prefix, price in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if prefix.lower() in m:
            return tuple(price) if isinstance(price, (list, tuple)) else (price, price)
    return (0.0, 0.0)


def cost_report(days: int = 30, config=None) -> dict:
    raw = read_text(_path())
    if not raw.strip():
        return {"days": days, "calls": 0, "total_cost_usd": 0.0, "by_model": {}}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_model: dict[str, dict] = {}
    total = 0.0
    calls = 0
    cache_read_total = 0
    for line in raw.strip().splitlines():
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["ts"])
        except Exception:  # noqa: BLE001
            continue
        if ts < cutoff:
            continue
        calls += 1
        model = e.get("model", "?")
        pin, pout = _price(model, config)
        # cached input tokens billed at ~10% (Anthropic/OpenAI cache read discount)
        cr = e.get("cache_read", 0)
        fresh_in = max(0, e["input"] - cr)
        cost = (fresh_in * pin + cr * pin * 0.1 + e["output"] * pout) / 1_000_000
        total += cost
        cache_read_total += cr
        m = by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["input"] += e["input"]
        m["output"] += e["output"]
        m["cache_read"] += cr
        m["cost_usd"] = round(m["cost_usd"] + cost, 4)
    return {"days": days, "calls": calls, "total_cost_usd": round(total, 4),
            "cache_read_tokens": cache_read_total, "by_model": by_model}


def daily_series(days: int = 30, config=None) -> list[dict]:
    """Per-day [{date, calls, cost_usd}] for the last ``days`` days (dashboard chart).
    Days with no usage are included as zeros so the chart axis is continuous."""
    today = datetime.now(timezone.utc).date()
    series = {str(today - timedelta(days=i)): {"calls": 0, "cost_usd": 0.0}
              for i in range(days - 1, -1, -1)}
    raw = read_text(_path())
    for line in raw.strip().splitlines() if raw.strip() else []:
        try:
            e = json.loads(line)
            day = str(datetime.fromisoformat(e["ts"]).date())
        except Exception:  # noqa: BLE001
            continue
        if day not in series:
            continue
        pin, pout = _price(e.get("model", "?"), config)
        cr = e.get("cache_read", 0)
        fresh_in = max(0, e["input"] - cr)
        series[day]["calls"] += 1
        series[day]["cost_usd"] = round(
            series[day]["cost_usd"]
            + (fresh_in * pin + cr * pin * 0.1 + e["output"] * pout) / 1_000_000, 6)
    return [{"date": d, **v} for d, v in series.items()]


def cmd_cost(args, config) -> int:
    days = int(getattr(args, "days", 30) or 30)
    r = cost_report(days, config)
    if getattr(args, "json", False):
        print(json.dumps(r, indent=2))
        return 0
    print(f"AEGIS cost — last {days} days · {r['calls']} call(s) · ~${r['total_cost_usd']:.4f}")
    if r.get("cache_read_tokens"):
        print(f"  cache reads: {r['cache_read_tokens']:,} tokens (billed ~10%)")
    for model, m in sorted(r["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"  {model:<28} {m['calls']:>4} calls  in {m['input']:>8,}  out {m['output']:>7,}"
              f"  ~${m['cost_usd']:.4f}")
    if not r["by_model"]:
        print("  (no usage recorded yet — run some chats)")
    return 0
