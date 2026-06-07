"""Interactive REPL + one-shot runner with streaming output.

Uses rich for rendering and prompt_toolkit for input when available, and falls
back to plain stdin/stdout otherwise so the harness runs anywhere.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

from .. import __version__
from ..agent.agent import Agent
from ..config import Config
from ..session import Session, SessionStore

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

_approve_lock = threading.Lock()

SLASH = ["/help", "/model", "/tools", "/skills", "/memory", "/usage", "/compress",
         "/background", "/tasks", "/rollback", "/personality",
         "/sessions", "/new", "/clear", "/quit", "/exit"]


def _out(text: str = "", style: str | None = None) -> None:
    if _console:
        _console.print(text, style=style)
    else:
        print(text)


def _raw(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


_AT_RE = re.compile(r"@([^\s]+)")


def expand_references(text: str, cwd: Path) -> str:
    """Expand `@path` tokens by appending the referenced file's contents."""
    extras = []
    for m in _AT_RE.finditer(text):
        p = Path(m.group(1)).expanduser()
        if not p.is_absolute():
            p = cwd / p
        if p.is_file():
            try:
                body = p.read_text(encoding="utf-8", errors="replace")[:20_000]
                extras.append(f'\n\n<file path="{m.group(1)}">\n{body}\n</file>')
            except Exception:  # noqa: BLE001
                pass
    return text + "".join(extras)


