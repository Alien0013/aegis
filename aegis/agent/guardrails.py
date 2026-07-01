"""Tool-call loop guardrails: stop the model burning its budget on repeats.

This module keeps the existing AEGIS ``ToolLoopGuard`` string API used by the
conversation loop, and also exposes Hermes-compatible structured primitives for
config parsing, signatures, decisions, synthetic blocked results, and standalone
failure classification.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


HERMES_IDEMPOTENT_TOOL_NAMES = frozenset({
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "session_search",
    "browser_snapshot",
    "browser_console",
    "browser_get_images",
    "mcp_filesystem_read_file",
    "mcp_filesystem_read_text_file",
    "mcp_filesystem_read_multiple_files",
    "mcp_filesystem_list_directory",
    "mcp_filesystem_list_directory_with_sizes",
    "mcp_filesystem_directory_tree",
    "mcp_filesystem_get_file_info",
    "mcp_filesystem_search_files",
})
HERMES_MUTATING_TOOL_NAMES = frozenset({
    "terminal",
    "execute_code",
    "write_file",
    "patch",
    "todo",
    "memory",
    "skill_manage",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_navigate",
    "send_message",
    "cronjob",
    "delegate_task",
    "process",
})

AEGIS_IDEMPOTENT_TOOL_NAMES = frozenset({
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
    "tool_describe",
    "vision_analyze",
    "web_extract",
    "web_fetch",
    "web_search",
})
AEGIS_MUTATING_TOOL_NAMES = frozenset({
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

IDEMPOTENT_TOOL_NAMES = HERMES_IDEMPOTENT_TOOL_NAMES
MUTATING_TOOL_NAMES = HERMES_MUTATING_TOOL_NAMES
AEGIS_RUNTIME_IDEMPOTENT_TOOL_NAMES = HERMES_IDEMPOTENT_TOOL_NAMES | AEGIS_IDEMPOTENT_TOOL_NAMES
AEGIS_RUNTIME_MUTATING_TOOL_NAMES = HERMES_MUTATING_TOOL_NAMES | AEGIS_MUTATING_TOOL_NAMES

# Backward-compatible constant names used by older AEGIS tests and callers.
IDEMPOTENT_TOOLS = IDEMPOTENT_TOOL_NAMES
MUTATING_TOOLS = MUTATING_TOOL_NAMES
FILE_MUTATION_RESULT_TOOLS = frozenset({"apply_patch", "patch", "write_file"})
TERMINAL_LIKE_TOOL_NAMES = frozenset({"terminal", "bash", "process", "execute_code"})

_ERROR_SUFFIX_MAX_LEN = 48


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection."""

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    idempotent_tools: frozenset[str] = field(default_factory=lambda: HERMES_IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: HERMES_MUTATING_TOOL_NAMES)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ToolCallGuardrailConfig":
        """Build config from the Hermes ``tool_loop_guardrails`` section."""
        if not isinstance(data, Mapping):
            return cls()

        warn_after = data.get("warn_after")
        if not isinstance(warn_after, Mapping):
            warn_after = {}
        hard_stop_after = data.get("hard_stop_after")
        if not isinstance(hard_stop_after, Mapping):
            hard_stop_after = {}

        defaults = cls()
        return cls(
            warnings_enabled=_as_bool(data.get("warnings_enabled"), defaults.warnings_enabled),
            hard_stop_enabled=_as_bool(data.get("hard_stop_enabled"), defaults.hard_stop_enabled),
            exact_failure_warn_after=_positive_int(
                warn_after.get("exact_failure", data.get("exact_failure_warn_after")),
                defaults.exact_failure_warn_after,
            ),
            same_tool_failure_warn_after=_positive_int(
                warn_after.get("same_tool_failure", data.get("same_tool_failure_warn_after")),
                defaults.same_tool_failure_warn_after,
            ),
            no_progress_warn_after=_positive_int(
                warn_after.get("idempotent_no_progress", data.get("no_progress_warn_after")),
                defaults.no_progress_warn_after,
            ),
            exact_failure_block_after=_positive_int(
                hard_stop_after.get("exact_failure", data.get("exact_failure_block_after")),
                defaults.exact_failure_block_after,
            ),
            same_tool_failure_halt_after=_positive_int(
                hard_stop_after.get("same_tool_failure", data.get("same_tool_failure_halt_after")),
                defaults.same_tool_failure_halt_after,
            ),
            no_progress_block_after=_positive_int(
                hard_stop_after.get("idempotent_no_progress", data.get("no_progress_block_after")),
                defaults.no_progress_block_after,
            ),
        )


