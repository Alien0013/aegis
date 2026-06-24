"""Built-in provider catalog + resolution of a concrete ``Provider`` from config.

Auth precedence (per provider):
  1. explicit ``base_url`` override in config -> treat as custom/local (api-key or none)
  2. an API key in the environment            -> API key
  3. a valid OAuth login in auth.json         -> OAuth
  4. Codex app-server auth                    -> the separate ``codex-app-server`` provider
API keys win when both are configured because OAuth scopes can be identity-only.
``aegis auth status`` prints the resolution.
"""

from __future__ import annotations

import difflib
import os
import time
from dataclasses import dataclass, field

import httpx

from .. import config as cfg
from ..constants import MIN_CONTEXT_LENGTH
from .anthropic import AnthropicTransport
from .auth import ApiKeyAuth, AuthProvider, AuthStore, CodexBackendAuth, CodexCliAuth, OAuthAuth, OAuthConfig
from .base import ApiMode, Provider, ProviderTransport
from .chat_completions import ChatCompletionsTransport
from .codex_app_server import CodexAppServerTransport
from .responses import ResponsesTransport


@dataclass
class ProviderSpec:
    name: str
    api_mode: ApiMode
    base_url: str
    default_model: str
    context_length: int
    env_vars: list[str] = field(default_factory=list)
    auth_scheme: str = "bearer"            # bearer | anthropic | codex-cli | none
    oauth: OAuthConfig | None = None
    max_tokens: int = 8192
    extra_headers: dict[str, str] = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    discover_models: bool = True


@dataclass(frozen=True)
class OAuthCatalogEntry:
    name: str
    display_name: str
    oauth_status: str
    auth_methods: tuple[str, ...]
    notes: str = ""
    catalog_only: bool = False


# --------------------------------------------------------------------------- #
# OAuth configs (data — override anything in config.yaml -> oauth_overrides)
# --------------------------------------------------------------------------- #
# Anthropic (Claude) public OAuth client used by first-party CLI tooling.
# Client IDs / endpoints can change; override via config if needed.
ANTHROPIC_OAUTH = OAuthConfig(
    provider="anthropic",
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    authorize_url="https://claude.ai/oauth/authorize",
    token_url="https://console.anthropic.com/v1/oauth/token",
    scopes=["org:create_api_key", "user:profile", "user:inference"],
    redirect_uri="https://console.anthropic.com/oauth/code/callback",
    use_localhost_callback=False,          # public client redirects to console page
    token_request_json=True,
    code_contains_state=True,
    # claude.ai requires `code=true` on the authorize URL or it returns "Invalid request
    # format" (this is the manual code-display flow the Claude CLI uses).
    extra_authorize_params={"code": "true"},
    api_extra_headers={"anthropic-beta": "oauth-2025-04-20"},
)

# OpenAI (ChatGPT / Codex) public OAuth client. Login + token storage works, but
# public API inference still depends on whether the token grants model.request.
OPENAI_OAUTH = OAuthConfig(
    provider="openai",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    authorize_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    scopes=["openid", "profile", "email", "offline_access"],
    required_api_scopes=["model.request"],
    use_localhost_callback=True,
    localhost_port=1455,
    callback_host="localhost",
    callback_path="/auth/callback",
)

OPENAI_CODEX_OAUTH = OAuthConfig(
    provider="openai-codex",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    authorize_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    scopes=["openid", "profile", "email", "offline_access"],
    use_localhost_callback=True,
    localhost_port=1455,
    callback_host="localhost",
    callback_path="/auth/callback",
    # Codex's Cloudflare front 403s requests without an allowed originator.
    api_extra_headers={"originator": "codex_cli_rs", "User-Agent": "codex_cli_rs/0.0.0 (AEGIS)"},
)

