"""`aegis` command-line entrypoint."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .. import __version__
from .. import config as cfg
from ..config import Config


def _print(s: str = "") -> None:
    print(s)


# --------------------------------------------------------------------------- #
# chat / interactive
# --------------------------------------------------------------------------- #
def _make_worktree() -> Path | None:
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True).stdout.strip()
        if not root:
            return None
        wt = Path(root).parent / f".aegis-worktree-{int(time.time())}"
        branch = f"aegis/{int(time.time())}"
        subprocess.run(["git", "worktree", "add", "-b", branch, str(wt)], cwd=root,
                       capture_output=True, text=True, check=True)
        return wt
    except Exception:  # noqa: BLE001
        return None


def cmd_chat(args, config: Config) -> int:
    from ..session import Session, SessionStore
    from . import repl

    if getattr(args, "worktree", False):
        wt = _make_worktree()
        if wt:
            os.chdir(wt)
            _print(f"(isolated worktree: {wt})")
        else:
            _print("(not a git repo — worktree skipped)")

    store = SessionStore()
    session = None
    if args.resume:
        session = store.load(args.resume) or _die(f"session '{args.resume}' not found")
    elif args.cont:
        session = store.latest()
        if session:
            _print(f"(continuing {session.id})")
    session = session or Session.create()

    prompt = args.query or (" ".join(args.prompt) if args.prompt else None)
    if prompt:
        out = repl.run_once(config, prompt, model=args.model, provider_name=args.provider,
                            session=session, store=store, auto=args.yolo)
        if not config.get("agent.stream", True):
            _print(out)
        return 0
    repl.interactive(config, model=args.model, provider_name=args.provider,
                     session=session, store=store, auto=args.yolo)
    return 0


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def cmd_model(args, config: Config) -> int:
    from ..providers import registry

    if args.action == "list":
        for name in registry.list_providers():
            spec = registry.get_spec(name)
            _print(f"  {name:<12} {spec.default_model:<40} ({spec.context_length:,} ctx)")
        return 0
    if args.action == "set":
        if not args.provider:
            return _die("usage: aegis model set <provider> [<model>]")
        spec = registry.get_spec(args.provider)
        config.set("model.provider", args.provider)
        config.set("model.default", args.model or (spec.default_model if spec else "default"))
        _print(f"model -> {config.get('model.provider')}/{config.get('model.default')}")
        return 0
    # show
    _print(f"provider: {config.get('model.provider')}")
    _print(f"model:    {config.get('model.default')}")
    if config.get("model.base_url"):
        _print(f"base_url: {config.get('model.base_url')}")
    return 0


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def cmd_auth(args, config: Config) -> int:
    from ..providers import registry
    from ..providers.auth import AuthError, AuthStore, OAuthAuth

    store = AuthStore()
    if args.action == "status":
        _print("Provider auth status:")
        for name in registry.list_providers():
            spec = registry.get_spec(name)
            api = "set" if any(__import__("os").environ.get(v) for v in spec.env_vars) else "—"
            oauth = "logged in" if (spec.oauth and store.load(name) and not store.load(name).get("quarantined")) else (
                "available" if spec.oauth else "—")
            _print(f"  {name:<12} api-key: {api:<5} oauth: {oauth}")
        return 0
    if args.action == "login":
        if not args.provider:
            return _die("usage: aegis auth login <provider> [--manual]")
        spec = registry.get_spec(args.provider)
        if not spec or not spec.oauth:
            return _die(f"provider '{args.provider}' has no OAuth config.")
        try:
            OAuthAuth(spec.oauth, store).login(manual=args.manual)
            _print(f"✓ logged in to {args.provider} via OAuth.")
        except AuthError as e:
            return _die(str(e))
        return 0
    if args.action == "logout":
        if not args.provider:
            return _die("usage: aegis auth logout <provider>")
        store.delete(args.provider)
        _print(f"logged out of {args.provider}.")
        return 0
    return _die("usage: aegis auth [status|login|logout]")


# --------------------------------------------------------------------------- #
# setup wizard
# --------------------------------------------------------------------------- #
def cmd_setup(args, config: Config) -> int:
    from ..providers import registry

    _print("AEGIS setup\n-----------")
    _print("Providers: " + ", ".join(registry.list_providers()))
    provider = input(f"provider [{config.get('model.provider')}]: ").strip() or config.get("model.provider")
    spec = registry.get_spec(provider)
    if not spec:
        return _die(f"unknown provider '{provider}'")
    config.set("model.provider", provider)
    model = input(f"model [{spec.default_model}]: ").strip() or spec.default_model
    config.set("model.default", model)

    if spec.env_vars:
        key = input(f"{spec.env_vars[0]} (blank to skip / use OAuth): ").strip()
        if key:
            config.set(spec.env_vars[0], key)
    elif spec.auth_scheme == "none":
        url = input(f"base_url [{spec.base_url}]: ").strip()
        if url:
            config.set("model.base_url", url)

    mode = input("exec mode [ask/auto/allowlist/deny/full] (ask): ").strip() or "ask"
    config.set("tools.exec_mode", mode)
    _print(f"\n✓ wrote {cfg.config_path()}")
    if spec.oauth:
        _print(f"  Tip: `aegis auth login {provider}` to use OAuth instead of an API key.")
    return 0


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
def cmd_skills(args, config: Config) -> int:
    from ..skills import SkillsLoader

    loader = SkillsLoader(config)
    if args.action == "view":
        body = loader.activate(args.name) if args.name else None
        _print(body or f"skill '{args.name}' not found.")
        return 0
    if args.action == "new":
        if not args.name:
            return _die("usage: aegis skills new <name>")
        d = cfg.skills_dir() / args.name
        d.mkdir(parents=True, exist_ok=True)
        skill_md = d / "SKILL.md"
        skill_md.write_text(_SKILL_TEMPLATE.format(name=args.name), encoding="utf-8")
        _print(f"created {skill_md}")
        return 0
    if args.action == "install":
        from .. import marketplace
        if not args.name:
            return _die("usage: aegis skills install <git:owner/repo[@ref][/dir] | url>")
        try:
            names = marketplace.install(args.name)
            _print(f"✓ installed: {', '.join(names)}")
        except Exception as e:  # noqa: BLE001
            return _die(str(e))
        return 0
    if args.action == "search":
        from .. import marketplace
        results = marketplace.search(args.name or "")
        if not results:
            _print("(no results — registry may be unavailable; try a git source directly)")
        for r in results:
            _print(f"  {r['name']:<24} {r['description'][:70]}\n      {r['source']}")
        return 0
    if args.action == "remove":
        from .. import marketplace
        _print("removed" if marketplace.remove(args.name) else "not found")
        return 0
    # list
    for s in sorted(loader.available(), key=lambda s: s.name):
        _print(f"  {s.name:<24} {s.description[:80]}")
    return 0


def cmd_mcp(args, config: Config) -> int:
    from ..mcp.client import build_manager

    if args.action == "add":
        if not args.name or not args.cmd:
            return _die('usage: aegis mcp add <name> "<command> [args...]"')
        servers = dict(config.get("mcp.servers", {}) or {})
        parts = args.cmd.split()
        servers[args.name] = {"command": parts[0], "args": parts[1:]}
        config.data.setdefault("mcp", {})["servers"] = servers
        config.save()
        _print(f"added MCP server '{args.name}'")
        return 0
    if args.action == "remove":
        servers = dict(config.get("mcp.servers", {}) or {})
        servers.pop(args.name, None)
        config.data.setdefault("mcp", {})["servers"] = servers
        config.save()
        _print(f"removed MCP server '{args.name}'")
        return 0
    # list / test
    mgr = build_manager(config)
    if not mgr.clients:
        _print("(no MCP servers configured — `aegis mcp add <name> \"<command>\"`)")
        return 0
    for client in mgr.clients:
        kind = client.url or f"{client.command} {' '.join(client.args)}"
        try:
            client.connect()
            tools = client.list_tools()
            _print(f"  {client.name:<16} {kind}\n      tools: {', '.join(t['name'] for t in tools) or '(none)'}")
            client.close()
        except Exception as e:  # noqa: BLE001
            _print(f"  {client.name:<16} {kind}\n      ERROR: {e}")
    return 0


def cmd_serve(args, config: Config) -> int:
    from ..server import serve
    serve(config, host=args.host or config.get("server.host", "127.0.0.1"),
          port=args.port or int(config.get("server.port", 8790)))
    return 0


def cmd_cron(args, config: Config) -> int:
    from ..cron import CronStore, run_scheduler

    store = CronStore()
    if args.action == "add":
        if not args.schedule or not args.prompt:
            return _die('usage: aegis cron add "<schedule>" "<prompt>"')
        job = store.add(args.schedule, " ".join(args.prompt) if isinstance(args.prompt, list) else args.prompt)
        _print(f"added cron {job.id}: [{job.schedule}] {job.prompt[:60]}")
        return 0
    if args.action == "rm":
        _print("removed" if store.remove(args.schedule or "") else "not found")
        return 0
    if args.action == "run":
        run_scheduler(config)
        return 0
    # list
    for j in store.list():
        _print(f"  {j.id}  [{j.schedule}]  {j.prompt[:60]}")
    return 0


# --------------------------------------------------------------------------- #
# tools / memory / config / sessions
# --------------------------------------------------------------------------- #
def cmd_tools(args, config: Config) -> int:
    from ..tools.registry import default_registry

    for t in default_registry().all():
        g = f"[{','.join(t.groups)}]" if t.groups else "[safe]"
        _print(f"  {t.name:<14} {g:<22} {t.description.splitlines()[0]}")
    return 0


def cmd_memory(args, config: Config) -> int:
    from ..memory import MemoryStore

    store = MemoryStore()
    if args.action == "add":
        if not args.text:
            return _die("usage: aegis memory add <text> [--user]")
        target = "user" if args.user else "memory"
        _print(store.add(target, " ".join(args.text)))
        return 0
    if args.action == "clear":
        target = "user" if args.user else "memory"
        for e in store.entries(target):
            store.remove(target, e)
        _print(f"cleared {target}")
        return 0
    _print("# MEMORY\n" + (store.raw("memory") or "(empty)"))
    _print("\n# USER\n" + (store.raw("user") or "(empty)"))
    return 0


def cmd_config(args, config: Config) -> int:
    if args.action == "path":
        _print(str(cfg.config_path()))
        _print(str(cfg.env_path()))
        return 0
    if args.action == "get":
        _print(str(config.get(args.key)))
        return 0
    if args.action == "set":
        where = config.set(args.key, args.value)
        _print(f"set {args.key} -> {where}")
        return 0
    # dump
    import yaml
    _print(yaml.safe_dump(config.data, sort_keys=False))
    return 0


def cmd_sessions(args, config: Config) -> int:
    from ..session import SessionStore

    store = SessionStore()
    if args.action == "show":
        s = store.load(args.id) if args.id else None
        if not s:
            return _die("session not found")
        for m in s.messages:
            if m.role in ("user", "assistant") and m.content:
                _print(f"[{m.role}] {m.content[:500]}")
        return 0
    if args.action == "rm":
        _print("removed" if store.delete(args.id) else "not found")
        return 0
    for s in store.list(50):
        _print(f"  {s['id']}  {s['title'][:40]:<40}  {s['updated_at']}")
    return 0


# --------------------------------------------------------------------------- #
# gateway
# --------------------------------------------------------------------------- #
def cmd_gateway(args, config: Config) -> int:
    from ..gateway.channels import build_adapter
    from ..gateway.runner import GatewayRunner

    runner = GatewayRunner(config)
    channels = (args.channels or "cli").split(",")
    for ch in channels:
        try:
            runner.add(build_adapter(ch.strip()))
        except Exception as e:  # noqa: BLE001
            _print(f"  ! skipping channel {ch}: {e}")
    _print(f"Starting gateway with channels: {', '.join(channels)}")
    runner.run()
    return 0


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args, config: Config) -> int:
    _print(f"AEGIS v{__version__}")
    _print(f"python: {sys.version.split()[0]}")
    _print(f"home:   {cfg.get_home()}")
    _print("core deps:")
    for mod in ("httpx", "yaml", "rich", "prompt_toolkit"):
        try:
            __import__(mod)
            _print(f"  ✓ {mod}")
        except Exception:  # noqa: BLE001
            _print(f"  ✗ {mod} (missing)")
    _print("optional extras:")
    for mod, extra in (("playwright", "browser"), ("pyautogui", "computer"),
                       ("discord", "discord"), ("slack_bolt", "slack")):
        try:
            __import__(mod)
            _print(f"  ✓ {mod}")
        except Exception:  # noqa: BLE001
            _print(f"  – {mod} (pip install aegis-agent[{extra}])")
    tools = config.get("tools.toolsets", [])
    mcp_servers = config.get("mcp.servers", {}) or {}
    _print(f"toolsets: {tools} · mcp servers: {len(mcp_servers)}")
    try:
        from ..providers import build_provider
        p = build_provider(config)
        _print(f"provider: {p.describe()}")
        _print(f"  auth available: {p.auth.available()}")
    except Exception as e:  # noqa: BLE001
        _print(f"provider: ERROR {e}")
    return 0


def cmd_update(args, config: Config) -> int:
    import aegis
    pkg_root = Path(aegis.__file__).resolve().parent.parent
    if (pkg_root / ".git").exists():
        _print(f"updating from git at {pkg_root}…")
        subprocess.run(["git", "pull", "--ff-only"], cwd=str(pkg_root))
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], cwd=str(pkg_root))
    else:
        _print("updating from PyPI…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", "aegis-agent"])
    _print("✓ updated. Run `aegis doctor` to confirm.")
    return 0


_CMDS = ("chat model auth setup onboard update skills mcp serve cron tools "
         "memory config sessions gateway doctor completion")


def cmd_completion(args, config: Config) -> int:
    if args.shell == "bash":
        _print(f"""_aegis_completion() {{
  COMPREPLY=( $(compgen -W "{_CMDS}" -- "${{COMP_WORDS[COMP_CWORD]}}") )
}}
complete -F _aegis_completion aegis""")
    elif args.shell == "zsh":
        _print(f"#compdef aegis\n_arguments '1:command:({_CMDS})'")
    elif args.shell == "fish":
        _print(f"complete -c aegis -f -n '__fish_use_subcommand' -a '{_CMDS}'")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
_SKILL_TEMPLATE = """\
---
name: {name}
description: One sentence on WHAT this does and WHEN to use it.
version: 1.0.0
metadata:
  category: general
