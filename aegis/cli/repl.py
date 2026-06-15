"""Interactive REPL + one-shot runner with streaming output.

Uses rich for rendering and prompt_toolkit for input when available, and falls
back to plain stdin/stdout otherwise so the harness runs anywhere.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import __version__
from ..config import Config
from ..session import Session, SessionStore

if TYPE_CHECKING:
    from ..agent.agent import Agent
from ..surface import (
    SurfaceRunner,
    apply_session_runtime,
    remember_session_runtime,
    run_control_action,
    session_runtime_controls,
)

# --- optional pretty deps ---------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    _console = Console()
except Exception:  # noqa: BLE001
    _console = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.completion import WordCompleter
except Exception:  # noqa: BLE001
    PromptSession = None
    WordCompleter = None

_approve_lock = threading.Lock()

@dataclass(frozen=True)
class SlashCommand:
    name: str
    group: str
    summary: str
    usage: str = ""


SLASH_COMMANDS = (
    SlashCommand("/help", "discover", "show or search slash commands", "/help [term]"),
    SlashCommand("/status", "discover", "show runtime, session, recap, and trace status"),
    SlashCommand("/model", "discover", "show the active provider and model"),
    SlashCommand("/provider", "discover", "show or switch the active provider", "/provider [name]"),
    SlashCommand("/tools", "discover", "list enabled tools"),
    SlashCommand("/skills", "discover", "list loaded skills"),
    SlashCommand("/trace", "observability", "list traces for this session or inspect one", "/trace [id]"),
    SlashCommand("/evals", "observability", "list eval runs or inspect one", "/evals [id]"),
    SlashCommand("/usage", "observability", "show token and rate-limit usage"),
    SlashCommand("/sessions", "sessions", "pick recent sessions or search history", "/sessions [query]"),
    SlashCommand("/resume", "sessions", "resume by picker number, id, title, or unique search", "/resume [number|id|title]"),
    SlashCommand("/branch", "sessions", "fork this conversation into a named child session", "/branch [title]"),
    SlashCommand("/new", "sessions", "start a fresh session"),
    SlashCommand("/clear", "sessions", "start a fresh session"),
    SlashCommand("/ultracode", "planning", "run the rigorous autonomous plan→implement→verify loop", "/ultracode <task>"),
    SlashCommand("/architect", "planning", "strong model plans → this model implements (Aider-style)", "/architect <task>"),
    SlashCommand("/spec", "planning", "spec-driven dev: persistent requirements→design→tasks", "/spec new|list|show|implement [arg]"),
    SlashCommand("/gstack", "planning", "run a goal through a sprint of roles (think→plan→build→review→test→ship→reflect)", "/gstack <goal>"),
    SlashCommand("/plan", "planning", "draft a plan without making changes", "/plan <task>"),
    SlashCommand("/proceed", "planning", "execute the plan from the last /plan"),
    SlashCommand("/context", "context", "show the token budget breakdown (system, history, tools)"),
    SlashCommand("/compress", "context", "compact context now", "/compress [here N|focus topic]"),
    SlashCommand("/retry", "context", "rerun the last user turn"),
    SlashCommand("/undo", "context", "remove the last user turn and its response"),
    SlashCommand("/save", "context", "export this session to markdown", "/save [path]"),
    SlashCommand("/title", "sessions", "rename this session", "/title <name>"),
    SlashCommand("/think", "model control", "set reasoning effort", "/think off|minimal|low|medium|high|xhigh"),
    SlashCommand("/reasoning", "model control", "set reasoning visibility or effort", "/reasoning off|none|summary|live|..."),
    SlashCommand("/busy", "model control", "set busy input behavior", "/busy queue|steer|interrupt"),
    SlashCommand("/goal", "goals", "set a standing goal and start it", "/goal <objective>"),
    SlashCommand("/subgoal", "goals", "set a nested standing goal", "/subgoal <objective>"),
    SlashCommand("/background", "agents", "launch a background agent task", "/background <prompt>"),
    SlashCommand("/tasks", "agents", "list background tasks"),
    SlashCommand("/agents", "agents", "list background agents"),
    SlashCommand("/kanban", "agents", "multi-agent task board", "/kanban [list|create <title>|show <id>|dispatch|stats]"),
    SlashCommand("/learn", "learning", "review this session for reusable memories or skills"),
    SlashCommand("/skill", "learning", "create or extract a skill", "/skill [new <name> [description]]"),
    SlashCommand("/memory", "learning", "show memory and user profile files"),
    SlashCommand("/personality", "learning", "set the active persona", "/personality <name>"),
    SlashCommand("/secret", "setup", "store a local secret with hidden input", "/secret set <ENV_KEY>"),
    SlashCommand("/handoff", "channels", "hand this session to a gateway channel", "/handoff <platform> <chat_id>"),
    SlashCommand("/diff", "workspace", "show changes since the last checkpoint", "/diff [checkpoint-id]"),
    SlashCommand("/rollback", "workspace", "restore files from a checkpoint", "/rollback [checkpoint-id]"),
    SlashCommand("/yolo", "workspace", "toggle this session's existing approval bypass"),
    SlashCommand("/quit", "exit", "leave the terminal surface"),
    SlashCommand("/exit", "exit", "leave the terminal surface"),
)

SLASH = [c.name for c in SLASH_COMMANDS]


def _prompt_session_supported() -> bool:
    """Whether prompt_toolkit can safely run its synchronous prompt.

    PromptSession.prompt() calls asyncio.run() internally. If AEGIS is launched
    from a parent that already owns an event loop, that raises RuntimeError, so
    fall back to plain input() instead of crashing the CLI.
    """
    if PromptSession is None:
        return False
    try:
        import asyncio
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def _read_repl_input(ps, prompt: Any = "aegis > ") -> str:
    """Read one REPL line, falling back when prompt_toolkit cannot run safely.

    The event-loop state can change after startup: a provider or UI integration may
    leave an asyncio loop active by the time the next prompt is drawn. Re-checking
    here prevents PromptSession.prompt() from crashing with asyncio.run() errors.
    """
    if ps is None or not _prompt_session_supported():
        return input(prompt if isinstance(prompt, str) else "aegis > ")
    try:
        return ps.prompt(prompt)
    except RuntimeError as exc:
        msg = str(exc)
        if "asyncio.run()" in msg or "running event loop" in msg:
            return input(prompt)
        raise


def _out(text: str = "", style: str | None = None) -> None:
    if _console:
        _console.print(text, style=style)
    else:
        print(text)


def _raw(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


# Warm AEGIS terminal palette: charcoal + amber + moss green + quiet cyan.
TERM_AMBER = "#d6a15e"
TERM_AMBER_DARK = "#8d6735"
TERM_GREEN = "#7ecf8f"
TERM_CYAN = "#6fb7d8"
TERM_PANEL = "#262a31"
TERM_MUTED = "#8f968f"
TERM_TEXT = "#f3f1e8"


# ANSI-Shadow "AEGIS" for the startup banner, drawn with the AEGIS desktop palette.
_AEGIS_ART = [
    " █████╗ ███████╗ ██████╗ ██╗███████╗",
    "██╔══██╗██╔════╝██╔════╝ ██║██╔════╝",
    "███████║█████╗  ██║  ███╗██║███████╗",
    "██╔══██║██╔══╝  ██║   ██║██║╚════██║",
    "██║  ██║███████╗╚██████╔╝██║███████║",
    "╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝╚══════╝",
]
_AEGIS_ART_COLORS = [TERM_AMBER, "#c9aa6b", TERM_GREEN, "#78c7a5", TERM_CYAN, "#8aa0a4"]


def _tool_icon(name: str) -> str:
    n = (name or "").lower()
    if "read" in n or "file" in n:
        return "📄"
    if "write" in n or "edit" in n:
        return "✎"
    if "bash" in n or "shell" in n or "exec" in n or "command" in n:
        return "▷"
    if "search" in n or "grep" in n or "glob" in n or "recall" in n:
        return "🔍"
    if "web" in n or "fetch" in n or "url" in n or "browser" in n:
        return "🌐"
    if "memory" in n:
        return "🧠"
    if "skill" in n:
        return "📦"
    if "kanban" in n or "todo" in n:
        return "🗂"
    return "⚙"


# Short aligned verbs so the tool trail reads as a tidy column rather than raw
# tool names. Falls back to the tool's own name when unmapped.
_TOOL_VERBS = {
    "read_file": "read", "read": "read", "write_file": "write", "write": "write",
    "edit_file": "edit", "edit": "edit", "patch": "edit", "apply_patch": "edit",
    "bash": "run", "shell": "run", "terminal": "run", "execute_code": "code",
    "search_files": "search", "grep": "search", "glob": "glob", "ripgrep": "search",
    "recall": "recall", "session_search": "recall", "web_search": "search",
    "web_fetch": "fetch", "web_extract": "fetch", "download": "fetch",
    "http_request": "http", "browser": "browse", "memory": "memory",
    "skill": "skill", "skill_manage": "skill", "kanban": "tasks", "todo": "tasks",
    "delegate_task": "delegate", "clarify": "ask", "vision_analyze": "vision",
}


def _tool_verb(name: str) -> str:
    n = (name or "").lower()
    if n in _TOOL_VERBS:
        return _TOOL_VERBS[n]
    for key, verb in _TOOL_VERBS.items():
        if key in n:
            return verb
    return n[:9] or "tool"


def _oneline(text: str, limit: int = 72) -> str:
    """Collapse whitespace to single spaces and truncate for one-line previews."""
    s = " ".join(str(text).split())
    return (s[: limit - 1] + "…") if len(s) > limit else s


def _tool_preview(name: str, args: dict) -> str:
    """A short, human-readable preview of a tool call's primary argument — the same
    idea as AEGIS' per-tool previews, so the trail says *what* each call does."""
    args = args or {}
    n = (name or "").lower()
    if n in ("memory",):
        act = args.get("action", "")
        return _oneline(f"{act} {args.get('content') or args.get('match') or ''}", 64)
    if "skill" in n:
        return _oneline(args.get("name") or args.get("action") or "", 48)
    if n in ("kanban", "todo"):
        return _oneline(args.get("action") or "update tasks", 48)
    if "web" in n or "fetch" in n or "http" in n or "download" in n:
        url = args.get("url") or (args.get("urls") or [""])[0] if isinstance(args.get("urls"), list) else args.get("url", "")
        url = url or args.get("query", "")
        return _oneline(str(url).replace("https://", "").replace("http://", ""), 56)
    detail = (args.get("command") or args.get("path") or args.get("url")
              or args.get("query") or args.get("pattern") or args.get("name")
              or args.get("goal") or args.get("question") or "")
    return _oneline(detail, 72)


