"""Per-turn token-usage logging + cost estimation with a pricing table.

Each agent turn appends a line to ~/.aegis/usage.jsonl; `aegis cost` aggregates it
into per-model spend, cache savings, and an estimated total. Prices are approximate
USD per 1M tokens and easily edited / overridden via config `pricing`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

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


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:  # noqa: BLE001
        return 0


def _anthropic_like(provider: str = "", model: str = "") -> bool:
    return "anthropic" in (provider or "").lower() or "claude" in (model or "").lower()


def _fresh_input_tokens(provider: str, model: str, input_tokens: int, cache_read: int) -> int:
    if _anthropic_like(provider, model):
        return input_tokens
    return max(0, input_tokens - cache_read)


def _entry_usage_summary(entry: dict) -> dict:
    """Return a reference-style token summary for an existing usage-log entry.

    The legacy ``input`` field is kept as logged because AEGIS providers do not all
    report it the same way. Derived ``prompt_tokens`` / ``total_tokens`` are based on
    a fresh-input view so cache reads and writes are represented once.
    """
    provider = str(entry.get("provider", "") or "")
    model = str(entry.get("model", "") or "")
    input_tokens = _to_int(entry.get("input", entry.get("input_tokens", 0)))
    output_tokens = _to_int(entry.get("output", entry.get("output_tokens", 0)))
    cache_read = _to_int(entry.get("cache_read", entry.get("cache_read_tokens", 0)))
    cache_write = _to_int(entry.get("cache_write", entry.get("cache_write_tokens", 0)))
    fresh_input = _fresh_input_tokens(provider, model, input_tokens, cache_read)
    prompt_tokens = fresh_input + cache_read + cache_write
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "fresh_input_tokens": fresh_input,
        "prompt_tokens": prompt_tokens,
        "total_tokens": prompt_tokens + output_tokens,
    }


def usage_summary(provider: str, model: str, usage) -> dict:
    """Canonical-ish summary for a ``types.Usage`` without changing Usage semantics."""
    return _entry_usage_summary({
        "provider": provider,
        "model": model,
        "input": getattr(usage, "input_tokens", 0),
        "output": getattr(usage, "output_tokens", 0),
        "cache_read": getattr(usage, "cache_read", 0),
        "cache_write": getattr(usage, "cache_write", 0),
    })


def _cost_label(amount: float | None, status: str) -> str:
    if status == "included":
        return "included"
    if amount is None or status == "unknown":
        return "n/a"
    prefix = "" if status == "actual" else "~"
    return f"{prefix}${amount:.4f}"


def _joined(values) -> str:
    return "+".join(sorted({str(v) for v in values if v})) or "none"


def _rollup_status(statuses) -> str:
    seen = {s for s in statuses if s}
    if not seen:
        return "unknown"
    if len(seen) == 1:
        return next(iter(seen))
    return "mixed"


def _pricing_source(model: str, config=None) -> str:
    source = _price_source(model, config)
    return {
        "override": "user_override",
        "builtin": "official_docs_snapshot",
        "models.dev": "models.dev",
        "none": "none",
    }.get(source, source or "none")


def _has_pricing_evidence(source: str, pin: float, pout: float, extra: dict | None = None) -> bool:
    if source != "none":
        return True
    extra = extra or {}
    return bool(pin or pout or extra.get("cache_read") or extra.get("cache_write") or extra.get("request_cost"))


def _turn_cost_metadata(e: dict, cost: float | None, pricing_source: str,
                        has_pricing: bool) -> dict:
    if e.get("cost") is not None:
        amount = float(e["cost"])
        return {
            "cost_status": "actual",
            "cost_source": "provider_generation_api",
            "pricing_source": "provider_generation_api",
            "cost_label": _cost_label(amount, "actual"),
        }
    status = "estimated" if has_pricing else "unknown"
    source = pricing_source if has_pricing else "none"
    return {
        "cost_status": status,
        "cost_source": source,
        "pricing_source": source,
        "cost_label": _cost_label(cost if has_pricing else None, status),
    }


def _usage_log_entry(provider: str, model: str, usage, config=None) -> dict:
    summary = usage_summary(provider, model, usage)
    entry = {
        "ts": now_iso(), "provider": provider, "model": model,
        "input": summary["input_tokens"], "output": summary["output_tokens"],
        "cache_read": summary["cache_read"],
        "cache_write": summary["cache_write"],
        "prompt_tokens": summary["prompt_tokens"],
        "total_tokens": summary["total_tokens"],
        "fresh_input_tokens": summary["fresh_input_tokens"]}
    reported = getattr(usage, "cost", None)
    if reported is not None:
        entry["cost"] = float(reported)   # provider-reported actual USD (vs estimate)
    pin, pout = _price(model, config)
    extra = _extra_rates(model, config)
    pricing_source = _pricing_source(model, config)
    estimated = _turn_cost(entry, pin, pout, _cache_write_mult(config), extra)
    has_pricing = _has_pricing_evidence(pricing_source, pin, pout, extra)
    if reported is None and has_pricing:
        entry["estimated_cost_usd"] = round(estimated, 6)
    entry.update(_turn_cost_metadata(entry, estimated, pricing_source, has_pricing))
    return entry


def cost_evidence(provider: str, model: str, usage, config=None) -> dict:
    """Reference-style per-turn cost and token evidence without writing the usage log."""
    entry = _usage_log_entry(provider, model, usage, config)
    amount = entry.get("cost", entry.get("estimated_cost_usd"))
    return {
        "provider": provider,
        "model": model,
        "input_tokens": entry["input"],
        "output_tokens": entry["output"],
        "cache_read": entry["cache_read"],
        "cache_write": entry["cache_write"],
        "prompt_tokens": entry["prompt_tokens"],
        "total_tokens": entry["total_tokens"],
        "fresh_input_tokens": entry["fresh_input_tokens"],
        "amount_usd": amount,
        "cost_status": entry["cost_status"],
        "cost_source": entry["cost_source"],
        "pricing_source": entry["pricing_source"],
        "cost_label": entry["cost_label"],
    }


def log(
    provider: str,
    model: str,
    usage,
    *,
    session_id: str = "",
    turn_id: str = "",
    trace_id: str = "",
    run_id: str = "",
    config=None,
) -> None:
    """Append one turn's token usage. ``usage`` is a types.Usage."""
    if not usage:
        return
    if not any((
        getattr(usage, "input_tokens", 0),
        getattr(usage, "output_tokens", 0),
        getattr(usage, "cache_read", 0),
        getattr(usage, "cache_write", 0),
    )):
        return
    try:
        entry = _usage_log_entry(provider, model, usage, config)
        for key, value in {
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "run_id": run_id,
        }.items():
            if value:
                entry[key] = str(value)
        append_line(_path(), json.dumps(entry))
    except Exception:  # noqa: BLE001
        pass


