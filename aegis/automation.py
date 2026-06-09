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


def skills_directive(skills) -> str:
    skills = [s for s in (skills or []) if s]
    return f"Load these skills first: {', '.join(skills)}.\n\n" if skills else ""


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


def build_prompt(prompt: str, *, skills=None, script: str = "") -> str:
    """Compose the final prompt: skills directive + script context + the base prompt."""
    return skills_directive(skills) + script_context(script) + prompt


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
