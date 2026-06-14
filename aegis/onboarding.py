"""Interactive first-run onboarding for AEGIS."""

from __future__ import annotations

import copy
import getpass
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from . import config as cfg
from .config import Config
from .providers import registry

Input = Callable[[str], str]
Output = Callable[[str], None]


# -- presentation -----------------------------------------------------------
# Color only on a real terminal (and never into a test's capture list).
def _tty_color(out: Output) -> bool:
    return (out is print and sys.stdout.isatty()
            and not os.environ.get("NO_COLOR"))


def _paint(text: str, code: str, out: Output) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _tty_color(out) else text


class _Stepper:
    """Numbers the wizard's section headers ([2/6] Model & inference …)."""

    def __init__(self, total: int):
        self.n = 0
        self.total = total


_STEP: _Stepper | None = None


def _hdr(out: Output, title: str) -> None:
    global _STEP
    tag = ""
    if _STEP is not None:
        _STEP.n += 1
        tag = f"[{_STEP.n}/{_STEP.total}] "
    line = f"{tag}{title}"
    out("")
    out(_paint(f"━━ {line} " + "━" * max(0, 53 - len(line)), "1;38;2;214;161;94", out))


def _truecolor(out: Output) -> bool:
    return _tty_color(out) and os.environ.get("COLORTERM", "") in ("truecolor", "24bit")


def _gradient_line(line: str, out: Output) -> str:
    """Paint one banner line with the warm AEGIS desktop gradient.
    Truecolor terminals get the real gradient; others get the flat accent."""
    if not _truecolor(out):
        return _paint(line, "1;33", out)
    a, b = (214, 161, 94), (126, 207, 143)
    n = max(1, len(line) - 1)
    chars = []
    for i, ch in enumerate(line):
        t = i / n
        r, g, bl = (round(a[k] + (b[k] - a[k]) * t) for k in range(3))
        chars.append(f"\x1b[1;38;2;{r};{g};{bl}m{ch}")
    return "".join(chars) + "\x1b[0m"


_LOGO = (
    "   ▗▄▄▄▖ ▗▄▄▄▖ ▗▄▄▖ ▗▄▄▄▖ ▗▄▄▖",
    "   ▐▌ ▐▌ ▐▌    ▐▌     █   ▐▌   ",
    "   ▐▛▀▜▌ ▐▛▀▀▘ ▐▌▝▜▌  █    ▝▀▚▖",
    "   ▐▌ ▐▌ ▐▙▄▄▖ ▝▚▄▞▘▗▄█▄▖▗▄▄▞▘",
)


def _banner(out: Output) -> None:
    from . import __version__
    out("")
    for line in _LOGO:
        out(_gradient_line(line, out))
    out(_paint("   ─────────────────────────────────", "2", out))
    out("   " + _paint(f"v{__version__}", "1", out)
        + _paint("  ·  local agent desktop", "2", out)
        + _paint("  ·  providers · tools · channels", "2", out))
    out("")


