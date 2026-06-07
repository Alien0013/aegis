"""Capability-gated permission cascade.

Layers (earlier layers are never overridden by later ones):
  1. deny_groups   — a hard stop for whole capability classes (fs/runtime/network/…)
  2. exec mode     — deny | allowlist | ask | auto | full
  3. allowlist     — command/arg prefixes auto-approved even in ask/allowlist mode
  4. user approval — interactive prompt (falls back to DENY if no approver wired)

Tools with no danger ``groups`` (read-only: read_file, list_dir, search, …) are
always allowed.
"""

from __future__ import annotations

from enum import Enum

from .base import Tool, ToolContext


class ExecMode(str, Enum):
    DENY = "deny"          # block all grouped tools
    ALLOWLIST = "allowlist"  # only allowlisted commands; else deny
    ASK = "ask"            # prompt for grouped tools
    AUTO = "auto"          # auto-approve grouped tools (still honors deny_groups)
    FULL = "full"          # approve everything (yolo)


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


class PermissionEngine:
    def __init__(self, config):
        self.config = config

    @property
    def mode(self) -> ExecMode:
        try:
            return ExecMode(self.config.get("tools.exec_mode", "ask"))
        except ValueError:
            return ExecMode.ASK

    @property
    def deny_groups(self) -> set[str]:
        return set(self.config.get("tools.deny_groups", []) or [])

    @property
    def allowlist(self) -> list[str]:
        return list(self.config.get("tools.allowlist", []) or [])

    def _matches_allowlist(self, tool: Tool, args: dict) -> bool:
        if not self.allowlist:
            return False
        # For command-style tools, match the command string; else match tool name.
        target = args.get("command") or args.get("cmd") or tool.name
        target = str(target).strip()
        return any(target.startswith(prefix) for prefix in self.allowlist)

    def check(self, tool: Tool, args: dict, ctx: ToolContext) -> Decision:
        if not tool.groups:
            return Decision.ALLOW
        if self.deny_groups & set(tool.groups):
            return Decision.DENY
        mode = self.mode
        if mode in (ExecMode.FULL, ExecMode.AUTO):
            return Decision.ALLOW
        if mode == ExecMode.DENY:
            return Decision.DENY
        if self._matches_allowlist(tool, args):
            return Decision.ALLOW
        if mode == ExecMode.ALLOWLIST:
            return Decision.DENY
        return Decision.PROMPT  # ASK

    def authorize(self, tool: Tool, args: dict, ctx: ToolContext) -> tuple[bool, str]:
        """Resolve a decision into allow/deny, prompting the user if needed."""
        decision = self.check(tool, args, ctx)
        if decision == Decision.ALLOW:
            return True, "allowed"
        if decision == Decision.DENY:
            return False, f"denied by policy (mode={self.mode.value}, groups={tool.groups})"
        # PROMPT
        if ctx.approver is None:
            return False, "denied (no approver available in this context)"
        prompt = self._format_prompt(tool, args)
        approved = ctx.approver(prompt)
        return (approved, "approved by user" if approved else "rejected by user")

    @staticmethod
    def _format_prompt(tool: Tool, args: dict) -> str:
        detail = args.get("command") or args.get("path") or args.get("url") or ""
        head = f"Allow {tool.name}"
        return f"{head}({detail})?" if detail else f"{head}?"