def make_approver(auto: bool = False):
    def approver(prompt_text: str) -> bool:
        if auto:
            return True
        with _approve_lock:
            try:
                ans = input(f"\n  ⚠ {prompt_text} [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            return ans in ("y", "yes")
    return approver


class Renderer:
    """Turns agent events into terminal output."""

    def __init__(self):
        self._streaming = False

    def __call__(self, e: dict) -> None:
        t = e["type"]
        if t == "assistant_delta":
            self._streaming = True
            _raw(e["text"])
        elif t == "assistant_message":
            if self._streaming:
                _raw("\n")
                self._streaming = False
            elif e.get("text"):
                _out(e["text"])
        elif t == "tool_start":
            args = e.get("args", {})
            detail = args.get("command") or args.get("path") or args.get("url") or args.get("query") or ""
            _out(f"  ⚙ {e['name']}({str(detail)[:80]})", style="cyan")
        elif t == "tool_result":
            style = "red" if e.get("is_error") else "green"
            _out(f"    ↳ {e['summary']}", style=style)
        elif t == "compacting":
            _out("  … compacting context …", style="yellow")
        elif t == "budget_exhausted":
            _out("  … step limit reached; summarizing …", style="yellow")
        elif t == "error":
            _out(f"  ✖ {e['message']}", style="red")
        elif t == "final":
            if self._streaming:
                _raw("\n")
                self._streaming = False


def _status_line(agent: Agent) -> str:
    from ..agent.compaction import estimated_tokens
    u = agent.budget.usage
    used = estimated_tokens(agent.session.messages)
    fill = int(100 * used / max(1, agent.provider.context_length))
    return f"  [{agent.provider.model} · ctx {fill}% · tokens in {u.input_tokens:,} out {u.output_tokens:,}]"


def banner(agent: Agent) -> None:
    text = (f"AEGIS v{__version__}\n"
            f"provider: {agent.provider.describe()}\n"
            f"cwd: {agent.cwd}\n"
            f"session: {agent.session.id}\n"
            f"type /help for commands, /quit to exit")
    if _console:
        _console.print(Panel.fit(text, title="agent harness", border_style="magenta"))
    else:
        print("=" * 60 + f"\n{text}\n" + "=" * 60)


# --------------------------------------------------------------------------- #
# Slash commands
# --------------------------------------------------------------------------- #
def handle_slash(cmd: str, agent: Agent) -> str:
    """Return 'break' to exit the REPL, else ''. """
    parts = cmd.strip().split()
    name = parts[0].lower()
    arg = " ".join(parts[1:])

    if name in ("/quit", "/exit"):
        return "break"
    if name == "/help":
        _out("Commands: " + ", ".join(SLASH))
        _out("Anything else is sent to the agent.")
    elif name == "/model":
        _out(f"current: {agent.provider.describe()}")
    elif name == "/tools":
        for t in agent.registry.all():
            g = f" [{','.join(t.groups)}]" if t.groups else ""
            _out(f"  {t.name}{g} — {t.description.splitlines()[0]}")
    elif name == "/skills":
        if agent.skills:
            _out(agent.skills.index_block() or "(no skills installed)")
    elif name == "/memory":
        if agent.memory:
            _out("# MEMORY\n" + (agent.memory.store.raw("memory") or "(empty)"))
            _out("# USER\n" + (agent.memory.store.raw("user") or "(empty)"))
    elif name == "/usage":
        u = agent.budget.usage
        _out(f"tokens this session — input: {u.input_tokens:,}  output: {u.output_tokens:,}", style="cyan")
    elif name == "/compress":
        from ..agent import compaction
        comp = agent.config.get("agent.compression", {}) or {}
        agent.session.messages = compaction.compress(
            agent.session.messages, agent.provider,
            preserve_first=comp.get("preserve_first", 3), preserve_last=comp.get("preserve_last", 20))
        agent.refresh_volatile()
        _out("context compressed.", style="yellow")
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
            tid = get_manager().spawn(agent.config, arg)
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
    elif name == "/rollback":
        from ..checkpoints import CheckpointStore
        restored = CheckpointStore(agent.cwd).rollback(arg or None)
        _out(f"rolled back {len(restored)} file(s): {', '.join(restored) or '(none)'}", style="yellow")
    elif name == "/sessions":
        for s in SessionStore().list(20):
            _out(f"  {s['id']}  {s['title']}  ({s['updated_at']})")
    elif name in ("/new", "/clear"):
        agent.session = Session.create()
        agent.tool_context.session = agent.session
        _out(f"started new session {agent.session.id}", style="green")
    else:
        _out(f"unknown command {name}; /help for list", style="yellow")
    return ""


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def _make_agent(config, *, session, store, model, provider_name, auto) -> Agent:
    return Agent.create(
        config, session=session, model=model, provider_name=provider_name,
        store=store, approver=make_approver(auto), include_mcp=True,
    )


def run_once(config: Config, prompt: str, *, model=None, provider_name=None,
             session: Session | None = None, store: SessionStore | None = None, auto=False,
             images: list[str] | None = None) -> str:
    from ..types import Message
    store = store or SessionStore()
    session = session or Session.create()
    agent = _make_agent(config, session=session, store=store, model=model,
                        provider_name=provider_name, auto=auto)
    text = expand_references(prompt, agent.cwd)
    user_input = Message.user(text, images=images) if images else text
    result = agent.run(user_input, Renderer())
    return result.content


def interactive(config: Config, *, model=None, provider_name=None,
                session: Session | None = None, store: SessionStore | None = None, auto=False) -> None:
    store = store or SessionStore()
    session = session or Session.create()
    agent = _make_agent(config, session=session, store=store, model=model,
                        provider_name=provider_name, auto=auto)
    banner(agent)

    ps = None
    if PromptSession is not None:
        from ..config import logs_dir
        ps = PromptSession(history=FileHistory(str(logs_dir() / "repl_history")),
                           completer=WordCompleter(SLASH, sentence=True))

    while True:
        try:
            user = ps.prompt(">>> ") if ps else input(">>> ")
        except (EOFError, KeyboardInterrupt):
            _out("\nbye.")
            break
        user = user.strip()
        if not user:
            continue
        if user.startswith("/"):
            if handle_slash(user, agent) == "break":
                break
            continue
        try:
            agent.run(expand_references(user, agent.cwd), Renderer())
        except KeyboardInterrupt:
            _out("\n  (interrupted)", style="yellow")
        store.save(agent.session)
        _out(_status_line(agent), style="bright_black")
