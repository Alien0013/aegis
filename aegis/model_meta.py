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
import ipaddress
import re
from urllib.parse import urlparse

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
    "gpt-5.5": 1_050_000, "gpt-5.4-nano": 400_000, "gpt-5.4-mini": 400_000,
    "gpt-5.4": 1_050_000, "gpt-5.3-codex-spark": 128_000, "gpt-5.3": 400_000,
    "gpt-5.2": 400_000, "gpt-5.1-chat": 128_000,
    "gpt-5": 400_000, "gpt-4.1": 1_047_576, "gpt-4o": 128_000, "gpt-4-turbo": 128_000,
    "o4": 200_000, "o3": 200_000, "o1": 200_000, "gpt-oss": 131_072,
    # Google — Gemini 2.5 / 3
    "gemini-3": 1_048_576, "gemini-2.5": 1_048_576, "gemini-2.0": 1_048_576,
    "gemini-1.5-pro": 2_097_152, "gemini-1.5": 1_048_576, "gemini": 1_048_576,
    # Open / others
    "deepseek-v3": 131_072, "deepseek-r1": 131_072, "deepseek": 131_072,
    "llama-4": 1_048_576, "llama-3.3": 131_072, "llama-3.1": 131_072, "llama": 131_072,
    "qwen3": 262_144, "qwen2.5": 131_072, "qwen": 131_072,
    "mistral-large": 131_072, "mistral": 131_072, "grok-build": 131_072, "grok-4": 256_000, "grok": 131_072,
    "command-r": 131_072, "kimi-k2": 256_000, "kimi": 131_072, "minimax": 1_000_000,
    "glm-5.2": 1_048_576, "glm-5.1": 200_000, "glm-4.6": 128_000,
}

_CODEX_PROVIDER_NAMES = {"codex", "codex-app-server", "openai-codex"}
_CODEX_CONTEXT_FALLBACK: dict[str, int] = {
    "gpt-5.1-codex-max": 272_000,
    "gpt-5.1-codex-mini": 272_000,
    "gpt-5.3-codex-spark": 128_000,
    "gpt-5.3-codex": 272_000,
    "gpt-5.2-codex": 272_000,
    "gpt-5.4-mini": 272_000,
    "gpt-5.5": 272_000,
    "gpt-5.4": 272_000,
    "gpt-5.2": 272_000,
    "gpt-5": 272_000,
}

_cache: dict | None = None

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_CONTAINER_LOCAL_SUFFIXES = (
    ".local",
    ".localhost",
    ".docker.internal",
    ".podman.internal",
    ".lima.internal",
)
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


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


def _provider_slug(provider: str | None) -> str:
    return (provider or "").lower().strip().replace("_", "-")


def _is_codex_route(provider: str | None, base_url: str | None = None) -> bool:
    provider_slug = _provider_slug(provider)
    if provider_slug in _CODEX_PROVIDER_NAMES:
        return True
    url = (base_url or "").lower().strip()
    return url.startswith("codex://") or "chatgpt.com/backend-api/codex" in url


def _codex_context_window(model: str) -> int | None:
    model_lower = model.lower().strip()
    for slug, ctx in sorted(_CODEX_CONTEXT_FALLBACK.items(), key=lambda item: len(item[0]), reverse=True):
        if slug in model_lower:
            return ctx
    return None


def _normalize_base_url(base_url: str | None) -> str:
    return str(base_url or "").strip().rstrip("/")


def is_local_endpoint(base_url: str | None) -> bool:
    """Return True for loopback, private-network, container, or mesh-local endpoints."""
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return False
    url = normalized if "://" in normalized else f"http://{normalized}"
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in _LOCAL_HOSTS:
        return True
    if any(host.endswith(suffix) for suffix in _CONTAINER_LOCAL_SUFFIXES):
        return True
    if "." not in host:
        return True
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return True
        if isinstance(addr, ipaddress.IPv4Address) and addr in _TAILSCALE_CGNAT:
            return True
    except ValueError:
        pass
    parts = host.split(".")
    if len(parts) == 4:
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        return (
            first == 10
            or (first == 172 and 16 <= second <= 31)
            or (first == 192 and second == 168)
            or (first == 100 and 64 <= second <= 127)
        )
    return False


def _cache_keys(model: str, provider: str | None) -> list[str]:
    keys: list[str] = []
    provider_slug = _provider_slug(provider)
    if provider_slug:
        keys.append(f"{provider_slug}/{model}")
        keys.append(f"{provider_slug}:{model}")
    keys.append(model)
    return keys


def _context_from_cache(model: str, provider: str | None = None) -> int | None:
    cache = _load_cache()
    for key in _cache_keys(model, provider):
        cached = cache.get(key)
        if isinstance(cached, dict) and cached.get("context"):
            return int(cached["context"])
    return None


def pricing(model: str | None, provider: str | None = None) -> tuple[float, float] | None:
    """Resolve (input, output) USD-per-1M-token pricing from the models.dev cache.

    Returns ``None`` when the model isn't in the cache (caller falls back to its own
    table). Populated by ``aegis models refresh``."""
    m = (model or "").lower().strip()
    if not m:
        return None
    cache = _load_cache()
    for key in _cache_keys(m, provider):
        cached = cache.get(key)
        if isinstance(cached, dict) and isinstance(cached.get("cost"), dict):
            cost = cached["cost"]
            inp, outp = float(cost.get("input") or 0.0), float(cost.get("output") or 0.0)
            if inp or outp:
                return (inp, outp)
    return None


