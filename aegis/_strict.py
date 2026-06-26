"""Developer 'strict' mode — surface fail-soft swallowed errors.

AEGIS is deliberately fail-soft: non-critical peripherals (event hooks, the trace
store, the provider observer, MCP, plugin middleware, memory-provider sync, LSP,
checkpoints) are wrapped in ``except Exception`` so a broken peripheral can never
crash a user's turn. That is the right default for production resilience.

The cost shows up in development: a genuine bug *inside* one of those blocks is
swallowed with no traceback — the feature quietly does nothing and you waste time
finding out why. Strict mode flips the swallow into a raise, but only when you opt
in, so production stays resilient while dev/CI fails loudly.

Enable with the ``AEGIS_STRICT`` environment variable (``1``/``true``/``yes``/``on``)
or ``aegis --strict``. The flag is read live on every call so tests can toggle it.

Two integration points:
  * :func:`aegis._log.log_exc` re-raises the exception being handled when strict —
    every ``except Exception: log_exc(...)`` site becomes strict-aware for free.
  * :func:`soft` is an opt-in context manager for bare ``except Exception: pass``
    sites that want the same strict visibility without changing production behavior.
"""

from __future__ import annotations

import contextlib
import os

_TRUTHY = {"1", "true", "yes", "on"}


def is_strict() -> bool:
    """Whether strict (fail-loud) developer mode is on. Read live from the env."""
    return os.environ.get("AEGIS_STRICT", "").strip().lower() in _TRUTHY


def set_strict(enabled: bool) -> None:
    """Toggle strict mode for the current process (used by the ``--strict`` flag)."""
    if enabled:
        os.environ["AEGIS_STRICT"] = "1"
    else:
        os.environ.pop("AEGIS_STRICT", None)


@contextlib.contextmanager
def soft(label: str = ""):
    """Run a fail-soft block: log any exception and swallow it in production; under
    strict mode, log then re-raise so the swallowed bug is visible.

    Use this for bare ``except Exception: pass`` sites that should stay resilient in
    production but fail loudly in dev/CI::

        with soft("optional trace write"):
            trace_store.finish_span(span_id, status="ok", data=data)
    """
    try:
        yield
    except Exception:  # noqa: BLE001
        try:
            from ._log import logger

            logger().exception(label or "fail-soft block")
        except Exception:  # noqa: BLE001
            pass
        if is_strict():
            raise
