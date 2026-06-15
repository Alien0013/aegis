"""Model metadata — resolve a model's context window (and pricing) for any model id.

AEGIS presets hardcode one context_length per provider, so a non-default model (a custom
OpenRouter model, a new release, a local GGUF) gets the wrong window — and the
compaction/auto-split thresholds are then wrong. This resolves the *actual* window:

    explicit config  >  models.dev cache (opt-in refresh)  >  bundled snapshot  >  None

The bundled snapshot is offline-first; `aegis models refresh` pulls the full models.dev
database (4000+ models) into ~/.aegis/models_cache.json.
"""

from __future__ import annotations

import json

from . import config as cfg
from .util import read_text

# Prefix → context window (tokens). Longest matching prefix wins. Offline-first.
# Current lineups lead; older families kept as fallbacks (prefix match covers point releases,
# e.g. "gpt-5" matches gpt-5.5/5.4/5.3, "claude-sonnet-4" matches sonnet-4.6).
_BUNDLED: dict[str, int] = {
    # Anthropic — Fable 5 + Claude 4.x (Opus 4.6+ have a 1M window; 4.5 and earlier 200K)
    "claude-fable": 1_000_000,
    "claude-opus-4-8": 1_000_000, "claude-opus-4-7": 1_000_000, "claude-opus-4-6": 1_000_000,
    "claude-opus-4": 200_000, "claude-sonnet-4": 1_000_000, "claude-haiku-4": 200_000,
    "claude-3-7": 200_000, "claude-3-5": 200_000, "claude": 200_000,
    # OpenAI — GPT-5.x + reasoning
    "gpt-5.5": 1_050_000, "gpt-5.4": 1_050_000, "gpt-5.3": 400_000, "gpt-5.2": 400_000,
    "gpt-5": 400_000, "gpt-4.1": 1_047_576, "gpt-4o": 128_000, "gpt-4-turbo": 128_000,
    "o4": 200_000, "o3": 200_000, "o1": 200_000, "gpt-oss": 131_072,
    # Google — Gemini 2.5 / 3
    "gemini-3": 1_048_576, "gemini-2.5": 1_048_576, "gemini-2.0": 1_048_576,
    "gemini-1.5-pro": 2_097_152, "gemini-1.5": 1_048_576, "gemini": 1_048_576,
    # Open / others
    "deepseek-v3": 131_072, "deepseek-r1": 131_072, "deepseek": 131_072,
    "llama-4": 1_048_576, "llama-3.3": 131_072, "llama-3.1": 131_072, "llama": 131_072,
    "qwen3": 262_144, "qwen2.5": 131_072, "qwen": 131_072,
    "mistral-large": 131_072, "mistral": 131_072, "grok-4": 256_000, "grok": 131_072,
    "command-r": 131_072, "kimi-k2": 256_000, "kimi": 131_072, "minimax": 1_000_000,
}

_cache: dict | None = None


def _cache_path():
    return cfg.sub("models_cache.json")


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        raw = read_text(_cache_path())
        try:
            _cache = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            _cache = {}
    return _cache


def context_window(model: str | None, config=None) -> int | None:
    """Resolve a model's context window: models.dev cache → bundled prefix match → None."""
    m = (model or "").lower().strip()
    if not m:
        return None
    cached = _load_cache().get(m)
    if isinstance(cached, dict) and cached.get("context"):
        return int(cached["context"])
    for prefix in sorted(_BUNDLED, key=len, reverse=True):
        if prefix in m:
            return _BUNDLED[prefix]
    return None


def refresh(timeout: float = 20.0) -> int:
    """Pull the models.dev database into the disk cache. Returns the number of models cached."""
    global _cache
    import httpx
    r = httpx.get("https://models.dev/api.json", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    out: dict[str, dict] = {}
    # models.dev shape: { provider: { models: { id: {limit:{context}, cost:{...} } } } }
    for prov in (data.values() if isinstance(data, dict) else []):
        models = (prov or {}).get("models", {}) if isinstance(prov, dict) else {}
        for mid, meta in (models.items() if isinstance(models, dict) else []):
            ctx = (((meta or {}).get("limit") or {}).get("context")
                   or (meta or {}).get("context_window"))
            if ctx:
                out[str(mid).lower()] = {"context": int(ctx)}
    from .util import atomic_write
    atomic_write(_cache_path(), json.dumps(out))
    _cache = out
    return len(out)


def cmd_models(args, config) -> int:
    action = getattr(args, "action", "list") or "list"
    if action == "refresh":
        try:
            n = refresh()
            print(f"cached {n} models from models.dev → {_cache_path()}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"refresh failed (offline?): {e}")
            return 1
    # default: show the resolved window for the active model
    model = config.get("model.default", "")
    win = context_window(model, config)
    print(f"model: {model}")
    print(f"context window: {win:,} tokens" if win else "context window: unknown (using preset default)")
    return 0
