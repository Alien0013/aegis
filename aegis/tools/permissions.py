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
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),           # fork bomb
    re.compile(r"\bmkfs\.\w+\s+/dev/"),                                # format a device
    re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|disk|hd)"),               # raw disk write
    re.compile(r">\s*/dev/(sd|nvme|disk|hd)\w*"),                      # redirect to block device
    re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh|zsh)\b"),  # pipe-to-shell
    re.compile(r"\bchmod\s+-R\s+0?0?0\s+/(\s|$)"),                     # nuke perms on /
]

_DANGER_TARGETS = {"/", "~", "/*", "$HOME", "/.", "~/", "/root", "/home"}


def _dangerous_rm(cmd: str) -> bool:
    """Catch a recursive+force rm aimed at a catastrophic target, in any flag order."""
    for m in re.finditer(r"(?:\bsudo\s+)?\brm\b([^;&|\n]*)", cmd):
        tokens = m.group(1).split()
        short = [t for t in tokens if t.startswith("-") and not t.startswith("--")]
        long = [t for t in tokens if t.startswith("--")]
        recursive = any("r" in t.lower() for t in short) or "--recursive" in long
        force = any("f" in t.lower() for t in short) or "--force" in long
        no_preserve = "--no-preserve-root" in long
        paths = [t for t in tokens if not t.startswith("-")]
        target = any(t in _DANGER_TARGETS or t.rstrip("/") == "" for t in paths)
        if recursive and (force or no_preserve) and (target or no_preserve):
            return True
    return False


def is_hardline_blocked(args: dict) -> str | None:
    """Return the offending text if any arg is a catastrophic command."""
    for key in ("command", "cmd", "code", "combo"):
        val = args.get(key)
        if isinstance(val, str):
            if _dangerous_rm(val):
                return val[:120]
            for pat in HARDLINE_PATTERNS:
                if pat.search(val):
                    return val[:120]
    return None


class ExecMode(str, Enum):
    DENY = "deny"          # block all grouped tools
    ALLOWLIST = "allowlist"  # only allowlisted commands; else deny
    ASK = "ask"            # prompt for grouped tools
    SMART = "smart"        # auxiliary LLM assesses risk; safe→allow, dangerous→deny, else prompt
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
        # A per-agent override (set for a cron run or a /yolo toggle) wins over the config,
        # without mutating the shared config that other surfaces read.
        override = getattr(self, "_mode_override", None)
        try:
            return ExecMode(override or self.config.get("tools.exec_mode", "ask"))
        except ValueError:
            return ExecMode.ASK

    @property
    def deny_groups(self) -> set[str]:
        return set(self.config.get("tools.deny_groups", []) or [])

    @property
    def allowlist(self) -> list[str]:
        return list(self.config.get("tools.allowlist", []) or [])

    def _target(self, tool: Tool, args: dict) -> str:
        # For command-style tools, match the command string; else the tool name.
        return str(args.get("command") or args.get("cmd") or tool.name).strip()

    def _matches_allowlist(self, tool: Tool, args: dict) -> bool:
        target = self._target(tool, args)
        # Runtime allow-always grants (added when the user picks "always" at a prompt);
        # session-scoped, never persisted unless the user also edits config.
        runtime = getattr(self, "_runtime_allow", None)
        if runtime and (target in runtime or any(target.startswith(p) for p in runtime)):
            return True
        if not self.allowlist:
            return False
        return any(target.startswith(prefix) for prefix in self.allowlist)

    def allow_always(self, tool: Tool, args: dict) -> None:
        """Remember this tool/command so ``ask`` mode stops re-prompting for it this session."""
        if not hasattr(self, "_runtime_allow"):
            self._runtime_allow: set[str] = set()
        cmd = str(args.get("command") or args.get("cmd") or "").strip()
        # For shell commands, generalize to the first token (e.g. 'git ' allows all git).
        self._runtime_allow.add((cmd.split()[0] + " ") if cmd else tool.name)

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

    def _scan(self, args: dict) -> str | None:
        """Tirith-style pre-execution scan. Returns a reason if suspicious."""
        if not self.config.get("security.scan_enabled", True):
            return None
        try:
            from ..security_scan import scan_command
        except Exception:  # noqa: BLE001
            return None
        for key in ("command", "cmd", "code"):
            val = args.get(key)
            if isinstance(val, str):
                suspicious, reason = scan_command(val)
                if suspicious:
                    return reason
        return None

    def _smart_classify(self, args: dict) -> str:
        """Ask the auxiliary model to classify a command. SAFE | DANGEROUS | UNCERTAIN."""
        target = args.get("command") or args.get("code") or ""
        if not target:
            return "UNCERTAIN"
        try:
            from ..providers.registry import build_aux_provider
            from ..types import Message
            provider = build_aux_provider(self.config)
            resp = provider.complete([
                Message.system("Classify the shell command's risk. Reply with exactly one word: "
                               "SAFE (read-only/benign), DANGEROUS (destructive/exfiltrating), or "
                               "UNCERTAIN."),
                Message.user(target[:500]),
            ], tools=None, stream=False)
            word = resp.text.strip().upper().split()[0] if resp.text.strip() else "UNCERTAIN"
            return word if word in ("SAFE", "DANGEROUS", "UNCERTAIN") else "UNCERTAIN"
        except Exception:  # noqa: BLE001
            return "UNCERTAIN"

    def authorize(self, tool: Tool, args: dict, ctx: ToolContext) -> tuple[bool, str]:
        """Resolve a decision into allow/deny, prompting the user if needed."""
        hard = is_hardline_blocked(args)
        if hard:
            return False, f"BLOCKED: catastrophic command refused (hardline): {hard}"
        flagged = self._scan(args)
        decision = self.check(tool, args, ctx)
        # A security-flagged command is escalated even if policy would allow it.
        if flagged and decision == Decision.ALLOW:
            decision = Decision.PROMPT
        if decision == Decision.ALLOW:
            return True, "allowed"
        if decision == Decision.DENY:
            return False, f"denied by policy (mode={self.mode.value}, groups={tool.groups})"
        # PROMPT — try smart classification first.
        if self.mode == ExecMode.SMART and not flagged:
            verdict = self._smart_classify(args)
            if verdict == "SAFE":
                return True, "smart-approved (auxiliary model)"
            if verdict == "DANGEROUS":
                return False, "smart-denied (auxiliary model)"
        if ctx.approver is None:
            return False, f"denied (no approver{'; ' + flagged if flagged else ''})"
        prompt = self._format_prompt(tool, args) + (f"  ⚠ {flagged}" if flagged else "")
        verdict = ctx.approver(prompt)
        # The approver may return True/False, or the string "always" to allow this
        # tool/command for the rest of the session without re-prompting.
        if verdict == "always" and not flagged:    # never auto-allow a flagged command
            self.allow_always(tool, args)
            return True, "approved by user (always, this session)"
        approved = bool(verdict)
        return (approved, "approved by user" if approved else "rejected by user")

    @staticmethod
    def _format_prompt(tool: Tool, args: dict) -> str:
        detail = args.get("command") or args.get("path") or args.get("url") or ""
        head = f"Allow {tool.name}"
        return f"{head}({detail})?" if detail else f"{head}?"
