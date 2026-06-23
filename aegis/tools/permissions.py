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

# Shell operators that chain separate commands — allowlist matching splits on these
# so every chained command must independently match (no bypass via `&&`/`|`/`;`).
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")


def _command_segments(command: str) -> list[str]:
    return [seg.strip() for seg in _SHELL_SPLIT_RE.split(command) if seg.strip()]


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
        prefixes = list(self.allowlist) + list(getattr(self, "_runtime_allow", None) or set())
        if not prefixes:
            return False
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            # Non-shell tool: match against the tool name as before.
            return any(tool.name == p or tool.name.startswith(p) for p in prefixes)
        # Command substitution can smuggle an arbitrary command inside an allowlisted
        # one (`git log $(rm -rf x)`), so never prefix-allow when it's present.
        if "$(" in command or "`" in command or "<(" in command:
            return False
        # Allowlist auto-approval requires EVERY shell segment to match — otherwise
        # `git log && rm -rf ~` would slip through on the `git ` prefix (chaining bypass).
        segments = _command_segments(command)
        if not segments:
            return False
        return all(
            any(seg == p.strip() or seg.startswith(p) for p in prefixes)
            for seg in segments
        )

    def explain(self, tool: Tool, args: dict | None = None, *, scan: bool = True) -> dict:
        """Return a structured, side-effect-free permission decision explanation.

        This mirrors ``check``/``authorize`` up to the point where a real user
        prompt or smart-model classification would be needed. It is safe for
        dashboard dry-runs and docs because it never calls the approver and never
        mutates the session allowlist.
        """
        args = args or {}
        groups = list(tool.groups or [])
        deny_groups = sorted(self.deny_groups)
        blocked = is_hardline_blocked(args)
        allowlist_match = self._matches_allowlist(tool, args)
        scan_reason = self._scan(args) if scan else None
        decision = self.check(tool, args, ToolContext(config=self.config))
        if scan_reason and decision == Decision.ALLOW:
            decision = Decision.PROMPT

        reasons: list[str] = []
        if not groups:
            reasons.append("tool has no danger groups")
        if blocked:
            reasons.append("catastrophic command matched hardline blocklist")
        denied_groups = sorted(set(groups) & set(deny_groups))
        if denied_groups:
            reasons.append("tool group denied by configuration: " + ", ".join(denied_groups))
        if allowlist_match:
            reasons.append("command/tool matched allowlist")
        if scan_reason:
            reasons.append("security scan flagged input: " + scan_reason)
        if decision == Decision.ALLOW and not reasons:
            reasons.append(f"mode {self.mode.value} allows grouped tools")
        elif decision == Decision.DENY and not reasons:
            reasons.append(f"mode {self.mode.value} denies this tool")
        elif decision == Decision.PROMPT and not reasons:
            reasons.append(f"mode {self.mode.value} requires approval")

        return {
            "tool": tool.name,
            "target": self._target(tool, args),
            "decision": decision.value,
            "allowed": decision == Decision.ALLOW,
            "requires_prompt": decision == Decision.PROMPT,
            "mode": self.mode.value,
            "groups": groups,
            "deny_groups": deny_groups,
            "denied_groups": denied_groups,
            "allowlist": list(self.allowlist),
            "allowlist_match": allowlist_match,
            "hardline_blocked": bool(blocked),
            "hardline_match": blocked or "",
            "security_scan": {"flagged": bool(scan_reason), "reason": scan_reason or ""},
            "reasons": reasons,
            "prompt": self._format_prompt(tool, args) if decision == Decision.PROMPT else "",
        }

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
