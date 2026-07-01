"""Verification-after-edit helpers for coding-agent turns.

This module is intentionally policy-only: it records successful file-edit tool
calls and builds a bounded follow-up prompt when a turn tries to finalize before
checking changed code. The conversation loop owns when to call it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


MUTATING_FILE_TOOLS = frozenset({"apply_patch", "edit_file", "patch", "write_file"})

_MAX_CHANGED_PATHS_IN_NUDGE = 8
_DEFAULT_MAX_NUDGES = 1
_PROSE_EXTENSIONS = frozenset(
    {
        ".adoc",
        ".asciidoc",
        ".csv",
        ".log",
        ".markdown",
        ".md",
        ".mdx",
        ".org",
        ".rst",
        ".text",
        ".tsv",
        ".txt",
    }
)
_PROSE_FILENAMES = frozenset(
    {
        "authors",
        "changelog",
        "codeowners",
        "contributors",
        "copying",
        "license",
        "licence",
        "notice",
    }
)


def _config_get(config: Any, dotted: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        if dotted in config:
            return config[dotted]
        node: Any = config
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(dotted, default)
        except TypeError:
            return getter(dotted)
    return default


def verify_after_edit_enabled(config: Any = None) -> bool:
    """Return whether verification-after-edit nudges are explicitly enabled."""

    value = _config_get(config, "agent.verify_after_edit", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def is_prose_or_docs_path(raw: str) -> bool:
    """Return True when a path is prose/docs with no runtime behavior to verify."""

    try:
        path = Path(str(raw).strip())
    except Exception:
        return False
    suffix = path.suffix.lower()
    if suffix in _PROSE_EXTENSIONS:
        return True
    if not suffix and path.name.lower() in _PROSE_FILENAMES:
        return True
    return False


def verifiable_changed_paths(paths: Iterable[str]) -> list[str]:
    """Deduplicate changed paths and drop docs/prose-only files."""

    kept: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = str(raw or "").strip()
        if not path or path in seen or is_prose_or_docs_path(path):
            continue
        seen.add(path)
        kept.append(path)
    return kept


def changed_paths_from_tool_call(tool_name: str, arguments: dict[str, Any] | None) -> list[str]:
    """Return paths touched by a successful mutating file tool call."""

    if tool_name not in MUTATING_FILE_TOOLS:
        return []
    args = arguments if isinstance(arguments, dict) else {}
    if tool_name in {"edit_file", "write_file"}:
        raw = args.get("path") or args.get("file_path")
        return [str(raw)] if raw else []
    patch = str(args.get("patch") or "")
    if not patch:
        return []
    try:
        from ..tools.extra_builtin import extract_patch_paths

        return extract_patch_paths(patch)
    except Exception:
        return []


def _payload_from_tool_result(result: Any = None, result_data: Any = None) -> dict[str, Any]:
    if isinstance(result_data, dict):
        return result_data
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    stripped = result.strip()
    if not stripped.startswith("{"):
        return {}
    try:
        parsed = json.loads(stripped)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def changed_paths_from_tool_result(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    result: Any = None,
    result_data: Any = None,
) -> list[str]:
    """Return concrete file paths reported by a successful mutation result.

    The reference implementation prefers landed paths from tool output (`files_modified` or
    `resolved_path`) when a mutating file tool can report them. Fall back to the
    requested arguments for simple write/edit tools and for result formats that
    do not expose concrete path metadata.
    """

    targets = changed_paths_from_tool_call(tool_name, arguments)
    if tool_name not in MUTATING_FILE_TOOLS:
        return targets
    payload = _payload_from_tool_result(result=result, result_data=result_data)
    files = payload.get("files_modified")
    if isinstance(files, list):
        landed = [str(path) for path in files if str(path or "").strip()]
        if landed:
            return landed
    for key in ("resolved_path", "path", "file"):
        raw = payload.get(key)
        if raw:
            return [str(raw)]
    return targets


def _verification_commands(cwd: str | Path | None) -> list[str]:
    if cwd is None:
        return []
    try:
        from .coding_context import project_facts_for

        facts = project_facts_for(cwd)
    except Exception:
        facts = None
    if not facts:
        return []
    return [str(cmd).strip() for cmd in facts.get("verify_commands", []) if str(cmd).strip()]


def _format_changed_paths(paths: list[str]) -> str:
    shown = paths[:_MAX_CHANGED_PATHS_IN_NUDGE]
    lines = [f"- `{path}`" for path in shown]
    remaining = len(paths) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


def build_verify_after_edit_nudge(
    *,
    config: Any = None,
    changed_paths: Iterable[str],
    attempts: int = 0,
    max_attempts: int = _DEFAULT_MAX_NUDGES,
    cwd: str | Path | None = None,
    verify_commands: Iterable[str] | None = None,
) -> str | None:
    """Build a one-shot prompt asking the model to verify edited code."""

    if not verify_after_edit_enabled(config) or attempts >= max_attempts:
        return None
    paths = verifiable_changed_paths(changed_paths)
    if not paths:
        return None

    commands = [
        str(command).strip()
        for command in (verify_commands if verify_commands is not None else _verification_commands(cwd))
        if str(command).strip()
    ]
    if commands:
        command_text = (
            "Run the relevant verification now ("
            + ", ".join(f"`{command}`" for command in commands[:3])
            + (", ..." if len(commands) > 3 else "")
            + "), read any failure, repair the code if needed, and report what passed."
        )
    else:
        command_text = (
            "Run the relevant focused test, build, lint, typecheck, or smoke check for "
            "the changed behavior. If no project command exists, use a small focused "
            "ad-hoc check and describe it as such."
        )

    return (
        "[System: You edited verifiable files in this turn and "
        "`agent.verify_after_edit` is enabled.\n\n"
        f"Changed paths:\n{_format_changed_paths(paths)}\n\n"
        f"{command_text} If verification is not possible, explain the concrete blocker "
        "instead of claiming the work is fully verified.]"
    )


@dataclass
class VerificationAfterEditHarness:
    """Per-turn state for the loop integration hook."""

    _changed_paths: list[str] = field(default_factory=list)
    _seen_paths: set[str] = field(default_factory=set)
    nudges_sent: int = 0

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(self._changed_paths)

    @property
    def verifiable_paths(self) -> tuple[str, ...]:
        return tuple(verifiable_changed_paths(self._changed_paths))

    def record_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        is_error: bool = False,
        result: Any = None,
        result_data: Any = None,
    ) -> None:
        """Record changed paths after a successful mutating file tool result."""

        if is_error:
            return
        for path in changed_paths_from_tool_result(
            tool_name,
            arguments,
            result=result,
            result_data=result_data,
        ):
            if path and path not in self._seen_paths:
                self._seen_paths.add(path)
                self._changed_paths.append(path)

    def build_nudge(
        self,
        *,
        config: Any = None,
        cwd: str | Path | None = None,
        verify_commands: Iterable[str] | None = None,
        max_attempts: int = _DEFAULT_MAX_NUDGES,
    ) -> str | None:
        nudge = build_verify_after_edit_nudge(
            config=config,
            changed_paths=self._changed_paths,
            attempts=self.nudges_sent,
            max_attempts=max_attempts,
            cwd=cwd,
            verify_commands=verify_commands,
        )
        if nudge is not None:
            self.nudges_sent += 1
        return nudge


__all__ = [
    "MUTATING_FILE_TOOLS",
    "VerificationAfterEditHarness",
    "build_verify_after_edit_nudge",
    "changed_paths_from_tool_call",
    "changed_paths_from_tool_result",
    "is_prose_or_docs_path",
    "verifiable_changed_paths",
    "verify_after_edit_enabled",
]