---

## When to Use
...

## Procedure
1. ...

## Verification
...
"""


def _die(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aegis", description="AEGIS — terminal agent harness.")
    p.add_argument("--version", action="version", version=f"aegis {__version__}")
    p.add_argument("--profile", help="use a named config profile")
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("chat", help="chat with the agent (default)")
    c.add_argument("prompt", nargs="*", help="one-shot prompt (omit for interactive)")
    c.add_argument("-q", "--query", help="one-shot prompt")
    c.add_argument("-m", "--model")
    c.add_argument("--provider")
    c.add_argument("--resume", help="resume a session id/title")
    c.add_argument("--continue", dest="cont", action="store_true", help="continue the latest session")
    c.add_argument("--yolo", action="store_true", help="auto-approve all tools")
    c.add_argument("--worktree", "-w", action="store_true", help="run in an isolated git worktree")
    c.set_defaults(func=cmd_chat)

    m = sub.add_parser("model", help="show/set the model")
    m.add_argument("action", nargs="?", choices=["list", "set"])
    m.add_argument("provider", nargs="?")
    m.add_argument("model", nargs="?")
    m.set_defaults(func=cmd_model)

    a = sub.add_parser("auth", help="API-key / OAuth authentication")
    a.add_argument("action", choices=["status", "login", "logout"])
    a.add_argument("provider", nargs="?")
    a.add_argument("--manual", action="store_true", help="manual code-paste OAuth flow")
    a.set_defaults(func=cmd_auth)

    s = sub.add_parser("setup", help="interactive setup wizard")
    s.set_defaults(func=cmd_setup)

    ob = sub.add_parser("onboard", help="interactive setup wizard (alias of setup)")
    ob.set_defaults(func=cmd_setup)

    up = sub.add_parser("update", help="update AEGIS to the latest version")
    up.set_defaults(func=cmd_update)

    cm = sub.add_parser("completion", help="output shell completion script")
    cm.add_argument("shell", choices=["bash", "zsh", "fish"])
    cm.set_defaults(func=cmd_completion)

    sk = sub.add_parser("skills", help="list/view/create/install/search/remove skills")
    sk.add_argument("action", nargs="?",
                    choices=["list", "view", "new", "install", "search", "remove"], default="list")
    sk.add_argument("name", nargs="?", help="skill name or install source")
    sk.set_defaults(func=cmd_skills)

    mc = sub.add_parser("mcp", help="manage MCP servers")
    mc.add_argument("action", nargs="?", choices=["list", "add", "remove", "test"], default="list")
    mc.add_argument("name", nargs="?")
    mc.add_argument("cmd", nargs="?", help='command line, e.g. "npx -y @modelcontextprotocol/server-filesystem /tmp"')
    mc.set_defaults(func=cmd_mcp)

    sv = sub.add_parser("serve", help="run an OpenAI-compatible API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.set_defaults(func=cmd_serve)

    cr = sub.add_parser("cron", help="schedule recurring agent tasks")
    cr.add_argument("action", nargs="?", choices=["list", "add", "rm", "run"], default="list")
    cr.add_argument("schedule", nargs="?", help='e.g. "30m", "@daily", or 5-field cron')
    cr.add_argument("prompt", nargs="*")
    cr.set_defaults(func=cmd_cron)

    t = sub.add_parser("tools", help="list built-in tools")
    t.set_defaults(func=cmd_tools)

    mem = sub.add_parser("memory", help="show/add long-term memory")
    mem.add_argument("action", nargs="?", choices=["show", "add", "clear"], default="show")
    mem.add_argument("text", nargs="*")
    mem.add_argument("--user", action="store_true", help="target the user profile")
    mem.set_defaults(func=cmd_memory)

    cf = sub.add_parser("config", help="get/set configuration")
    cf.add_argument("action", nargs="?", choices=["get", "set", "path", "dump"], default="dump")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.set_defaults(func=cmd_config)

    se = sub.add_parser("sessions", help="list/show/remove sessions")
    se.add_argument("action", nargs="?", choices=["list", "show", "rm"], default="list")
    se.add_argument("id", nargs="?")
    se.set_defaults(func=cmd_sessions)

    g = sub.add_parser("gateway", help="run the multi-channel gateway")
    g.add_argument("--channels", help="comma list: cli,telegram (default cli)")
    g.set_defaults(func=cmd_gateway)

    d = sub.add_parser("doctor", help="diagnose the installation")
    d.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load(profile=args.profile)

    if not getattr(args, "command", None):
        # default: interactive chat
        from ..session import Session, SessionStore
        from . import repl
        repl.interactive(config, session=Session.create(), store=SessionStore())
        return 0
    try:
        return args.func(args, config)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