GuardrailConfig = ToolCallGuardrailConfig


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable, non-reversible identity for a tool name plus canonical args."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        canonical = canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """Decision returned by the structured tool-call guardrail controller."""

    action: str = "allow"
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


def _safe_json_loads(text: str | None) -> Any:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text.strip())
    except (TypeError, ValueError):
        return None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_tool_args(arguments: Mapping[str, Any] | None) -> str:
    """Return sorted compact JSON for parsed tool arguments."""
    if not isinstance(arguments, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(arguments).__name__}")
    return _canonical_json(arguments)


def file_mutation_result_landed(name: str, content: Any) -> bool:
    """Return True when a file mutation result proves the write/patch landed."""
    if name not in FILE_MUTATION_RESULT_TOOLS or not isinstance(content, str):
        return False
    data = _safe_json_loads(content)
    if not isinstance(data, dict) or data.get("error") or data.get("success") is False:
        return False
    if name == "write_file":
        return "bytes_written" in data
    if name == "patch":
        return data.get("success") is True
    if name == "apply_patch":
        files_modified = data.get("files_modified")
        return data.get("success") is True or (
            isinstance(files_modified, list)
            and any(str(path or "").strip() for path in files_modified)
        )
    return False


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Inspect a tool result string for failure signs using Hermes display rules."""
    if result is None:
        return False, ""
    if file_mutation_result_landed(tool_name, result):
        return False, ""

    data = _safe_json_loads(result)

    if tool_name in TERMINAL_LIKE_TOOL_NAMES:
        if isinstance(data, dict):
            exit_code = data.get("exit_code", data.get("returncode"))
            if exit_code is not None and exit_code != 0:
                err_msg = data.get("error")
                if err_msg:
                    return True, f" [{_trim_error(str(err_msg))}]"
                return True, f" [exit {exit_code}]"
        return False, ""

    if tool_name == "memory" and isinstance(data, dict):
        if data.get("success") is False and "exceed the limit" in str(data.get("error", "")):
            return True, " [full]"

    if isinstance(data, dict):
        err = data.get("error") or data.get("message")
        if err and (data.get("success") is False or "error" in data):
            return True, f" [{_trim_error(str(err))}]"

    if not isinstance(result, str):
        return False, ""
    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"

    return False, ""