def _result_is_failure(summary: str) -> bool:
    """Heuristic failure detection for tool results that didn't set is_error but
    whose summary still reports a problem (exit codes, error text)."""
    s = (summary or "").lower()
    return s.startswith("error") or "exit 1" in s or "exit code" in s and "exit code 0" not in s


def slash_matches(query: str = "") -> list[SlashCommand]:
    q = query.strip().lower()
    if not q:
        return list(SLASH_COMMANDS)
    return [
        cmd for cmd in SLASH_COMMANDS
        if q in cmd.name.lower()
        or q in cmd.group.lower()
        or q in cmd.summary.lower()
        or q in cmd.usage.lower()
    ]


def slash_help_lines(query: str = "") -> list[str]:
    rows = slash_matches(query)
    if not rows:
        return [f"no slash commands match {query!r}"]
    lines: list[str] = []
    last_group = ""
    if query:
        lines.append(f"slash commands matching {query!r}:")
    for cmd in rows:
        if cmd.group != last_group:
            lines.append(f"{cmd.group}:")
            last_group = cmd.group
        usage = cmd.usage or cmd.name
        lines.append(f"  {usage:<34} {cmd.summary}")
    if not query:
        lines.append("Use /help <term> to filter.")
    return lines


def make_slash_completer():
    if WordCompleter is None:
        return None
    display = {cmd.name: cmd.usage or cmd.name for cmd in SLASH_COMMANDS}
    meta = {cmd.name: f"{cmd.group}: {cmd.summary}" for cmd in SLASH_COMMANDS}
    return WordCompleter(
        SLASH,
        ignore_case=True,
        display_dict=display,
        meta_dict=meta,
        sentence=True,
        match_middle=True,
    )


def expand_references(text: str, cwd: Path) -> str:
    """Compatibility wrapper for shared prompt context references."""
    from ..context_refs import expand_references as _expand

    return _expand(text, cwd)


