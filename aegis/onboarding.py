"""Interactive first-run onboarding for AEGIS."""

from __future__ import annotations

import getpass
import secrets
import sys
from dataclasses import dataclass
from typing import Callable

from . import config as cfg
from .config import Config
from .providers import registry

Input = Callable[[str], str]
Output = Callable[[str], None]


MODEL_PRESETS: dict[str, list[tuple[str, str]]] = {
    "openai": [
        ("gpt-5.2", "GPT-5.2 (latest frontier)"),
        ("gpt-5.1", "GPT-5.1"),
        ("gpt-5", "GPT-5"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1", "GPT-4.1"),
    ],
    "anthropic": [
        ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
        ("claude-opus-4-1", "Claude Opus 4.1"),
        ("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet"),
    ],
    "google": [
        ("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-1.5-pro", "Gemini 1.5 Pro"),
    ],
    "ollama": [
        ("llama3.1", "Llama 3.1"),
        ("qwen2.5-coder", "Qwen 2.5 Coder"),
        ("mistral", "Mistral"),
    ],
    "openrouter": [
        ("anthropic/claude-sonnet-4.5", "Claude Sonnet via OpenRouter"),
        ("openai/gpt-5.2", "GPT-5.2 via OpenRouter"),
        ("google/gemini-2.5-pro", "Gemini 2.5 Pro via OpenRouter"),
    ],
    "deepseek": [
        ("deepseek-chat", "DeepSeek Chat"),
        ("deepseek-reasoner", "DeepSeek Reasoner"),
    ],
    "groq": [
        ("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile"),
        ("openai/gpt-oss-120b", "GPT-OSS 120B"),
        ("openai/gpt-oss-20b", "GPT-OSS 20B"),
    ],
}


@dataclass
class OnboardingState:
    provider: str = ""
    model: str = ""
    auth_method: str = ""
    web_backend: str = "duckduckgo"
    channels: list[str] | None = None
    workspace_files: list[str] | None = None
    dashboard_url: str = ""
    services: list[str] | None = None


def run_onboarding(
    config: Config,
    *,
    quick: bool = False,
    advanced: bool = False,
    probe: bool = True,
    services: bool = True,
    input_func: Input = input,
    secret_func: Input | None = None,
    output_func: Output = print,
) -> int:
    secret_func = secret_func or _secret
    state = OnboardingState(channels=[], services=[], workspace_files=[])

    out = output_func
    out("")
    out("AEGIS ONBOARDING")
    out("─────────────────────────────────────────────────────────")
    out("SECURITY NOTICE: AEGIS can execute commands, edit files, and connect")
    out("to messaging networks when you enable those tools. Use it only in a")
    out("trusted environment and keep API keys private.")
    if not _confirm("Acknowledge security notice and proceed?", True, input_func, out):
        out("onboarding cancelled.")
        return 1

    if not quick and not advanced:
        path = _choose(
            "Select onboarding path:",
            [("quick", "QuickStart (fast local defaults)"), ("advanced", "Advanced (manual control)")],
            default=0,
            input_func=input_func,
            output_func=out,
        )
        advanced = path == "advanced"
    elif quick:
        advanced = False

    _configure_model(config, state, advanced, probe, input_func, secret_func, out)
    _configure_web(config, state, advanced, input_func, secret_func, out)
    _configure_channels(config, state, advanced, input_func, secret_func, out)
    _seed_workspace(state, out)
    _configure_dashboard(config, state, out)
    if services:
        _configure_services(config, state, advanced, input_func, out)
    config.save()
    _summary(config, state, out)
    return 0


def _secret(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except (EOFError, OSError):
        return input(prompt)


def _ask(prompt: str, default: str | None, input_func: Input) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    raw = input_func(f"{prompt}{suffix}: ").strip()
    return raw or (default or "")


def _confirm(prompt: str, default: bool, input_func: Input, output_func: Output) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"? {prompt} ({suffix}) ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        output_func("  enter y or n")


def _choose(
    prompt: str,
    options: list[tuple[str, str]],
    *,
    default: int = 0,
    input_func: Input,
    output_func: Output,
) -> str:
    picked = _dialog_choose(prompt, options, default, input_func, output_func)
    if picked is not None:
        return picked

    output_func(f"? {prompt}")
    for i, (_, label) in enumerate(options, 1):
        marker = ">" if i == default + 1 else " "
        output_func(f"  {marker} {i}. {label}")
    for _ in range(3):
        raw = input_func(f"selection [{default + 1}]: ").strip()
        if not raw:
            return options[default][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        lowered = raw.lower()
        if lowered.startswith("sk-"):
            output_func("  that looks like an API key; choose the provider first.")
            continue
        for value, label in options:
            if lowered in (value.lower(), label.lower()) or lowered in label.lower():
                return value
        output_func("  unknown choice; try the number or provider name.")
    return options[default][0]


def _multi_choose(
    prompt: str,
    options: list[tuple[str, str]],
    *,
    default_values: list[str] | None = None,
    input_func: Input,
    output_func: Output,
) -> list[str]:
    default_values = default_values or []
    picked = _dialog_multi_choose(prompt, options, default_values, input_func, output_func)
    if picked is not None:
        return picked

    output_func(f"? {prompt}")
    for i, (value, label) in enumerate(options, 1):
        marker = "x" if value in default_values else " "
        output_func(f"  [{marker}] {i}. {label}")
    output_func("  enter comma-separated selections, or leave blank for none")
    raw = input_func("selection(s) []: ").strip()
    if not raw:
        return list(default_values)
    selected: list[str] = []
    by_value = {value.lower(): value for value, _ in options}
    for part in raw.replace(" ", ",").split(","):
        item = part.strip().lower()
        if not item:
            continue
        if item.isdigit() and 1 <= int(item) <= len(options):
            selected.append(options[int(item) - 1][0])
        elif item in by_value:
            selected.append(by_value[item])
    return [value for value, _ in options if value in selected]


def _choose_model(provider: str, default_model: str, input_func: Input, output_func: Output) -> str:
    options: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value: str, label: str) -> None:
        if value and value not in seen:
            options.append((value, label))
            seen.add(value)

    add(default_model, f"Provider default ({default_model})")
    for value, label in MODEL_PRESETS.get(provider, []):
        add(value, label)
    add("custom", "Custom model id")

    choice = _choose(
        "Select model:",
        options,
        default=0,
        input_func=input_func,
        output_func=output_func,
    )
    if choice == "custom":
        custom = _ask("Custom model id", default_model, input_func)
        return custom or default_model
    return choice


def _dialog_choose(
    prompt: str,
    options: list[tuple[str, str]],
    default: int,
    input_func: Input,
    output_func: Output,
) -> str | None:
    if not _can_use_dialogs(input_func, output_func):
        return None
    try:
        from prompt_toolkit.shortcuts import radiolist_dialog

        result = radiolist_dialog(
            title="AEGIS onboarding",
            text=prompt,
            ok_text="Continue",
            cancel_text="Use default",
            values=options,
            default=options[default][0],
        ).run()
    except Exception:  # noqa: BLE001
        return None
    return result or options[default][0]


def _dialog_multi_choose(
    prompt: str,
    options: list[tuple[str, str]],
    default_values: list[str],
    input_func: Input,
    output_func: Output,
) -> list[str] | None:
    if not _can_use_dialogs(input_func, output_func):
        return None
    try:
        from prompt_toolkit.shortcuts import checkboxlist_dialog

        result = checkboxlist_dialog(
            title="AEGIS onboarding",
            text=f"{prompt}\nUse Space to toggle selections.",
            ok_text="Continue",
            cancel_text="Skip",
            values=options,
            default_values=default_values,
        ).run()
    except Exception:  # noqa: BLE001
        return None
    return list(result or [])


def _can_use_dialogs(input_func: Input, output_func: Output) -> bool:
    return (
        input_func is input
        and output_func is print
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _configure_model(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    probe: bool,
    input_func: Input,
    secret_func: Input,
    out: Output,
) -> None:
    out("")
    out("CONFIGURING MODEL INFERENCE")
    out("─────────────────────────────────────────────────────────")
    common = [
        ("openai", "OpenAI (GPT-4o / GPT-5 API)"),
        ("anthropic", "Anthropic (Claude)"),
        ("google", "Google Gemini"),
        ("ollama", "Ollama (local / offline)"),
        ("openrouter", "OpenRouter"),
        ("deepseek", "DeepSeek"),
        ("groq", "Groq"),
    ]
    if advanced:
        known = {v for v, _ in common}
        common.extend((name, name) for name in registry.list_providers() if name not in known)
    provider = _choose(
        "Select your primary LLM provider:",
        common,
        default=0,
        input_func=input_func,
        output_func=out,
    )
    spec = registry.get_spec(provider)
    if not spec:
        out(f"! unknown provider {provider}; keeping current config.")
        return
    state.provider = provider
    config.set("model.provider", provider)

    if spec.env_vars:
        env_name = spec.env_vars[0]
        auth_options: list[tuple[str, str]]
        if spec.oauth:
            oauth_label = "OAuth browser login"
            auth_options = [("oauth", oauth_label), ("api_key", f"API key ({env_name})")]
            if provider == "openai":
                auth_options = [
                    ("oauth", "OAuth browser login (ChatGPT, experimental)"),
                    ("api_key", f"API key ({env_name}) - reliable OpenAI API path"),
                ]
        else:
            auth_options = [("api_key", f"API key ({env_name})")]
        auth_options.append(("skip", "Skip credentials for now"))
        auth_method = _choose(
            "Choose authentication method:",
            auth_options,
            default=0,
            input_func=input_func,
            output_func=out,
        )
        state.auth_method = auth_method
        if auth_method == "api_key":
            key = secret_func(f"🔑 Enter {env_name}: ").strip()
            if key:
                config.set(env_name, key)
                out(f"✓ saved {env_name} to {cfg.env_path()}")
            else:
                out(f"! {env_name} skipped.")
        elif auth_method == "oauth" and spec.oauth:
            if not _oauth_login(provider, spec, out):
                out("  Use an API key if your OAuth client cannot grant model inference scopes.")
    elif spec.auth_scheme == "none":
        state.auth_method = "local"
        base_url = _ask("Base URL", spec.base_url, input_func)
        if base_url and base_url != spec.base_url:
            config.set("model.base_url", base_url)

    model = _choose_model(provider, spec.default_model, input_func, out)
    state.model = model
    config.set("model.default", model)

    mode_default = "auto" if advanced else "ask"
    mode = _ask("Tool execution mode (ask/auto/allowlist/deny/full)", mode_default, input_func)
    if mode not in {"ask", "auto", "allowlist", "deny", "full", "smart"}:
        out("! unknown exec mode; using ask")
        mode = "ask"
    config.set("tools.exec_mode", mode)

    if probe:
        _probe_model(config, out)


def _oauth_login(provider: str, spec, out: Output) -> bool:
    from .providers.auth import AuthError, AuthStore, OAuthAuth

    try:
        oauth = OAuthAuth(spec.oauth, AuthStore())
        creds = oauth.login()
        out(f"✓ logged in to {provider} via OAuth.")
        missing = oauth.missing_required_scopes(creds)
        if missing:
            out("! OAuth token lacks API scope(s): " + ", ".join(missing))
            return False
        return True
    except AuthError as e:
        out(f"! OAuth failed: {e}")
        return False


def _probe_model(config: Config, out: Output) -> bool:
    out("Testing model connection...")
    try:
        from .providers import build_provider
        from .types import Message

        provider = build_provider(config)
        provider.complete([Message.user("Reply with OK.")], tools=None, stream=False, max_tokens=16)
        out(f"✓ Connection successful! ({provider.name}: {provider.model})")
        return True
    except Exception as e:  # noqa: BLE001
        out(f"! Connection test failed: {type(e).__name__}: {e}")
        out("  You can fix credentials and re-run `aegis setup` or `aegis doctor`.")
        return False


def _configure_web(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    secret_func: Input,
    out: Output,
) -> None:
    out("")
    out("CONFIGURING WEB BROWSING TOOLS")
    out("─────────────────────────────────────────────────────────")
    options = [
        ("duckduckgo", "DuckDuckGo (key-free)"),
        ("brave", "Brave Search API"),
        ("tavily", "Tavily API"),
        ("serper", "Serper API"),
        ("auto", "Auto-detect from environment"),
        ("skip", "Skip / configure later"),
    ]
    backend = _choose("Select your preferred web search provider:", options, default=0,
                      input_func=input_func, output_func=out)
    if backend != "skip":
        state.web_backend = backend
        config.set("web.search_backend", backend)
        env_map = {"brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY"}
        env_name = env_map.get(backend)
        if env_name and _confirm(f"Configure {env_name} now?", advanced, input_func, out):
            key = secret_func(f"🔑 Enter {env_name}: ").strip()
            if key:
                config.set(env_name, key)
        out(f"✓ web search profile: {backend}")


def _configure_channels(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    secret_func: Input,
    out: Output,
) -> None:
    out("")
    out("MESSAGING & CHANNELS")
    out("─────────────────────────────────────────────────────────")
    channel_options = [("telegram", "Telegram")]
    if advanced:
        channel_options.extend([("discord", "Discord"), ("slack", "Slack")])
    selected = _multi_choose(
        "Which messaging integrations would you like to configure?",
        channel_options,
        default_values=[],
        input_func=input_func,
        output_func=out,
    )
    channels: list[str] = []
    if "telegram" in selected:
        token = secret_func("🔑 Enter TELEGRAM_BOT_TOKEN: ").strip()
        if token:
            config.set("TELEGRAM_BOT_TOKEN", token)
            channels.append("telegram")
            allowed = _ask("Allowlisted Telegram user id or @username", "", input_func)
            if allowed:
                config.set("TELEGRAM_ALLOWED_USERS", allowed)
                out("✓ Telegram allowlist enabled.")
            else:
                out("! No allowlist set; unknown users will use pairing mode.")
    if advanced:
        for channel, env_name in (("discord", "DISCORD_BOT_TOKEN"), ("slack", "SLACK_BOT_TOKEN")):
            if channel in selected:
                token = secret_func(f"🔑 Enter {env_name}: ").strip()
                if token:
                    config.set(env_name, token)
                    channels.append(channel)
    state.channels = channels
    config.data.setdefault("gateway", {})["channels"] = channels
    config.save()


def _seed_workspace(state: OnboardingState, out: Output) -> None:
    workspace = cfg.workspace_dir()
    templates = {
        "SOUL.md": (
            "# AEGIS Persona\n\n"
            "Be concise, careful, and useful. Ask before high-risk actions.\n"
        ),
        "AGENTS.md": (
            "# AEGIS Operating Rules\n\n"
            "- Prefer small, verifiable changes.\n"
            "- Explain risky actions before running them.\n"
            "- Keep secrets out of logs and replies.\n"
        ),
        "USER.md": (
            "# User Profile\n\n"
            "Add stable preferences, aliases, or project notes here.\n"
        ),
    }
    created: list[str] = []
    for name, body in templates.items():
        path = workspace / name
        if path.exists() and path.read_text(encoding="utf-8").strip():
            continue
        path.write_text(body, encoding="utf-8")
        created.append(name)
    state.workspace_files = created
    if created:
        out(f"✓ workspace initialized: {workspace}")


def _configure_dashboard(config: Config, state: OnboardingState, out: Output) -> None:
    token = config.get("server.dashboard_token")
    if not token:
        token = "aegis_tok_" + secrets.token_urlsafe(24)
        config.data.setdefault("server", {})["dashboard_token"] = token
        config.save()
    host = config.get("server.dashboard_host", "127.0.0.1")
    port = int(config.get("server.dashboard_port", 9119))
    state.dashboard_url = f"http://{host}:{port}/?token={token}"


def _configure_services(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    out: Output,
) -> None:
    out("")
    out("GATEWAY & DAEMON INSTALLATION")
    out("─────────────────────────────────────────────────────────")
    default = False
    if not _confirm("Install/start user systemd services for dashboard/gateway?", default, input_func, out):
        return
    from .daemon import install_dashboard_service, install_gateway_service

    dash = install_dashboard_service(config)
    out(("✓ " if dash.ok else "! ") + dash.message)
    if dash.ok:
        state.services.append("dashboard")
    if state.channels:
        gate = install_gateway_service(config, state.channels)
        out(("✓ " if gate.ok else "! ") + gate.message)
        if gate.ok:
            state.services.append("gateway")


def _summary(config: Config, state: OnboardingState, out: Output) -> None:
    out("")
    out("ONBOARDING COMPLETE")
    out("─────────────────────────────────────────────────────────")
    out(f"Config:          {cfg.config_path()}")
    out(f"Primary brain:   {config.get('model.provider')} {config.get('model.default')}")
    out(f"Web search:      {config.get('web.search_backend')}")
    out(f"Auth:            {state.auth_method or 'not configured'}")
    out(f"Integrations:    {', '.join(state.channels or []) or 'none'}")
    out(f"Workspace:       {cfg.workspace_dir()}")
    out(f"Services:        {', '.join(state.services or []) or 'not installed'}")
    out("")
    out("Control UI:")
    out(f"  {state.dashboard_url}")
    out("")
    out("Start chatting:")
    out("  aegis")
