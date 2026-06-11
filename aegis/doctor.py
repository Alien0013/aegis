"""Deep health probes for `aegis doctor --probe`.

Static checks (deps, dirs, perms) live in cli.main.cmd_doctor; this module does
the parts that hit the network: a real one-token provider completion (proves
auth + model end to end, reports latency) and per-channel token validation
(proves the gateway will actually connect before you start it).
"""

from __future__ import annotations

import os
import time

import httpx

from .config import Config

_TIMEOUT = 12.0


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


CHANNEL_PROBES = {"telegram": probe_telegram, "discord": probe_discord, "slack": probe_slack}


def run_probes(config: Config, out=print) -> int:
    """Run all applicable probes. Returns the number of failures."""
    failures = 0
    out("probes (network):")
    ok, detail = probe_provider(config)
    out(f"  {'✓' if ok else '✗'} provider — {detail}")
    failures += 0 if ok else 1
    channels = config.get("gateway.channels", []) or []
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
        out("  – no gateway channels configured (channel probes skipped)")
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