def _price(model: str, config=None) -> tuple[float, float]:
    overrides = (config.get("pricing", {}) if config else None) or {}
    table = {**PRICING, **overrides}
    m = (model or "").lower()
    for prefix, price in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if prefix.lower() in m:
            return tuple(price) if isinstance(price, (list, tuple)) else (price, price)
    # Fall back to the models.dev catalog (aegis models refresh) so models not in the
    # built-in table are still priced instead of silently costing $0.
    try:
        from .model_meta import pricing as _mm_pricing
        hit = _mm_pricing(model)
        if hit:
            return hit
    except Exception:  # noqa: BLE001
        pass
    return (0.0, 0.0)


def _cache_write_mult(config=None) -> float:
    ttl = str((config.get("prompt_caching.cache_ttl", "5m") if config else "5m") or "5m").lower()
    return 2.0 if ttl in ("1h", "60m") else 1.25


def _extra_rates(model: str, config=None) -> dict:
    """Per-model cache + per-request rates (USD/1M, USD/request) from a ``pricing`` override
    or the models.dev catalog. Empty when unknown — the caller derives cache cost from a
    multiplier instead. Lets estimates use real per-model cache rates (cf. Anthropic vs
    Gemini cache pricing) rather than assuming 0.1x / 1.25x of input everywhere."""
    overrides = (config.get("pricing", {}) if config else None) or {}
    m = (model or "").lower()
    for prefix in sorted(overrides, key=lambda k: -len(k)):
        ov = overrides[prefix]
        if prefix.lower() in m and isinstance(ov, dict):
            return {"cache_read": ov.get("cache_read"), "cache_write": ov.get("cache_write"),
                    "request_cost": ov.get("request_cost")}
    try:
        from .model_meta import pricing_full as _mm_full
        full = _mm_full(model)
        if full:
            return {"cache_read": full.get("cache_read"), "cache_write": full.get("cache_write"),
                    "request_cost": full.get("request_cost")}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _price_source(model: str, config=None) -> str:
    """Where the *estimated* price came from: override > built-in table > models.dev > none.
    Mirrors :func:`_price` precedence so the report can label its numbers."""
    overrides = (config.get("pricing", {}) if config else None) or {}
    m = (model or "").lower()
    for prefix in overrides:
        if prefix.lower() in m:
            return "override"
    for prefix in PRICING:
        if prefix.lower() in m:
            return "builtin"
    try:
        from .model_meta import pricing as _mm_pricing
        if _mm_pricing(model):
            return "models.dev"
    except Exception:  # noqa: BLE001
        pass
    return "none"


