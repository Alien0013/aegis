"""Deep health probes for `aegis doctor --probe`.

Static checks (deps, dirs, perms) live in cli.main.cmd_doctor; this module does
the parts that hit the network: a real one-token provider completion (proves
auth + model end to end, reports latency) and per-channel token validation
(proves the gateway will actually connect before you start it).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

from .config import Config
from .platforms import BRIDGE_PLATFORM_DEFINITIONS

_TIMEOUT = 12.0

WINDOWS_SIGNING_SECRETS = ("DESKTOP_WINDOWS_CSC_LINK", "DESKTOP_WINDOWS_CSC_NAME", "CSC_LINK", "CSC_NAME")
MAC_SIGNING_SECRETS = ("DESKTOP_MAC_CSC_LINK", "DESKTOP_MAC_CSC_NAME", "CSC_LINK", "CSC_NAME")
MAC_APPLE_ID_NOTARY_SECRETS = (
    ("APPLE_ID",),
    ("APPLE_APP_SPECIFIC_PASSWORD", "APPLE_ID_PASSWORD"),
    ("APPLE_TEAM_ID", "APPLE_ID_TEAM_ID"),
)
MAC_API_KEY_NOTARY_SECRETS = (
    ("APPLE_API_KEY", "APPLE_API_KEY_PATH"),
    ("APPLE_API_KEY_ID", "APPLE_API_KEYID"),
    ("APPLE_API_ISSUER", "APPLE_API_ISSUER_ID"),
)
CHANNEL_ENV_HINTS = {
    "api_server": (
        "API_SERVER_ENABLED",
        "API_SERVER_HOST",
        "API_SERVER_PORT",
        "API_SERVER_KEY",
        "API_SERVER_API_KEY",
        "AEGIS_SERVER_KEY",
    ),
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
    "webhook": (
        "WEBHOOK_CHANNEL_SECRET",
        "WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
        "WEBHOOK_CHANNEL_INSECURE_NO_AUTH",
    ),
    "whatsapp": (
        "WHATSAPP_CHANNEL_SECRET",
        "WHATSAPP_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
        "WHATSAPP_CHANNEL_INSECURE_NO_AUTH",
    ),
}

for _bridge_id, _bridge in BRIDGE_PLATFORM_DEFINITIONS.items():
    _prefix = str(_bridge["env_prefix"])
    CHANNEL_ENV_HINTS.setdefault(_bridge_id, (
        f"{_prefix}_SECRET",
        f"{_prefix}_ALLOW_UNSIGNED_LOOPBACK",
        f"{_prefix}_INSECURE_NO_AUTH",
        f"{_prefix}_OUTBOUND_URL",
    ))


def _secret_present(name: str, available: set[str] | None = None) -> bool:
    if os.environ.get(name):
        return True
    return available is not None and name in available


def _any_secret_present(names: tuple[str, ...], available: set[str] | None = None) -> bool:
    return any(_secret_present(name, available) for name in names)


def _secret_groups_present(groups: tuple[tuple[str, ...], ...], available: set[str] | None = None) -> bool:
    return all(_any_secret_present(group, available) for group in groups)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _github_repo_slug(cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(cwd or Path.cwd()),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return ""
    remote = proc.stdout.strip()
    if not remote:
        return ""
    if remote.startswith("git@github.com:"):
        return remote.removeprefix("git@github.com:").removesuffix(".git")
    marker = "github.com/"
    if marker in remote:
        return remote.split(marker, 1)[1].removesuffix(".git")
    return ""


def github_secret_names(repo: str | None = None) -> tuple[set[str], str]:
    """Return visible GitHub secret names. Values are never available through gh."""
    repo = repo or _github_repo_slug()
    if not repo:
        return set(), "repo remote not detected"
    try:
        proc = subprocess.run(
            ["gh", "secret", "list", "--repo", repo],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return set(), "gh not installed"
    except Exception as exc:  # noqa: BLE001
        return set(), f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return set(), detail or f"gh exited {proc.returncode}"
    names = {
        line.split()[0].strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    }
    return names, f"{repo}: {len(names)} secret name(s) visible"


def release_preflight(secret_names: set[str] | None = None) -> tuple[bool, list[str]]:
    names = set(secret_names or set())
    rows: list[str] = []
    ok = True

    windows_ready = _any_secret_present(WINDOWS_SIGNING_SECRETS, names)
    rows.append(
        ("✓" if windows_ready else "✗")
        + " Windows signing — "
        + (
            "certificate identity configured"
            if windows_ready
            else "missing DESKTOP_WINDOWS_CSC_LINK/DESKTOP_WINDOWS_CSC_NAME or local CSC_LINK/CSC_NAME"
        )
    )
    ok = ok and windows_ready

    mac_signing = _any_secret_present(MAC_SIGNING_SECRETS, names)
    rows.append(
        ("✓" if mac_signing else "✗")
        + " macOS signing — "
        + (
            "certificate identity configured"
            if mac_signing
            else "missing DESKTOP_MAC_CSC_LINK/DESKTOP_MAC_CSC_NAME or local CSC_LINK/CSC_NAME"
        )
    )
    ok = ok and mac_signing

    apple_id_notary = _secret_groups_present(MAC_APPLE_ID_NOTARY_SECRETS, names)
    api_key_notary = _secret_groups_present(MAC_API_KEY_NOTARY_SECRETS, names)
    notary_ready = apple_id_notary or api_key_notary
    if apple_id_notary:
        notary_detail = "Apple ID notarization configured"
    elif api_key_notary:
        notary_detail = "App Store Connect API key notarization configured"
    else:
        notary_detail = (
            "missing APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD/APPLE_ID_PASSWORD + APPLE_TEAM_ID/APPLE_ID_TEAM_ID "
            "or APPLE_API_KEY/APPLE_API_KEY_PATH + APPLE_API_KEY_ID/APPLE_API_KEYID "
            "+ APPLE_API_ISSUER/APPLE_API_ISSUER_ID"
        )
    rows.append(("✓" if notary_ready else "✗") + " macOS notarization — " + notary_detail)
    ok = ok and notary_ready
    return ok, rows


def run_release_preflight(out=print, *, secret_names: set[str] | None = None) -> int:
    """Check whether CI can produce signed/notarized desktop releases."""
    names = secret_names
    source = "env"
    if names is None:
        names, source = github_secret_names()
    ok, rows = release_preflight(names)
    out("desktop release signing preflight:")
    out(f"  source: {source}")
    for row in rows:
        out(f"  {row}")
    if ok:
        out("  ✓ signed Windows and signed/notarized macOS release inputs are present")
        return 0
    out("  ✗ release will fall back to unsigned artifacts until these secrets are configured")
    return 1


def probe_provider(config: Config) -> tuple[bool, str]:
    """One real micro-completion against the configured provider."""
    try:
        from .providers.fallback import build_with_fallbacks
        from .types import Message
        p = build_with_fallbacks(config)
        t0 = time.monotonic()
        resp = p.complete([Message.user("Reply with the single word: ok")],
                          tools=None, stream=False)
        ms = int((time.monotonic() - t0) * 1000)
        text = (resp.text or "").strip()[:40] or "(empty)"
        return True, f"{p.name}/{p.model} responded in {ms} ms: {text!r}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _get(url: str, headers: dict | None = None) -> httpx.Response:
    return httpx.get(url, headers=headers or {}, timeout=_TIMEOUT)


def probe_telegram() -> tuple[bool, str]:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        return False, "TELEGRAM_BOT_TOKEN not set"
    r = _get(f"https://api.telegram.org/bot{tok}/getMe")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 200 and data.get("ok"):
        return True, f"bot @{data['result'].get('username', '?')}"
    return False, f"getMe failed (HTTP {r.status_code})"


def probe_discord() -> tuple[bool, str]:
    tok = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not tok:
        return False, "DISCORD_BOT_TOKEN not set"
    r = _get("https://discord.com/api/v10/users/@me", {"Authorization": f"Bot {tok}"})
    if r.status_code == 200:
        return True, f"bot {r.json().get('username', '?')}"
    return False, f"users/@me failed (HTTP {r.status_code})"


def probe_slack() -> tuple[bool, str]:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        return False, "SLACK_BOT_TOKEN not set"
    r = httpx.post("https://slack.com/api/auth.test",
                   headers={"Authorization": f"Bearer {tok}"}, timeout=_TIMEOUT)
    data = r.json()
    if data.get("ok"):
        return True, f"workspace {data.get('team', '?')} as {data.get('user', '?')}"
    return False, f"auth.test: {data.get('error', f'HTTP {r.status_code}')}"


def _probe_webhook_prefix(prefix: str) -> tuple[bool, str]:
    if os.environ.get(f"{prefix}_SECRET"):
        return True, f"{prefix}_SECRET configured"
    if _env_truthy(f"{prefix}_ALLOW_UNSIGNED_LOOPBACK"):
        return True, "unsigned loopback explicitly enabled"
    if _env_truthy(f"{prefix}_INSECURE_NO_AUTH"):
        return True, "insecure no-auth mode explicitly enabled"
    return False, f"{prefix}_SECRET not set and unsigned loopback/no-auth not enabled"


def probe_webhook() -> tuple[bool, str]:
    return _probe_webhook_prefix("WEBHOOK_CHANNEL")


def probe_whatsapp() -> tuple[bool, str]:
    return _probe_webhook_prefix("WHATSAPP_CHANNEL")


def probe_api_server() -> tuple[bool, str]:
    config = Config.load()
    api_cfg = config.get("gateway.api_server", {}) or {}
    if not isinstance(api_cfg, dict):
        api_cfg = {}
    host = os.environ.get("API_SERVER_HOST") or api_cfg.get("host") or config.get("server.host", "127.0.0.1")
    port = os.environ.get("API_SERVER_PORT") or api_cfg.get("port") or config.get("server.port", 8790)
    url = f"http://{host}:{int(port or 8790)}/v1/health"
    api_key = (
        os.environ.get("API_SERVER_KEY")
        or os.environ.get("API_SERVER_API_KEY")
        or api_cfg.get("api_key")
        or config.get("server.api_key")
        or os.environ.get("AEGIS_SERVER_KEY")
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = _get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return False, f"{url} unreachable: {type(exc).__name__}: {exc}"
    if r.status_code == 200:
        return True, f"{url} healthy"
    return False, f"{url} failed (HTTP {r.status_code})"


CHANNEL_PROBES = {
    "api_server": probe_api_server,
    "telegram": probe_telegram,
    "discord": probe_discord,
    "slack": probe_slack,
    "webhook": probe_webhook,
    "whatsapp": probe_whatsapp,
}

for _bridge_id, _bridge in BRIDGE_PLATFORM_DEFINITIONS.items():
    _prefix = str(_bridge["env_prefix"])
    CHANNEL_PROBES.setdefault(_bridge_id, lambda prefix=_prefix: _probe_webhook_prefix(prefix))


def _probe_channels(config: Config) -> list[str]:
    configured = [str(c).strip() for c in (config.get("gateway.channels", []) or []) if str(c).strip()]
    seen: set[str] = set()
    channels: list[str] = []
    for name in configured:
        if name not in seen:
            seen.add(name)
            channels.append(name)
    for name, hints in CHANNEL_ENV_HINTS.items():
        if name in seen:
            continue
        if any(os.environ.get(hint) for hint in hints):
            seen.add(name)
            channels.append(name)
    return channels


def run_probes(config: Config, out=print) -> int:
    """Run all applicable probes. Returns the number of failures."""
    failures = 0
    out("probes (network):")
    ok, detail = probe_provider(config)
    out(f"  {'✓' if ok else '✗'} provider — {detail}")
    failures += 0 if ok else 1
    channels = _probe_channels(config)
    for name in channels:
        probe = CHANNEL_PROBES.get(name)
        if probe is None:
            out(f"  – {name} — no probe (presence checked at gateway start)")
            continue
        try:
            ok, detail = probe()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        out(f"  {'✓' if ok else '✗'} {name} — {detail}")
        failures += 0 if ok else 1
    if not channels:
        out("  – no gateway channels configured and no channel token env vars present (channel probes skipped)")
    return failures


# --------------------------------------------------------------------------- #
# Restart forensics: was the previous gateway run shut down cleanly?
# --------------------------------------------------------------------------- #

def record_start() -> None:
    """Log a gateway start (pairs with the shutdown records in shutdowns.jsonl)."""
    try:
        import json
        from . import config as cfg
        from .util import now_iso
        with open(cfg.logs_dir() / "shutdowns.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now_iso(), "cause": "START", "pid": os.getpid()}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def crash_report() -> str:
    """'' if the previous run ended cleanly, else a one-line human report.
    A START record immediately preceding the current start (with no shutdown
    between) means the prior process died without logging — i.e. a crash/kill."""
    try:
        import json
        from . import config as cfg
        from .util import read_text
        lines = read_text(cfg.logs_dir() / "shutdowns.jsonl").strip().splitlines()
        if not lines:
            return ""
        last = json.loads(lines[-1])
        if last.get("cause") == "START":
            return (f"previous gateway run (pid {last.get('pid')}, started {last.get('at')}) "
                    "ended without a clean shutdown — it likely crashed or was killed.")
        return ""
    except Exception:  # noqa: BLE001
        return ""
