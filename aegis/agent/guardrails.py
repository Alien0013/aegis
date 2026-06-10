"""Tool-call loop guardrails: stop the model burning its budget on repeats.

Per-turn controller. Two failure shapes are tracked by (tool, args) signature:

* **repeated exact failure** — the same call failing with the same error again
  and again: warn at ``warn_after``, refuse to execute at ``block_after`` with
  a synthetic error telling the model to change strategy.
* **no-progress loop** — the same call *succeeding* with the identical result
  repeatedly (e.g. re-reading one file forever): warn only; success repeats are
  sometimes legitimate, so they are never hard-blocked.

Pure bookkeeping — the executor owns what to do with each decision.
"""

from __future__ import annotations

import hashlib
import json


def _sig(name: str, arguments: dict) -> str:
    try:
        args = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        args = str(arguments)
    return f"{name}:{hashlib.sha1(args.encode()).hexdigest()[:16]}"


def _hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:16]


class ToolLoopGuard:
    """One instance per turn (created in run_conversation)."""

    def __init__(self, warn_after: int = 3, block_after: int = 5):
        self.warn_after = warn_after
        self.block_after = block_after
        self._failures: dict[str, tuple[str, int]] = {}    # sig -> (err_hash, count)
        self._results: dict[str, tuple[str, int]] = {}     # sig -> (result_hash, count)

    def check(self, name: str, arguments: dict) -> str | None:
        """Before executing: returns a synthetic-error string to use INSTEAD of
        running the tool when this exact call should be blocked, else None."""
        sig = _sig(name, arguments)
        rec = self._failures.get(sig)
        if rec and rec[1] >= self.block_after:
            return (f"[loop guard] this exact {name} call has failed identically "
                    f"{rec[1]} times — refusing to run it again. The command/arguments are "
                    "the problem: inspect the error, change the arguments or the approach, "
                    "or report the blocker to the user.")
        return None

    def record(self, name: str, arguments: dict, content: str, is_error: bool) -> str | None:
        """After executing: returns a warning string to append to the result when a
        loop is forming, else None."""
        sig = _sig(name, arguments)
        h = _hash(content)
        if is_error:
            prev = self._failures.get(sig)
            count = prev[1] + 1 if prev and prev[0] == h else 1
            self._failures[sig] = (h, count)
            if count >= self.warn_after:
                return (f"[loop guard] identical {name} call failed the same way {count} "
                        f"time(s). It will be blocked after {self.block_after}. Change "
                        "strategy instead of retrying unchanged.")
            return None
        self._failures.pop(sig, None)              # success resets the failure streak
        prev = self._results.get(sig)
        count = prev[1] + 1 if prev and prev[0] == h else 1
        self._results[sig] = (h, count)
        if count >= self.warn_after:
            return (f"[loop guard] this {name} call returned the identical result {count} "
                    "times — you're not gaining new information. Move to the next step.")
        return None