# Google (Gemini CLI) installed-app OAuth client. The bearer authorizes the Code
# Assist API (cloudcode-pa.googleapis.com); set that base_url to use it for inference.
GOOGLE_OAUTH = OAuthConfig(
    provider="google",
    client_id="681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
    # Google installed-app "secret" is not truly secret, but we never ship it in
    # source. Set GOOGLE_OAUTH_CLIENT_SECRET to the Gemini-CLI public value to enable.
    client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=[
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    use_localhost_callback=True,
    callback_host="127.0.0.1",
    callback_path="/oauth2callback",
    extra_authorize_params={"access_type": "offline", "prompt": "consent"},
)


# --------------------------------------------------------------------------- #
# Built-in providers
# --------------------------------------------------------------------------- #
PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        "anthropic", ApiMode.ANTHROPIC_MESSAGES, "https://api.anthropic.com",
        "claude-sonnet-4-6", 200_000, ["ANTHROPIC_API_KEY"], "anthropic", ANTHROPIC_OAUTH,
    ),
    "openai": ProviderSpec(
        "openai", ApiMode.CHAT_COMPLETIONS, "https://api.openai.com/v1",
        "gpt-5.5", 400_000, ["OPENAI_API_KEY"], oauth=OPENAI_OAUTH,
    ),
    "codex": ProviderSpec(
        "codex", ApiMode.RESPONSES, "https://chatgpt.com/backend-api/codex",
        "gpt-5.5", 272_000, [], "codex-backend",
    ),
    "codex-app-server": ProviderSpec(
        "codex-app-server", ApiMode.CODEX_APP_SERVER, "codex://app-server",
        "gpt-5.5", 272_000, [], "codex-cli",
    ),
    "openai-codex": ProviderSpec(
        "openai-codex", ApiMode.RESPONSES, "https://chatgpt.com/backend-api/codex",
        "gpt-5.5", 272_000, [], oauth=OPENAI_CODEX_OAUTH,
    ),
    "google": ProviderSpec(
        "google", ApiMode.CHAT_COMPLETIONS,
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.5-pro", 1_000_000, ["GEMINI_API_KEY", "GOOGLE_API_KEY"], oauth=GOOGLE_OAUTH,
    ),
    "openrouter": ProviderSpec(
        "openrouter", ApiMode.CHAT_COMPLETIONS, "https://openrouter.ai/api/v1",
        "anthropic/claude-sonnet-4.5", 200_000, ["OPENROUTER_API_KEY"],
    ),
    "groq": ProviderSpec(
        "groq", ApiMode.CHAT_COMPLETIONS, "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile", 128_000, ["GROQ_API_KEY"],
    ),
    "deepseek": ProviderSpec(
        "deepseek", ApiMode.CHAT_COMPLETIONS, "https://api.deepseek.com/v1",
        "deepseek-chat", 64_000, ["DEEPSEEK_API_KEY"],
    ),
    "xai": ProviderSpec(
        "xai", ApiMode.CHAT_COMPLETIONS, "https://api.x.ai/v1",
        "grok-build-0.1", 131_072, ["XAI_API_KEY"],
        models=["grok-build-0.1", "grok-4.3"],
    ),
    "mistral": ProviderSpec(
        "mistral", ApiMode.CHAT_COMPLETIONS, "https://api.mistral.ai/v1",
        "mistral-large-latest", 128_000, ["MISTRAL_API_KEY"],
    ),
    "together": ProviderSpec(
        "together", ApiMode.CHAT_COMPLETIONS, "https://api.together.xyz/v1",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo", 128_000, ["TOGETHER_API_KEY"],
    ),
    # --- additional OpenAI-compatible cloud providers ---
    "huggingface": ProviderSpec(
        "huggingface", ApiMode.CHAT_COMPLETIONS, "https://router.huggingface.co/v1",
        "Qwen/Qwen2.5-72B-Instruct", 128_000, ["HF_TOKEN", "HUGGINGFACE_API_KEY"],
    ),
    "novita": ProviderSpec(
        "novita", ApiMode.CHAT_COMPLETIONS, "https://api.novita.ai/v3/openai",
        "deepseek/deepseek-v3", 64_000, ["NOVITA_API_KEY"],
    ),
    "zai": ProviderSpec(
        "zai", ApiMode.CHAT_COMPLETIONS, "https://api.z.ai/api/paas/v4",
        "glm-5.2", 1_048_576, ["ZAI_API_KEY", "GLM_API_KEY"],
        models=["glm-5.2", "glm-4.6"],
    ),
    "kimi": ProviderSpec(
        "kimi", ApiMode.CHAT_COMPLETIONS, "https://api.moonshot.ai/v1",
        "kimi-k2-0905-preview", 128_000, ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
    ),
    "minimax": ProviderSpec(
        "minimax", ApiMode.CHAT_COMPLETIONS, "https://api.minimax.io/v1",
        "MiniMax-M2", 128_000, ["MINIMAX_API_KEY"],
    ),
    "qwen": ProviderSpec(
        "qwen", ApiMode.CHAT_COMPLETIONS,
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen-max", 131_072, ["QWEN_API_KEY", "DASHSCOPE_API_KEY"],
    ),
    "nvidia": ProviderSpec(
        "nvidia", ApiMode.CHAT_COMPLETIONS, "https://integrate.api.nvidia.com/v1",
        "nvidia/llama-3.3-nemotron-super-49b-v1", 128_000, ["NVIDIA_API_KEY"],
    ),
    "dashscope": ProviderSpec(
        "dashscope", ApiMode.CHAT_COMPLETIONS,
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen-max", 131_072, ["DASHSCOPE_API_KEY"],
    ),
    "stepfun": ProviderSpec(
        "stepfun", ApiMode.CHAT_COMPLETIONS, "https://api.stepfun.com/v1",
        "step-2-16k", 64_000, ["STEPFUN_API_KEY"],
    ),
    "cerebras": ProviderSpec(
        "cerebras", ApiMode.CHAT_COMPLETIONS, "https://api.cerebras.ai/v1",
        "llama-3.3-70b", 128_000, ["CEREBRAS_API_KEY"],
    ),
    "perplexity": ProviderSpec(
        "perplexity", ApiMode.CHAT_COMPLETIONS, "https://api.perplexity.ai",
        "sonar-pro", 128_000, ["PERPLEXITY_API_KEY"],
    ),
    "fireworks": ProviderSpec(
        "fireworks", ApiMode.CHAT_COMPLETIONS, "https://api.fireworks.ai/inference/v1",
        "accounts/fireworks/models/deepseek-v3", 128_000, ["FIREWORKS_API_KEY"],
    ),
    "hyperbolic": ProviderSpec(
        "hyperbolic", ApiMode.CHAT_COMPLETIONS, "https://api.hyperbolic.xyz/v1",
        "deepseek-ai/DeepSeek-V3", 128_000, ["HYPERBOLIC_API_KEY"],
    ),
    "sambanova": ProviderSpec(
        "sambanova", ApiMode.CHAT_COMPLETIONS, "https://api.sambanova.ai/v1",
        "Meta-Llama-3.3-70B-Instruct", 64_000, ["SAMBANOVA_API_KEY"],
    ),
    "vllm": ProviderSpec(
        "vllm", ApiMode.CHAT_COMPLETIONS, "http://localhost:8000/v1",
        "local-model", 64_000, [], "none",
    ),
    "ollama": ProviderSpec(
        "ollama", ApiMode.CHAT_COMPLETIONS, "http://localhost:11434/v1",
        "llama3.1", 128_000, [], "none",
    ),
    "lmstudio": ProviderSpec(
        "lmstudio", ApiMode.CHAT_COMPLETIONS, "http://localhost:1234/v1",
        "local-model", 64_000, [], "none",
    ),
}

# Runtime plugin registrations
_PLUGINS: dict[str, ProviderSpec] = {}
_PLUGIN_BOOTSTRAPPING = False
_STRICT_MODEL_PRESET_PROVIDERS = {
    "anthropic",
    "codex",
    "codex-app-server",
    "deepseek",
    "google",
    "groq",
    "openai",
    "openai-codex",
}
_AGGREGATOR_PROVIDERS = {"openrouter", "huggingface", "novita"}
_LIVE_MODEL_CACHE_TTL_SECONDS = 60.0
_LIVE_MODEL_CACHE: dict[tuple[str, str, str], tuple[float, list[str]]] = {}


_OAUTH_CATALOG: dict[str, OAuthCatalogEntry] = {
    "qwen": OAuthCatalogEntry(
        name="qwen",
        display_name="Qwen",
        oauth_status="planned",
        auth_methods=("api_key",),
        notes="Runnable today with QWEN_API_KEY or DASHSCOPE_API_KEY; OAuth metadata is scaffolded only.",
    ),
    "minimax": OAuthCatalogEntry(
        name="minimax",
        display_name="MiniMax",
        oauth_status="planned",
        auth_methods=("api_key",),
        notes="Runnable today with MINIMAX_API_KEY; OAuth metadata is scaffolded only.",
    ),
    "xai": OAuthCatalogEntry(
        name="xai",
        display_name="xAI",
        oauth_status="planned",
        auth_methods=("api_key",),
        notes="Runnable today with XAI_API_KEY; OAuth metadata is scaffolded only.",
    ),
    "copilot": OAuthCatalogEntry(
        name="copilot",
        display_name="GitHub Copilot",
        oauth_status="planned",
        auth_methods=("oauth",),
        notes="Catalog-only until GitHub/Copilot OAuth token exchange and transport auth are implemented.",
        catalog_only=True,
    ),
}


def register_provider(spec: ProviderSpec) -> None:
    """Register a provider at runtime (plugins). No core edits needed."""
    _PLUGINS[spec.name] = spec


def unregister_provider(name: str) -> None:
    """Remove a runtime plugin provider registration."""
    _PLUGINS.pop(name, None)