def pricing_full(model: str | None, provider: str | None = None) -> dict | None:
    """Full per-1M-token pricing from the models.dev cache, including cache and request
    fees: ``{input, output, cache_read, cache_write, request_cost}`` (cache/request keys
    are ``None`` when the catalog doesn't list them). Returns ``None`` when uncached."""
    m = (model or "").lower().strip()
    if not m:
        return None
    cache = _load_cache()
    for key in _cache_keys(m, provider):
        cached = cache.get(key)
        if isinstance(cached, dict) and isinstance(cached.get("cost"), dict):
            cost = cached["cost"]
            inp, outp = float(cost.get("input") or 0.0), float(cost.get("output") or 0.0)
            if not (inp or outp):
                continue

            def _opt(field, cost=cost):
                v = cost.get(field)
                return float(v) if v is not None else None

            return {"input": inp, "output": outp, "cache_read": _opt("cache_read"),
                    "cache_write": _opt("cache_write"), "request_cost": _opt("request_cost")}
    return None


def context_window(
    model: str | None,
    config=None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> int | None:
    """Resolve a model's context window: provider overrides → cache → bundled."""
    m = (model or "").lower().strip()
    if not m:
        return None
    if provider is None and config is not None:
        try:
            provider = config.get("model.provider")
        except Exception:  # noqa: BLE001
            provider = None
    if base_url is None and config is not None:
        try:
            base_url = config.get("model.base_url")
        except Exception:  # noqa: BLE001
            base_url = None
    if _is_codex_route(provider, base_url):
        codex_ctx = _codex_context_window(m)
        if codex_ctx:
            return codex_ctx
    cached = _context_from_cache(m, provider)
    if cached:
        return cached
    for prefix in sorted(_BUNDLED, key=len, reverse=True):
        if prefix in m:
            return _BUNDLED[prefix]
    return None


def query_ollama_num_ctx(model: str, base_url: str, api_key: str = "") -> int | None:
    """Return Ollama's runtime context window for ``model`` via ``/api/show``.

    Prefer an explicit Modelfile ``num_ctx`` parameter, then fall back to GGUF
    ``model_info.*context_length``. Returns ``None`` when the endpoint is not
    reachable or does not look like Ollama.
    """
    import httpx

    model_name = str(model or "").strip()
    for prefix in ("ollama/", "ollama:", "local:"):
        if model_name.lower().startswith(prefix):
            model_name = model_name[len(prefix):]
            break
    if not model_name:
        return None
    server_url = str(base_url or "").strip().rstrip("/")
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]
    if not server_url:
        return None
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=3.0, headers=headers) as client:
            response = client.post(f"{server_url}/api/show", json={"name": model_name})
        if getattr(response, "status_code", 0) != 200:
            return None
        data = response.json()
    except Exception:  # noqa: BLE001
        return None
    params = str(data.get("parameters") or "")
    for line in params.splitlines():
        if "num_ctx" not in line:
            continue
        match = re.search(r"\bnum_ctx\b\s+(\d+)", line)
        if match:
            return int(match.group(1))
    model_info = data.get("model_info") or {}
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            if "context_length" in str(key) and isinstance(value, (int, float)):
                return int(value)
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
    for provider_name, prov in (data.items() if isinstance(data, dict) else []):
        models = (prov or {}).get("models", {}) if isinstance(prov, dict) else {}
        for mid, meta in (models.items() if isinstance(models, dict) else []):
            ctx = (((meta or {}).get("limit") or {}).get("context")
                   or (meta or {}).get("context_window"))
            cost = (meta or {}).get("cost") or {}
            row: dict = {}
            if ctx:
                row["context"] = int(ctx)
            # models.dev cost is USD per 1M tokens: {input, output, cache_read, cache_write}
            # (plus an optional flat per-request fee). Preserve the cache + request fields so
            # cost estimates use real per-model cache rates instead of a derived multiplier.
            if isinstance(cost, dict) and (cost.get("input") is not None
                                           or cost.get("output") is not None):
                row["cost"] = {
                    "input": float(cost.get("input") or 0.0),
                    "output": float(cost.get("output") or 0.0),
                }
                for src, dst in (("cache_read", "cache_read"), ("cache_write", "cache_write"),
                                 ("cache_reads", "cache_read"), ("cache_writes", "cache_write"),
                                 ("request", "request_cost"), ("request_cost", "request_cost")):
                    if cost.get(src) is not None:
                        row["cost"][dst] = float(cost[src])
            if not row:
                continue
            model_key = str(mid).lower()
            out[f"{str(provider_name).lower()}/{model_key}"] = row
            out.setdefault(model_key, row)
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
    win = context_window(
        model,
        config,
        provider=config.get("model.provider", "") if config is not None else None,
        base_url=config.get("model.base_url", "") if config is not None else None,
    )
    print(f"model: {model}")
    print(f"context window: {win:,} tokens" if win else "context window: unknown (using preset default)")
    return 0