def make_approver(auto: bool = False):
    def approver(prompt_text: str):
        if auto:
            return True
        with _approve_lock:
            try:
                ans = input(f"\n  ⚠ {prompt_text} [y/N/a=always] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if ans in ("a", "always"):
                return "always"        # allow this tool/command for the rest of the session
            return ans in ("y", "yes")
    return approver


def make_asker():
    """Interactive answerer for the clarify tool: prints the question + numbered
    choices and reads the user's reply inline."""
    def asker(question: str, choices: list[str]) -> str:
        with _approve_lock:
            print(f"\n  ❓ {question}")
            for i, c in enumerate(choices, 1):
                print(f"     {i}. {c}")
            try:
                ans = input("  your answer: ").strip()
            except (EOFError, KeyboardInterrupt):
                return ""
        if choices and ans.isdigit() and 1 <= int(ans) <= len(choices):
            return choices[int(ans) - 1]
        return ans
    return asker


def make_secret_capture():
    """Hidden local prompt for tools that need to store a secret safely."""
    def capture(key: str, prompt: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        with _approve_lock:
            from ..secret_capture import capture_secret_interactive

            return capture_secret_interactive(key, prompt, metadata)

    return capture


class Renderer:
    """Turns agent events into terminal output."""

    def __init__(self, config: Config | None = None):
        self._streaming = False
        self.config = config
        self._thinking_chars = 0
        self._provider_reasoning_chars = 0
        self._provider_reasoning = ""
        self.status = TerminalStatusState()

    def _reasoning_mode(self) -> str:
        if self.config is None:
            return "summary"
        return str(self.config.get("display.reasoning", "summary") or "summary")

    def __call__(self, e: dict) -> None:
        self.status.update(e)
        t = e["type"]
        if t == "reasoning_delta":
            mode = self._reasoning_mode()
            if mode == "off":
                return
            text = e.get("text") or ""
            self._thinking_chars += len(text)
            self._provider_reasoning_chars += len(text)
            if mode == "live":
                # Provider-native thinking stream in a dim bordered box, so it reads
                # as process (not answer) \u2014 like a live "reasoning" panel.
                if not getattr(self, "_thinking", False):
                    self._thinking = True
                    import shutil
                    w = max(24, min(shutil.get_terminal_size((80, 20)).columns, 100))
                    _raw("\x1b[2m\u256d\u2500 reasoning " + "\u2500" * (w - 13) + "\x1b[0m\n")
                _raw("\x1b[2m" + text + "\x1b[0m")
            elif not getattr(self, "_thinking_summary", False):
                self._thinking_summary = True
                _out("  thinking…", style="bright_black")
            return
        if getattr(self, "_thinking", False):
            self._thinking = False
            import shutil
            w = max(24, min(shutil.get_terminal_size((80, 20)).columns, 100))
            _raw("\x1b[0m\n\x1b[2m╰" + "─" * (w - 1) + "\x1b[0m\n")
        if t in ("assistant_message", "final", "tool_start", "error") and \
                getattr(self, "_thinking_summary", False):
            self._thinking_summary = False
            if self._thinking_chars:
                _out(f"  thinking complete ({self._thinking_chars:,} chars captured; "
                     "use /reasoning live to stream)", style="bright_black")
                self._thinking_chars = 0
        if t == "assistant_delta":
            self._streaming = True
            _raw(e["text"])
        elif t == "assistant_message":
            if self._streaming:
                _raw("\n")
                self._streaming = False
            elif e.get("text"):
                _out(e["text"])
        elif t == "provider_start":
            self._provider_streaming = bool(e.get("stream"))
            self._provider_reasoning_chars = 0
            self._provider_reasoning = str(e.get("reasoning") or "")
            reason = ""
            if self._reasoning_mode() == "live" and self._provider_reasoning not in {"", "off", "none"}:
                reason = f" · reasoning live/{self._provider_reasoning}"
            _out(
                f"  contacting {e.get('provider') or 'provider'} / {e.get('model') or 'model'}...{reason}",
                style="bright_black",
            )
        elif t == "provider_end":
            ms = int(e.get("duration_ms") or 0)
            if (
                e.get("status") != "error"
                and self._reasoning_mode() == "live"
                and self._provider_reasoning not in {"", "off", "none"}
                and not self._provider_reasoning_chars
            ):
                _out(
                    "  reasoning live requested, but this provider emitted no reasoning stream.",
                    style="bright_black",
                )
            if getattr(self, "_provider_streaming", False) and e.get("status") != "error":
                return
            if e.get("status") == "error" or ms >= 1500:
                style = "red" if e.get("status") == "error" else "bright_black"
                _out(
                    f"  provider {e.get('status') or 'done'} in {ms / 1000:.1f}s",
                    style=style,
                )
        elif t == "tool_start":
            name = e["name"]
            preview = _tool_preview(name, e.get("args", {}))
            gutter = f"  {_tool_icon(name)} {_tool_verb(name):<8}"
            _out(f"{gutter} {preview}".rstrip(), style=TERM_AMBER)
        elif t == "tool_result":
            ms = int(e.get("duration_ms") or 0)
            secs = f"  {ms / 1000:.1f}s" if ms else ""
            summary = _oneline(e.get("summary") or "", 76)
            failure = e.get("is_error") or _result_is_failure(summary)
            if failure:
                _out(f"    ✗ {summary}{secs}", style="red")
            elif e.get("name") == "memory":
                _out(f"    🧠 {summary}{secs}", style=TERM_AMBER)
            elif e.get("name") == "skill":
                _out(f"    📦 {summary}{secs}", style=TERM_AMBER)
            else:
                _out(f"    ✓ {summary}{secs}", style=TERM_GREEN)
        elif t == "ultracode_continue":
            _out(f"  ↻ ultracode: {e.get('remaining', '?')} todo(s) left — continuing "
                 f"(push {e.get('n', '')}/{12})", style=TERM_CYAN)
        elif t == "compacting":
            _out("  ⋯ context filling up — compacting older turns to free room …", style="yellow")
        elif t == "compacted":
            before = int(e.get("tokens_before") or 0)
            after = int(e.get("tokens_after") or 0)
            if before and after and before > after:
                freed = before - after
                pct = int(100 * freed / before)
                _out(f"  ✓ compacted — freed {_fmt_token_count(freed)} tokens "
                     f"({_fmt_token_count(after)} kept, {pct}% lighter)", style=TERM_GREEN)
            else:
                _out("  ✓ compacted context", style=TERM_GREEN)
        elif t == "compaction_aborted":
            _out(f"  ⚠ compaction couldn't shrink further — {e.get('error') or 'tail is the floor'}",
                 style="yellow")
        elif t == "budget_exhausted":
            _out("  ⋯ step limit reached; summarizing progress so far …", style="yellow")
        elif t == "review_started":
            _out(f"  🧠 reflecting on this session ({e.get('kind', '')})…", style="bright_black")
        elif t == "review_done":
            acts = e.get("actions") or []
            if acts:
                _out("  🧠 learned & saved: " + "; ".join(acts), style="magenta")
        elif t == "error":
            _out(f"  ✖ {e['message']}", style="red")
        elif t == "final":
            if self._streaming:
                _raw("\n")
                self._streaming = False


@dataclass
class TerminalStatusState:
    iteration: int = 0
    max_iterations: int = 0
    active_tool: str = ""
    last_tool: str = ""
    compacting: bool = False
    budget_exhausted: bool = False
    last_event: str = ""

    def update(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        self.last_event = etype
        if etype == "iteration":
            self.iteration = int(event.get("n") or 0)
            self.max_iterations = int(event.get("max") or 0)
            self.compacting = False
        elif etype == "tool_start":
            self.active_tool = str(event.get("name") or "")
            self.last_tool = self.active_tool
        elif etype == "tool_result":
            self.last_tool = str(event.get("name") or self.last_tool)
            self.active_tool = ""
        elif etype == "compacting":
            self.compacting = True
        elif etype == "budget_exhausted":
            self.budget_exhausted = True
        elif etype in {"assistant_message", "final", "error"}:
            self.active_tool = ""
            self.compacting = False

    def segment(self) -> str:
        bits: list[str] = []
        if self.iteration and self.max_iterations:
            bits.append(f"iter {self.iteration}/{self.max_iterations}")
        if self.active_tool:
            bits.append(f"tool {self.active_tool}")
        elif self.last_tool:
            bits.append(f"last tool {self.last_tool}")
        if self.compacting:
            bits.append("compacting")
        if self.budget_exhausted:
            bits.append("budget exhausted")
        return " · ".join(bits)


def _run_refs(agent: Any) -> tuple[str, str, str]:
    meta = getattr(getattr(agent, "session", None), "meta", {}) or {}
    trace_ctx = getattr(agent, "_trace_context", {}) or {}
    run_id = str(meta.get("last_run_id") or "")
    trace_id = str(meta.get("last_trace_id") or meta.get("trace_id") or trace_ctx.get("trace_id") or "")
    turn_id = str(meta.get("last_turn_id") or meta.get("turn_id") or trace_ctx.get("turn_id") or "")
    return run_id, trace_id, turn_id


def _fmt_token_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{round(n / 1000):.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def _context_window(agent: Any) -> dict[str, int]:
    from ..agent.compaction import estimated_tokens

    window = int(getattr(getattr(agent, "provider", None), "context_length", 0) or 0)
    used = int(estimated_tokens(getattr(getattr(agent, "session", None), "messages", []) or []))
    remaining = max(0, window - used) if window else 0
    percent = int(100 * used / max(1, window)) if window else 0
    return {"used": used, "window": window, "remaining": remaining, "percent": percent}


def _context_breakdown(agent: Any) -> dict[str, Any]:
    """Token accounting for the active context window: system prompt (with its parts),
    conversation history, and the live tool schemas — so the user can see what fills the window."""
    import json

    from ..agent.compaction import estimated_tokens
    from ..util import estimate_tokens

    session = getattr(agent, "session", None)
    msgs = list(getattr(session, "messages", []) or [])
    window = int(getattr(getattr(agent, "provider", None), "context_length", 0) or 0)
    sys_msg = msgs[0] if msgs and msgs[0].role == "system" else None
    system = estimate_tokens(sys_msg.content) if sys_msg and sys_msg.content else 0
    non_system = [m for m in msgs if m.role != "system"]
    history = int(estimated_tokens(non_system))
    parts = [
        {"name": p.get("name", "?"), "tier": p.get("tier", ""), "tokens": int(p.get("tokens", 0) or 0)}
        for p in (getattr(session, "meta", {}) or {}).get("prompt_parts", []) or []
    ]
    tools = tool_count = 0
    try:
        sel = agent.registry.available(
            agent.config.get("tools.toolsets", ["core"]),
            disabled=agent.config.get("tools.disabled", []),
        )
        tool_count = len(sel)
        schemas = agent.registry.schemas(sel)
        tools = int(estimate_tokens(json.dumps(
            schemas, default=lambda o: getattr(o, "__dict__", str(o)))))
    except Exception:  # noqa: BLE001
        pass
    used = system + history + tools
    return {"window": window, "system": system, "history": history, "tools": tools,
            "tool_count": tool_count, "parts": parts, "used": used, "messages": len(non_system)}


def _render_context(agent: Any) -> list[str]:
    b = _context_breakdown(agent)
    win = b["window"]
    rows = [
        ("system prompt", b["system"], f"{len(b['parts'])} parts"),
        ("conversation", b["history"], f"{b['messages']} messages"),
        ("tool schemas", b["tools"], f"{b['tool_count']} enabled"),
    ]
    peak = max(1, max(v for _, v, _ in rows))
    lines: list[str] = []
    if win:
        pct = int(100 * b["used"] / max(1, win))
        lines.append(f"Context window — {_fmt_token_count(b['used'])} / {_fmt_token_count(win)} ({pct}%)")
        free = max(0, win - b["used"])
        fill = min(28, round(28 * b["used"] / win))
        lines.append("  [" + "█" * fill + "·" * (28 - fill) + f"]  {_fmt_token_count(free)} free")
    else:
        lines.append(f"Context — {_fmt_token_count(b['used'])} tokens (window unknown)")
    for label, val, note in rows:
        bar = "▇" * max(1, round(16 * val / peak)) if val else ""
        lines.append(f"  {label:<14} {_fmt_token_count(val):>8}  {bar} {note}")
    big = sorted(b["parts"], key=lambda p: -p["tokens"])[:5]
    if big:
        lines.append("  largest prompt parts: " + ", ".join(f"{p['name']} {_fmt_token_count(p['tokens'])}" for p in big))
    return lines


def _record_terminal_run(agent: Any, run: Any) -> None:
    meta = getattr(getattr(agent, "session", None), "meta", None)
    if not isinstance(meta, dict):
        return
    if getattr(run, "run_id", ""):
        meta["last_run_id"] = str(run.run_id)
    if getattr(run, "trace_id", ""):
        meta["last_trace_id"] = str(run.trace_id)
        meta["trace_id"] = str(run.trace_id)
    if getattr(run, "turn_id", ""):
        meta["last_turn_id"] = str(run.turn_id)
        meta["turn_id"] = str(run.turn_id)


def _status_line(agent: Agent, progress: TerminalStatusState | None = None) -> str:
    usage = getattr(getattr(agent, "budget", None), "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    ctx = _context_window(agent)
    run_id, trace, _turn = _run_refs(agent)
    reasoning = f"{agent.config.get('display.reasoning', 'summary')}/{getattr(agent, 'reasoning', 'off')}"
    perms = str(agent.config.get("tools.exec_mode", "auto") or "auto")
    model = str(getattr(getattr(agent, "provider", None), "model", "") or "?")
    if ctx["window"]:
        ctx_text = f"ctx {_fmt_token_count(ctx['used'])}/{_fmt_token_count(ctx['window'])} ({ctx['percent']}%)"
    else:
        ctx_text = f"ctx {_fmt_token_count(ctx['used'])}"
    suffix = ""
    if progress and progress.segment():
        suffix += f" · {progress.segment()}"
    if run_id:
        suffix += f" · run {run_id[:12]}"
    if trace:
        suffix += f" · trace {trace[:12]}"
    return (
        f"  AEGIS · {model} · {ctx_text} · tokens in {input_tokens:,} "
        f"out {output_tokens:,} · reasoning {reasoning} · perms {perms}{suffix}"
    )


def _ctx_bar(percent: int, width: int = 10) -> str:
    """Compact context-usage sparkline, e.g. ███░░░░░░░ for 30%."""
    percent = max(0, min(100, int(percent or 0)))
    filled = round(percent / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _bottom_toolbar(agent: Agent):
    """Persistent status bar under the prompt: model · live context meter · reasoning
    · permissions. Returns prompt_toolkit formatted text (falls back to plain on error)."""
    from html import escape
    try:
        from prompt_toolkit.formatted_text import HTML
    except Exception:  # noqa: BLE001
        return ""
    model = str(getattr(getattr(agent, "provider", None), "model", "") or "?")
    ctx = _context_window(agent)
    reasoning = f"{agent.config.get('display.reasoning', 'summary')}/{getattr(agent, 'reasoning', 'off')}"
    perms = str(agent.config.get("tools.exec_mode", "auto") or "auto")
    if ctx["window"]:
        pct = ctx["percent"]
        ctx_text = (f"{_ctx_bar(pct)} {pct}% "
                    f"({_fmt_token_count(ctx['used'])}/{_fmt_token_count(ctx['window'])})")
    else:
        ctx_text = _fmt_token_count(ctx["used"])
    return HTML(
        f" <style fg='{TERM_AMBER}'><b>AEGIS</b></style> "
        f"<style fg='{TERM_TEXT}'>{escape(model)}</style>  "
        f"<style fg='{TERM_MUTED}'>ctx</style> <style fg='{TERM_GREEN}'>{escape(ctx_text)}</style>  "
        f"<style fg='{TERM_MUTED}'>reasoning</style> <style fg='{TERM_TEXT}'>{escape(reasoning)}</style>  "
        f"<style fg='{TERM_MUTED}'>perms</style> <style fg='{TERM_TEXT}'>{escape(perms)}</style> "
    )


def _prompt_message(agent: Agent):
    """Prompt shown by the local terminal surface."""
    try:
        from prompt_toolkit.formatted_text import HTML
    except Exception:  # noqa: BLE001
        return "aegis > "
    label = "aegis"
    profile = str(agent.config.get("agent.personality") or "").strip()
    if profile:
        label += f":{profile}"
    from html import escape
    return HTML(
        f"<style fg='{TERM_AMBER}'><b>{escape(label)}</b></style>"
        f"<style fg='{TERM_MUTED}'> › </style>"
    )


def _maybe_print_status_footer(agent: Any, on_event: Callable[[dict], None]) -> None:
    config = getattr(agent, "config", None)
    if config is not None and not bool(config.get("display.status_footer", True)):
        return
    progress = getattr(on_event, "status", None)
    _raw(_status_line(agent, progress) + "\n")


def banner(agent: Agent) -> None:
    model = getattr(agent.provider, "model", "?")
    if _console:
        from rich.text import Text
        art = Text()
        for line, color in zip(_AEGIS_ART, _AEGIS_ART_COLORS, strict=False):
            art.append("  " + line + "\n", style=f"bold {color}")
        _console.print(art)
        body = Text()
        body.append("  AEGIS local runtime", style=f"bold {TERM_AMBER}")
        body.append(f"  ·  v{__version__}\n", style=f"{TERM_MUTED}")
        body.append("  terminal agent + operator desktop\n\n", style=f"{TERM_MUTED}")
        body.append("  provider ", style=f"{TERM_MUTED}")
        body.append(f"{agent.config.get('model.provider', 'provider')}\n", style=f"bold {TERM_TEXT}")
        body.append("  model    ", style=f"{TERM_MUTED}")
        body.append(f"{model}\n", style=f"bold {TERM_GREEN}")
        ctx = _context_window(agent)
        if ctx["window"]:
            body.append("  context  ", style=f"{TERM_MUTED}")
            body.append(
                f"{_ctx_bar(ctx['percent'], 14)}  "
                f"{_fmt_token_count(ctx['used'])}/{_fmt_token_count(ctx['window'])} "
                f"({ctx['remaining']:,} left)\n",
                style=f"bold {TERM_TEXT}",
            )
        body.append("  controls ", style=f"{TERM_MUTED}")
        body.append(
            f"reasoning {agent.config.get('display.reasoning', 'summary')}/"
            f"{getattr(agent, 'reasoning', 'off')} · permissions "
            f"{agent.config.get('tools.exec_mode', 'auto')}\n",
            style=f"{TERM_TEXT}",
        )
        body.append("  cwd      ", style=f"{TERM_MUTED}")
        body.append(f"{agent.cwd}\n", style=f"{TERM_TEXT}")
        body.append("  session  ", style=f"{TERM_MUTED}")
        body.append(f"{agent.session.id}\n\n", style=f"{TERM_TEXT}")
        body.append("  Surfaces\n", style=f"bold {TERM_AMBER}")
        body.append("    aegis ui", style=f"bold {TERM_GREEN}")
        body.append("   browser control panel\n", style=f"{TERM_MUTED}")
        body.append("    aegis desktop", style=f"bold {TERM_GREEN}")
        body.append("  Electron desktop app\n\n", style=f"{TERM_MUTED}")
        body.append("  Try  ", style=f"{TERM_MUTED}")
        body.append("/help", style=f"bold {TERM_GREEN}")
        body.append(" commands · ", style="dim")
        body.append("@file.py", style=f"bold {TERM_GREEN}")
        body.append(" attach a file · ", style="dim")
        body.append("/goal", style=f"bold {TERM_GREEN}")
        body.append(" run to completion · ", style="dim")
        body.append("/quit", style=f"bold {TERM_GREEN}")
        body.append(" exit", style="dim")
        _console.print(Panel(
            body,
            title=f"[bold {TERM_AMBER}]agent desktop[/]",
            subtitle=f"[{TERM_MUTED}]ready for local work[/]",
            border_style=TERM_AMBER_DARK,
            padding=(0, 1),
        ))
    else:
        print("=" * 60)
        print(f"AEGIS local runtime v{__version__} · {model} · session {agent.session.id}")
        ctx = _context_window(agent)
        if ctx["window"]:
            print(f"context: {_fmt_token_count(ctx['used'])}/{_fmt_token_count(ctx['window'])} "
                  f"({ctx['remaining']:,} left)")
        print(f"controls: reasoning {agent.config.get('display.reasoning', 'summary')}/"
              f"{getattr(agent, 'reasoning', 'off')} · permissions "
              f"{agent.config.get('tools.exec_mode', 'auto')}")
        print(f"cwd: {agent.cwd}")
        print("Surfaces:  aegis ui (browser control panel) · aegis desktop (Electron app)")
        print("Try: /help · @file · /goal · /ultracode · /context · /quit")
        print("=" * 60)


def _skill_scaffold(name: str, description: str) -> str:
    """A self-documenting SKILL.md body for `/skill new`, so a fresh skill reads like a
    usable procedure (trigger → steps → done-check) instead of an empty stub."""
    return (
        f"# {name}\n\n"
        f"{description}\n\n"
        "## When to use\n"
        "Describe the trigger — the kind of request or situation where this skill applies.\n"
        "Keep it specific; this is what the agent matches against.\n\n"
        "## Procedure\n"
        "1. First step — be concrete and verifiable.\n"
        "2. Next step.\n"
        "3. …\n\n"
        "## Done when\n"
        "State the success check (e.g. tests pass, file exists, output matches) so the agent\n"
        "knows it has finished rather than guessing.\n"
    )


def _ultracode_skill_body(agent: Any) -> str:
    """The ultracode SKILL.md body, loaded directly so the loop is guaranteed in context
    instead of waiting for the model to choose the skill."""
    loader = getattr(agent, "skills", None)
    if loader is None:
        return ""
    try:
        skill = next((s for s in loader.available() if s.name == "ultracode"), None)
        return skill.full_body() if skill is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def handle_ultracode_command(text: str, agent: Any) -> str | None:
    """First-class ultracode: ``/ultracode <task>`` force-loads the ultracode skill and runs the
    task through the full autonomous plan→implement→verify loop. Returns the prompt to run, or
    None when there's nothing to run. Unlike /plan it doesn't pause for approval — it's the
    rigorous one-shot loop, gated on real, observed verification before it can claim done."""
    parts = text.strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        _out("usage: /ultracode <task> — run the rigorous plan → implement → verify loop", style="yellow")
        return None
    _out("🚀 ultracode — autonomous loop: plan → test → implement → verify.", style="cyan")
    # The rigorous autonomous loop needs room to actually finish: raise the step budget
    # for this run, and mark the turn so the loop won't stop while todo items remain open.
    try:
        uc_budget = int(agent.config.get("agent.ultracode_max_iterations", 250) or 250)
        agent.budget.max_iterations = max(int(agent.budget.max_iterations), uc_budget)
        agent._ultracode_active = True
    except Exception:  # noqa: BLE001
        pass
    body = _ultracode_skill_body(agent)
    skill_block = f"<system-reminder>ULTRACODE SKILL — follow this loop exactly:\n{body}\n</system-reminder>\n\n" if body else ""
    return (
        skill_block
        + "<system-reminder>Run the ultracode loop above end to end for the task below, "
        "autonomously. This is EXECUTION on the real workspace, not a demonstration: actually "
        "create and modify the real files and run real commands in this session NOW. Producing a "
        "sample, a stub, a skeleton, or an illustrative example INSTEAD of doing the task is a "
        "failure — keep going until the task itself is actually done. Restate the goal as a "
        "checkable success criterion; plan in verifiable steps with todo_write; for behavioral "
        "changes write a FAILING test first; make the smallest change that satisfies the goal; "
        "then RUN the build/tests/linter and read the real output. Do NOT declare the task done "
        "until that success criterion is met and proven by tool output you actually ran and "
        "observed. If something blocks the real path, say so honestly and try another route — "
        "never fabricate results.</system-reminder>\n\n" + arg
    )


_ARCHITECT_SYSTEM = (
    "You are the ARCHITECT. Produce a precise, minimal implementation plan for the task — "
    "do NOT write the full solution. Identify the exact files to create/modify, the specific "
    "changes in each, the order to do them, edge cases to handle, and the concrete verification "
    "(tests/build/lint) that proves it works. Be concrete and surgical; prefer the smallest change "
    "that satisfies the goal. Output just the plan as numbered steps."
)


def handle_architect_command(text: str, agent: Any) -> str | None:
    """Aider-style architect mode: a (typically stronger) model drafts the implementation
    plan, then THIS model executes it. The architect model is the ``auxiliary.architect``
    slot, falling back to the main provider when unset. Returns the execution prompt."""
    parts = text.strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        _out("usage: /architect <task> — a strong model plans, then this model implements",
             style="yellow")
        return None
    _out("🏛  architect — planning with the reasoning model…", style="cyan")
    plan = ""
    try:
        from ..auxiliary import router_for
        from ..types import Message
        provider = router_for(agent).provider_for("architect")
        resp = provider.complete(
            [Message.system(_ARCHITECT_SYSTEM), Message.user(arg)], tools=None, stream=False)
        plan = (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        _out(f"  architect model unavailable ({e}); implementing directly.", style="yellow")
    if plan:
        _out("  ✓ plan ready — implementing.", style=TERM_GREEN)
        return (
            "<system-reminder>An architect model produced this implementation plan. Execute it "
            "now on the real workspace: make the real edits and run the real verification. Adapt "
            "if you find the plan is wrong, but do the actual work — do not just restate the plan."
            "</system-reminder>\n\nIMPLEMENTATION PLAN:\n" + plan + "\n\nTASK: " + arg
        )
    return arg   # no plan -> run the task directly


def handle_gstack_command(text: str) -> str | None:
    """`/gstack <goal>` — run the goal through a gstack sprint (think→plan→build→
    review→test→ship→reflect) in this turn. Returns the sprint prompt for the agent."""
    parts = text.strip().split(maxsplit=1)
    goal = parts[1].strip() if len(parts) > 1 else ""
    if not goal:
        from ..gstack import PHASES
        _out("usage: /gstack <goal>", style="yellow")
        _out("  phases: " + " → ".join(p.name for p in PHASES), style="cyan")
        return None
    from ..gstack import repl_sprint_prompt
    _out("🏃 gstack — running the sprint (think → plan → build → review → test → ship → reflect)…",
         style="cyan")
    return repl_sprint_prompt(goal)


_SPEC_SYSTEM = (
    "You are a software architect writing a SPEC for a feature. Output GitHub-flavored "
    "markdown with EXACTLY three sections in this order:\n"
    "## Requirements — numbered, testable acceptance criteria (what 'done' means).\n"
    "## Design — the approach: files to touch, data flow, key decisions, trade-offs.\n"
    "## Tasks — a checklist of small, ordered, independently-verifiable steps, each a "
    "line `- [ ] <task>`. End each task with the concrete check that proves it.\n"
    "Be concrete and minimal — the smallest design that satisfies the goal. Start with "
    "a single `# <Title>` line. Do not write the implementation code."
)


def handle_spec_command(text: str, agent: Any) -> str | None:
    """Spec-driven development. `new <title>` drafts a persistent requirements→design→tasks
    spec with the architect model; `implement <slug>` feeds it back for execution; `list`/`show`
    inspect. Returns an execution prompt only for `implement`, else None (handled inline)."""
    from ..spec import SpecStore, implementation_prompt

    parts = text.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    arg = parts[2].strip() if len(parts) > 2 else ""
    store = SpecStore.from_config(getattr(agent, "config", None), cwd=getattr(agent, "cwd", None))

    if sub == "list":
        specs = store.list()
        for s in specs:
            done, total = s.progress()
            _out(f"  {s.slug:<26} [{s.status}]  {done}/{total} tasks", style="cyan")
        if not specs:
            _out("  (no specs yet — /spec new <title> to draft one)", style="yellow")
        return None

    if sub == "show":
        spec = store.get(arg) if arg else None
        if not spec:
            _out(f"  no spec: {arg or '(missing slug)'}", style="yellow")
            return None
        _out(spec.body)
        return None

    if sub == "new":
        if not arg:
            _out("usage: /spec new <title or one-line goal>", style="yellow")
            return None
        _out("📐 spec — drafting requirements → design → tasks…", style="cyan")
        body = ""
        try:
            from ..auxiliary import router_for
            from ..types import Message
            provider = router_for(agent).provider_for("architect")
            resp = provider.complete(
                [Message.system(_SPEC_SYSTEM), Message.user(arg)], tools=None, stream=False)
            body = (resp.text or "").strip()
        except Exception as e:  # noqa: BLE001
            _out(f"  spec model unavailable ({e}); writing a skeleton.", style="yellow")
        title = arg if len(arg) <= 60 else arg[:57] + "…"
        spec = store.create(title, body or None)
        done, total = spec.progress()
        _out(f"  ✓ spec saved: {spec.path}  ({total} tasks)", style=TERM_GREEN)
        _out(f"    implement with: /spec implement {spec.slug}", style="cyan")
        return None

    if sub == "implement":
        spec = store.get(arg) if arg else None
        if not spec:
            _out(f"  no spec: {arg or '(missing slug)'} — /spec list", style="yellow")
            return None
        store.set_status(spec.slug, "in_progress")
        done, total = spec.progress()
        _out(f"  ▶ implementing spec '{spec.title}' — {done}/{total} tasks done", style=TERM_GREEN)
        return implementation_prompt(spec)

    _out("usage: /spec new|list|show|implement [arg]", style="yellow")
    return None


def handle_plan_command(text: str, agent: Any) -> str | None:
    """Plan mode. ``/plan <task>`` runs a planning turn (investigate read-only, draft a numbered
    plan, change nothing) and stashes the task; ``/proceed`` runs the stashed task for real.
    Returns the prompt to run as this turn, or None when there's nothing to run."""
    parts = text.strip().split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    meta = getattr(getattr(agent, "session", None), "meta", None)
    if name == "/plan":
        if not arg:
            _out("usage: /plan <task> — draft a plan first, then /proceed to execute", style="yellow")
            return None
        if isinstance(meta, dict):
            meta["pending_plan"] = arg
        _out("📋 plan mode — drafting a plan (no changes yet). Review, then /proceed.", style="cyan")
        return (
            "<system-reminder>PLAN MODE. Produce a concise, numbered step-by-step plan for the task "
            "below. You MAY investigate with read-only tools (read/search/list), but do NOT edit "
            "files, run side-effecting commands, or make any changes yet. End by telling the user to "
            "run /proceed to execute.</system-reminder>\n\n" + arg
        )
    # /proceed
    task = meta.pop("pending_plan", "") if isinstance(meta, dict) else ""
    if not task:
        _out("nothing to proceed with — run /plan <task> first", style="yellow")
        return None
    _out("▶ executing the approved plan…", style="green")
    return (
        "<system-reminder>The user approved the plan. Execute it now end-to-end — make the changes "
        "and run the commands needed to finish, then verify.</system-reminder>\n\n" + task
    )


def handle_goal_command(
    text: str,
    agent: Any,
    store: SessionStore,
    *,
    out: Callable[[str, str | None], None] | None = None,
) -> str | None:
    """Handle `/goal` and `/subgoal`. Returns the prompt to run immediately."""

    from .. import goals

    emit = out or _out
    reply, start_turn = goals.handle_command(agent.session, text, agent.config)
    if reply:
        emit(reply, "cyan")
    store.save(agent.session)
    if not start_turn:
        return None
    active = goals.get(agent.session)
    return active["text"] if active else None


def _process_notification_target_session(
    agent: Any,
    store: SessionStore,
    meta: dict | None,
) -> Session | None:
    if not isinstance(meta, dict) or meta.get("synthetic") != "process_notification":
        return None
    session_key = str(meta.get("process_session_key") or "").strip()
    if not session_key:
        return None
    current = getattr(agent, "session", None)
    if current is not None and getattr(current, "id", "") == session_key:
        return current
    try:
        return store.load(session_key) or Session(id=session_key, title=session_key)
    except Exception:  # noqa: BLE001
        return Session(id=session_key, title=session_key)


def _switch_for_process_notification(
    agent: Any,
    store: SessionStore,
    meta: dict | None,
) -> Session | None:
    target = _process_notification_target_session(agent, store, meta)
    if target is None:
        return None
    current = getattr(agent, "session", None)
    if current is not None and getattr(current, "id", "") == target.id:
        return None
    _switch_session(agent, target, reason="process_notification")
    return current


def _restore_after_process_notification(
    agent: Any,
    store: SessionStore,
    restore_session: Session,
) -> None:
    current = getattr(agent, "session", None)
    if current is not None:
        try:
            store.save(current)
        except Exception:  # noqa: BLE001
            pass
    _switch_session(agent, restore_session, reason="process_notification_return")


def run_terminal_turn(
    text: str,
    agent: Any,
    runner: SurfaceRunner,
    store: SessionStore,
    *,
    surface: str,
    on_event: Callable[[dict], None],
    notify: Callable[[str], None] | None = None,
    add_profile_directive: bool = True,
    meta: dict | None = None,
    include_wakeups: bool = True,
):
    """Run a terminal turn, routing synthetic process notifications by session key."""

    restore_session = _switch_for_process_notification(agent, store, meta)
    try:
        return _run_terminal_turn_active_session(
            text,
            agent,
            runner,
            store,
            surface=surface,
            on_event=on_event,
            notify=notify,
            add_profile_directive=add_profile_directive,
            meta=meta,
            include_wakeups=include_wakeups,
        )
    finally:
        if restore_session is not None:
            _restore_after_process_notification(agent, store, restore_session)


def _run_terminal_turn_active_session(
    text: str,
    agent: Any,
    runner: SurfaceRunner,
    store: SessionStore,
    *,
    surface: str,
    on_event: Callable[[dict], None],
    notify: Callable[[str], None] | None = None,
    add_profile_directive: bool = True,
    meta: dict | None = None,
    include_wakeups: bool = True,
):
    """Run one terminal-surface turn with the same REPL lifecycle.

    This is the terminal path: prompt preparation, shared
    SurfaceRunner execution, goal continuation, first-run tips, and persistence
    live in one place instead of being reimplemented per UI.
    """

    from ..firstrun import maybe_tip, profile_build_directive
    from .. import goals

    tools_before = getattr(agent, "tools_used", 0)
    prompt = text + (profile_build_directive(agent.config) if add_profile_directive else "")
    run = runner.run_prompt(
        prompt,
        session=agent.session,
        agent=agent,
        surface=surface,
        platform="cli",
        meta=meta,
        on_event=on_event,
        include_wakeups=include_wakeups,
    )
    _record_terminal_run(agent, run)

    def run_continuation(prompt_text: str):
        cont = runner.run_prompt(
            prompt_text,
            session=agent.session,
            agent=agent,
            surface=surface,
            platform="cli",
            meta={"goal_continuation": True, **(meta or {})},
            on_event=on_event,
            include_wakeups=include_wakeups,
        )
        _record_terminal_run(agent, cont)
        store.save(agent.session)
        return cont.message

    if notify is None:
        def notify(line):
            _out(f"  {line}", style="magenta")
    goals.run_loop(agent, run.message.content or "", notify, on_event, run_turn=run_continuation)

    tools_this = getattr(agent, "tools_used", 0) - tools_before
    trigger = ("many_tools" if tools_this >= 8 else
               "long_session" if len(agent.session.messages) >= 40 else None)
    if trigger and (tip := maybe_tip(agent.config, trigger)):
        notify(tip)
    store.save(agent.session)
    _maybe_print_status_footer(agent, on_event)
    return run.message


# --------------------------------------------------------------------------- #
# Slash commands
# --------------------------------------------------------------------------- #
def session_recap(session) -> list[str]:
    """Local recap of the conversation so far — counts, top tools, files touched,
    last prompt/reply. Computed from in-memory messages; no LLM call."""
    from collections import Counter
    users = [m for m in session.messages if m.role == "user"]
    assistants = [m for m in session.messages if m.role == "assistant" and m.content]
    tools = [m for m in session.messages if m.role == "tool"]
    if not users and not tools:
        return []
    lines = ["— recap —",
             f"turns: {len(users)} user / {len(assistants)} assistant · {len(tools)} tool results"]
    names = Counter(getattr(m, "name", None) for m in tools if getattr(m, "name", None))
    if names:
        lines.append("top tools: " + ", ".join(f"{n}×{c}" for n, c in names.most_common(5)))
    files = []
    for m in reversed(session.messages):
        if m.role == "assistant":
            for tc in (getattr(m, "tool_calls", None) or []):
                p = (getattr(tc, "arguments", None) or {}).get("path")
                if p and p not in files:
                    files.append(p)
        if len(files) >= 5:
            break
    if files:
        lines.append("recent files: " + ", ".join(files[:5]))
    if users:
        lines.append(f"last prompt: {users[-1].content[:120]}")
    if assistants:
        lines.append(f"last reply: {assistants[-1].content[:120]}")
    return lines


def _session_choices(store: SessionStore, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_session(sid: str | None, *, snippet: str = "") -> None:
        if not sid or sid in seen:
            return
        sess = store.load(sid)
        if not sess:
            return
        seen.add(sess.id)
        rows.append({
            "id": sess.id,
            "title": sess.title,
            "updated_at": sess.updated_at,
            "parent_id": sess.parent_id,
            "summary": sess.meta.get("summary", ""),
            "last_run_id": sess.meta.get("last_run_id", ""),
            "last_trace_id": sess.meta.get("last_trace_id") or sess.meta.get("trace_id", ""),
            "snippet": snippet,
            "messages": len(sess.messages),
        })

    if query:
        for row in store.search(query, limit=limit):
            add_session(str(row.get("id") or ""))
        if len(rows) < limit:
            for hit in store.search_messages(query, limit=limit):
                add_session(str(hit.get("session") or ""), snippet=str(hit.get("snippet") or ""))
                if len(rows) >= limit:
                    break
    else:
        for row in store.list(limit):
            add_session(str(row.get("id") or ""))
    return rows[:limit]


def _remember_session_choices(agent: Any, rows: list[dict[str, Any]]) -> None:
    try:
        agent._terminal_session_choices = [str(r["id"]) for r in rows]
    except Exception:  # noqa: BLE001
        pass


def _session_picker_lines(rows: list[dict[str, Any]], query: str = "") -> list[str]:
    if not rows:
        return [f"no sessions match {query!r}" if query else "no sessions yet"]
    title = f"sessions matching {query!r}:" if query else "recent sessions:"
    lines = [title]
    for i, row in enumerate(rows, 1):
        updated = str(row.get("updated_at") or "").replace("T", " ")[:16]
        branch = " branch" if row.get("parent_id") else ""
        msgs = row.get("messages", 0)
        refs = []
        if row.get("last_run_id"):
            refs.append(f"run {str(row['last_run_id'])[:12]}")
        if row.get("last_trace_id"):
            refs.append(f"trace {str(row['last_trace_id'])[:12]}")
        ref_text = " · " + " · ".join(refs) if refs else ""
        lines.append(
            f"  {i:>2}. {str(row['id'])[:14]:<14} {updated:<16} "
            f"{row.get('title') or row['id']} ({msgs} msgs{branch}){ref_text}"
        )
        detail = row.get("snippet") or row.get("summary")
        if detail:
            lines.append(f"      {str(detail)[:140]}")
    lines.append("resume with /resume <number|id|title>; fork with /branch [title]")
    return lines


def _resolve_session_ref(store: SessionStore, agent: Any, ref: str) -> Session | None:
    ref = ref.strip()
    if ref.isdigit():
        choices = list(getattr(agent, "_terminal_session_choices", []) or [])
        idx = int(ref) - 1
        if 0 <= idx < len(choices):
            return store.load(choices[idx])
    sess = store.load(ref)
    if sess and sess.id == ref:
        return sess
    title_match = store.resolve_title_to_tip(ref)
    if title_match:
        return title_match
    if sess:
        return sess
    matches = _session_choices(store, ref, limit=2)
    if len(matches) == 1:
        return store.load(matches[0]["id"])
    return None


def _switch_session(agent: Any, session: Session, *, reason: str) -> None:
    old = getattr(agent, "session", None)
    from ..surface import _retarget_agent

    _retarget_agent(agent, session=session, reason=reason, reset=(reason == "manual_new"))
    apply_session_runtime(agent)
    try:
        agent.refresh_volatile()
    except Exception:  # noqa: BLE001
        pass
    if old is not None and getattr(old, "id", None) != getattr(session, "id", None):
        try:
            from ..agent.context_engine import call_hook, get_engine

            call_hook(get_engine(agent.config), "on_session_switch", agent, old, session, reason=reason)
        except Exception:  # noqa: BLE001
            pass


def _parse_model_override(arg: str) -> tuple[str, str]:
    raw = arg.strip()
    if "/" in raw:
        provider, model = raw.split("/", 1)
        return provider.strip(), model.strip()
    return "", raw


def quick_memory(raw: str, agent: Agent) -> bool:
    """Quick-memory shortcut: a line starting with ``#`` is saved straight to memory with no
    model turn. ``# user: <fact>`` routes to the USER profile instead of MEMORY. Returns True
    when the line was a quick-memory directive (so the REPL skips the model)."""
    if not raw.startswith("#"):
        return False
    body = raw[1:].strip()
    target = "memory"
    if body.lower().startswith("user:"):
        target, body = "user", body[5:].strip()
    if not body:
        _out("  # <fact>   — remember a fact instantly  ·  # user: <fact> for your profile",
             style="yellow")
        return True
    mem = getattr(agent, "memory", None)
    store = getattr(mem, "store", None) if mem is not None else None
    if store is None:
        _out("  memory is disabled — set memory.enabled: true to use #", style="yellow")
        return True
    try:
        result = store.add(target, body)
    except Exception as e:  # noqa: BLE001
        _out(f"  couldn't save memory: {e}", style="red")
        return True
    # store.add() returns a human message — surface it honestly instead of always
    # claiming success: it may refuse an injection pattern, dedup, or hit the limit.
    low = result.lower()
    if low.startswith("refused") or "limit" in low or "drift" in low:
        _out(f"  ⚠ {result}", style="yellow")
    elif low.startswith("already"):
        _out(f"  ↺ {result}", style="bright_black")
    else:
        _out(f"  ✓ remembered → {'USER.md' if target == 'user' else 'MEMORY.md'}  ·  {result}",
             style="green")
    return True


def handle_slash(
    cmd: str,
    agent: Agent,
    *,
    runner: SurfaceRunner | None = None,
    store: SessionStore | None = None,
    surface: str = "repl",
    on_event: Callable[[dict], None] | None = None,
) -> str:
    """Return 'break' to exit the REPL, else ''. """
    parts = cmd.strip().split()
    name = parts[0].lower()
    arg = " ".join(parts[1:])

    if name in ("/quit", "/exit"):
        return "break"
    if name == "/help":
        for line in slash_help_lines(arg):
            _out(line)
        _out("Anything else is sent to the agent.")
    elif name == "/yolo":
        eng = agent.permissions
        on = getattr(eng, "_mode_override", None) == "full"
        eng._mode_override = None if on else "full"
        _out("🟢 exec mode restored (approvals on)." if on
             else "⚠ YOLO ON — all tool approvals bypassed for this session "
                  "(hardline blocklist still applies). /yolo again to turn off.")
    elif name == "/model":
        if not arg:
            controls = agent.session.meta.get("runtime_controls") or {}
            stored = ""
            if controls.get("provider") or controls.get("model"):
                stored = f" · session override: {controls.get('provider') or '*'}" \
                         f"/{controls.get('model') or '*'}"
            _out(f"current: {agent.provider.describe()}{stored}")
        else:
            provider, model = _parse_model_override(arg)
            if not model:
                _out("usage: /model <model> or /model <provider>/<model>")
            else:
                from ..providers import registry
                controls = session_runtime_controls(agent.session)
                target_provider = provider or controls.get("provider") or agent.config.get("model.provider", "")
                validation = registry.validate_model_choice(target_provider, model, agent.config)
                warning = registry.model_validation_message(validation)
                if not validation.get("ok", True):
                    _out(warning, style="yellow")
                    return ""
                updates = {"model": model}
                if provider:
                    updates["provider"] = provider
                remember_session_runtime(agent, **updates)
                apply_session_runtime(agent)
                try:
                    agent.refresh_volatile()
                except Exception:  # noqa: BLE001
                    pass
                if store is not None:
                    store.save(agent.session)
                label = f"{provider}/" if provider else ""
                _out(f"model for this session → {label}{model}", style="green")
                if warning and validation.get("warning"):
                    _out(f"warning: {warning}", style="yellow")
    elif name == "/provider":
        controls = session_runtime_controls(agent.session)
        if not arg:
            current = controls.get("provider") or getattr(agent.provider, "name", "") \
                or agent.config.get("model.provider", "")
            _out(f"provider: {current}\nSwitch for this session with /provider <name>.")
        else:
            from ..providers import registry
            model = controls.get("model") or getattr(agent.provider, "model", "") \
                or agent.config.get("model.default", "")
            validation = registry.validate_model_choice(arg, model, agent.config)
            warning = registry.model_validation_message(validation)
            if not validation.get("ok", True):
                _out(warning, style="yellow")
                return ""
            remember_session_runtime(agent, provider=arg)
            apply_session_runtime(agent)
            try:
                agent.refresh_volatile()
            except Exception:  # noqa: BLE001
                pass
            if store is not None:
                store.save(agent.session)
            _out(f"provider for this session → {arg}", style="green")
            if warning and validation.get("warning"):
                _out(f"warning: {warning}", style="yellow")
    elif name == "/status":
        _out(f"provider: {agent.provider.describe()}")
        _out(f"session: {agent.session.id} ({len(agent.session.messages)} msgs)")
        ctx = _context_window(agent)
        if ctx["window"]:
            _out(
                "context: "
                f"{ctx['used']:,}/{ctx['window']:,} tokens "
                f"({ctx['percent']}%, {ctx['remaining']:,} remaining)"
            )
        else:
            _out(f"context: {ctx['used']:,} estimated tokens")
        u = agent.budget.usage
        _out(
            f"usage: input {u.input_tokens:,} · output {u.output_tokens:,} · "
            f"cache read {u.cache_read:,} · cache write {u.cache_write:,}"
        )
        _out(
            f"reasoning: display {agent.config.get('display.reasoning', 'summary')} · "
            f"effort {getattr(agent, 'reasoning', 'off')}"
        )
        _out(
            f"permissions: exec_mode {agent.config.get('tools.exec_mode')} · "
            f"toolsets {', '.join(agent.config.get('tools.toolsets', []) or []) or 'none'}"
        )
        comps = agent.session.meta.get("compactions") or []
        if comps:
            saved = sum(c["tokens_before"] - c["tokens_after"] for c in comps)
            _out(f"compactions: {len(comps)} (~{saved:,} tokens reclaimed; {comps[-1]['reason']})")
        from .. import goals
        g = goals.get(agent.session)
        if g:
            _out(goals.status_line(g), style="cyan")
        run_id, trace_id, turn_id = _run_refs(agent)
        if run_id or trace_id:
            refs = []
            if run_id:
                refs.append(f"run {run_id}")
            if trace_id:
                refs.append(f"trace {trace_id}")
            if turn_id:
                refs.append(f"turn {turn_id}")
            _out("last turn: " + " · ".join(refs), style="bright_black")
        for line in session_recap(agent.session):
            _out(line, style="bright_black")
        _maybe_print_status_footer(agent, on_event or Renderer(agent.config))
    elif name == "/think":
        level = arg or "medium"
        if level not in ("off", "minimal", "low", "medium", "high", "xhigh"):
            _out("usage: /think off|minimal|low|medium|high|xhigh")
        else:
            agent.reasoning = level
            remember_session_runtime(agent, reasoning_effort=level)
            if store is not None:
                store.save(agent.session)
            _out(f"reasoning effort → {level}", style="green")
    elif name == "/reasoning":
        modes = {"off", "summary", "live"}
        efforts = {"minimal", "low", "medium", "high", "xhigh"}
        if not arg:
            _out(f"display: {agent.config.get('display.reasoning', 'summary')} · "
                 f"effort: {getattr(agent, 'reasoning', 'off')}")
        elif arg in modes:
            agent.config.data.setdefault("display", {})["reasoning"] = arg
            remember_session_runtime(agent, reasoning_display=arg)
            # Showing reasoning needs the model to actually produce it — if effort is
            # off there's nothing to stream, so turn it on (matches "it just works").
            note = ""
            if arg != "off" and getattr(agent, "reasoning", "off") == "off":
                agent.reasoning = "medium"
                remember_session_runtime(agent, reasoning_effort="medium")
                note = " · effort → medium (was off; nothing to show otherwise)"
            if store is not None:
                store.save(agent.session)
            live_note = ""
            if arg == "live":
                live_note = " · next turn will show provider reasoning when emitted"
            _out(f"reasoning display → {arg}{note}{live_note}", style="green")
        elif arg in efforts or arg in {"off", "none"}:
            value = "off" if arg == "none" else arg
            agent.reasoning = value
            remember_session_runtime(agent, reasoning_effort=value)
            if store is not None:
                store.save(agent.session)
            _out(f"reasoning effort → {value}", style="green")
        else:
            _out("usage: /reasoning off|none|summary|live|minimal|low|medium|high|xhigh")
    elif name == "/context":
        for line in _render_context(agent):
            _out(line)
    elif name == "/busy":
        mode = arg or agent.config.get("gateway.busy_mode", "queue")
        if mode not in ("queue", "steer", "interrupt"):
            _out("usage: /busy queue|steer|interrupt")
        else:
            agent.config.data.setdefault("gateway", {})["busy_mode"] = mode
            remember_session_runtime(agent, busy_mode=mode)
            if store is not None:
                store.save(agent.session)
            _out(f"busy input mode → {mode}", style="green")
    elif name == "/tools":
        for t in agent.registry.all():
            g = f" [{','.join(t.groups)}]" if t.groups else ""
            _out(f"  {t.name}{g} — {t.description.splitlines()[0]}")
    elif name == "/skills":
        if agent.skills:
            _out(agent.skills.index_block() or "(no skills installed)")
    elif name == "/skill":
        sub = parts[1].lower() if len(parts) > 1 else "save"
        if sub == "new":
            if len(parts) < 3:
                _out("usage: /skill new <name> [description]")
            else:
                sname = parts[2]
                desc = " ".join(parts[3:]) or f"{sname} skill"
                path = agent.skills.create(sname, desc, _skill_scaffold(sname, desc))
                agent.refresh_volatile()
                _out(f"✓ created scaffold → {path}\n  edit the Procedure section, then it "
                     "loads automatically when its trigger matches.", style="green")
        else:  # /skill (save): auto-write a skill from what we just did
            from .. import learn
            _out("extracting a reusable skill from this session…", style="cyan")
            try:
                found = learn.review_session(agent.config, agent.session.id)
                made = [learn.apply_candidate(c["id"], agent.config)
                        for c in found if c.get("type") == "skill"]
                if made:
                    agent.refresh_volatile()
                    _out("\n".join("✓ " + m for m in made), style="green")
                else:
                    _out("nothing reusable to save yet — try after solving a concrete task.")
            except Exception as e:  # noqa: BLE001
                _out(f"skill extraction needs a working provider/key: {e}", style="yellow")
    elif name == "/memory":
        if agent.memory:
            _out("# MEMORY\n" + (agent.memory.store.raw("memory") or "(empty)"))
            _out("# USER\n" + (agent.memory.store.raw("user") or "(empty)"))
    elif name == "/secret":
        sub = parts[1].lower() if len(parts) > 1 else ""
        key = parts[2].strip() if len(parts) > 2 else ""
        if sub != "set" or not key:
            _out("usage: /secret set <ENV_KEY>")
        else:
            try:
                result = make_secret_capture()(key, f"Enter {key}", {"slash": "/secret"})
            except ValueError as exc:
                _out(str(exc), style="red")
            else:
                if result.get("skipped"):
                    _out(f"secret setup skipped for {key}", style="yellow")
                else:
                    _out(f"secret stored as {key}", style="green")
    elif name == "/usage":
        u = agent.budget.usage
        _out(f"tokens this session — input: {u.input_tokens:,}  output: {u.output_tokens:,}", style="cyan")
        from .. import ratelimit
        rl = ratelimit.summary()
        if rl:
            _out("  " + rl, style="bright_black")
    elif name == "/compress":
        # /compress            — standard boundaries
        # /compress here [N]   — keep only the last N exchanges verbatim (default 2)
        # /compress focus <t>  — weight the summary toward topic <t>
        from ..agent.loop import compact_now
        preserve_last, focus = None, ""
        words = arg.split() if arg else []
        if words and words[0] == "here":
            n = int(words[1]) if len(words) > 1 and words[1].isdigit() else 2
            preserve_last = max(2, n * 2)        # an exchange ≈ user + assistant
        elif words and words[0] == "focus":
            focus = " ".join(words[1:])
        before = len(agent.session.messages)
        before_session = agent.session.id
        emit = on_event or (lambda _event: None)

        def action(control_emit):
            compact_now(
                agent,
                agent.session,
                control_emit,
                reason="manual_context_compression",
                focus=focus,
                preserve_last=preserve_last,
            )
            after = len(agent.session.messages)
            tokens = ""
            recs = agent.session.meta.get("compactions") or []
            if recs:
                latest = recs[-1]
                saved = int(latest.get("tokens_before", 0) or 0) - int(
                    latest.get("tokens_after", 0) or 0
                )
                tokens = f" (~{saved:,} tokens reclaimed)"
            moved = f" · session {agent.session.id}" if agent.session.id != before_session else ""
            return f"context compressed: {before} → {after} messages{tokens}{moved}."

        run = run_control_action(
            agent,
            action,
            config=agent.config,
            session=agent.session,
            surface=surface,
            kind="compaction",
            title="manual context compression",
            prompt=cmd,
            data={"focus": focus, "preserve_last": preserve_last},
            on_event=emit,
        )
        _record_terminal_run(agent, run)
        if store is not None:
            store.save(agent.session)
        _out(run.text, style="yellow")
    elif name == "/personality":
        if arg:
            agent.config.data.setdefault("agent", {})["personality"] = arg
            agent.refresh_volatile()
            _out(f"personality → {arg}", style="green")
        else:
            _out("usage: /personality <name>")
    elif name == "/background":
        if arg:
            from ..background import get_manager

            def _announce(task) -> None:
                try:
                    from ..tools.agentic import _notify_delegation
                    _notify_delegation(agent, task.prompt, task.result or task.error)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from ..agent.wakeups import add_wakeup
                    add_wakeup("background", f"{task.id}: {task.prompt[:80]}",
                               task.result or task.error,
                               session_key=str(getattr(getattr(agent, "session", None), "id", "") or ""))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    text = (f"background task done:\n{task.result}" if task.status == "done"
                            else f"background task failed: {task.error}")
                    from ..eventbus import BUS
                    BUS.publish({"type": "background_done", "platform": "cli",
                                 "chat_id": None, "text": text[:2000]})
                except Exception:  # noqa: BLE001
                    pass
            tid = get_manager().spawn(
                agent.config,
                arg,
                cwd=getattr(agent, "cwd", None),
                on_done=_announce,
                parent_session=agent.session,
            )
            _out(f"started background task {tid}", style="green")
        else:
            _out("usage: /background <prompt>")
    elif name == "/tasks":
        from ..background import get_manager
        tasks = get_manager().list()
        if not tasks:
            _out("(no background tasks)")
        for t in tasks:
            _out(f"  {t['id']}  [{t['status']}]  {t['prompt']}  {t['result_preview']}")
    elif name == "/agents":
        from ..background import get_manager
        tasks = get_manager().list()
        if not tasks:
            _out("(no background agents)")
        for t in tasks:
            _out(f"  {t['id']}  [{t['status']}]  {t['prompt'][:80]}")
    elif name == "/kanban":
        from argparse import Namespace

        from ..kanban import cmd_kanban
        parts2 = arg.split(maxsplit=1)
        action = (parts2[0] or "list") if parts2 else "list"
        rest = parts2[1].strip() if len(parts2) > 1 else ""
        ns = Namespace(action=action, title=None, id=None, body=None, priority=None,
                       status=None, assignee=None, worker=None, parent=None, child=None,
                       tenant=None, workspace=None, reason=None, summary=None, note=None,
                       no_spawn=False)
        # the single free-form arg is a title for create/decompose, otherwise an id
        if action in ("create", "decompose"):
            ns.title = rest or None
        else:
            ns.id = rest or None
        cmd_kanban(ns, agent.config)
    elif name == "/handoff":
        parts = (arg or "").split()
        if len(parts) < 2:
            _out("usage: /handoff <platform> <chat_id>  (e.g. /handoff telegram 123456789)")
        else:
            platform, chat_id = parts[0], parts[1]
            SessionStore().save(agent.session)              # make history adoptable
            from ..handoff import set_handoff
            set_handoff(platform, chat_id, agent.session.id)
            try:
                from ..gateway.queue import DeliveryQueue
                DeliveryQueue().enqueue(platform, chat_id,
                    f"\u25b6 Session '{agent.session.title or agent.session.id}' handed off "
                    "from the CLI \u2014 send a message here to continue it.")
            except Exception:  # noqa: BLE001
                pass
            _out(f"\u2713 handoff queued: the next message from {platform}:{chat_id} continues "
                 "this session (gateway must be running).", style="green")
    elif name == "/diff":
        from ..checkpoints import CheckpointStore
        d = CheckpointStore(agent.cwd).diff(arg or None)
        _out(d or "(no changes since the last checkpoint)")
    elif name == "/rollback":
        from ..checkpoints import CheckpointStore
        restored = CheckpointStore(agent.cwd).rollback(arg or None)
        _out(f"rolled back {len(restored)} file(s): {', '.join(restored) or '(none)'}", style="yellow")
    elif name == "/retry":
        # drop the last assistant turn (+ its tool messages) and re-run the last user msg
        msgs = agent.session.messages
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "user"), None)
        if last_user is None:
            _out("nothing to retry")
        else:
            prompt = msgs[last_user].content
            agent.session.messages = msgs[:last_user]
            if runner is not None and store is not None:
                run_terminal_turn(
                    prompt,
                    agent,
                    runner,
                    store,
                    surface=surface,
                    on_event=on_event or Renderer(agent.config),
                    add_profile_directive=False,
                )
            else:
                retry_renderer = Renderer(agent.config)
                agent.run(prompt, retry_renderer)
                _maybe_print_status_footer(agent, retry_renderer)
    elif name == "/undo":
        msgs = agent.session.messages
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "user"), None)
        if last_user is None:
            _out("nothing to undo")
        else:
            agent.session.messages = msgs[:last_user]
            active_store = store or getattr(agent, "store", None)
            if active_store is not None:
                active_store.save(agent.session)
            _out(f"undid last turn ({len(msgs) - last_user} messages removed)", style="yellow")
    elif name == "/learn":
        from ..learn import review_session
        try:
            found = review_session(agent.config, agent.session.id)
            _out(f"proposed {len(found)} candidate(s); review with `aegis learn list`", style="green")
        except Exception as e:  # noqa: BLE001
            _out(f"learn failed: {e}", style="red")
    elif name == "/title":
        if not arg.strip():
            _out(f"current title: {agent.session.title or '(untitled)'}\nusage: /title <new title>")
        else:
            agent.session.title = arg.strip()
            agent.session.meta["title_locked"] = True   # don't auto-overwrite a hand-set title
            (store or SessionStore()).save(agent.session)
            _out(f"session renamed → {agent.session.title}", style="green")
    elif name == "/save":
        out = Path(arg).expanduser() if arg else (agent.cwd / f"{agent.session.id}.md")
        lines = [f"# {agent.session.title}\n"]
        for m in agent.session.messages:
            if m.role in ("user", "assistant") and m.content:
                lines.append(f"\n## {m.role}\n\n{m.content}")
        out.write_text("\n".join(lines), encoding="utf-8")
        _out(f"saved session → {out}", style="green")
    elif name == "/sessions":
        active_store = store or SessionStore()
        rows = _session_choices(active_store, arg, limit=20)
        _remember_session_choices(agent, rows)
        for line in _session_picker_lines(rows, arg):
            _out(line)
    elif name == "/resume":
        active_store = store or SessionStore()
        if not arg:
            rows = _session_choices(active_store, limit=20)
            _remember_session_choices(agent, rows)
            for line in _session_picker_lines(rows):
                _out(line)
        else:
            sess = _resolve_session_ref(active_store, agent, arg)
            if not sess:
                _out("session not found; try /sessions <query>", style="yellow")
            else:
                _switch_session(agent, sess, reason="manual_resume")
                active_store.save(agent.session)
                run_id, trace_id, _turn_id = _run_refs(agent)
                refs = f" · run {run_id[:12]}" if run_id else ""
                refs += f" · trace {trace_id[:12]}" if trace_id else ""
                _out(f"resumed {sess.id} ({sess.title}){refs}", style="green")
    elif name == "/branch":
        active_store = store or SessionStore()
        title = arg.strip() or agent.session.title
        child = active_store.fork(agent.session)
        if title:
            child.title = title
        child.meta["branch_surface"] = surface
        child.meta["branch_reason"] = "manual_terminal_branch"
        active_store.save(child)
        parent_trace = (agent.session.meta.get("last_trace_id") or agent.session.meta.get("trace_id") or "")
        _switch_session(agent, child, reason="manual_branch")
        suffix = f" · trace {str(parent_trace)[:12]}" if parent_trace else ""
        _out(f"branched session → {child.id} (from {child.parent_id}){suffix}", style="green")
    elif name == "/trace":
        try:
            from ..tracing import TraceStore
            store = TraceStore.from_config(agent.config)
            if arg:
                trace = store.get_trace(arg)
                if not trace:
                    _out("trace not found", style="yellow")
                else:
                    _out(f"trace {trace['trace_id']} · {trace['status']} · "
                         f"{trace['span_count']} spans", style="cyan")
                    for r in trace.get("spans", [])[:20]:
                        _out(f"  {r['span_id'][:14]:<14} {r['kind']:<16} {r['status']:<9} "
                             f"{r.get('tool_name') or r.get('model') or ''}")
            else:
                rows = store.list_traces(session_id=agent.session.id, limit=10)
                if not rows:
                    _out("(no traces for this session yet)")
                for r in rows:
                    _out(f"  {r['trace_id']}  {r.get('status', '')}  "
                         f"{r.get('span_count', r.get('spans', 0))} spans")
        except Exception as e:  # noqa: BLE001
            _out(f"trace unavailable: {e}", style="yellow")
    elif name == "/evals":
        try:
            from ..evals import EvalStore
            store = EvalStore.from_config(agent.config)
            if arg:
                run = store.get_run(arg)
                if not run:
                    _out("eval run not found", style="yellow")
                else:
                    _out(f"{run['id']}  {run['suite']}  {run['passed']}/{run['total']}  "
                         f"score={run['score']}", style="cyan")
                    for result in run.get("results", [])[:20]:
                        mark = "✓" if result.get("passed") else "✗"
                        _out(f"  {mark} {result.get('case') or result.get('id')}  "
                             f"score={result.get('score')}")
            else:
                rows = store.list_runs(limit=10)
                if not rows:
                    _out("(no eval runs yet)")
                for r in rows:
                    _out(f"  {r['id']}  {r['suite']}  {r['passed']}/{r['total']}  {r['created_at']}")
        except Exception as e:  # noqa: BLE001
            _out(f"evals unavailable: {e}", style="yellow")
    elif name in ("/new", "/clear"):
        active_store = store or SessionStore()
        new_session = Session.create()
        end = getattr(agent, "end_session", None)
        if callable(end):
            try:
                end()
            except Exception:  # noqa: BLE001
                pass
        _switch_session(agent, new_session, reason="manual_new")
        active_store.save(agent.session)
        # thaw the memory snapshot: facts saved THIS process
        n = len(agent.memory.store.entries("user")) if agent.memory else 0
        extra = f" · {n} user fact(s) loaded" if n else ""
        _out(f"started new session {agent.session.id}{extra}", style="green")
    else:
        _out(f"unknown command {name}; /help for list", style="yellow")
    return ""


def _process_notification_meta(event: dict) -> dict:
    return {
        "synthetic": "process_notification",
        "process_event_type": event.get("type", ""),
        "process_session_id": event.get("session_id", ""),
        "process_session_key": event.get("session_key", ""),
    }


def drain_process_notification_events(max_events: int | None = None) -> list[tuple[dict, str]]:
    try:
        from ..tools.process_registry import process_registry

        events = process_registry.drain_notifications(max_events=max_events)
    except Exception:  # noqa: BLE001
        return []
    if events:
        try:
            from ..agent import wakeups

            wakeups.drain_wakeups(source="process")
        except Exception:  # noqa: BLE001
            pass
    return events


def drain_process_notifications(
    agent: Any,
    runner: SurfaceRunner,
    store: SessionStore,
    *,
    surface: str,
    on_event: Callable[[dict], None],
    notify: Callable[[str], None] | None = None,
    max_turns: int = 10,
) -> int:
    count = 0
    for event, text in drain_process_notification_events(max_events=max_turns):
        if notify is not None:
            notify(f"background process notification: {event.get('session_id', '')}")
        run_terminal_turn(
            text,
            agent,
            runner,
            store,
            surface=surface,
            on_event=on_event,
            notify=notify,
            add_profile_directive=False,
            meta=_process_notification_meta(event),
            include_wakeups=False,
        )
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def run_once(config: Config, prompt: str, *, model=None, provider_name=None,
             session: Session | None = None, store: SessionStore | None = None, auto=False,
             images: list[str] | None = None, surface: str = "cli",
             meta: dict | None = None) -> str:
    from ..types import Message
    store = store or SessionStore()
    session = session or Session.create()
    runner = SurfaceRunner(config, store=store, include_mcp=True)
    user_input = Message.user(prompt, images=images) if images else prompt
    renderer = Renderer(config)
    result = runner.run_prompt(
        user_input,
        session=session,
        model=model,
        provider_name=provider_name,
        approver=make_approver(auto),
        asker=make_asker(),
        secret_capture=make_secret_capture(),
        surface=surface,
        platform="cli",
        meta=meta,
        on_event=renderer,
    )
    _maybe_print_status_footer(result.agent, renderer)
    return result.text


def interactive(config: Config, *, model=None, provider_name=None,
                session: Session | None = None, store: SessionStore | None = None, auto=False) -> None:
    store = store or SessionStore()
    session = session or Session.create()
    runner = SurfaceRunner(config, store=store, include_mcp=True)
    agent = runner.make_agent(
        session=session,
        model=model,
        provider_name=provider_name,
        approver=make_approver(auto),
        asker=make_asker(),
        secret_capture=make_secret_capture(),
        include_mcp=True,
    )
    store.save(agent.session)
    banner(agent)

    ps = None
    if _prompt_session_supported():
        from ..config import logs_dir
        ps = PromptSession(
            history=FileHistory(str(logs_dir() / "repl_history")),
            completer=make_slash_completer(),
            bottom_toolbar=((lambda: _bottom_toolbar(agent))
                            if config.get("display.status_bar", True) else None),
        )

    try:
        while True:
            try:
                user = _read_repl_input(ps, _prompt_message(agent))
            except (EOFError, KeyboardInterrupt):
                _out("\nbye.")
                if config.get("learn.auto") and len(agent.session.messages) > 4:
                    try:
                        from ..learn import review_session
                        found = review_session(config, agent.session.id)
                        if found:
                            _out(f"💡 learned {len(found)} candidate(s); `aegis learn list` to review.",
                                 style="magenta")
                    except Exception:  # noqa: BLE001
                        pass
                break
            user = user.strip()
            if not user:
                continue
            if quick_memory(user, agent):    # '#' saves a memory instantly, no model turn
                continue
            if user.startswith("/ultracode"):
                uc_prompt = handle_ultracode_command(user, agent)
                if not uc_prompt:
                    continue
                user = uc_prompt   # run the full autonomous loop
            elif user.startswith("/architect"):
                arch_prompt = handle_architect_command(user, agent)
                if not arch_prompt:
                    continue
                user = arch_prompt   # plan (strong model) then implement (this model)
            elif user.startswith("/spec"):
                spec_prompt = handle_spec_command(user, agent)
                if not spec_prompt:
                    continue
                user = spec_prompt   # /spec implement -> execute the persisted spec
            elif user.startswith("/gstack"):
                gs_prompt = handle_gstack_command(user)
                if not gs_prompt:
                    continue
                user = gs_prompt   # run the full think→…→reflect sprint in one turn
            elif user.startswith(("/plan", "/proceed")):
                plan_prompt = handle_plan_command(user, agent)
                if not plan_prompt:
                    continue
                user = plan_prompt   # run the planning/execution turn
            elif user.startswith(("/goal", "/subgoal")):
                goal_prompt = handle_goal_command(user, agent, store)
                if not goal_prompt:
                    continue
                user = goal_prompt   # run the new goal as this turn
            elif user.startswith("/") and agent.skills and agent.skills.slash_invocation_exists(user):
                pass                  # let Agent.run load /<skill-name> like AEGIS
            elif user.startswith("/"):
                renderer = Renderer(config)
                if handle_slash(user, agent, runner=runner, store=store,
                                surface="repl", on_event=renderer) == "break":
                    break
                if user.split(maxsplit=1)[0] in {
                    "/model", "/provider", "/think", "/reasoning", "/busy",
                    "/resume", "/branch", "/new", "/compact", "/compress",
                }:
                    _maybe_print_status_footer(agent, renderer)
                continue
            try:
                renderer = Renderer(config)
                run_terminal_turn(user, agent, runner, store, surface="repl", on_event=renderer)
            except KeyboardInterrupt:
                agent.cancel()   # stop the loop at the next safe point; discard partial work
                _out("\n  ⏹ interrupted — stopped this turn (your session is intact)", style="yellow")
            store.save(agent.session)
            drain_process_notifications(
                agent,
                runner,
                store,
                surface="repl",
                on_event=renderer,
                notify=lambda line: _out(f"  {line}", style="magenta"),
            )
    finally:
        end = getattr(agent, "end_session", None)
        if callable(end):
            try:
                end()
            except Exception:  # noqa: BLE001
                pass