def _turn_cost(e: dict, pin: float, pout: float, cache_write_mult: float = 1.25,
               extra: dict | None = None) -> float:
    """Cost of one logged turn in USD, honoring provider cache semantics.

    A provider-reported ``cost`` (actual billing) always wins over the estimate. Otherwise:
    Anthropic reports ``input_tokens`` as the *fresh* (uncached) count with cache reads and
    writes tallied separately; OpenAI-style ``prompt_tokens`` includes cached input, so
    fresh = input − cache_read. Cache reads bill ~10%; cache writes bill a premium (1.25x at
    5m TTL, 2x at 1h). When ``extra`` carries real per-model cache rates they're used
    instead of the multiplier (the cache-write rate is scaled by the TTL factor), and a flat
    ``request_cost`` is added per turn."""
    reported = e.get("cost")
    if reported is not None:
        return float(reported)
    extra = extra or {}
    inp = e.get("input", 0)
    cr = e.get("cache_read", 0)
    cw = e.get("cache_write", 0)
    out = e.get("output", 0)
    provider = str(e.get("provider", "")).lower()
    model = str(e.get("model", "")).lower()
    anthropic_like = "anthropic" in provider or "claude" in model
    fresh = inp if anthropic_like else max(0, inp - cr)
    cr_rate = extra.get("cache_read")
    cr_rate = cr_rate if cr_rate is not None else pin * 0.1
    cw_cat = extra.get("cache_write")
    # Catalog cache-write is the 5m rate; scale to the active TTL (x1.0 at 5m, x1.6 at 1h).
    cw_rate = cw_cat * (cache_write_mult / 1.25) if cw_cat is not None else pin * cache_write_mult
    req = float(extra.get("request_cost") or 0.0)
    return (fresh * pin + cr * cr_rate + cw * cw_rate + out * pout) / 1_000_000 + req


def price_for_model(model: str, config=None) -> dict:
    """Return dashboard/API friendly per-1M-token pricing metadata."""
    input_price, output_price = _price(model, config)
    source = _pricing_source(model, config)
    return {
        "input_per_million": float(input_price),
        "output_per_million": float(output_price),
        "known": bool(input_price or output_price),
        "pricing_source": source,
    }


def cost_report(days: int = 30, config=None) -> dict:
    raw = read_text(_path())
    if not raw.strip():
        return {
            "days": days,
            "calls": 0,
            "total_cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_status": "unknown",
            "cost_source": "none",
            "pricing_source": "none",
            "cost_label": "n/a",
            "by_model": {},
        }
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cw_mult = _cache_write_mult(config)
    by_model: dict[str, dict] = {}
    total = 0.0
    calls = 0
    input_total = 0
    output_total = 0
    prompt_total = 0
    token_total = 0
    cache_read_total = 0
    cache_write_total = 0
    sources: set[str] = set()
    pricing_sources: set[str] = set()
    statuses: list[str] = []
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
        extra = _extra_rates(model, config)
        pricing_source = _pricing_source(model, config)
        cost = _turn_cost(e, pin, pout, cw_mult, extra)
        meta = _turn_cost_metadata(
            e, cost, pricing_source,
            _has_pricing_evidence(pricing_source, pin, pout, extra))
        summary = _entry_usage_summary(e)
        cr = summary["cache_read"]
        cw = summary["cache_write"]
        total += cost
        input_total += summary["input_tokens"]
        output_total += summary["output_tokens"]
        prompt_total += summary["prompt_tokens"]
        token_total += summary["total_tokens"]
        cache_read_total += cr
        cache_write_total += cw
        statuses.append(meta["cost_status"])
        sources.add(meta["cost_source"])
        pricing_sources.add(meta["pricing_source"])
        m = by_model.setdefault(model, {
            "calls": 0,
            "input": 0,
            "output": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "cost_usd": 0.0,
            "_cost_statuses": [],
            "_cost_sources": set(),
            "_pricing_sources": set(),
        })
        m["calls"] += 1
        m["input"] += summary["input_tokens"]
        m["output"] += summary["output_tokens"]
        m["input_tokens"] += summary["input_tokens"]
        m["output_tokens"] += summary["output_tokens"]
        m["prompt_tokens"] += summary["prompt_tokens"]
        m["total_tokens"] += summary["total_tokens"]
        m["cache_read"] += cr
        m["cache_write"] += cw
        m["cost_usd"] += cost
        m["_cost_statuses"].append(meta["cost_status"])
        m["_cost_sources"].add(meta["cost_source"])
        m["_pricing_sources"].add(meta["pricing_source"])
    for m in by_model.values():
        m["cost_usd"] = round(m["cost_usd"], 4)
        m["cost_status"] = _rollup_status(m.pop("_cost_statuses"))
        m["cost_source"] = _joined(m.pop("_cost_sources"))
        m["pricing_source"] = _joined(m.pop("_pricing_sources"))
        m["cost_label"] = _cost_label(m["cost_usd"], m["cost_status"])
    status = _rollup_status(statuses)
    rounded_total = round(total, 4)
    return {"days": days, "calls": calls, "total_cost_usd": round(total, 4),
            "input_tokens": input_total, "output_tokens": output_total,
            "prompt_tokens": prompt_total, "total_tokens": token_total,
            "cache_read_tokens": cache_read_total, "cache_write_tokens": cache_write_total,
            "cost_status": status, "cost_source": _joined(sources),
            "pricing_source": _joined(pricing_sources), "cost_label": _cost_label(rounded_total, status),
            "by_model": by_model}


