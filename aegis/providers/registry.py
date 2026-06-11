"""Built-in provider catalog + resolution of a concrete ``Provider`` from config.

Auth precedence (per provider):
  1. explicit ``base_url`` override in config -> treat as custom/local (api-key or none)
  2. an API key in the environment            -> API key
  3. a valid OAuth login in auth.json         -> OAuth
  4. Codex subscription auth                  -> the separate ``codex`` provider
API keys win when both are configured because OAuth scopes can be identity-only.
``aegis auth status`` prints the resolution.
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field

from .. import config as cfg
from ..constants import MIN_CONTEXT_LENGTH
from .anthropic import AnthropicTransport
from .auth import ApiKeyAuth, AuthProvider, AuthStore, CodexCliAuth, OAuthAuth, OAuthConfig
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
        "codex", ApiMode.CODEX_APP_SERVER, "codex://app-server",
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
        "grok-2-latest", 131_072, ["XAI_API_KEY"],
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
        "glm-4.6", 128_000, ["ZAI_API_KEY", "GLM_API_KEY"],
    ),
    "kimi": ProviderSpec(
        "kimi", ApiMode.CHAT_COMPLETIONS, "https://api.moonshot.ai/v1",
        "kimi-k2-0905-preview", 128_000, ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
    ),
    "minimax": ProviderSpec(
        "minimax", ApiMode.CHAT_COMPLETIONS, "https://api.minimax.io/v1",
        "MiniMax-M2", 128_000, ["MINIMAX_API_KEY"],
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


def list_providers() -> list[str]:
    ensure_plugin_providers()
    return sorted(_all_specs().keys())


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
            out[c["name"]] = ProviderSpec(
                name=c["name"],
                api_mode=ApiMode(c.get("api_mode", "chat_completions")),
                base_url=c["base_url"],
                default_model=c.get("default_model", c.get("model", "local-model")),
                context_length=int(c.get("context_length", 64_000)),
                env_vars=[c["env_var"]] if c.get("env_var") else [],
                auth_scheme=c.get("auth_scheme", "none" if not c.get("env_var") else "bearer"),
            )
        except (KeyError, ValueError):
            continue
    return out


def _resolve_auth(spec: ProviderSpec, prefer: str | None = None) -> AuthProvider:
    """Pick OAuth or API key. ``prefer`` can force 'oauth' or 'apikey'."""
    if spec.api_mode == ApiMode.CODEX_APP_SERVER or spec.auth_scheme == "codex-cli":
        return CodexCliAuth()
    store = AuthStore()
    oauth = OAuthAuth(spec.oauth, store) if spec.oauth else None
    if oauth and not spec.env_vars and spec.auth_scheme != "none":
        return oauth
    api = ApiKeyAuth(spec.env_vars, spec.auth_scheme, dict(spec.extra_headers))

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
    specs = {**_all_specs(), **_custom_specs(config)}

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
            raise ValueError(
                f"Unknown provider '{name}'. Known: {', '.join(sorted(specs))}. "
                f"Set model.base_url for a custom endpoint."
            )

    api_mode = ApiMode(api_mode_override) if api_mode_override else spec.api_mode
    base_url = base_url_override or spec.base_url
    # explicit config > model metadata (the actual model's window) > the preset default
    from .. import model_meta
    resolved_model = model or config.get("model.default") or spec.default_model
    context_length = int(ctx_override or model_meta.context_window(resolved_model, config)
                         or spec.context_length)
    if context_length < MIN_CONTEXT_LENGTH:
        raise ValueError(
            f"Provider '{name}' context_length={context_length} < minimum {MIN_CONTEXT_LENGTH}. "
            f"Refusing to start; override model.context_length if this is wrong."
        )

    auth = _resolve_auth(spec)
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
    return build_provider(config)


def get_spec(name: str) -> ProviderSpec | None:
    ensure_plugin_providers()
    return _all_specs().get(name)


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


def _model_capabilities(model: str, api_mode: ApiMode | str) -> dict:
    mode = api_mode.value if isinstance(api_mode, ApiMode) else str(api_mode or "")
    m = (model or "").lower()
    openai_reasoning = m.startswith(("gpt-5", "o1", "o3", "o4"))
    anthropic_reasoning = mode == ApiMode.ANTHROPIC_MESSAGES.value and "claude" in m
    chat_vision = any(
        marker in m
        for marker in (
            "claude", "gemini", "gpt-4o", "gpt-4.1", "gpt-5", "llava",
            "pixtral", "qwen-vl", "vision", "vl-",
        )
    )
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
        "reasoning_effort": bool(anthropic_reasoning or (
            openai_reasoning and mode in {ApiMode.CHAT_COMPLETIONS.value, ApiMode.RESPONSES.value}
        )),
        "reasoning_stream": bool(anthropic_reasoning or (
            openai_reasoning and mode == ApiMode.CHAT_COMPLETIONS.value
        )),
        "response_state": mode == ApiMode.RESPONSES.value,
        "response_cancel": mode == ApiMode.RESPONSES.value,
        "dynamic_tools": mode == ApiMode.CODEX_APP_SERVER.value,
    }


def _capability_summary(capabilities: dict) -> str:
    labels = [
        ("tool_calls", "tools"),
        ("streaming", "stream"),
        ("images", "images"),
        ("reasoning_effort", "reasoning"),
        ("reasoning_stream", "reasoning-stream"),
        ("response_state", "response-state"),
        ("response_cancel", "cancel"),
        ("dynamic_tools", "dynamic-tools"),
    ]
    enabled = [label for key, label in labels if capabilities.get(key)]
    return ", ".join(enabled) if enabled else "none"


def _provider_status(provider: Provider, *, role: str, configured: dict | None = None) -> dict:
    api_mode = getattr(provider, "api_mode", "")
    capabilities = _model_capabilities(getattr(provider, "model", ""), api_mode)
    return {
        "role": role,
        "name": getattr(provider, "name", ""),
        "model": getattr(provider, "model", ""),
        "api_mode": getattr(api_mode, "value", str(api_mode) if api_mode else ""),
        "base_url": getattr(provider, "base_url", ""),
        "context_length": int(getattr(provider, "context_length", 0) or 0),
        "auth": _auth_status(getattr(provider, "auth", None)),
        "configured": configured or {},
        "capabilities": capabilities,
        "capability_summary": _capability_summary(capabilities),
    }


def _spec_status(name: str, spec: ProviderSpec, *, origin: str) -> dict:
    auth = _resolve_auth(spec)
    capabilities = _model_capabilities(spec.default_model, spec.api_mode)
    return {
        "name": name,
        "origin": origin,
        "default_model": spec.default_model,
        "api_mode": spec.api_mode.value,
        "base_url": spec.base_url,
        "context_length": spec.context_length,
        "auth_scheme": spec.auth_scheme,
        "env_vars": list(spec.env_vars),
        "oauth": bool(spec.oauth),
        "auth": _auth_status(auth),
        "capabilities": capabilities,
        "capability_summary": _capability_summary(capabilities),
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
    specs = {**builtins, **plugins, **custom}
    configured_provider = config.get("model.provider", "anthropic")
    configured_model = config.get("model.default")

    active: dict
    chain: list[dict] = []
    try:
        primary = build_provider(config, model=configured_model, name=configured_provider)
        active = _provider_status(
            primary,
            role="primary",
            configured={"provider": configured_provider, "model": configured_model or ""},
        )
        chain.append(active)
    except Exception as exc:  # noqa: BLE001
        active = {
            "role": "primary",
            "name": configured_provider,
            "model": configured_model or "",
            "error": f"{type(exc).__name__}: {exc}",
            "configured": {"provider": configured_provider, "model": configured_model or ""},
        }

    fallbacks: list[dict] = []
    for index, item in enumerate(config.get("fallback_providers", []) or [], start=1):
        configured = item if isinstance(item, dict) else {}
        provider_name = configured.get("provider") or ""
        model = configured.get("model") or ""
        try:
            resolved = build_provider(config, model=model or None, name=provider_name or None)
            row = _provider_status(
                resolved,
                role=f"fallback:{index}",
                configured={"provider": provider_name, "model": model},
            )
            fallbacks.append(row)
            chain.append(row)
        except Exception as exc:  # noqa: BLE001
            fallbacks.append({
                "role": f"fallback:{index}",
                "name": provider_name,
                "model": model,
                "error": f"{type(exc).__name__}: {exc}",
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
            capabilities = _model_capabilities(model, spec.api_mode)
            row["capabilities"] = capabilities
            row["capability_summary"] = _capability_summary(capabilities)
        if provider_name not in specs and not config.get("model.base_url"):
            row["warning"] = "unknown provider"
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
        "fallbacks": fallbacks,
        "routing": routing,
        "provider_catalog": provider_catalog,
        "custom_providers": [_spec_status(name, spec, origin="custom")
                             for name, spec in sorted(custom.items())],
        "provider": configured_provider,
        "model": configured_model,
        "base_url_override": config.get("model.base_url", "") or "",
        "api_mode_override": config.get("model.api_mode", "") or "",
        "context_length_override": config.get("model.context_length", "") or "",
    }
