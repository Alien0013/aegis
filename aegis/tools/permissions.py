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

import re
from enum import Enum

from .base import Tool, ToolContext

# Catastrophic commands that are NEVER allowed — even in full/yolo mode.
HARDLINE_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|/\*)(\s|$)"),  # rm -rf / | ~ | /*
    re.compile(r"\brm\s+-[a-z]*f[a-z]*r\s+(/|~)(\s|$)"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),           # fork bomb
    re.compile(r"\bmkfs\.\w+\s+/dev/"),                                # format a device
    re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|disk|hd)"),               # raw disk write
    re.compile(r">\s*/dev/(sd|nvme|disk|hd)\w*"),                      # redirect to block device
    re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh|zsh)\b"),  # pipe-to-shell
    re.compile(r"\bchmod\s+-R\s+0?00\s+/(\s|$)"),                      # nuke perms on /
    re.compile(r"\bsudo\s+rm\s+-[a-z]*r"),                            # sudo recursive rm
]


def is_hardline_blocked(args: dict) -> str | None:
    """Return the offending text if any arg matches a catastrophic pattern."""
    for key in ("command", "cmd", "code", "combo"):
        val = args.get(key)
        if isinstance(val, str):
            for pat in HARDLINE_PATTERNS:
                if pat.search(val):
                    return val[:120]
    return None


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
        # Hardline blocklist: catastrophic commands are never allowed, any mode.
        if is_hardline_blocked(args):
            return Decision.DENY
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
        hard = is_hardline_blocked(args)
        if hard:
            return False, f"BLOCKED: catastrophic command refused (hardline): {hard}"
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