MODEL_PRESETS: dict[str, list[tuple[str, str]]] = {
    "codex": [
        ("gpt-5.5", "GPT-5.5 Codex (most capable)"),
        ("gpt-5.4", "GPT-5.4 Codex"),
        ("gpt-5.4-mini", "GPT-5.4 mini Codex"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5.2", "GPT-5.2"),
        ("codex-auto-review", "Codex auto review"),
    ],
    "codex-app-server": [
        ("gpt-5.5", "GPT-5.5 Codex (most capable)"),
        ("gpt-5.4", "GPT-5.4 Codex"),
        ("gpt-5.4-mini", "GPT-5.4 mini Codex"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5.2", "GPT-5.2"),
        ("codex-auto-review", "Codex auto review"),
    ],
    "openai-codex": [
        ("gpt-5.5", "GPT-5.5 Codex (most capable)"),
        ("gpt-5.4", "GPT-5.4 Codex"),
        ("gpt-5.4-mini", "GPT-5.4 mini Codex"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5.2", "GPT-5.2"),
        ("codex-auto-review", "Codex auto review"),
    ],
    "openai": [
        ("gpt-5.5", "GPT-5.5 (latest frontier)"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.3", "GPT-5.3"),
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.2-chat-latest", "GPT-5.2 Chat (API-key friendly)"),
        ("gpt-5-mini", "GPT-5 mini (fast, cheap)"),
        ("gpt-5-nano", "GPT-5 nano (fastest)"),
        ("o3", "o3 (deep reasoning)"),
        ("o4-mini", "o4-mini (fast reasoning)"),
        ("gpt-4.1", "GPT-4.1"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4o-mini", "GPT-4o mini"),
    ],
    "anthropic": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 (balanced, recommended)"),
        ("claude-opus-4-8", "Claude Opus 4.8 (most capable Opus)"),
        ("claude-fable-5", "Claude Fable 5 (frontier tier above Opus)"),
        ("claude-haiku-4-5", "Claude Haiku 4.5 (fast, cheap)"),
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
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
        ("anthropic/claude-sonnet-4.6", "Claude Sonnet via OpenRouter"),
        ("openai/gpt-5.5", "GPT-5.5 via OpenRouter"),
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
    "qwen": [
        ("qwen-max", "Qwen Max"),
        ("qwen-plus", "Qwen Plus"),
        ("qwen-turbo", "Qwen Turbo"),
    ],
    "minimax": [
        ("MiniMax-M2", "MiniMax M2"),
    ],
    "xai": [
        ("grok-2-latest", "Grok default"),
    ],
}

VALID_WEB_BACKENDS = {"auto", "duckduckgo", "brave", "tavily", "serper", "skip"}
VALID_TOOLSETS = {"core", "browser", "computer", "voice", "lsp", "mcp", "all"}
VALID_CHANNELS = {"cli", "telegram", "discord", "slack", "signal", "matrix", "email", "webhook", "ntfy"}


@dataclass
class OnboardingState:
    provider: str = ""
    model: str = ""
    auth_method: str = ""
    web_backend: str = ""
    channels: list[str] | None = None
    workspace_files: list[str] | None = None
    toolsets: list[str] | None = None
    enabled_tools: int = 0
    total_tools: int = 0
    available_skills: int = 0
    bundled_skills: int = 0
    plugin_files: int = 0
    plugin_tools: int = 0
    plugin_errors: int = 0
    dashboard_url: str = ""
    services: list[str] | None = None
    service_errors: list[str] | None = None
    errors: list[str] | None = None


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
    state = OnboardingState(channels=[], services=[], workspace_files=[], service_errors=[], errors=[])

    out = output_func
    _banner(out)
    out(_paint("SECURITY NOTICE:", "1;33", out) +
        " AEGIS can execute commands, edit files, and connect")
    out("to messaging networks when you enable those tools. Use it only in a")
    out("trusted environment and keep API keys private.")
    if not _confirm("Acknowledge security notice and proceed?", True, input_func, out):
        out("onboarding cancelled.")
        return 1

    if cfg.config_path().exists() and not quick and not advanced:
        choice = _choose(
            "Existing configuration found:",
            [
                ("modify", "Review and modify current setup"),
                ("keep", "Keep current setup and exit"),
                ("reset", "Reset non-secret config and onboard again"),
            ],
            default=0,
            input_func=input_func,
            output_func=out,
        )
        if choice == "keep":
            out("keeping existing setup.")
            return 0
        if choice == "reset":
            from .config import DEFAULT_CONFIG

            config.data = copy.deepcopy(DEFAULT_CONFIG)
            config.save()
            out("✓ reset config.yaml to defaults. Secrets and OAuth tokens were left untouched.")

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

    global _STEP
    _STEP = _Stepper(6 if advanced else 5)   # memory is an advanced-only section
    if not _configure_model(config, state, advanced, probe, input_func, secret_func, out):
        return 1
    if input_func is input:          # real interactive setup only — never hit the network in tests
        _refresh_model_metadata(out)
    _configure_web(config, state, advanced, input_func, secret_func, out)
    _configure_memory(config, state, advanced, input_func, out)
    _configure_agent_surface(config, state, advanced, input_func, out)
    _configure_channels(config, state, advanced, input_func, secret_func, out)
    _seed_workspace(state, out)
    _configure_dashboard(config, state, out)
    if services:
        _configure_services(config, state, advanced, input_func, out)
    config.save()
    _summary(config, state, out)
    return 0


def run_onboarding_noninteractive(
    config: Config,
    *,
    accept_risk: bool = False,
    json_output: bool = False,
    provider: str | None = None,
    auth: str = "skip",
    model: str | None = None,
    web: str = "auto",
    toolsets: str | None = None,
    channels: str | None = None,
    exec_mode: str = "ask",
    services: bool = False,
    output_func: Output = print,
) -> int:
    state = OnboardingState(channels=[], services=[], workspace_files=[], service_errors=[], errors=[])

    def fail(message: str, code: int = 2) -> int:
        state.errors.append(message)
        if json_output:
            output_func(json.dumps(_summary_data(config, state, ok=False), indent=2))
        else:
            output_func(f"error: {message}")
        return code

    if not accept_risk:
        return fail("noninteractive onboarding requires --accept-risk")
    if auth == "oauth":
        return fail(
            "OAuth requires an interactive browser login; use --auth codex, "
            "--auth api-key, or --auth skip"
        )
    if auth not in {"skip", "api-key", "local", "codex"}:
        return fail(f"unknown auth method: {auth}")
    if exec_mode not in {"ask", "auto", "allowlist", "deny", "full", "smart"}:
        return fail(f"unknown exec mode: {exec_mode}")
    if web not in VALID_WEB_BACKENDS:
        return fail(f"unknown web backend: {web}")

    provider_name = provider or config.get("model.provider", "anthropic")
    if provider_name == "openai" and auth == "codex":
        provider_name = "codex"
    spec = registry.get_spec(provider_name)
    if not spec:
        return fail(f"unknown provider: {provider_name}")
    if auth == "local" and spec.auth_scheme != "none":
        return fail(f"provider {provider_name} is not a local/no-auth provider")
    if auth == "codex":
        if spec.auth_scheme == "codex-cli":
            ok, detail = _codex_login_status()
            if not ok:
                return fail(f"Codex CLI auth is not ready: {detail}")
        elif spec.auth_scheme == "codex-backend":
            from .providers.auth import CodexBackendAuth
            if not CodexBackendAuth().available():
                return fail(
                    "Codex auth is not ready: run `codex login` "
                    "or use --auth api-key/skip"
                )
        elif spec.oauth and spec.oauth.provider == "openai-codex":
            from .providers.auth import AuthStore, OAuthAuth
            oauth = OAuthAuth(spec.oauth, AuthStore())
            if not oauth.available():
                return fail(
                    "Codex OAuth auth is not ready: run `aegis auth login openai-codex` "
                    "or use --auth api-key/skip"
                )
        else:
            return fail(f"provider {provider_name} does not use Codex subscription auth")
    if auth == "api-key":
        if not spec.env_vars:
            return fail(f"provider {provider_name} does not use API-key auth")
        if not any(os.environ.get(env) for env in spec.env_vars):
            return fail(
                "API-key onboarding requires an existing environment variable: "
                + ", ".join(spec.env_vars)
            )
    selected_toolsets = _parse_csv(toolsets) if toolsets else _recommended_toolsets()
    if "core" not in selected_toolsets:
        selected_toolsets.insert(0, "core")
    unknown_toolsets = sorted(set(selected_toolsets) - VALID_TOOLSETS)
    if unknown_toolsets:
        return fail("unknown toolset(s): " + ", ".join(unknown_toolsets))
    selected_channels = _parse_csv(channels)
    unknown_channels = sorted(set(selected_channels) - VALID_CHANNELS)
    if unknown_channels:
        return fail("unknown channel(s): " + ", ".join(unknown_channels))

    config.set("model.provider", provider_name)
    chosen_model = model or spec.default_model
    config.set("model.default", chosen_model)
    config.set("tools.exec_mode", exec_mode)
    state.provider = provider_name
    state.model = chosen_model
    state.auth_method = auth

    if web == "skip":
        state.web_backend = "skip"
    else:
        config.set("web.search_backend", web)
        state.web_backend = web

    config.set("tools.toolsets", selected_toolsets)

    config.data.setdefault("gateway", {})["channels"] = selected_channels
    state.channels = selected_channels

    _populate_surface_state(config, state)
    _seed_workspace(state, lambda _msg: None)
    _configure_dashboard(config, state, lambda _msg: None)

    if services:
        _install_services_noninteractive(config, state)

    config.save()
    data = _summary_data(config, state, ok=not state.errors)
    if json_output:
        output_func(json.dumps(data, indent=2))
    else:
        output_func("✓ noninteractive onboarding complete.")
        output_func(f"Config: {data['paths']['config']}")
        output_func(f"Dashboard: {data['dashboard_url']}")
        output_func("Next: aegis status && aegis")
    return 0 if not state.errors else 1


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
        try:
            raw = input_func(f"? {prompt} ({suffix}) ").strip().lower()
        except KeyboardInterrupt:
            output_func("")
            return False
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
    # Inline arrow-key menu when this is a real interactive terminal.
    if input_func is input and output_func is print:
        try:
            from .cli.menu import select_one
            chosen = select_one(prompt, options, default)
            if chosen is not None:
                return chosen
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001  (any terminal issue -> typed fallback)
            pass

    output_func(f"? {prompt}")
    for i, (_, label) in enumerate(options, 1):
        marker = "❯" if i == default + 1 else " "
        output_func(f"  {marker} {label}")
    for _ in range(3):
        raw = input_func(f"choice [{options[default][1]}]: ").strip()
        if not raw:
            return options[default][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        lowered = raw.lower()
        if lowered.startswith("sk-"):
            output_func("  that looks like an API key; choose the provider first.")
            continue
        for value, label in options:
            if _choice_matches(raw, value, label):
                return value
        output_func("  unknown choice; try the number or provider name.")
    return options[default][0]


def _choice_matches(raw: str, value: str, label: str) -> bool:
    needle = _choice_key(raw)
    value_key = _choice_key(value)
    label_key = _choice_key(label)
    if not needle:
        return False
    if needle in {value_key, label_key}:
        return True
    if needle in label_key or needle in value_key:
        return True
    label_words = set(label_key.split()) | set(value_key.split())
    return all(part in label_words for part in needle.split())


def _choice_key(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return " ".join(cleaned.split())


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


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
    # Inline checkbox menu (Space toggles) when this is a real interactive terminal.
    if input_func is input and output_func is print:
        try:
            from .cli.menu import select_many
            chosen = select_many(prompt, options, default_values)
            if chosen is not None:
                return chosen
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            pass

    output_func(f"? {prompt}")
    for _i, (value, label) in enumerate(options, 1):
        marker = "⬢" if value in default_values else "⬡"
        output_func(f"  {marker} {label}")
    output_func("  type names separated by commas; blank keeps the marked defaults")
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


def _codex_login_status() -> tuple[bool, str]:
    if shutil.which("codex") is None:
        return False, "codex CLI not found; install with `npm i -g @openai/codex`"
    try:
        proc = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"codex login status failed: {exc}"
    text = "\n".join(p.strip() for p in (proc.stdout, proc.stderr) if p.strip())
    if proc.returncode == 0 and "logged in" in text.lower():
        return True, text or "logged in"
    return False, text or "not logged in"


def _ensure_codex_cli_login(input_func: Input, out: Output) -> bool:
    ok, detail = _codex_login_status()
    if ok:
        out(f"✓ Codex CLI auth ready: {detail.splitlines()[0]}")
        return True
    out(f"! Codex CLI auth is not ready: {detail}")
    if shutil.which("codex") is None:
        if _confirm("Install Codex CLI now with `npm install -g @openai/codex`?", True, input_func, out):
            if not _install_codex_cli(out):
                return False
        else:
            out("  Install Codex CLI, then run `codex login` and re-run `aegis setup`.")
            return False
    ok, detail = _codex_login_status()
    if ok:
        out(f"✓ Codex CLI auth ready: {detail.splitlines()[0]}")
        return True
    if shutil.which("codex") is None:
        out("! Codex CLI is still not on PATH after install.")
        return False
    if not _confirm("Sign in with ChatGPT using `codex login` now?", True, input_func, out):
        out("  Run `codex login` later, then re-run `aegis setup` or start AEGIS.")
        return False
    try:
        proc = subprocess.run(["codex", "login"], check=False)
    except Exception as exc:  # noqa: BLE001
        out(f"! codex login failed to start: {exc}")
        return False
    if proc.returncode != 0:
        out(f"! codex login exited with status {proc.returncode}.")
        return False
    ok, detail = _codex_login_status()
    if ok:
        out(f"✓ Codex CLI auth ready: {detail.splitlines()[0]}")
        return True
    out(f"! Codex CLI auth still not ready: {detail}")
    return False


def _install_codex_cli(out: Output) -> bool:
    npm = shutil.which("npm")
    if npm is None:
        out("! npm is not installed, so AEGIS cannot install the Codex CLI automatically.")
        out("  Install Node.js/npm, then run `npm install -g @openai/codex`.")
        return False
    out("Installing Codex CLI with npm...")
    try:
        proc = subprocess.run([npm, "install", "-g", "@openai/codex"], check=False)
    except Exception as exc:  # noqa: BLE001
        out(f"! Codex CLI install failed to start: {exc}")
        return False
    if proc.returncode != 0:
        out(f"! Codex CLI install exited with status {proc.returncode}.")
        out("  Try manually: npm install -g @openai/codex")
        return False
    if shutil.which("codex") is None:
        out("! Codex CLI installed, but `codex` is not on PATH in this shell.")
        out("  Restart your shell or add npm's global bin directory to PATH, then run `codex login`.")
        return False
    out("✓ Codex CLI installed.")
    return True


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
    flag = os.environ.get("AEGIS_ONBOARD_DIALOGS", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
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
) -> bool:
    _hdr(out, "Model & inference")
    _detected = [e for e in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                             "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
                             "DEEPSEEK_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY")
                 if os.environ.get(e)]
    if _detected:
        out(f"✓ detected credentials in your environment: {', '.join(_detected)}")
        out("  Pick the matching provider and AEGIS will use the detected key automatically.")
    previous_provider = config.get("model.provider", "anthropic")
    previous_model = config.get("model.default", "claude-sonnet-4-5")

    def abort_required_auth(message: str) -> bool:
        out(message)
        out("  Re-run `aegis setup` after configuring auth, or choose OpenAI API key / Skip credentials.")
        config.set("model.provider", previous_provider)
        config.set("model.default", previous_model)
        return False

    common = [
        ("openai", "OpenAI / Codex"),
        ("anthropic", "Anthropic (Claude)"),
        ("google", "Google Gemini"),
        ("ollama", "Ollama (local / offline)"),
        ("openrouter", "OpenRouter"),
        ("deepseek", "DeepSeek"),
        ("groq", "Groq"),
        ("qwen", "Qwen"),
        ("minimax", "MiniMax"),
        ("xai", "xAI (Grok)"),
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
        return True
    state.provider = provider
    config.set("model.provider", provider)
    auth_ready = spec.auth_scheme == "none"

    if provider == "openai":
        env_name = spec.env_vars[0] if spec.env_vars else "OPENAI_API_KEY"
        auth_method = _choose(
            "Choose authentication method:",
            [
                ("codex", "ChatGPT subscription via Codex login"),
                ("api_key", f"OpenAI API key ({env_name})"),
                ("skip", "Skip credentials for now"),
            ],
            default=0,
            input_func=input_func,
            output_func=out,
        )
        state.auth_method = auth_method
        if auth_method == "codex":
            codex_spec = registry.get_spec("codex")
            if codex_spec is None:
                state.auth_method = "skipped"
                return abort_required_auth("! Codex provider is unavailable in this build.")
            else:
                provider = "codex"
                spec = codex_spec
                state.provider = provider
                config.set("model.provider", provider)
                if spec.auth_scheme == "codex-backend":
                    auth_ready = _ensure_codex_cli_login(input_func, out)
                else:
                    auth_ready = _oauth_login(provider, spec, out)
                if not auth_ready:
                    state.auth_method = "skipped"
                    return abort_required_auth("! ChatGPT subscription setup did not finish.")
        elif auth_method == "api_key":
            auth_ready = _configure_api_key(config, env_name, secret_func, out, input_func)
            if not auth_ready:
                state.auth_method = "skipped"
        else:
            state.auth_method = "skipped"
    elif spec.auth_scheme == "codex-cli":
        state.auth_method = "codex"
        auth_ready = _ensure_codex_cli_login(input_func, out)
        if not auth_ready:
            state.auth_method = "skipped"
            return abort_required_auth("! ChatGPT subscription setup did not finish.")
    elif spec.env_vars or spec.oauth:
        env_name = spec.env_vars[0] if spec.env_vars else ""
        auth_options: list[tuple[str, str]] = []
        # For Anthropic, reusing an existing Claude Code / Claude CLI login is the most
        # reliable subscription path (no fragile OAuth flow) — offer it first.
        if provider == "anthropic":
            import pathlib
            has_claude = (pathlib.Path.home() / ".claude" / ".credentials.json").exists()
            label = "Reuse Claude Code login (Claude subscription"
            label += ", detected ✓)" if has_claude else " — run `claude` login first)"
            auth_options.append(("claude_cli", label))
        if spec.oauth:
            auth_options.append(("oauth", "OAuth browser login"))
        if env_name:
            auth_options.append(("api_key", f"API key ({env_name})"))
        auth_options.append(("skip", "Skip credentials for now"))
        auth_method = _choose(
            "Choose authentication method:",
            auth_options,
            default=0,
            input_func=input_func,
            output_func=out,
        )
        state.auth_method = auth_method
        if auth_method == "claude_cli":
            from .providers.auth import AuthStore, import_claude_cli_login
            auth_ready, detail = import_claude_cli_login(AuthStore())
            out(("✓ " if auth_ready else "! ") + detail)
            if not auth_ready:
                out("  Run Claude Code (`claude`) and log in, then re-run `aegis setup`,"
                    " or choose API key.")
                state.auth_method = "skipped"
        elif auth_method == "api_key":
            auth_ready = _configure_api_key(config, env_name, secret_func, out, input_func)
            if not auth_ready:
                state.auth_method = "skipped"
        elif auth_method == "oauth" and spec.oauth:
            auth_ready = _oauth_login(provider, spec, out)
            if not auth_ready:
                out("  Use an API key if OAuth is unavailable for this provider.")
                fallback_env = env_name
                if fallback_env and _confirm(f"Configure {fallback_env} instead?", True, input_func, out):
                    env_name = fallback_env
                    auth_ready = _configure_api_key(config, env_name, secret_func, out, input_func)
                    state.auth_method = "api_key" if auth_ready else "skipped"
                else:
                    state.auth_method = "skipped"
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

    if probe and auth_ready:
        _probe_model(config, out)
    elif probe:
        out("Skipping model connection test until usable credentials are configured.")
    return True


def _configure_api_key(config: Config, env_name: str, secret_func: Input, out: Output,
                       input_func: Input | None = None) -> bool:
    # If the key is already in the environment, OFFER it — the user decides whether to use it.
    existing = os.environ.get(env_name, "").strip()
    if existing and input_func is not None and _confirm(
            f"Found {env_name} in your environment — use it?", True, input_func, out):
        config.set(env_name, existing)
        out(f"✓ using your existing {env_name}.")
        return True
    out(f"  Paste your {env_name} (input hidden); press Enter to skip.")
    key = secret_func(f"🔑 {env_name}: ").strip()
    if key:
        config.set(env_name, key)
        out(f"✓ saved {env_name} to {cfg.env_path()}")
        return True
    out(f"! {env_name} skipped — set it later with `aegis config set {env_name} <key>`.")
    return False


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
    _hdr(out, "Web search")
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


def _recommended_toolsets() -> list[str]:
    toolsets = ["core"]
    if importlib.util.find_spec("playwright"):
        toolsets.append("browser")
    toolsets.append("lsp")
    toolsets.append("mcp")
    return toolsets


def _refresh_model_metadata(out: Output) -> None:
    """Best-effort: pull live model context windows from models.dev once at setup so the
    compaction/auto-split math is right for any model. Silent + non-fatal if offline."""
    try:
        from . import model_meta
        n = model_meta.refresh(timeout=8.0)
        out(f"✓ cached context windows for {n} models (models.dev)")
    except Exception:  # noqa: BLE001
        pass   # offline / blocked — the bundled snapshot is used instead


def _configure_memory(config: Config, state: OnboardingState, advanced: bool,
                      input_func: Input, out: Output) -> None:
    """Long-term memory backend. File memory (MEMORY.md/USER.md) is always on; this layers
    an optional external provider on top. Advanced-only so the quick path stays fast."""
    if not advanced:
        return
    _hdr(out, "Memory")
    out("File memory (MEMORY.md / USER.md) is always on. Optionally add a backend:")
    choice = _choose(
        "Long-term memory backend:",
        [("", "Built-in files only (recommended)"),
         ("jsonl", "JSONL event log — zero-dependency, local"),
         ("mem0", "mem0 — semantic memory (pip install mem0ai)"),
         ("honcho", "Honcho — hosted memory (pip install honcho-ai)")],
        default=0,
        input_func=input_func,
        output_func=out,
    )
    config.set("memory.provider", choice)
    if choice:
        out(f"✓ memory provider → {choice}")


def _configure_agent_surface(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    out: Output,
) -> None:
    _hdr(out, "Tools & skills")
    current = list(config.get("tools.toolsets", []) or ["core", "mcp"])
    recommended = _recommended_toolsets()
    if advanced:
        selected = _multi_choose(
            "Which optional toolsets should AEGIS expose to the model?",
            [
                ("browser", "Browser automation (Playwright)"),
                ("computer", "Computer control (screen/keyboard/mouse)"),
                ("voice", "Voice and transcription tools"),
                ("lsp", "Language-server code intelligence"),
                ("mcp", "MCP server tools"),
            ],
            default_values=[t for t in current if t != "core"] or [t for t in recommended if t != "core"],
            input_func=input_func,
            output_func=out,
        )
        toolsets = ["core"] + [t for t in ("browser", "computer", "voice", "lsp", "mcp") if t in selected]
    else:
        toolsets = recommended
        if current and current != ["core", "mcp"]:
            toolsets = current
    config.set("tools.toolsets", toolsets)

    tools, skills, plugins = _populate_surface_state(config, state)
    out(f"✓ enabled toolsets: {', '.join(tools.toolsets)}")
    out(f"✓ model-visible tools: {tools.enabled_count}/{tools.total_count}")
    if tools.disabled_sets:
        disabled = ", ".join(f"{name} ({count})" for name, count in sorted(tools.disabled_sets.items()))
        out(f"  optional disabled toolsets: {disabled}")
    out(f"✓ skills available: {skills.available_count} ({skills.bundled_count} bundled)")
    out(f"✓ plugins loaded: {plugins.files_count} file(s), {len(plugins.tools)} tool(s)")
    if plugins.errors:
        out(f"  plugin load errors: {len(plugins.errors)}; run `aegis plugins doctor`")
    out("  Use `aegis status`, `aegis tools`, `aegis skills`, and `aegis plugins` to inspect them.")


def _populate_surface_state(config: Config, state: OnboardingState):
    from .surface import plugin_inventory, skill_inventory, tool_inventory

    tools = tool_inventory(config)
    skills = skill_inventory(config)
    plugins = plugin_inventory()
    state.toolsets = tools.toolsets
    state.enabled_tools = tools.enabled_count
    state.total_tools = tools.total_count
    state.available_skills = skills.available_count
    state.bundled_skills = skills.bundled_count
    state.plugin_files = plugins.files_count
    state.plugin_tools = len(plugins.tools)
    state.plugin_errors = len(plugins.errors)
    return tools, skills, plugins


def _configure_channels(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    secret_func: Input,
    out: Output,
) -> None:
    _hdr(out, "Messaging & channels")
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
        # NOTE: no workspace/USER.md — the user profile lives in ONE place,
        # memories/USER.md (managed by the `memory` tool). A legacy
        # workspace/USER.md from an older install is auto-migrated there once
        # and parked as USER.md.migrated, so there is a single source of truth.
        "README.md": (
            "# AEGIS Workspace\n\n"
            "This directory is persistent context for AEGIS.\n\n"
            "- SOUL.md: persona and tone.\n"
            "- AGENTS.md: operating rules.\n"
            "- Your profile (name, preferences) lives in memories/USER.md and is\n"
            "  managed by the agent's `memory` tool — just tell it and it remembers.\n"
            "- Skills are available with `aegis skills` and the `skill` tool.\n"
            "- Tools are visible with `aegis tools`.\n"
            "- Plugins are visible with `aegis plugins`.\n"
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
    # Create the memory store up front so MEMORY.md / USER.md are visible and editable
    # from first run (not only after the agent's first write).
    from .memory import MemoryStore
    MemoryStore().ensure_files()
    out(f"✓ memory store ready: {cfg.memories_dir()} (MEMORY.md, USER.md)")


def _configure_dashboard(config: Config, state: OnboardingState, out: Output) -> None:
    token = config.get("server.dashboard_token")
    if not token:
        token = "aegis_tok_" + secrets.token_urlsafe(24)
        config.data.setdefault("server", {})["dashboard_token"] = token
        config.save()
    host = config.get("server.dashboard_host", "127.0.0.1")
    port = int(config.get("server.dashboard_port", 9119))
    from .daemon import port_available

    if not port_available(host, port):
        original = port
        for candidate in range(port + 1, port + 51):
            if port_available(host, candidate):
                port = candidate
                config.data.setdefault("server", {})["dashboard_port"] = port
                config.save()
                out(f"✓ dashboard port {original} is busy; using {port}.")
                break
        else:
            out(f"! dashboard port {original} is busy and no free nearby port was found.")
    state.dashboard_url = f"http://{host}:{port}/?token={token}"


def _configure_services(
    config: Config,
    state: OnboardingState,
    advanced: bool,
    input_func: Input,
    out: Output,
) -> None:
    _hdr(out, "Background services")
    from .daemon import systemd_available

    if not systemd_available():
        msg = "user systemd is not available; skipping service install."
        out(f"! {msg}")
        state.service_errors.append(msg)
        return
    default = False
    if not _confirm("Install/start user systemd services for dashboard/gateway?", default, input_func, out):
        return
    from .daemon import install_dashboard_service, install_gateway_service

    dash = install_dashboard_service(config)
    out(("✓ " if dash.ok else "! ") + dash.message)
    if dash.ok:
        state.services.append("dashboard")
    else:
        state.service_errors.append(dash.message)
    if state.channels:
        gate = install_gateway_service(config, state.channels)
        out(("✓ " if gate.ok else "! ") + gate.message)
        if gate.ok:
            state.services.append("gateway")
        else:
            state.service_errors.append(gate.message)


def _install_services_noninteractive(config: Config, state: OnboardingState) -> None:
    from .daemon import install_dashboard_service, install_gateway_service, systemd_available

    if not systemd_available():
        state.service_errors.append("user systemd is not available")
        return
    dash = install_dashboard_service(config)
    if dash.ok:
        state.services.append("dashboard")
    else:
        state.service_errors.append(dash.message)
    if state.channels:
        gate = install_gateway_service(config, state.channels)
        if gate.ok:
            state.services.append("gateway")
        else:
            state.service_errors.append(gate.message)


def _summary_data(config: Config, state: OnboardingState, *, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "paths": {
            "home": str(cfg.get_home()),
            "config": str(cfg.config_path()),
            "secrets": str(cfg.env_path()),
            "workspace": str(cfg.sub("workspace")),
        },
        "model": {
            "provider": state.provider or config.get("model.provider"),
            "model": state.model or config.get("model.default"),
            "auth": state.auth_method or "not configured",
        },
        "web_search": state.web_backend or config.get("web.search_backend"),
        "surface": {
            "toolsets": state.toolsets or config.get("tools.toolsets", []),
            "tools_enabled": state.enabled_tools,
            "tools_total": state.total_tools,
            "skills_available": state.available_skills,
            "skills_bundled": state.bundled_skills,
            "plugin_files": state.plugin_files,
            "plugin_tools": state.plugin_tools,
            "plugin_errors": state.plugin_errors,
        },
        "integrations": state.channels or [],
        "services": {
            "installed": state.services or [],
            "errors": state.service_errors or [],
        },
        "dashboard_url": state.dashboard_url,
        "workspace_files": state.workspace_files or [],
        "errors": state.errors or [],
        "next_commands": ["aegis ui", "aegis", "aegis status", "aegis doctor"],
    }


def _summary(config: Config, state: OnboardingState, out: Output) -> None:
    out("")
    out(_paint("   ┌─────────────────────────────────────┐", "1;32", out))
    out(_paint("   │   ✓  AEGIS is ready to fly           │", "1;32", out))
    out(_paint("   └─────────────────────────────────────┘", "1;32", out))
    out(f"Config:          {cfg.config_path()}")
    out(f"Primary brain:   {config.get('model.provider')} {config.get('model.default')}")
    out(f"Web search:      {config.get('web.search_backend')}")
    out(f"Auth:            {state.auth_method or 'not configured'}")
    out(f"Integrations:    {', '.join(state.channels or []) or 'none'}")
    out(f"Toolsets:        {', '.join(state.toolsets or config.get('tools.toolsets', []) or [])}")
    out(f"Tools:           {state.enabled_tools}/{state.total_tools} model-visible")
    out(f"Skills:          {state.available_skills} available ({state.bundled_skills} bundled)")
    out(f"Plugins:         {state.plugin_files} file(s), {state.plugin_tools} tool(s), "
        f"{state.plugin_errors} error(s)")
    out(f"Workspace:       {cfg.workspace_dir()}")
    if state.workspace_files:
        out(f"Workspace files: {', '.join(state.workspace_files)}")
    out(f"Services:        {', '.join(state.services or []) or 'not installed'}")
    if state.service_errors:
        out(f"Service issues:  {', '.join(state.service_errors)}")
    out("")
    out(_paint("Next steps — three ways to use AEGIS:", "1", out))
    out(f"  {_paint('aegis', '1;36', out)}         → chat in the terminal")
    out(f"  {_paint('aegis ui', '1;36', out)}      → web control panel ({state.dashboard_url})")
    out(f"  {_paint('aegis doctor', '1;36', out)}  → verify the install end to end")
