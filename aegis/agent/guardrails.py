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

IDEMPOTENT_TOOLS = frozenset({
    "agent_state",
    "dependency_audit",
    "glob",
    "list_dir",
    "read_file",
    "search",
    "session_search",
    "skill",
    "system_status",
    "tool_search",
    "vision_analyze",
    "web_extract",
    "web_fetch",
    "web_search",
})
MUTATING_TOOLS = frozenset({
    "apply_patch",
    "bash",
    "browser",
    "computer",
    "cronjob",
    "edit_file",
    "execute_code",
    "github",
    "memory",
    "process",
    "schedule_task",
    "send_message",
    "skill_manage",
    "todo_write",
    "write_file",
})


def _sig(name: str, arguments: dict) -> str:
    try:
        args = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        args = str(arguments)
    return f"{name}:{hashlib.sha1(args.encode()).hexdigest()[:16]}"


def _hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _same_tool_failure_hint(name: str, count: int) -> str:
    common = (f"[loop guard] {name} has failed {count} times this turn. Diagnose before "
              "retrying: inspect the latest error/output, verify assumptions, and change "
              "arguments or tool strategy.")
    if name == "bash":
        return (common + " For shell failures, try a small diagnostic such as `pwd && ls -la`, "
                "then use an absolute path, simpler command, or a file tool if appropriate.")
    return common


class ToolLoopGuard:
    """One instance per turn (created in run_conversation)."""

    def __init__(self, warn_after: int = 3, block_after: int = 5,
                 same_tool_warn_after: int | None = None):
        self.warn_after = warn_after
        self.block_after = block_after
        self.same_tool_warn_after = same_tool_warn_after or max(warn_after + 1, 3)
        self._failures: dict[str, tuple[str, int]] = {}    # sig -> (err_hash, count)
        self._tool_failures: dict[str, int] = {}            # tool name -> failed count
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
            tool_count = self._tool_failures.get(name, 0) + 1
            self._tool_failures[name] = tool_count
            if count >= self.warn_after:
                return (f"[loop guard] identical {name} call failed the same way {count} "
                        f"time(s). It will be blocked after {self.block_after}. Change "
                        "strategy instead of retrying unchanged.")
            if tool_count >= self.same_tool_warn_after:
                return _same_tool_failure_hint(name, tool_count)
            return None
        self._failures.pop(sig, None)              # success resets the failure streak
        self._tool_failures.pop(name, None)
        if name in MUTATING_TOOLS or (name not in IDEMPOTENT_TOOLS and not name.startswith("mcp__")):
            self._results.pop(sig, None)
            return None
        prev = self._results.get(sig)
        count = prev[1] + 1 if prev and prev[0] == h else 1
        self._results[sig] = (h, count)
        if count >= self.warn_after:
            return (f"[loop guard] this {name} call returned the identical result {count} "
                    "times — you're not gaining new information. Move to the next step.")
        return None
