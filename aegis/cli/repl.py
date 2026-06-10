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

SLASH = ["/help", "/model", "/status", "/tools", "/skills", "/skill", "/memory", "/usage",
         "/compress", "/think", "/retry", "/undo", "/learn", "/background", "/tasks", "/rollback",
         "/personality", "/save", "/sessions", "/new", "/clear", "/yolo", "/goal", "/subgoal",
         "/quit", "/exit"]


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
            if not e.get("is_error") and e.get("name") == "memory":
                _out(f"    💾 remembered: {e['summary']}", style="magenta")
            elif not e.get("is_error") and e.get("name") == "skill":
                _out(f"    📝 skill: {e['summary']}", style="magenta")
            else:
                style = "red" if e.get("is_error") else "green"
                _out(f"    ↳ {e['summary']}", style=style)
        elif t == "compacting":
            _out("  … compacting context …", style="yellow")
        elif t == "budget_exhausted":
            _out("  … step limit reached; summarizing …", style="yellow")
        elif t == "skill_nudge":
            _out("  💡 tip: save this workflow as a skill (`skill` create) so you can reuse it.", style="magenta")
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
    elif name == "/yolo":
        eng = agent.permissions
        on = getattr(eng, "_mode_override", None) == "full"
        eng._mode_override = None if on else "full"
        _out("🟢 exec mode restored (approvals on)." if on
             else "⚠ YOLO ON — all tool approvals bypassed for this session "
                  "(hardline blocklist still applies). /yolo again to turn off.")
    elif name == "/model":
        _out(f"current: {agent.provider.describe()}")
    elif name == "/status":
        _out(f"provider: {agent.provider.describe()}")
        _out(f"session: {agent.session.id} ({len(agent.session.messages)} msgs)")
        _out(f"reasoning: {getattr(agent, 'reasoning', 'off')} · exec_mode: {agent.config.get('tools.exec_mode')}")
        comps = agent.session.meta.get("compactions") or []
        if comps:
            saved = sum(c["tokens_before"] - c["tokens_after"] for c in comps)
            _out(f"compactions: {len(comps)} (~{saved:,} tokens reclaimed; {comps[-1]['reason']})")
        from .. import goals
        g = goals.get(agent.session)
        if g:
            _out(goals.status_line(g), style="cyan")
        for line in session_recap(agent.session):
            _out(line, style="bright_black")
        _out(_status_line(agent))
    elif name == "/think":
        level = arg or "medium"
        if level not in ("off", "minimal", "low", "medium", "high", "xhigh"):
            _out("usage: /think off|minimal|low|medium|high|xhigh")
        else:
            agent.reasoning = level
            _out(f"reasoning effort → {level}", style="green")
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
                path = agent.skills.create(
                    sname, desc, "## When to Use\n\n## Procedure\n1. \n")
                agent.refresh_volatile()
                _out(f"✓ created scaffold → {path}\n  edit it to add the procedure.", style="green")
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
    elif name == "/usage":
        u = agent.budget.usage
        _out(f"tokens this session — input: {u.input_tokens:,}  output: {u.output_tokens:,}", style="cyan")
    elif name == "/compress":
        from ..agent import compaction, governance
        from ..agent.loop import _summarizer
        comp = agent.config.get("agent.compression", {}) or {}
        before = len(agent.session.messages)
        agent.session.messages = governance.normalize(compaction.compress(
            agent.session.messages, _summarizer(agent),   # cheap aux model, like auto-compaction
            preserve_first=comp.get("preserve_first", 3),
            preserve_last=comp.get("preserve_last", 20)))
        agent.refresh_volatile()
        _out(f"context compressed: {before} → {len(agent.session.messages)} messages.", style="yellow")
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
    elif name == "/retry":
        # drop the last assistant turn (+ its tool messages) and re-run the last user msg
        msgs = agent.session.messages
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "user"), None)
        if last_user is None:
            _out("nothing to retry")
        else:
            prompt = msgs[last_user].content
            agent.session.messages = msgs[:last_user]
            agent.run(prompt, Renderer())
            _out(_status_line(agent), style="bright_black")
    elif name == "/undo":
        msgs = agent.session.messages
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "user"), None)
        if last_user is None:
            _out("nothing to undo")
        else:
            agent.session.messages = msgs[:last_user]
            _out(f"undid last turn ({len(msgs) - last_user} messages removed)", style="yellow")
    elif name == "/learn":
        from ..learn import review_session
        try:
            found = review_session(agent.config, agent.session.id)
            _out(f"proposed {len(found)} candidate(s); review with `aegis learn list`", style="green")
        except Exception as e:  # noqa: BLE001
            _out(f"learn failed: {e}", style="red")
    elif name == "/save":
        out = Path(arg).expanduser() if arg else (agent.cwd / f"{agent.session.id}.md")
        lines = [f"# {agent.session.title}\n"]
        for m in agent.session.messages:
            if m.role in ("user", "assistant") and m.content:
                lines.append(f"\n## {m.role}\n\n{m.content}")
        out.write_text("\n".join(lines), encoding="utf-8")
        _out(f"saved session → {out}", style="green")
    elif name == "/sessions":
        for s in SessionStore().list(20):
            _out(f"  {s['id']}  {s['title']}  ({s['updated_at']})")
    elif name in ("/new", "/clear"):
        agent.session = Session.create()
        agent.tool_context.session = agent.session
        agent.refresh_volatile()   # thaw the memory snapshot: facts saved THIS process
        n = len(agent.memory.store.entries("user")) if agent.memory else 0
        extra = f" · {n} user fact(s) loaded" if n else ""
        _out(f"started new session {agent.session.id}{extra}", style="green")
    else:
        _out(f"unknown command {name}; /help for list", style="yellow")
    return ""


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def _make_agent(config, *, session, store, model, provider_name, auto) -> Agent:
    agent = Agent.create(
        config, session=session, model=model, provider_name=provider_name,
        store=store, approver=make_approver(auto), include_mcp=True,
    )
    agent.tool_context.asker = make_asker()   # let the clarify tool prompt inline
    return agent


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
        if user.startswith(("/goal", "/subgoal")):
            from .. import goals
            reply, start_turn = goals.handle_command(agent.session, user, config)
            if reply:
                _out(reply, style="cyan")
            store.save(agent.session)
            if not start_turn:
                continue
            user = goals.get(agent.session)["text"]   # run the new goal as this turn
        elif user.startswith("/"):
            if handle_slash(user, agent) == "break":
                break
            continue
        try:
            from ..firstrun import profile_build_directive
            renderer = Renderer()
            res = agent.run(expand_references(user, agent.cwd) + profile_build_directive(config),
                            renderer)
            from .. import goals
            goals.run_loop(agent, res.content or "",
                           lambda s: _out(f"  {s}", style="magenta"), renderer)
        except KeyboardInterrupt:
            agent.cancel()   # stop the loop at the next safe point; discard partial work
            _out("\n  ⏹ interrupted — stopped this turn (your session is intact)", style="yellow")
        store.save(agent.session)
        _out(_status_line(agent), style="bright_black")
