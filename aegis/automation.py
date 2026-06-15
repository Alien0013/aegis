"""Shared automation helpers for cron + webhooks.

Both the cron scheduler and the webhook listener turn an event into an agent run, then
optionally deliver the reply to messaging channels. The common bits live here so the two
paths don't drift:
  * skill chaining   — prepend a "load these skills first" directive
  * script context   — run a local script and prepend its stdout as a # Context block
  * [SILENT] convention — empty / "[SILENT]…" replies are suppressed (anti-spam for monitors)
  * delivery         — parse "platform:chat_id" targets and enqueue to the durable DeliveryQueue
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def skills_directive(skills, *, config=None, cwd=None) -> str:
    """Resolve configured skill names into concrete prompt content.

    Cron/webhook runs are headless, so a weak "please load these" sentence is not
    reliable enough. This mirrors the interactive preload path: inject the actual
    skill bodies, record usage, and report missing skills in-band.
    """
    names = [str(s).strip() for s in (skills or []) if str(s).strip()]
    if not names:
        return ""
    try:
        from .config import Config
        from .skills import SkillsLoader

        cfg_obj = config or Config.load()
        loader = SkillsLoader(cfg_obj, Path(cwd).expanduser() if cwd else Path.cwd())
        block, loaded, missing = loader.preload_block(names, source="automation run")
        header = "# Preloaded skills\n"
        if loaded:
            header += "Loaded: " + ", ".join(loaded) + "\n"
        if missing:
            header += "Missing: " + ", ".join(missing) + "\n"
        return header + "\n" + block.strip() + "\n\n"
    except Exception as exc:  # noqa: BLE001
        return (
            "# Preloaded skills\n"
            f"Configured skills could not be loaded ({type(exc).__name__}: {exc}). "
            f"Requested: {', '.join(names)}.\n\n"
        )


def script_context(script: str, timeout: int = 120) -> str:
    """Run ``script`` and return its stdout as a ``# Context`` block. Fail-soft: a non-zero exit,
    timeout, or missing file yields ``""`` so the agent still runs with the original prompt."""
    if not script:
        return ""
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return f"# Context\n{out}\n\n"
    except Exception:  # noqa: BLE001 - fail-soft by design
        pass
    return ""


def build_prompt(prompt: str, *, skills=None, script: str = "", config=None, cwd=None) -> str:
    """Compose the final prompt: skills directive + script context + the base prompt."""
    return skills_directive(skills, config=config, cwd=cwd) + script_context(script) + prompt


def is_silent(reply: str) -> bool:
    """True when a reply should NOT be delivered: empty, or starts with ``[SILENT]``."""
    r = (reply or "").strip()
    return not r or r.lower().startswith("[silent]")


def delivery_targets(deliver: str) -> list[str]:
    """Split a comma-separated ``platform:chat_id`` list into individual targets."""
    return [t.strip() for t in (deliver or "").split(",") if t.strip()]


def enqueue_delivery(target: str, text: str) -> bool:
    """Parse one ``platform:chat_id`` target and enqueue to the durable outbox. True if enqueued."""
    platform, _, chat_id = (target or "").partition(":")
    if platform and chat_id:
        from .gateway.queue import DeliveryQueue
        DeliveryQueue().enqueue(platform, chat_id, text)
        return True
    return False