class ToolCallGuardrailController:
    """Hermes-compatible structured controller for repeated tool-call loops."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None):
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {exact_count} "
                    "times with identical arguments. Stop retrying it unchanged; "
                    "change strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None:
                _result_hash_value, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        message=(
                            f"Blocked {tool_name}: this read-only call returned the same "
                            f"result {repeat_count} times. Stop repeating it unchanged; "
                            "use the result already provided or try a different query."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)

        if failed:
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._no_progress.pop(signature, None)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
                decision = ToolGuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"Stopped {tool_name}: it failed {same_count} times this turn. "
                        "Stop retrying the same failing tool path and choose a different approach."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )
                self._halt_decision = decision
                return decision

            if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    message=(
                        f"{tool_name} has failed {exact_count} times with identical arguments. "
                        "This looks like a loop; inspect the error and change strategy "
                        "instead of retrying it unchanged."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                )

            if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=_tool_failure_recovery_hint(tool_name, same_count),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. "
                    "Use the result already provided or change the query instead of "
                    "repeating it unchanged."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    """Build a synthetic role=tool content string for a blocked tool call."""
    return json.dumps(
        {
            "error": decision.message,
            "guardrail": decision.to_metadata(),
        },
        ensure_ascii=False,
    )


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """Append runtime guidance to the current tool result content."""
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    suffix = (
        f"\n\n[{label}: "
        f"{decision.code}; count={decision.count}; {decision.message}]"
    )
    return (result or "") + suffix


def _sig(name: str, arguments: Mapping[str, Any] | None) -> str:
    try:
        args = canonical_tool_args(arguments or {})
    except Exception:  # noqa: BLE001
        args = str(arguments)
    return f"{name}:{hashlib.sha1(args.encode()).hexdigest()[:16]}"


def _hash(text: str | None) -> str:
    return _result_hash(text)[:16]


def _trim_error(msg: str) -> str:
    msg = msg.strip()
    if "File not found:" in msg:
        _, _, tail = msg.partition("File not found:")
        tail = tail.strip()
        if "/" in tail:
            msg = f"File not found: {tail.rsplit('/', 1)[-1]}"
    if len(msg) > _ERROR_SUFFIX_MAX_LEN:
        msg = msg[: _ERROR_SUFFIX_MAX_LEN - 3] + "..."
    return msg


def _same_tool_failure_hint(name: str, count: int) -> str:
    common = (
        f"[loop guard] {name} has failed {count} times this turn. Diagnose before "
        "retrying: inspect the latest error/output, verify assumptions, and change "
        "arguments or tool strategy."
    )
    if name in {"terminal", "bash"}:
        return (
            common
            + " For shell failures, try a small diagnostic such as `pwd && ls -la`, "
            "then use an absolute path, simpler command, or a file tool if appropriate."
        )
    return common


def _tool_failure_recovery_hint(tool_name: str, count: int) -> str:
    common = (
        f"{tool_name} has failed {count} times this turn. This looks like a loop. "
        "Do not switch to text-only replies; keep using tools, but diagnose before retrying. "
        "First inspect the latest error/output and verify your assumptions. "
    )
    if tool_name in {"terminal", "bash"}:
        return common + (
            "For terminal failures, run a small diagnostic such as `pwd && ls -la` "
            "in the same tool, then try an absolute path, a simpler command, a different "
            "working directory, or a different tool such as read_file/write_file/patch."
        )
    return common + (
        "Try different arguments, a narrower query/path, an absolute path when relevant, "
        "or a different tool that can make progress. If the blocker is external, report "
        "the blocker after one diagnostic attempt instead of repeating the same failing path."
    )


class ToolLoopGuard:
    """Backward-compatible string-returning guard used by the AEGIS executor."""

    def __init__(
        self,
        warn_after: int = 3,
        block_after: int = 5,
        same_tool_warn_after: int | None = None,
        *,
        hard_stop: bool = False,
        no_progress_block_after: int | None = None,
        same_tool_halt_after: int | None = None,
    ):
        self.warn_after = warn_after
        self.block_after = block_after
        self.same_tool_warn_after = same_tool_warn_after or max(warn_after + 1, 3)
        self.hard_stop = bool(hard_stop)
        self.no_progress_block_after = no_progress_block_after or block_after
        self.same_tool_halt_after = same_tool_halt_after or max(self.same_tool_warn_after + 3, block_after)
        self._failures: dict[str, tuple[str, int]] = {}
        self._tool_failures: dict[str, int] = {}
        self._results: dict[str, tuple[str, int]] = {}
        self._halted_tools: dict[str, str] = {}
        self.halt_reason: str | None = None

    @classmethod
    def from_config(cls, data: Mapping[str, Any] | None) -> "ToolLoopGuard":
        cfg = ToolCallGuardrailConfig.from_mapping(data)
        return cls(
            warn_after=cfg.exact_failure_warn_after,
            block_after=cfg.exact_failure_block_after,
            same_tool_warn_after=cfg.same_tool_failure_warn_after,
            hard_stop=cfg.hard_stop_enabled,
            no_progress_block_after=cfg.no_progress_block_after,
            same_tool_halt_after=cfg.same_tool_failure_halt_after,
        )

    def check(self, name: str, arguments: Mapping[str, Any] | None) -> str | None:
        """Return a synthetic error string instead of running a blocked call."""
        sig = _sig(name, arguments)
        rec = self._failures.get(sig)
        if rec and rec[1] >= self.block_after:
            message = (
                f"[loop guard] this exact {name} call has failed identically "
                f"{rec[1]} times — refusing to run it again. The command/arguments are "
                "the problem: inspect the error, change the arguments or the approach, "
                "or report the blocker to the user."
            )
            if self.hard_stop:
                self.halt_reason = message
                self._halted_tools[name] = message
            return message
        if self.hard_stop and name in self._halted_tools:
            return (
                self._halted_tools[name]
                + " Tool execution is halted for this tool in this loop; change tools "
                "or report the blocker."
            )
        if self.hard_stop and name in AEGIS_RUNTIME_IDEMPOTENT_TOOL_NAMES:
            result_rec = self._results.get(sig)
            if result_rec and result_rec[1] >= self.no_progress_block_after:
                message = (
                    f"[loop guard] this {name} call returned the same result "
                    f"{result_rec[1]} times — refusing to run it again. Use the "
                    "result already provided, change the query/path, or explain the blocker."
                )
                self.halt_reason = message
                self._halted_tools[name] = message
                return message
        return None

    def record(self, name: str, arguments: Mapping[str, Any] | None, content: str, is_error: bool) -> str | None:
        """Return warning guidance to append when a loop is forming."""
        sig = _sig(name, arguments)
        h = _hash(content)
        if is_error and file_mutation_result_landed(name, content):
            is_error = False
        if is_error:
            prev = self._failures.get(sig)
            count = prev[1] + 1 if prev and prev[0] == h else 1
            self._failures[sig] = (h, count)
            tool_count = self._tool_failures.get(name, 0) + 1
            self._tool_failures[name] = tool_count
            if count >= self.warn_after:
                return (
                    f"[loop guard] identical {name} call failed the same way {count} "
                    f"time(s). It will be blocked after {self.block_after}. Change "
                    "strategy instead of retrying unchanged."
                )
            if self.hard_stop and tool_count >= self.same_tool_halt_after:
                self.halt_reason = _same_tool_failure_hint(name, tool_count)
                self._halted_tools[name] = self.halt_reason
                return self.halt_reason + " Tool execution is now in hard-stop mode for this loop."
            if tool_count >= self.same_tool_warn_after:
                return _same_tool_failure_hint(name, tool_count)
            return None
        self._failures.pop(sig, None)
        self._tool_failures.pop(name, None)
        self._halted_tools.pop(name, None)
        if (
            name in AEGIS_RUNTIME_MUTATING_TOOL_NAMES
            or (name not in AEGIS_RUNTIME_IDEMPOTENT_TOOL_NAMES and not name.startswith("mcp__"))
        ):
            self._results.pop(sig, None)
            return None
        prev = self._results.get(sig)
        count = prev[1] + 1 if prev and prev[0] == h else 1
        self._results[sig] = (h, count)
        if count >= self.warn_after:
            return (
                f"[loop guard] this {name} call returned the identical result {count} "
                "times — you're not gaining new information. Move to the next step."
            )
        return None


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _result_hash(result: str | None) -> str:
    parsed = _safe_json_loads(result or "")
    if parsed is not None:
        try:
            canonical = _canonical_json(parsed)
        except TypeError:
            canonical = str(parsed)
    else:
        canonical = result or ""
    return _sha256(canonical)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