def ensure_plugin_providers(config: cfg.Config | None = None) -> None:
    """Load plugins once for provider discovery before resolving providers/models.

    Plugins can register providers, context engines, tools, and channels. Provider
    resolution happens early in the agent lifecycle, so this bootstrap makes
    plugin providers first-class instead of depending on tool registry loading.
    """
    global _PLUGIN_BOOTSTRAPPING
    if _PLUGIN_BOOTSTRAPPING:
        return
    _PLUGIN_BOOTSTRAPPING = True
    try:
        from ..plugins import load_plugins
        if config is None:
            config = cfg.Config.load()
        load_plugins(quiet=True, config=config)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _PLUGIN_BOOTSTRAPPING = False


def _all_specs() -> dict[str, ProviderSpec]:
    return {**PROVIDERS, **_PLUGINS}


def list_providers(config: cfg.Config | None = None) -> list[str]:
    ensure_plugin_providers(config)
    return sorted(_specs_for(config).keys())


def _transport_for(api_mode: ApiMode) -> ProviderTransport:
    if api_mode == ApiMode.ANTHROPIC_MESSAGES:
        return AnthropicTransport()
    if api_mode == ApiMode.RESPONSES:
        return ResponsesTransport()
    if api_mode == ApiMode.CODEX_APP_SERVER:
        return CodexAppServerTransport()
    return ChatCompletionsTransport()


def _custom_specs(config: cfg.Config) -> dict[str, ProviderSpec]:
    out: dict[str, ProviderSpec] = {}
    for c in config.get("custom_providers", []) or []:
        try:
            raw_discover = c.get("discover_models", True)
            if isinstance(raw_discover, str):
                discover_models = raw_discover.strip().lower() not in {"0", "false", "no", "off"}
            else:
                discover_models = bool(raw_discover)
            models: list[str] = []
            raw_models = c.get("models") or []
            if isinstance(raw_models, dict):
                raw_models = list(raw_models)
            for item in raw_models:
                if isinstance(item, str):
                    _append_unique(models, item)
                elif isinstance(item, dict):
                    _append_unique(models, item.get("id") or item.get("name") or item.get("model"))
            env_var = (
                c.get("env_var")
                or c.get("key_env")
                or c.get("api_key_env")
                or c.get("api_key_env_var")
            )
            out[c["name"]] = ProviderSpec(
                name=c["name"],
                api_mode=ApiMode(c.get("api_mode", "chat_completions")),
                base_url=c["base_url"],
                default_model=c.get("default_model", c.get("model", "local-model")),
                context_length=int(c.get("context_length", 64_000)),
                env_vars=[env_var] if env_var else [],
                auth_scheme=c.get("auth_scheme", "none" if not env_var else "bearer"),
                models=models,
                discover_models=discover_models,
            )
        except (KeyError, ValueError):
            continue
    return out


def _specs_for(config: cfg.Config | None = None) -> dict[str, ProviderSpec]:
    specs = _all_specs()
    if config is not None:
        specs = {**specs, **_custom_specs(config)}
    return specs


def _preset_model_entries(provider_name: str) -> list[tuple[str, str]]:
    try:
        from ..onboarding import MODEL_PRESETS
    except Exception:  # noqa: BLE001
        return []
    return [(str(value), str(label or value)) for value, label in MODEL_PRESETS.get(provider_name, []) if value]


def _append_unique(items: list[str], value: str | None) -> None:
    value = str(value or "").strip()
    if value and value.lower() not in {item.lower() for item in items}:
        items.append(value)


def _parse_model_ids(payload) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("data")
        if raw is None:
            raw = payload.get("models")
        if raw is None:
            raw = payload.get("items")
    else:
        raw = payload
    models: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            _append_unique(models, item)
        elif isinstance(item, dict):
            _append_unique(models, item.get("id") or item.get("name") or item.get("model"))
    return models


def _model_listing_url(base_url: str, api_mode: ApiMode | str) -> str:
    urls = _model_listing_urls(base_url, api_mode)
    return urls[0] if urls else ""


def _model_listing_urls(base_url: str, api_mode: ApiMode | str) -> list[str]:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return []
    mode = api_mode.value if isinstance(api_mode, ApiMode) else str(api_mode or "")
    if mode == ApiMode.ANTHROPIC_MESSAGES.value and not base.endswith("/v1"):
        primary_base = f"{base}/v1"
    else:
        primary_base = base
    urls = [f"{primary_base}/models"]
    alternate_base = primary_base[:-3].rstrip("/") if primary_base.endswith("/v1") else f"{primary_base}/v1"
    alternate_url = f"{alternate_base}/models" if alternate_base else ""
    if alternate_url and alternate_url not in urls:
        urls.append(alternate_url)
    return urls


def _effective_model_listing_base(provider_name: str, spec: ProviderSpec, config: cfg.Config | None) -> str:
    if (
        config is not None
        and str(config.get("model.provider") or "") == provider_name
        and str(config.get("model.base_url") or "").strip()
    ):
        return str(config.get("model.base_url") or "").strip()
    return spec.base_url


def _live_model_fetch_allowed(provider_name: str, spec: ProviderSpec, config: cfg.Config | None) -> bool:
    if spec.api_mode == ApiMode.CODEX_APP_SERVER:
        return False
    if not spec.discover_models:
        return False
    if config is not None and config.get("models.live_fetch") is False:
        return False
    custom_names = set(_custom_specs(config)) if config is not None else set()
    is_active = bool(config is not None and str(config.get("model.provider") or "") == provider_name)
    active_override = bool(
        config is not None
        and is_active
        and str(config.get("model.base_url") or "").strip()
    )
    return provider_name in custom_names or active_override or (is_active and spec.auth_scheme == "none")


def _live_model_ids_for(provider_name: str, spec: ProviderSpec, config: cfg.Config | None) -> list[str]:
    if not _live_model_fetch_allowed(provider_name, spec, config):
        return []
    base_url = _effective_model_listing_base(provider_name, spec, config)
    urls = _model_listing_urls(base_url, spec.api_mode)
    if not urls:
        return []
    cache_key = (provider_name, spec.api_mode.value, base_url.rstrip("/"))
    now = time.monotonic()
    cached = _LIVE_MODEL_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _LIVE_MODEL_CACHE_TTL_SECONDS:
        return list(cached[1])
    try:
        auth = _resolve_auth(spec, config=config)
        if not auth.available():
            _LIVE_MODEL_CACHE[cache_key] = (now, [])
            return []
        headers = auth.headers()
    except Exception:  # noqa: BLE001
        _LIVE_MODEL_CACHE[cache_key] = (now, [])
        return []
    headers = dict(headers or {})
    headers.setdefault("Accept", "application/json")
    timeout = 2.0
    if config is not None:
        timeout = float(config.get("models.live_fetch_timeout_seconds", timeout) or timeout)
    request_timeout = max(0.2, min(timeout, 10.0))
    models: list[str] = []
    for url in urls:
        try:
            response = httpx.get(url, headers=headers, timeout=request_timeout)
            response.raise_for_status()
            models = _parse_model_ids(response.json())
            break
        except Exception:  # noqa: BLE001
            continue
    _LIVE_MODEL_CACHE[cache_key] = (now, list(models))
    return models


