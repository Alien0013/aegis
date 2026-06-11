"""Lifecycle hooks: run user shell scripts on agent events.

Events fire at well-known points in the agent lifecycle. Each event maps to a
list of shell commands in config under ``hooks.{event}``::

    hooks:
      session_start:
        - 'echo "session $AEGIS_HOOK_SESSION_ID started" >> ~/aegis.log'
      pre_tool:
        - 'logger "aegis tool $AEGIS_HOOK_TOOL"'

Recognised events: ``session_start``, ``user_prompt``, ``pre_tool``,
``post_tool``, ``pre_api_request``, ``post_api_request``,
``api_request_error``, ``session_stop``.

Each command runs through the shell with the event and context exposed as
environment variables: ``AEGIS_HOOK_EVENT`` plus ``AEGIS_HOOK_<KEY>`` for every
key in the context dict (upper-cased, values stringified). Hooks are
best-effort: a 10 s timeout applies and any failure is swallowed so a broken
hook never blocks the agent. Results are returned for inspection/testing.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any

EVENTS: tuple[str, ...] = (
    "session_start",
    "user_prompt",
    "pre_tool",
    "post_tool",
    "pre_api_request",
    "post_api_request",
    "api_request_error",
    "session_stop",
)

_TIMEOUT = 10  # seconds, per command


@dataclass
class HookResult:
    """Outcome of a single hook command invocation."""

    event: str
    command: str
    ok: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


def _hook_env(event: str, context: dict[str, Any]) -> dict[str, str]:
    """Build the child environment: inherit os.environ + AEGIS_HOOK_* overlays."""
    env = dict(os.environ)
    env["AEGIS_HOOK_EVENT"] = event
    for key, value in (context or {}).items():
        name = "AEGIS_HOOK_" + str(key).upper()
        env[name] = "" if value is None else str(value)
    return env


def run_hooks(config, event: str, context: dict[str, Any] | None = None) -> list[HookResult]:
    """Run every command configured for ``event``. Best-effort, never raises.

    ``context`` keys are exposed to each command as ``AEGIS_HOOK_<KEY>`` env
    vars alongside ``AEGIS_HOOK_EVENT``. Returns one :class:`HookResult` per
    configured command (empty list when nothing is configured or the event is
    unknown).
    """
    if event not in EVENTS:
        return []
    commands = config.get(f"hooks.{event}", []) or []
    if isinstance(commands, str):
        commands = [commands]
    env = _hook_env(event, context or {})
    results: list[HookResult] = []
    for command in commands:
        cmd = str(command)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
            )
            results.append(
                HookResult(
                    event=event,
                    command=cmd,
                    ok=proc.returncode == 0,
                    returncode=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            )
        except subprocess.TimeoutExpired:
            results.append(
                HookResult(event=event, command=cmd, ok=False, error=f"timeout after {_TIMEOUT}s")
            )
        except Exception as e:  # noqa: BLE001 — hooks must never crash the agent
            results.append(HookResult(event=event, command=cmd, ok=False, error=str(e)))
    return results


def list_hooks(config) -> dict[str, list[str]]:
    """Return the configured commands for every known event (events with none omitted)."""
    out: dict[str, list[str]] = {}
    for event in EVENTS:
        commands = config.get(f"hooks.{event}", []) or []
        if isinstance(commands, str):
            commands = [commands]
        if commands:
            out[event] = [str(c) for c in commands]
    return out


# --------------------------------------------------------------------------- #
# CLI: `aegis hooks [list|test]`
# --------------------------------------------------------------------------- #
def cmd_hooks(args, config) -> int:
    """``list`` shows configured hooks; ``test`` fires one event with sample context."""
    action = getattr(args, "action", None) or "list"

    if action == "test":
        event = getattr(args, "event", None)
        if not event:
            print('usage: aegis hooks test <event>  (one of: ' + ", ".join(EVENTS) + ")")
            return 2
        if event not in EVENTS:
            print(f"unknown event '{event}'. known: {', '.join(EVENTS)}")
            return 2
        results = run_hooks(config, event, {"session_id": "test", "test": "1"})
        if not results:
            print(f"no hooks configured for '{event}'")
            return 0
        for r in results:
            mark = "ok" if r.ok else "FAIL"
            detail = r.error or (r.stderr.strip() if not r.ok else "")
            print(f"  [{mark}] {r.command}" + (f"  -> {detail}" if detail else ""))
        return 0 if all(r.ok for r in results) else 1

    # default: list
    hooks = list_hooks(config)
    if not hooks:
        print("no hooks configured. Add commands under hooks.<event> in config.yaml.")
        print("events: " + ", ".join(EVENTS))
        return 0
    for event in EVENTS:
        for command in hooks.get(event, []):
            print(f"  {event:<14} {command}")
    return 0