def daily_series(days: int = 30, config=None) -> list[dict]:
    """Per-day [{date, calls, cost_usd}] for the last ``days`` days (dashboard chart).
    Days with no usage are included as zeros so the chart axis is continuous."""
    today = datetime.now(timezone.utc).date()
    series = {
        str(today - timedelta(days=i)): {
            "calls": 0,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "_cost_statuses": [],
            "_cost_sources": set(),
            "_pricing_sources": set(),
        }
        for i in range(days - 1, -1, -1)
    }
    raw = read_text(_path())
    for line in raw.strip().splitlines() if raw.strip() else []:
        try:
            e = json.loads(line)
            day = str(datetime.fromisoformat(e["ts"]).date())
        except Exception:  # noqa: BLE001
            continue
        if day not in series:
            continue
        model = e.get("model", "?")
        pin, pout = _price(model, config)
        extra = _extra_rates(model, config)
        pricing_source = _pricing_source(model, config)
        cost = _turn_cost(e, pin, pout, _cache_write_mult(config), extra)
        meta = _turn_cost_metadata(
            e, cost, pricing_source,
            _has_pricing_evidence(pricing_source, pin, pout, extra))
        summary = _entry_usage_summary(e)
        series[day]["calls"] += 1
        series[day]["cost_usd"] += cost
        series[day]["input_tokens"] += summary["input_tokens"]
        series[day]["output_tokens"] += summary["output_tokens"]
        series[day]["prompt_tokens"] += summary["prompt_tokens"]
        series[day]["total_tokens"] += summary["total_tokens"]
        series[day]["cache_read_tokens"] += summary["cache_read"]
        series[day]["cache_write_tokens"] += summary["cache_write"]
        series[day]["_cost_statuses"].append(meta["cost_status"])
        series[day]["_cost_sources"].add(meta["cost_source"])
        series[day]["_pricing_sources"].add(meta["pricing_source"])
    out = []
    for d, v in series.items():
        status = _rollup_status(v.pop("_cost_statuses"))
        v["cost_usd"] = round(v["cost_usd"], 6)
        v["cost_status"] = status
        v["cost_source"] = _joined(v.pop("_cost_sources"))
        v["pricing_source"] = _joined(v.pop("_pricing_sources"))
        v["cost_label"] = _cost_label(v["cost_usd"], status)
        out.append({"date": d, **v})
    return out


def cmd_cost(args, config) -> int:
    days = int(getattr(args, "days", 30) or 30)
    r = cost_report(days, config)
    if getattr(args, "json", False):
        print(json.dumps(r, indent=2))
        return 0
    label = {
        "actual": "actual (provider-billed)",
        "mixed": "mixed est/actual",
        "unknown": "unknown",
    }.get(r.get("cost_status"), "estimated")
    print(f"AEGIS cost — last {days} days · {r['calls']} call(s) · {r.get('cost_label', 'n/a')}"
          f"  [{label} · {r.get('cost_source', 'none')}]")
    if r.get("cache_read_tokens"):
        print(f"  cache reads:  {r['cache_read_tokens']:,} tokens (billed ~10%)")
    if r.get("cache_write_tokens"):
        print(f"  cache writes: {r['cache_write_tokens']:,} tokens (billed ~125% at 5m TTL, 200% at 1h)")
    for model, m in sorted(r["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"  {model:<28} {m['calls']:>4} calls  in {m['input']:>8,}  out {m['output']:>7,}"
              f"  ~${m['cost_usd']:.4f}")
    if not r["by_model"]:
        print("  (no usage recorded yet — run some chats)")
    return 0