def known_model_entries_for(provider_name: str, config: cfg.Config | None = None) -> list[dict]:
    """Provider-scoped known model rows, used by picker/dashboard/API inventory."""
    ensure_plugin_providers(config)
    specs = _specs_for(config)
    spec = specs.get(provider_name)
    rows: list[dict] = []
    seen: set[str] = set()

    def add(model: str | None, label: str = "", source: str = "preset") -> None:
        mid = str(model or "").strip()
        key = mid.lower()
        if not mid or key in seen:
            return
        seen.add(key)
        api_mode = spec.api_mode if spec is not None else ApiMode.CHAT_COMPLETIONS
        capabilities = _model_capabilities(
            mid,
            api_mode,
            provider_name=provider_name,
            base_url=getattr(spec, "base_url", "") if spec is not None else "",
        )
        row = {
            "id": mid,
            "provider": provider_name,
            "key": f"{provider_name}:{mid}",
            "label": label or mid,
            "source": source,
            "api_mode": api_mode.value if isinstance(api_mode, ApiMode) else str(api_mode),
            "capabilities": capabilities,
            "capability_flags": _normalized_capabilities(capabilities),
            "capability_summary": _capability_summary(capabilities),
        }
        if spec is not None:
            from .. import model_meta
            from ..usage_log import price_for_model

            row["context_length"] = int(
                model_meta.context_window(
                    mid,
                    provider=provider_name,
                    base_url=spec.base_url,
                )
                or spec.context_length
            )
            row["max_output_tokens"] = int(spec.max_tokens or 0)
            row["pricing"] = price_for_model(mid, config)
        rows.append(row)

    if spec is not None:
        add(spec.default_model, f"Provider default ({spec.default_model})", "default")
        for model in getattr(spec, "models", []) or []:
            add(model, model, "catalog")
    for model, label in _preset_model_entries(provider_name):
        add(model, label, "preset")
    if spec is not None:
        for model in _live_model_ids_for(provider_name, spec, config):
            add(model, model, "live")
    return rows


def known_models_for(provider_name: str, config: cfg.Config | None = None) -> list[str]:
    """Known/preset model ids for a provider, used for suggestions only."""
    return [row["id"] for row in known_model_entries_for(provider_name, config)]


def _custom_model_ids(config: cfg.Config | None) -> tuple[set[str], set[str]]:
    custom_provider_names = set(_custom_specs(config)) if config is not None else set()
    custom_model_ids: set[str] = set()
    for custom_name in custom_provider_names:
        for row in known_model_entries_for(custom_name, config):
            model_id = str(row.get("id") or "").strip().lower()
            if model_id:
                custom_model_ids.add(model_id)
    return custom_provider_names, custom_model_ids


def picker_model_entries_for(provider_name: str, config: cfg.Config | None = None) -> list[dict]:
    """Picker-facing model rows.

    If a user-defined provider and an aggregator advertise the same model id,
    hide the aggregator copy so selecting the row keeps calls on the specific
    custom endpoint.
    """
    rows = known_model_entries_for(provider_name, config)
    custom_provider_names, custom_model_ids = _custom_model_ids(config)
    if provider_name in custom_provider_names or provider_name not in _AGGREGATOR_PROVIDERS:
        return rows
    return [
        row for row in rows
        if str(row.get("id") or "").strip().lower() not in custom_model_ids
    ]


def picker_models_for(provider_name: str, config: cfg.Config | None = None) -> list[str]:
    return [row["id"] for row in picker_model_entries_for(provider_name, config)]


def model_inventory(config: cfg.Config, provider_names: list[str] | None = None) -> list[dict]:
    """All known model rows, deduped by provider+model while preserving ownership."""
    providers = provider_names or list_providers(config)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for provider_name in providers:
        for row in known_model_entries_for(provider_name, config):
            model_key = str(row.get("id") or "").strip().lower()
            key = (str(row.get("provider") or ""), model_key)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def _close_matches(value: str, choices: list[str]) -> list[str]:
    return difflib.get_close_matches(str(value or ""), choices, n=3, cutoff=0.45)


def validate_model_choice(
    provider_name: str | None,
    model: str | None,
    config: cfg.Config | None = None,
) -> dict:
    """Validate provider/model selection without treating model presets as a hard allowlist."""
    ensure_plugin_providers(config)
    provider_name = str(provider_name or "").strip()
    model = str(model or "").strip()
    custom = _custom_specs(config) if config is not None else {}
    specs = _specs_for(config)
    spec = specs.get(provider_name)
    base_url_override = bool(config is not None and config.get("model.base_url"))
    result = {
        "ok": True,
        "provider": provider_name,
        "model": model,
        "provider_known": spec is not None,
        "model_known": None,
        "provider_suggestions": [],
        "model_suggestions": [],
    }
    if spec is None and not base_url_override:
        names = sorted(specs)
        result.update({
            "ok": False,
            "message": (
                f"Unknown provider '{provider_name}'. Known: {', '.join(names)}. "
                "Set model.base_url for a custom endpoint."
            ),
            "provider_suggestions": _close_matches(provider_name, names),
        })
        return result

    if spec is None:
        result.update({"provider_known": False, "model_known": False, "custom_allowed": True})
        return result

    models = known_models_for(provider_name, config)
    if not model:
        result["model_known"] = False
        return result
    if model in models:
        result["model_known"] = True
        return result

    custom_model_ok = (
        provider_name in custom
        or provider_name in _PLUGINS
        or provider_name in {"lmstudio", "ollama", "openrouter", "vllm"}
        or base_url_override
        or provider_name not in _STRICT_MODEL_PRESET_PROVIDERS
    )
    result["model_known"] = False
    if custom_model_ok or not models:
        result["custom_allowed"] = True
        return result

    result.update({
        "warning": f"Model '{model}' is not in the known {provider_name} preset list.",
        "model_suggestions": _close_matches(model, models),
    })
    return result


def model_validation_message(validation: dict | None) -> str:
    if not validation:
        return ""
    text = validation.get("message") or validation.get("warning") or ""
    suggestions = validation.get("provider_suggestions") or validation.get("model_suggestions") or []
    if suggestions:
        label = "Did you mean" if validation.get("provider_suggestions") else "Closest known models"
        suffix = f"{label}: {', '.join(str(s) for s in suggestions)}."
        text = f"{text} {suffix}".strip()
    return text


def _resolve_auth(spec: ProviderSpec, prefer: str | None = None, config=None) -> AuthProvider:
    """Pick OAuth or API key. ``prefer`` can force 'oauth' or 'apikey'."""
    if spec.api_mode == ApiMode.CODEX_APP_SERVER or spec.auth_scheme == "codex-cli":
        return CodexCliAuth()
    if spec.auth_scheme == "codex-backend":
        return CodexBackendAuth()
    store = AuthStore()
    oauth = OAuthAuth(spec.oauth, store) if spec.oauth else None
    if oauth and not spec.env_vars and spec.auth_scheme != "none":
        return oauth
    api = ApiKeyAuth(spec.env_vars, spec.auth_scheme, dict(spec.extra_headers),
                     provider_name=spec.name, config=config)

    if prefer == "oauth" and oauth:
        return oauth
    if prefer == "apikey":
        return api
    # auto: API key first, then OAuth. This avoids using identity-only OAuth
    # tokens for providers that need model-request scopes (notably OpenAI).
    if api.available():
        return api
    if oauth and oauth.available():
        return oauth
    return api


def build_provider(config: cfg.Config, *, model: str | None = None, name: str | None = None) -> Provider:
    """Resolve a concrete Provider from config (+ optional overrides)."""
    ensure_plugin_providers(config)
    name = name or config.get("model.provider", "anthropic")
    specs = _specs_for(config)

    base_url_override = config.get("model.base_url")
    api_mode_override = config.get("model.api_mode")
    ctx_override = config.get("model.context_length")

    spec = specs.get(name)
    if spec is None:
        # Unknown name but base_url given -> ad-hoc custom provider
        if base_url_override:
            spec = ProviderSpec(
                name=name,
                api_mode=ApiMode(api_mode_override or "chat_completions"),
                base_url=base_url_override,
                default_model=model or config.get("model.default", "local-model"),
                context_length=int(ctx_override or 64_000),
                auth_scheme="none",
            )
        else:
            validation = validate_model_choice(name, model or config.get("model.default", ""), config)
            raise ValueError(model_validation_message(validation))

    api_mode = ApiMode(api_mode_override) if api_mode_override else spec.api_mode
    base_url = base_url_override or spec.base_url
    # explicit config > model metadata (the actual model's window) > the preset default
    from .. import model_meta
    resolved_model = model or config.get("model.default") or spec.default_model
    context_length = int(
        ctx_override
        or model_meta.context_window(
            resolved_model,
            config,
            provider=name,
            base_url=base_url,
        )
        or spec.context_length
    )
    if context_length < MIN_CONTEXT_LENGTH:
        raise ValueError(
            f"Provider '{name}' context_length={context_length} < minimum {MIN_CONTEXT_LENGTH}. "
            f"Refusing to start; override model.context_length if this is wrong."
        )

    auth = _resolve_auth(spec, config=config)
    transport = _transport_for(api_mode)
    return Provider(
        name=spec.name,
        transport=transport,
        auth=auth,
        base_url=base_url,
        model=model or config.get("model.default") or spec.default_model,
        context_length=context_length,
        api_mode=api_mode,
        max_tokens=spec.max_tokens,
        extra_headers=dict(spec.extra_headers),
    )


def build_aux_provider(
    config: cfg.Config,
    *,
    purpose: str | None = None,
    fallback_provider: Provider | None = None,
) -> Provider:
    """Build the auxiliary provider for an internal purpose.

    Purpose-specific keys such as ``auxiliary.compaction.provider`` override
    global ``auxiliary.provider``. When no auxiliary route is configured, callers
    can pass the live main provider as ``fallback_provider`` so internal work
    follows per-prompt routing instead of rebuilding the default main provider.
    """
    purpose = purpose or ""
    prefix = f"auxiliary.{purpose}." if purpose else ""
    aux_provider = (config.get(prefix + "provider") if purpose else None) or config.get("auxiliary.provider") or None
    aux_model = (config.get(prefix + "model") if purpose else None) or config.get("auxiliary.model") or None
    ctx_length = (config.get(prefix + "context_length") if purpose else None) or None
    if str(aux_provider or "").strip().lower() == "auto":
        try:
            import copy

            data = copy.deepcopy(getattr(config, "data", {}))
            if ctx_length:
                data.setdefault("model", {})["context_length"] = int(ctx_length)
            route_config = type(config)(data)
            from .fallback import FallbackProvider, build_with_fallbacks

            if fallback_provider is not None:
                if isinstance(fallback_provider, FallbackProvider):
                    return fallback_provider
                fallbacks: list[Provider] = []
                for spec in route_config.get("fallback_providers", []) or []:
                    if not isinstance(spec, dict):
                        continue
                    try:
                        candidate = build_provider(
                            route_config,
                            model=spec.get("model") or None,
                            name=spec.get("provider") or None,
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    same_name = getattr(candidate, "name", None) == getattr(fallback_provider, "name", None)
                    same_model = getattr(candidate, "model", None) == getattr(fallback_provider, "model", None)
                    if same_name and same_model:
                        continue
                    fallbacks.append(candidate)
                return FallbackProvider(fallback_provider, fallbacks) if fallbacks else fallback_provider
            return build_with_fallbacks(route_config, model=aux_model or None)
        except Exception:  # noqa: BLE001
            if fallback_provider is not None:
                return fallback_provider
    if aux_provider or aux_model or ctx_length:
        try:
            if ctx_length:
                import copy

                data = copy.deepcopy(getattr(config, "data", {}))
                data.setdefault("model", {})["context_length"] = int(ctx_length)
                config = type(config)(data)
            return build_provider(config, model=aux_model, name=aux_provider)
        except Exception:  # noqa: BLE001
            pass
    if fallback_provider is not None:
        return fallback_provider
    from .fallback import build_with_fallbacks
    return build_with_fallbacks(config)


def get_spec(name: str, config: cfg.Config | None = None) -> ProviderSpec | None:
    ensure_plugin_providers(config)
    return _specs_for(config).get(name)


def auth_for(name: str, prefer: str | None = None) -> AuthProvider:
    ensure_plugin_providers()
    spec = _all_specs().get(name)
    if not spec:
        raise ValueError(f"Unknown provider '{name}'.")
    return _resolve_auth(spec, prefer)


def _auth_status(auth: AuthProvider) -> dict:
    if auth is None:
        return {"description": "unknown", "available": False}
    try:
        description = auth.describe()
    except Exception as exc:  # noqa: BLE001
        description = f"unknown ({type(exc).__name__})"
    try:
        available = bool(auth.available())
    except Exception:  # noqa: BLE001
        available = False
    return {"description": description, "available": available}


def _auth_methods_for_spec(spec: ProviderSpec) -> list[str]:
    methods: list[str] = []
    if spec.env_vars:
        methods.append("api_key")
    if spec.oauth:
        methods.append("oauth")
    if spec.auth_scheme == "codex-cli":
        methods.append("codex_cli")
    elif spec.auth_scheme == "codex-backend":
        methods.append("codex_backend")
    elif spec.auth_scheme == "none":
        methods.append("none")
    return methods or [spec.auth_scheme]


def _oauth_status_for_spec(name: str, spec: ProviderSpec) -> str:
    entry = _OAUTH_CATALOG.get(name)
    if entry is not None:
        return entry.oauth_status
    if spec.oauth:
        return "configured"
    if spec.auth_scheme == "none":
        return "not_applicable"
    return "not_configured"


def _oauth_notes_for_spec(name: str) -> str:
    entry = _OAUTH_CATALOG.get(name)
    return entry.notes if entry is not None else ""


def oauth_catalog(config: cfg.Config | None = None) -> list[dict]:
    """OAuth discoverability catalog, including planned provider scaffolds.

    Rows with ``catalog_only`` are intentionally not runnable providers yet.
    They let onboarding/UI surfaces advertise the auth work without inventing
    endpoints or weakening existing provider auth behavior.
    """

    ensure_plugin_providers(config)
    specs = _specs_for(config)
    rows: list[dict] = []
    for name, spec in sorted(specs.items()):
        entry = _OAUTH_CATALOG.get(name)
        rows.append({
            "name": name,
            "display_name": entry.display_name if entry else name,
            "known_provider": True,
            "catalog_only": False,
            "oauth": bool(spec.oauth),
            "oauth_status": _oauth_status_for_spec(name, spec),
            "auth_methods": _auth_methods_for_spec(spec),
            "env_vars": list(spec.env_vars),
            "notes": _oauth_notes_for_spec(name),
        })
    for name, entry in sorted(_OAUTH_CATALOG.items()):
        if name in specs:
            continue
        rows.append({
            "name": name,
            "display_name": entry.display_name,
            "known_provider": False,
            "catalog_only": entry.catalog_only,
            "oauth": False,
            "oauth_status": entry.oauth_status,
            "auth_methods": list(entry.auth_methods),
            "env_vars": [],
            "notes": entry.notes,
        })
    return rows


def _strip_vendor_prefix(model: str) -> str:
    raw = str(model or "").strip().lower()
    return raw.split("/", 1)[1] if "/" in raw else raw


def _openai_fast_model(model: str) -> bool:
    base = _strip_vendor_prefix(model).split(":", 1)[0]
    if "codex" in base:
        return False
    return base.startswith(("gpt-5", "gpt-4.1", "o1", "o3", "o4"))


def _anthropic_fast_model(model: str) -> bool:
    base = _strip_vendor_prefix(model).split(":", 1)[0]
    return base.startswith("claude-") and ("opus-4-6" in base or "opus-4.6" in base)


def _model_capabilities(
    model: str,
    api_mode: ApiMode | str,
    *,
    provider_name: str = "",
    base_url: str = "",
) -> dict:
    mode = api_mode.value if isinstance(api_mode, ApiMode) else str(api_mode or "")
    m = (model or "").lower()
    provider = str(provider_name or "").lower()
    url = str(base_url or "").lower()
    openai_reasoning = m.startswith(("gpt-5", "o1", "o3", "o4"))
    anthropic_reasoning = mode == ApiMode.ANTHROPIC_MESSAGES.value and "claude" in m
    xai_target = provider == "xai" or "api.x.ai" in url or "grok" in m
    fast_mode = False
    if not xai_target and mode in {ApiMode.CHAT_COMPLETIONS.value, ApiMode.RESPONSES.value}:
        fast_mode = _openai_fast_model(m)
    if mode == ApiMode.ANTHROPIC_MESSAGES.value:
        native_anthropic = provider == "anthropic" or "api.anthropic.com" in url
        fast_mode = bool(native_anthropic and _anthropic_fast_model(m))
    chat_vision = any(
        marker in m
        for marker in (
            "claude", "gemini", "gpt-4o", "gpt-4.1", "gpt-5", "llava",
            "pixtral", "qwen-vl", "vision", "vl-",
        )
    )
    audio = any(marker in m for marker in ("audio", "realtime", "transcribe", "tts", "speech"))
    native_images = mode in {
        ApiMode.ANTHROPIC_MESSAGES.value,
        ApiMode.CHAT_COMPLETIONS.value,
        ApiMode.RESPONSES.value,
    }
    return {
        "tool_calls": mode in {
            ApiMode.ANTHROPIC_MESSAGES.value,
            ApiMode.CHAT_COMPLETIONS.value,
            ApiMode.RESPONSES.value,
            ApiMode.CODEX_APP_SERVER.value,
        },
        "streaming": mode in {
            ApiMode.ANTHROPIC_MESSAGES.value,
            ApiMode.CHAT_COMPLETIONS.value,
            ApiMode.RESPONSES.value,
            ApiMode.CODEX_APP_SERVER.value,
        },
        "images": bool(native_images and chat_vision),
        "audio": bool(audio and mode in {
            ApiMode.CHAT_COMPLETIONS.value,
            ApiMode.RESPONSES.value,
        }),
        "reasoning_effort": bool(anthropic_reasoning or (
            openai_reasoning and mode in {
                ApiMode.CHAT_COMPLETIONS.value,
                ApiMode.RESPONSES.value,
                ApiMode.CODEX_APP_SERVER.value,
            }
        )),
        "reasoning_stream": bool(anthropic_reasoning or (
            openai_reasoning and mode in {
                ApiMode.CHAT_COMPLETIONS.value,
                ApiMode.RESPONSES.value,
                ApiMode.CODEX_APP_SERVER.value,
            }
        )),
        "response_state": mode == ApiMode.RESPONSES.value,
        "response_cancel": mode == ApiMode.RESPONSES.value,
        "dynamic_tools": mode == ApiMode.CODEX_APP_SERVER.value,
        "fast_mode": fast_mode,
    }


def _capability_summary(capabilities: dict) -> str:
    labels = [
        ("tool_calls", "tools"),
        ("streaming", "stream"),
        ("images", "images"),
        ("audio", "audio"),
        ("reasoning_effort", "reasoning"),
        ("reasoning_stream", "reasoning-stream"),
        ("response_state", "response-state"),
        ("response_cancel", "cancel"),
        ("dynamic_tools", "dynamic-tools"),
        ("fast_mode", "fast"),
    ]
    enabled = [label for key, label in labels if capabilities.get(key)]
    return ", ".join(enabled) if enabled else "none"


def _normalized_capabilities(capabilities: dict) -> dict:
    """Stable provider-matrix capability names for dashboard/API consumers."""
    return {
        "tools": bool(capabilities.get("tool_calls")),
        "tool_calls": bool(capabilities.get("tool_calls")),
        "streaming": bool(capabilities.get("streaming")),
        "vision": bool(capabilities.get("images")),
        "images": bool(capabilities.get("images")),
        "audio": bool(capabilities.get("audio")),
        "reasoning": bool(capabilities.get("reasoning_effort")),
        "reasoning_effort": bool(capabilities.get("reasoning_effort")),
        "reasoning_stream": bool(capabilities.get("reasoning_stream")),
        "responses_state": bool(capabilities.get("response_state")),
        "response_cancel": bool(capabilities.get("response_cancel")),
        "dynamic_tools": bool(capabilities.get("dynamic_tools")),
        "fast_mode": bool(capabilities.get("fast_mode")),
        # AEGIS has schema-clean tool calls, but no dedicated provider response_format
        # contract yet. Keep this explicit so the matrix does not overclaim.
        "structured_output": False,
    }


def _resolved_spec_context_length(provider_name: str, spec: ProviderSpec) -> int:
    from .. import model_meta
    return int(
        model_meta.context_window(
            spec.default_model,
            provider=provider_name,
            base_url=spec.base_url,
        )
        or spec.context_length
    )


def _provider_probe_cache(config: cfg.Config | None) -> dict[str, dict]:
    if config is None:
        return {}
    raw = config.get("providers.probe_cache", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _probe_record_for(config: cfg.Config | None, provider_name: str) -> dict:
    raw = _provider_probe_cache(config).get(provider_name)
    return dict(raw) if isinstance(raw, dict) else {}


def _fallback_chain_rows(report: dict) -> list[dict]:
    rows: list[dict] = []
    chain = report.get("chain") if isinstance(report.get("chain"), list) else []
    for index, row in enumerate(chain):
        if not isinstance(row, dict):
            continue
        rows.append({
            "index": index,
            "role": row.get("role", ""),
            "provider": row.get("name") or row.get("provider") or "",
            "model": row.get("model", ""),
            "status": "error" if row.get("error") else "configured",
            "reason": row.get("warning") or row.get("error") or "",
        })
    return rows


def _provider_status(provider: Provider, *, role: str, configured: dict | None = None) -> dict:
    api_mode = getattr(provider, "api_mode", "")
    capabilities = _model_capabilities(
        getattr(provider, "model", ""),
        api_mode,
        provider_name=getattr(provider, "name", ""),
        base_url=getattr(provider, "base_url", ""),
    )
    return {
        "role": role,
        "name": getattr(provider, "name", ""),
        "model": getattr(provider, "model", ""),
        "api_mode": getattr(api_mode, "value", str(api_mode) if api_mode else ""),
        "base_url": getattr(provider, "base_url", ""),
        "context_length": int(getattr(provider, "context_length", 0) or 0),
        "max_output_tokens": int(getattr(provider, "max_tokens", 0) or 0),
        "auth": _auth_status(getattr(provider, "auth", None)),
        "configured": configured or {},
        "capabilities": capabilities,
        "capability_flags": _normalized_capabilities(capabilities),
        "capability_summary": _capability_summary(capabilities),
    }


def _spec_status(name: str, spec: ProviderSpec, *, origin: str) -> dict:
    auth = _resolve_auth(spec)
    capabilities = _model_capabilities(
        spec.default_model,
        spec.api_mode,
        provider_name=name,
        base_url=spec.base_url,
    )
    entry = _OAUTH_CATALOG.get(name)
    context_length = _resolved_spec_context_length(name, spec)
    auth_state = _auth_status(auth)
    return {
        "name": name,
        "display_name": entry.display_name if entry else name,
        "origin": origin,
        "default_model": spec.default_model,
        "api_mode": spec.api_mode.value,
        "base_url": spec.base_url,
        "context_length": context_length,
        "max_tokens": int(spec.max_tokens or 0),
        "max_output_tokens": int(spec.max_tokens or 0),
        "auth_scheme": spec.auth_scheme,
        "auth_methods": _auth_methods_for_spec(spec),
        "env_vars": list(spec.env_vars),
        "models": list(getattr(spec, "models", []) or []),
        "oauth": bool(spec.oauth),
        "oauth_status": _oauth_status_for_spec(name, spec),
        "oauth_notes": _oauth_notes_for_spec(name),
        "auth": auth_state,
        "credential_status": "ready" if auth_state.get("available") else "missing",
        "capabilities": capabilities,
        "capability_flags": _normalized_capabilities(capabilities),
        "capability_summary": _capability_summary(capabilities),
    }


def provider_capability_matrix(config: cfg.Config, provider_names: list[str] | None = None) -> dict:
    """Normalized provider/model capability matrix for API/dashboard surfaces.

    This is intentionally passive: it reports configured auth readiness and known
    model metadata, but it does not make live provider network calls. Use the
    provider probe route for live reachability and latency.
    """
    report = provider_report(config)
    active = report.get("active") if isinstance(report.get("active"), dict) else {}
    active_provider = str(active.get("name") or report.get("provider") or "")
    active_model = str(active.get("model") or report.get("model") or "")
    allowed = set(provider_names or [])
    catalog = [
        row for row in (report.get("provider_catalog") or [])
        if not allowed or str(row.get("name") or "") in allowed
    ]
    names = [str(row.get("name") or "") for row in catalog if row.get("name")]
    inventory = model_inventory(config, names)
    by_provider: dict[str, list[dict]] = {name: [] for name in names}
    for row in inventory:
        by_provider.setdefault(str(row.get("provider") or ""), []).append(row)

    totals = {
        "providers": 0,
        "ready": 0,
        "missing_auth": 0,
        "models": 0,
        "tools": 0,
        "vision": 0,
        "reasoning": 0,
        "streaming": 0,
        "known_pricing": 0,
        "audio": 0,
    }
    provider_rows: list[dict] = []
    fallback_chain = _fallback_chain_rows(report)
    has_fallback_chain = len(fallback_chain) > 1
    fallback_names = (
        {str(row.get("provider") or "") for row in fallback_chain if row.get("provider")}
        if has_fallback_chain else set()
    )
    for row in catalog:
        name = str(row.get("name") or "")
        auth = row.get("auth") if isinstance(row.get("auth"), dict) else {}
        models = list(by_provider.get(name) or [])
        if not models and row.get("default_model"):
            from ..usage_log import price_for_model

            capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
            models = [{
                "id": row.get("default_model"),
                "provider": name,
                "source": "default",
                "api_mode": row.get("api_mode", ""),
                "context_length": int(row.get("context_length") or 0),
                "max_output_tokens": int(row.get("max_tokens") or 0),
                "pricing": price_for_model(str(row.get("default_model") or ""), config),
                "capabilities": capabilities,
                "capability_flags": _normalized_capabilities(capabilities),
                "capability_summary": _capability_summary(capabilities),
            }]

        normalized_models = []
        for model_row in models:
            caps = model_row.get("capability_flags") or _normalized_capabilities(model_row.get("capabilities") or {})
            pricing = model_row.get("pricing") or {}
            normalized_models.append({
                "id": model_row.get("id", ""),
                "source": model_row.get("source", ""),
                "api_mode": model_row.get("api_mode") or row.get("api_mode", ""),
                "context_length": int(model_row.get("context_length") or row.get("context_length") or 0),
                "max_output_tokens": int(model_row.get("max_output_tokens") or row.get("max_tokens") or 0),
                "pricing": pricing,
                "capabilities": caps,
                "capability_summary": model_row.get("capability_summary", ""),
                "active": name == active_provider and str(model_row.get("id") or "") == active_model,
            })
            totals["models"] += 1
            totals["tools"] += 1 if caps.get("tools") else 0
            totals["vision"] += 1 if caps.get("vision") else 0
            totals["audio"] += 1 if caps.get("audio") else 0
            totals["reasoning"] += 1 if caps.get("reasoning") else 0
            totals["streaming"] += 1 if caps.get("streaming") else 0
            totals["known_pricing"] += 1 if pricing.get("known") else 0

        ready = bool(auth.get("available")) or row.get("auth_scheme") == "none"
        totals["providers"] += 1
        totals["ready"] += 1 if ready else 0
        totals["missing_auth"] += 0 if ready else 1
        capabilities = row.get("capability_flags") or _normalized_capabilities(row.get("capabilities") or {})
        probe_record = _probe_record_for(config, name)
        live_probe = bool(probe_record)
        probe_status = str(probe_record.get("status") or ("ready" if ready else "missing_auth"))
        if not ready:
            probe_status = "missing_auth"
        probe_detail = str(
            probe_record.get("detail")
            or probe_record.get("message")
            or ("configured for live probe" if ready else "provider auth is not configured")
        )
        last_error = "" if probe_status == "ready" else probe_detail
        provider_rows.append({
            "provider": name,
            "name": name,
            "display_name": row.get("display_name") or name,
            "origin": row.get("origin", ""),
            "api_mode": row.get("api_mode", ""),
            "base_url": row.get("base_url", ""),
            "default_model": row.get("default_model", ""),
            "active": name == active_provider,
            "auth": {
                "available": bool(auth.get("available")),
                "description": auth.get("description", ""),
                "scheme": row.get("auth_scheme", ""),
                "methods": list(row.get("auth_methods") or []),
                "env_vars": list(row.get("env_vars") or []),
                "oauth": bool(row.get("oauth")),
                "oauth_status": row.get("oauth_status", ""),
                "credential_status": "ready" if ready else "missing",
            },
            "probe": {
                "status": probe_status,
                "live": live_probe,
                "ok": bool(probe_record.get("ok", probe_status == "ready")),
                "latency_ms": probe_record.get("latency_ms"),
                "message": probe_detail,
                "tested_at": str(probe_record.get("tested_at") or ""),
                "timeout_seconds": probe_record.get("timeout_seconds"),
            },
            "last_probe": probe_record,
            "last_error": last_error,
            "limits": {
                "context": int(row.get("context_length") or 0),
                "max_output": max((int(m.get("max_output_tokens") or 0) for m in normalized_models), default=0),
            },
            "capabilities": capabilities,
            "capability_summary": row.get("capability_summary", ""),
            "fallback_chain": fallback_chain if name in fallback_names else [],
            "fallback_enabled": has_fallback_chain and name in fallback_names,
            "models": normalized_models,
            "model_count": len(normalized_models),
        })
    return {
        "ok": True,
        "active": {"provider": active_provider, "model": active_model},
        "totals": totals,
        "providers": provider_rows,
        "fallback_chain": fallback_chain,
    }


def provider_report(config: cfg.Config) -> dict:
    """Describe provider resolution without exposing secret values.

    This is the shared "provider resolver" surface for CLI/dashboard/API views:
    primary route, fallback chain, prompt-routing rules, custom providers, and
    auth readiness. It intentionally reports only env var names and auth state,
    never credential material.
    """

    ensure_plugin_providers(config)
    builtins = dict(PROVIDERS)
    plugins = dict(_PLUGINS)
    custom = _custom_specs(config)
    specs = _specs_for(config)
    configured_provider = config.get("model.provider", "anthropic")
    configured_model = config.get("model.default")
    configured_validation = validate_model_choice(configured_provider, configured_model, config)

    active: dict
    chain: list[dict] = []
    try:
        primary = build_provider(config, model=configured_model, name=configured_provider)
        active = _provider_status(
            primary,
            role="primary",
            configured={"provider": configured_provider, "model": configured_model or ""},
        )
        active["model_validation"] = configured_validation
        validation_msg = model_validation_message(configured_validation)
        if validation_msg and configured_validation.get("warning"):
            active["warning"] = validation_msg
        chain.append(active)
    except Exception as exc:  # noqa: BLE001
        active = {
            "role": "primary",
            "name": configured_provider,
            "model": configured_model or "",
            "error": f"{type(exc).__name__}: {exc}",
            "model_validation": configured_validation,
            "configured": {"provider": configured_provider, "model": configured_model or ""},
        }

    fallbacks: list[dict] = []
    for index, item in enumerate(config.get("fallback_providers", []) or [], start=1):
        configured = item if isinstance(item, dict) else {}
        provider_name = configured.get("provider") or ""
        model = configured.get("model") or ""
        validation = validate_model_choice(provider_name or configured_provider, model or configured_model, config)
        try:
            resolved = build_provider(config, model=model or None, name=provider_name or None)
            row = _provider_status(
                resolved,
                role=f"fallback:{index}",
                configured={"provider": provider_name, "model": model},
            )
            row["model_validation"] = validation
            validation_msg = model_validation_message(validation)
            if validation_msg and validation.get("warning"):
                row["warning"] = validation_msg
            fallbacks.append(row)
            chain.append(row)
        except Exception as exc:  # noqa: BLE001
            fallbacks.append({
                "role": f"fallback:{index}",
                "name": provider_name,
                "model": model,
                "error": f"{type(exc).__name__}: {exc}",
                "model_validation": validation,
                "configured": {"provider": provider_name, "model": model},
            })

    routing: list[dict] = []
    for index, rule in enumerate(config.get("routing", []) or [], start=1):
        if not isinstance(rule, dict):
            continue
        provider_name = rule.get("provider") or configured_provider
        model = rule.get("model") or configured_model or ""
        row = {
            "index": index,
            "match": rule.get("match", ""),
            "provider": provider_name,
            "model": model,
            "known_provider": provider_name in specs,
        }
        spec = specs.get(provider_name)
        if spec is not None:
            capabilities = _model_capabilities(
                model,
                spec.api_mode,
                provider_name=str(provider_name or ""),
                base_url=spec.base_url,
            )
            row["capabilities"] = capabilities
            row["capability_summary"] = _capability_summary(capabilities)
        if provider_name not in specs and not config.get("model.base_url"):
            row["warning"] = "unknown provider"
        validation = validate_model_choice(provider_name, model, config)
        row["model_validation"] = validation
        validation_msg = model_validation_message(validation)
        if validation_msg:
            row["warning"] = validation_msg
        routing.append(row)

    provider_catalog = []
    for name, spec in sorted(builtins.items()):
        provider_catalog.append(_spec_status(name, spec, origin="built-in"))
    for name, spec in sorted(plugins.items()):
        provider_catalog.append(_spec_status(name, spec, origin="plugin"))
    for name, spec in sorted(custom.items()):
        provider_catalog.append(_spec_status(name, spec, origin="custom"))

    return {
        "active": active,
        "chain": chain,
        "fallback_chain": _fallback_chain_rows({"chain": chain}),
        "fallbacks": fallbacks,
        "routing": routing,
        "provider_catalog": provider_catalog,
        "oauth_catalog": oauth_catalog(config),
        "custom_providers": [_spec_status(name, spec, origin="custom")
                             for name, spec in sorted(custom.items())],
        "provider": configured_provider,
        "model": configured_model,
        "base_url_override": config.get("model.base_url", "") or "",
        "api_mode_override": config.get("model.api_mode", "") or "",
        "context_length_override": config.get("model.context_length", "") or "",
    }
