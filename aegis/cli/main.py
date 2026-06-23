"""`aegis` command-line entrypoint."""

from __future__ import annotations

import argparse
import importlib.metadata as importlib_metadata
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .. import __version__
from .. import config as cfg
from ..config import Config
from ..redact import redact_secret_values, redact_secrets


def _print(s: str = "") -> None:
    print(s)


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _terminal_unicode_enabled() -> bool:
    if _env_enabled("AEGIS_ASCII"):
        return False
    forced = os.environ.get("AEGIS_UNICODE")
    if forced is not None:
        return forced.strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


def _terminal_title(title: str, *, width: int = 64, unicode: bool = False) -> str:
    title = str(title or "").strip()
    if not unicode:
        return "+" + "-" * (width - 2) + "+\n|" + title.center(width - 2) + "|\n+" + "-" * (width - 2) + "+"
    inner = width - 2
    label = f" {title} "
    if len(label) >= inner:
        return f"╭{label[:inner]}╮"
    left = (inner - len(label)) // 2
    right = inner - len(label) - left
    return f"╭{'─' * left}{label}{'─' * right}╮"


def _terminal_section(title: str, *, unicode: bool = False) -> str:
    return f"◇ {title}" if unicode else f"== {title} =="


_BUSY_MODE_HINTS = {
    "queue": "new input waits behind the active run",
    "steer": "new input is sent as guidance to the active run",
    "interrupt": "new input stops the active run and starts fresh",
}


def _busy_mode(value: object) -> str:
    mode = str(value or "queue").strip().lower()
    return mode if mode in _BUSY_MODE_HINTS else "queue"


def _busy_mode_status(value: object) -> str:
    mode = _busy_mode(value)
    return f"{mode} - {_BUSY_MODE_HINTS[mode]}"


def _redact_string_values(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_string_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_string_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_string_values(item) for item in value)
    return value


# --------------------------------------------------------------------------- #
# chat / interactive
# --------------------------------------------------------------------------- #
def _terminal_session(args, store):
    from ..session import Session

    resume = getattr(args, "resume", None)
    if resume:
        session = store.load(resume)
        if not session:
            return _die(f"session '{resume}' not found")
        return session
    if getattr(args, "cont", False):
        session = store.latest()
        if session:
            _print(f"(continuing {session.id})")
            return session
    return Session.create()


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
    from ..session import SessionStore
    from . import repl

    if getattr(args, "worktree", False):
        wt = _make_worktree()
        if wt:
            os.chdir(wt)
            _print(f"(isolated worktree: {wt})")
        else:
            _print("(not a git repo — worktree skipped)")

    store = SessionStore()

    # Gated, idle-aware background skill maintenance (no-op unless it's actually due).
    try:
        from .. import curator as _curator
        _curator.maybe_run_background(config)
    except Exception:  # noqa: BLE001
        pass

    session = _terminal_session(args, store)
    if isinstance(session, int):
        return session
    skills = [s.strip() for s in (getattr(args, "skills", "") or "").split(",") if s.strip()]
    if skills:
        session.meta["pending_skill_preload"] = skills
        session.meta["pending_skill_preload_source"] = "chat"
        store.save(session)

    images = None
    if getattr(args, "image", None):
        from ..util import encode_image
        images = [encode_image(Path(p).expanduser()) for p in args.image]

    prompt = args.query or (" ".join(args.prompt) if args.prompt else None)
    if prompt:
        repl.run_once(config, prompt, model=args.model, provider_name=args.provider,
                      session=session, store=store, auto=args.yolo, images=images)
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
        for name in registry.list_providers(config):
            spec = registry.get_spec(name, config)
            _print(f"  {name:<12} {spec.default_model:<40} ({spec.context_length:,} ctx)")
        return 0
    if args.action == "set":
        if not args.provider:
            return _die("usage: aegis model set <provider> [<model>]")
        spec = registry.get_spec(args.provider, config)
        model = args.model or (spec.default_model if spec else "default")
        validation = registry.validate_model_choice(args.provider, model, config)
        if not validation.get("ok", True):
            return _die(registry.model_validation_message(validation))
        config.set("model.provider", args.provider)
        config.set("model.default", model)
        _print(f"model -> {config.get('model.provider')}/{config.get('model.default')}")
        warning = registry.model_validation_message(validation)
        if warning and validation.get("warning"):
            _print(f"warning: {warning}")
        return 0
    # show
    report = registry.provider_report(config)
    active = report.get("active", {})
    _print(f"provider: {config.get('model.provider')}")
    _print(f"model:    {config.get('model.default')}")
    if active.get("error"):
        _print(f"resolver: ERROR {active['error']}")
    else:
        _print(f"transport: {active.get('api_mode', '')}")
        _print(f"context:   {int(active.get('context_length') or 0):,}")
        if active.get("capability_summary"):
            _print(f"capabilities: {active.get('capability_summary')}")
        auth = active.get("auth") or {}
        _print(f"auth:      {auth.get('description', '')} ({'ready' if auth.get('available') else 'missing'})")
        if active.get("base_url"):
            _print(f"base_url:  {active.get('base_url')}")
    warning = registry.model_validation_message(active.get("model_validation"))
    if warning and active.get("warning"):
        _print(f"model warning: {warning}")
    fallbacks = report.get("fallbacks") or []
    if fallbacks:
        _print("fallbacks:")
        for row in fallbacks:
            if row.get("error"):
                _print(f"  - {row.get('name') or '(default)'}: ERROR {row['error']}")
            else:
                _print(f"  - {row.get('name')} / {row.get('model')} "
                       f"({row.get('api_mode')}, {int(row.get('context_length') or 0):,} ctx)")
    routes = report.get("routing") or []
    if routes:
        _print("routing:")
        for row in routes:
            status = "known" if row.get("known_provider") else row.get("warning", "unknown")
            _print(f"  - /{row.get('match', '')}/ -> {row.get('provider')} / {row.get('model')} ({status})")
    return 0


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def cmd_auth(args, config: Config) -> int:
    from ..providers import registry
    from ..providers.auth import AuthError, AuthStore, CodexBackendAuth, CodexCliAuth, OAuthAuth

    store = AuthStore()
    if args.action == "pool":
        from .. import credentials
        args.name = args.provider
        return credentials.cmd_auth_pool(args, config)
    if args.action == "status":
        _print("Provider auth status:")
        for name in registry.list_providers():
            spec = registry.get_spec(name)
            api = "set" if any(__import__("os").environ.get(v) for v in spec.env_vars) else "—"
            if spec.oauth:
                oauth_auth = OAuthAuth(spec.oauth, store)
                oauth = oauth_auth.describe().removeprefix(
                    f"oauth ({spec.oauth.provider}: "
                ).removesuffix(")")
            else:
                oauth = "—"
            if spec.auth_scheme == "codex-cli":
                codex = CodexCliAuth().describe()
            elif spec.auth_scheme == "codex-backend":
                codex = CodexBackendAuth().describe()
            else:
                codex = "—"
            _print(f"  {name:<12} api-key: {api:<5} oauth: {oauth:<30} codex: {codex}")
        return 0
    if args.action == "login":
        if not args.provider:
            return _die("usage: aegis auth login <provider> [--manual]")
        spec = registry.get_spec(args.provider)
        if not spec or not spec.oauth:
            return _die(f"provider '{args.provider}' has no OAuth config.")
        try:
            oauth = OAuthAuth(spec.oauth, store)
            creds = oauth.login(manual=args.manual)
            _print(f"✓ logged in to {args.provider} via OAuth.")
            missing = oauth.missing_required_scopes(creds)
            if missing:
                _print(
                    "  ! Token is missing API scope(s): "
                    + ", ".join(missing)
                    + ". Use an API key for model inference."
                )
        except AuthError as e:
            return _die(str(e))
        return 0
    if args.action == "logout":
        if not args.provider:
            return _die("usage: aegis auth logout <provider>")
        store.delete(args.provider)
        _print(f"logged out of {args.provider}.")
        return 0
    if args.action == "import-claude":
        from ..providers.auth import import_claude_cli_login
        ok, detail = import_claude_cli_login(store)
        _print(("✓ " if ok else "! ") + detail)
        if ok:
            _print("  set `aegis model set anthropic claude-sonnet-4-5` and you're ready.")
        return 0 if ok else 1
    return _die("usage: aegis auth [status|login|logout|import-claude]")


# --------------------------------------------------------------------------- #
# setup wizard
# --------------------------------------------------------------------------- #
def cmd_setup(args, config: Config) -> int:
    from ..onboarding import (
        run_onboarding,
        run_onboarding_noninteractive,
        run_setup_section,
        run_setup_section_noninteractive,
    )

    section = str(getattr(args, "section", "") or "").strip().lower()
    if getattr(args, "json", False) and not getattr(args, "non_interactive", False):
        return _die("--json requires --non-interactive")
    if section and getattr(args, "non_interactive", False):
        return run_setup_section_noninteractive(
            config,
            section,
            accept_risk=getattr(args, "accept_risk", False),
            json_output=getattr(args, "json", False),
            provider=getattr(args, "provider", None),
            auth=getattr(args, "auth", "skip"),
            model=getattr(args, "model", None),
            web=getattr(args, "web", "auto"),
            toolsets=getattr(args, "toolsets", None),
            channels=getattr(args, "channels", None),
            exec_mode=getattr(args, "exec_mode", "ask"),
            services=getattr(args, "install_services", False)
            and not getattr(args, "no_services", False),
        )
    if getattr(args, "non_interactive", False):
        return run_onboarding_noninteractive(
            config,
            accept_risk=getattr(args, "accept_risk", False),
            json_output=getattr(args, "json", False),
            provider=getattr(args, "provider", None),
            auth=getattr(args, "auth", "skip"),
            model=getattr(args, "model", None),
            web=getattr(args, "web", "auto"),
            toolsets=getattr(args, "toolsets", None),
            channels=getattr(args, "channels", None),
            exec_mode=getattr(args, "exec_mode", "ask"),
            services=getattr(args, "install_services", False)
            and not getattr(args, "no_services", False),
        )
    if section:
        return run_setup_section(
            config,
            section,
            quick=getattr(args, "quick", False),
            advanced=getattr(args, "advanced", False),
            probe=not getattr(args, "no_probe", False),
            services=not getattr(args, "no_services", False),
        )

    return run_onboarding(
        config,
        quick=getattr(args, "quick", False),
        advanced=getattr(args, "advanced", False),
        probe=not getattr(args, "no_probe", False),
        services=not getattr(args, "no_services", False),
    )


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
            names = marketplace.install(args.name, force=getattr(args, "force", False))
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
    if args.action in {"remove", "uninstall"}:
        from .. import marketplace
        _print("removed" if marketplace.remove(args.name) else "not found")
        return 0
    if args.action == "hub":
        from .. import marketplace
        taps = marketplace.list_taps(config)
        if not args.name:
            _print("known skill hubs (install all with `aegis skills hub <name>`):")
            for k, v in taps.items():
                _print(f"  {k:<12} {v}")
            return 0
        try:
            names = marketplace.install_hub(args.name, config, force=getattr(args, "force", False))
            _print(f"✓ imported {len(names)} skill(s) from {args.name}: {', '.join(names[:20])}"
                   + (" …" if len(names) > 20 else ""))
        except Exception as e:  # noqa: BLE001
            return _die(str(e))
        return 0
    if args.action == "bundles":
        from ..skill_bundles import list_bundles

        bundles = list_bundles()
        if not bundles:
            _print("(no skill bundles)")
            return 0
        for bundle in bundles:
            _print(
                f"  {bundle['slug']:<24} {', '.join(bundle.get('skills') or [])}"
                + (f"\n      {bundle['description']}" if bundle.get("description") else "")
            )
        return 0
    if args.action == "bundle":
        from ..skill_bundles import save_bundle

        if not args.name or not getattr(args, "members", ""):
            return _die("usage: aegis skills bundle <name> --members skill-a,skill-b")
        try:
            bundle = save_bundle(
                args.name,
                [s.strip() for s in args.members.split(",") if s.strip()],
                description=getattr(args, "description", "") or "",
                instruction=getattr(args, "instruction", "") or "",
            )
            _print(f"saved bundle {bundle['slug']} → {bundle['path']}")
        except Exception as e:  # noqa: BLE001
            return _die(str(e))
        return 0
    if args.action == "unbundle":
        from ..skill_bundles import delete_bundle

        if not args.name:
            return _die("usage: aegis skills unbundle <name>")
        _print("removed" if delete_bundle(args.name) else "not found")
        return 0
    # list
    for s in sorted(loader.available(), key=lambda s: s.name):
        _print(f"  {s.name:<24} {s.description[:80]}")
    return 0


def cmd_mcp(args, config: Config) -> int:
    from ..mcp.client import build_manager

    if args.action == "catalog":
        from ..mcp.client import catalog
        entries = catalog(config)
        if not entries:
            _print("(no MCP catalog entries configured in mcp.catalog)")
            return 0
        for e in entries:
            target = e.get("url") or " ".join([e.get("command", ""), *e.get("args", [])])
            _print(f"  {e['name']:<18} {e.get('description', '')}\n      {target.strip()}")
        return 0
    if args.action == "install":
        if not args.name:
            return _die("usage: aegis mcp install <catalog-name>")
        try:
            from ..mcp.client import install_from_catalog
            spec = install_from_catalog(config, args.name)
            target = spec.get("url") or " ".join([spec.get("command", ""), *spec.get("args", [])])
            _print(f"installed MCP server '{args.name}' → {target.strip()}")
            return 0
        except KeyError:
            return _die(f"MCP catalog entry '{args.name}' not found")
    if args.action == "serve":
        from ..mcp.server import run_mcp_server
        run_mcp_server(config)
        return 0
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
    # list / test / tools
    mgr = build_manager(config)
    if not mgr.clients:
        _print("(no MCP servers configured — `aegis mcp add <name> \"<command>\"`)")
        return 0
    for client in mgr.clients:
        if args.action == "tools" and args.name and client.name != args.name:
            continue
        kind = client.url or f"{client.command} {' '.join(client.args)}"
        try:
            client.connect()
            tools = client.list_tools()
            if args.action == "tools":
                _print(f"{client.name} ({kind})")
                for t in tools:
                    _print(f"  {t.get('name', ''):<24} {str(t.get('description', ''))[:90]}")
                try:
                    resources = client.list_resources()
                except Exception:  # noqa: BLE001
                    resources = []
                if resources:
                    _print("  resources:")
                    for r in resources:
                        _print(f"    {r.get('uri', ''):<32} {str(r.get('name') or r.get('description') or '')[:80]}")
                try:
                    prompts = client.list_prompts()
                except Exception:  # noqa: BLE001
                    prompts = []
                if prompts:
                    _print("  prompts:")
                    for p in prompts:
                        _print(f"    {p.get('name', ''):<32} {str(p.get('description', ''))[:80]}")
            else:
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


def cmd_rpc(args, config: Config) -> int:
    from ..rpc import run_rpc_server
    run_rpc_server(config)
    return 0


def cmd_cron(args, config: Config) -> int:
    from ..cron import CronStore, run_scheduler

    store = CronStore()
    if args.action == "add":
        if not args.schedule or not args.prompt:
            return _die('usage: aegis cron add "<schedule>" "<prompt>"')
        prompt = " ".join(args.prompt) if isinstance(args.prompt, list) else args.prompt
        skills = [s.strip() for s in (getattr(args, "skills", "") or "").split(",") if s.strip()]
        context_from = [s.strip() for s in (getattr(args, "context_from", "") or "").split(",") if s.strip()]
        for ref in context_from:
            if store.resolve(ref) is None:
                return _die(f"context_from job not found: {ref}")
        job = store.add(prompt=prompt, schedule=args.schedule,
                        script=getattr(args, "script", "") or "", skills=skills,
                        deliver=getattr(args, "deliver", "") or "",
                        no_agent=bool(getattr(args, "no_agent", False)),
                        context_from=context_from)
        _print(f"added cron {job.id}: [{job.schedule}] {job.prompt[:60]}")
        return 0
    if args.action == "rm":
        _print("removed" if store.remove(args.schedule or "") else "not found")
        return 0
    if args.action == "run":
        run_scheduler(config)
        return 0
    if args.action == "install":
        from ..daemon import install_cron_service
        res = install_cron_service(config, enable_now=not getattr(args, "no_start", False))
        _print(("✓ " if res.ok else "! ") + res.message)
        return 0 if res.ok else 1
    if args.action == "status":
        from ..daemon import cron_service_status
        _print(f"aegis-cron.service: {cron_service_status()}")
        return 0
    if args.action in ("start", "stop", "restart"):
        from ..daemon import control_cron_service
        res = control_cron_service(args.action)
        _print(("✓ " if res.ok else "! ") + res.message)
        return 0 if res.ok else 1
    if args.action == "uninstall":
        from ..daemon import remove_cron_service
        res = remove_cron_service()
        _print(("✓ " if res.ok else "! ") + res.message)
        return 0 if res.ok else 1
    # list
    for j in store.list():
        suffix = f"  <- {','.join(j.context_from)}" if j.context_from else ""
        _print(f"  {j.id}  [{j.schedule}]  {j.prompt[:60]}{suffix}")
    return 0


def cmd_profile(args, config: Config) -> int:
    from .. import profiles

    action = getattr(args, "profile_action", None) or "show"
    try:
        if action == "list":
            rows = profiles.list_profiles()
            _print(f"{'':2} {'profile':<18} {'model':<28} {'skills':>6} {'memory':>6} {'cron':>5} path")
            for row in rows:
                marker = "*" if row.active else " "
                _print(
                    f"{marker:2} {row.name:<18} {(row.model or '-'): <28.28} "
                    f"{row.skills:>6} {row.memories:>6} {row.cron_jobs:>5} {row.path}"
                )
            return 0
        if action == "use":
            active = profiles.use_profile(args.profile_name)
            _print(f"active profile: {active}")
            return 0
        if action == "create":
            clone_all = bool(getattr(args, "clone_all", False))
            clone_config = bool(getattr(args, "clone", False) or getattr(args, "clone_from", None))
            path = profiles.create_profile(
                args.profile_name,
                clone_from=getattr(args, "clone_from", None),
                clone_config=clone_config,
                clone_all=clone_all,
            )
            detail = "fresh"
            if clone_all:
                detail = f"full clone from {profiles.label(getattr(args, 'clone_from', None) or cfg.current_profile())}"
            elif clone_config:
                detail = f"clone from {profiles.label(getattr(args, 'clone_from', None) or cfg.current_profile())}"
            _print(f"created profile {args.profile_name}: {path} ({detail})")
            return 0
        if action == "clone":
            path = profiles.clone_profile(args.source_profile, args.profile_name,
                                          clone_all=bool(getattr(args, "clone_all", False)))
            _print(f"cloned profile {args.source_profile} -> {args.profile_name}: {path}")
            return 0
        if action == "show":
            requested = getattr(args, "profile_name", None)
            name = cfg.current_profile() if requested is None else cfg.profile_name(requested)
            if name and not profiles.profile_exists(name):
                return _die(f"profile '{name}' does not exist")
            info = profiles.profile_info(name)
            _print(f"Profile:  {info.name}{' (active)' if info.active else ''}")
            _print(f"Path:     {info.path}")
            _print(f"Model:    {info.model or '-'}")
            _print(f"Provider: {info.provider or '-'}")
            _print(f"Skills:   {info.skills}")
            _print(f"Memory:   {info.memories} non-empty line(s)")
            _print(f"Cron:     {info.cron_jobs} job(s)")
            return 0
        if action == "export":
            requested = getattr(args, "profile_name", None)
            name = cfg.current_profile() if requested is None else cfg.profile_name(requested)
            path = profiles.export_profile(
                name,
                getattr(args, "out", None),
                include_history=bool(getattr(args, "include_history", False)),
                include_secrets=bool(getattr(args, "include_secrets", False)),
            )
            _print(f"exported profile {profiles.label(name)}: {path}")
            return 0
        if action == "import":
            path = profiles.import_profile(args.archive, name=getattr(args, "name", None))
            _print(f"imported profile {path.name}: {path}")
            return 0
        return _die(f"unknown profile action: {action}")
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        return _die(str(exc))


# --------------------------------------------------------------------------- #
# tools / memory / config / sessions
# --------------------------------------------------------------------------- #
def cmd_tools(args, config: Config) -> int:
    from ..tools.registry import default_registry
    from ..surface import tool_inventory

    if getattr(args, "action", None) == "status":
        from ..tools.cloud import cmd_tools_status
        return cmd_tools_status(args, config)
    reg = default_registry()
    if getattr(args, "action", None) == "doctor":
        from ..tools.schema_validation import validate_tool_registry

        validation = validate_tool_registry(reg.all())
        _print("Tool schema validation:")
        _print(
            f"  {'✓' if validation.ok else '✗'} "
            f"{validation.valid}/{validation.total} valid"
            + (f" · {validation.warnings} warning(s)" if validation.warnings else "")
        )
        for issue in validation.issues[:12]:
            _print(f"  {issue.severity.upper():<7} {issue.tool:<18} {issue.path}: {issue.message}")
        if len(validation.issues) > 12:
            _print(f"  … {len(validation.issues) - 12} more schema issue(s)")
        _print("")
        _print("Tool availability:")
        unusable = 0
        for t in reg.all():
            ok, reason = t.available()
            if ok:
                _print(f"  ✓ {t.name:<16} {t.toolset}")
            else:
                unusable += 1
                _print(f"  ✗ {t.name:<16} {t.toolset:<9} {reason}")
        _print(f"\n{len(reg.all()) - unusable}/{len(reg.all())} tools usable in this environment.")
        return 1 if unusable or not validation.ok else 0
    inv = tool_inventory(config)
    enabled = set(inv.enabled_names)
    _print(f"enabled toolsets: {', '.join(inv.toolsets)}")
    _print(f"model-visible tools: {inv.enabled_count}/{inv.total_count}")
    if inv.disabled_sets:
        _print("disabled optional toolsets: " + ", ".join(
            f"{name} ({count})" for name, count in sorted(inv.disabled_sets.items())
        ))
    _print("")
    for t in reg.all():
        g = f"[{','.join(t.groups)}]" if t.groups else "[safe]"
        ok, reason = t.available()
        if not ok:
            mark, tail = "✗", f"  ← unavailable: {reason}"
        elif t.name in enabled:
            mark, tail = "✓", ""
        else:
            mark, tail = "–", ""
        _print(f"  {mark} {t.name:<14} {t.toolset:<8} {g:<22} {t.description.splitlines()[0]}{tail}")
    return 0


def cmd_plugins(args, config: Config) -> int:
    from .. import plugins as plugin_runtime
    from ..surface import plugin_inventory

    inv = plugin_inventory()
    action = getattr(args, "action", "list")
    if action == "path":
        _print(str(inv.path))
        return 0
    if action == "install":
        if not getattr(args, "name", None):
            return _die("usage: aegis plugins install <local-file-or-directory>")
        try:
            name = plugin_runtime.install(args.name, config, force=getattr(args, "force", False))
            _print(f"installed plugin '{name}'")
            return 0
        except Exception as e:  # noqa: BLE001
            return _die(str(e))
    if action == "enable":
        if plugin_runtime.enable(args.name, config):
            _print(f"enabled {args.name}")
            return 0
        return _die(f"plugin '{args.name}' not found")
    if action == "disable":
        if plugin_runtime.disable(args.name, config):
            _print(f"disabled {args.name}")
            return 0
        return _die(f"plugin '{args.name}' not found")
    if action == "remove":
        if plugin_runtime.remove(args.name, config):
            _print(f"removed {args.name}")
            return 0
        return _die(f"plugin '{args.name}' not found")
    _print(f"plugin dir: {inv.path}")
    _print(f"plugin files: {inv.files_count}")
    manifests = plugin_runtime.list_manifests(config)
    if manifests:
        _print("manifests:")
        for m in manifests:
            state = "on" if m.enabled else "off"
            _print(f"  {state:<3} {m.name:<22} {m.version or '-':<10} {m.description}")
    if inv.loaded_files:
        for path in inv.loaded_files:
            _print(f"  ✓ {Path(path).name}")
    elif not inv.errors:
        _print("  (none installed)")
    if inv.tools:
        _print("tools: " + ", ".join(inv.tools))
    if inv.channels:
        _print("channels: " + ", ".join(inv.channels))
    if inv.providers:
        _print("providers: " + ", ".join(inv.providers))
    if inv.errors:
        _print("errors:")
        for path, msg in inv.errors:
            _print(f"  ✗ {Path(path).name}: {msg}")
    else:
        _print("errors: none")
    return 1 if action == "doctor" and inv.errors else 0


def cmd_trace(args, config: Config) -> int:
    try:
        from ..tracing import TraceStore
    except Exception as e:  # noqa: BLE001
        return _die(f"trace store unavailable: {e}")
    store = TraceStore.from_config(config)
    action = getattr(args, "action", "list")
    status_filter = str(getattr(args, "status", "") or "").strip().lower()
    if action == "show":
        if not args.id:
            return _die("usage: aegis trace show <trace-id>")
        trace = store.get_trace(args.id)
        if not trace:
            return _die(f"trace '{args.id}' not found")
        if getattr(args, "json", False):
            _print(json.dumps(trace, indent=2))
            return 0
        _print(f"trace:   {trace['trace_id']}")
        _print(f"session: {trace.get('session_id') or '(none)'}")
        _print(f"status:  {trace.get('status')} · spans: {trace.get('span_count')} · "
               f"cache read/write: {trace.get('cache_read', 0):,}/{trace.get('cache_write', 0):,}")
        for r in trace.get("spans", []):
            label = r.get("tool_name") or r.get("model") or r.get("provider") or ""
            _print(f"  {r['span_id'][:14]:<14} {r['kind']:<16} {r['status']:<9} {label}")
        return 0
    if action == "export":
        traces = [store.get_trace(args.id)] if args.id else [
            store.get_trace(t["trace_id"]) for t in store.list_traces(
                session_id=args.session,
                limit=args.limit,
            )
        ]
        traces = [t for t in traces if t and _trace_status_matches(t, status_filter)]
        data = "\n".join(json.dumps(t) for t in traces)
        if args.out:
            Path(args.out).expanduser().write_text(data + ("\n" if data else ""), encoding="utf-8")
            _print(f"exported {len(traces)} trace(s) → {args.out}")
        else:
            _print(data)
        return 0
    if getattr(args, "spans", False):
        rows = store.list_spans(session_id=args.session, limit=args.limit)
        if status_filter:
            rows = [r for r in rows if status_filter in str(r.get("status", "")).lower()]
        if getattr(args, "json", False):
            _print(json.dumps(rows, indent=2))
            return 0
        for r in rows:
            _print(f"  {r['started_at']}  {r['trace_id'][:12]}  {r['kind']:<16} "
                   f"{r['status']:<9} {r.get('tool_name') or r.get('model') or ''}")
        if not rows:
            _print("(no trace spans)")
        return 0
    rows = store.list_traces(session_id=args.session, limit=args.limit)
    if status_filter:
        rows = [r for r in rows if _trace_status_matches(r, status_filter)]
    if getattr(args, "json", False):
        _print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        _print("(no traces)")
        return 0
    for r in rows:
        _print(f"  {r['trace_id']:<18} {r.get('status', ''):<8} "
               f"{r.get('span_count', r.get('spans', 0)):>3} span(s)  "
               f"{r.get('session_id') or ''}  {r.get('started_at') or ''}")
    return 0


def _trace_status_matches(trace: dict, status_filter: str) -> bool:
    return not status_filter or status_filter in str(trace.get("status", "")).lower()


def cmd_eval(args, config: Config) -> int:
    try:
        from ..evals import EvalStore, run_suite
    except Exception as e:  # noqa: BLE001
        return _die(f"evals unavailable: {e}")
    store = EvalStore.from_config(config)
    action = getattr(args, "action", "list")
    if action == "show":
        if not args.path:
            return _die("usage: aegis eval show <run-id>")
        run = store.get_run(args.path)
        if not run:
            return _die(f"eval run '{args.path}' not found")
        if args.json:
            _print(json.dumps(run, indent=2))
            return 0
        _print(f"eval:  {run['id']}  suite={run.get('suite')}  "
               f"{run.get('passed')}/{run.get('total')} passed  score={run.get('score')}")
        for result in run.get("results", []):
            mark = "✓" if result.get("passed") else "✗"
            _print(f"  {mark} {result.get('case') or result.get('id')}  score={result.get('score')}")
            for grade in result.get("grades", []):
                _print(f"      {grade.get('name')}: {grade.get('reason', '')}")
        return 0
    if action == "run":
        if not args.path:
            return _die("usage: aegis eval run <suite.jsonl>")
        result = run_suite(args.path, config=config, store=store)
        _print(json.dumps(result, indent=2) if args.json else
               f"{result['suite']}: {result['passed']}/{result['total']} passed")
        return 0 if result["passed"] == result["total"] else 1
    rows = store.list_runs(limit=args.limit)
    if args.json:
        _print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        _print(f"  {r['id']}  {r['suite']:<24} {r['passed']}/{r['total']}  {r['created_at']}")
    if not rows:
        _print("(no eval runs)")
    return 0


def cmd_status(args, config: Config) -> int:
    from .. import config as cfg
    from ..providers.registry import provider_report
    from ..surface import plugin_inventory, skill_inventory, tool_inventory

    report_error = ""
    try:
        provider = provider_report(config)
    except Exception as exc:  # noqa: BLE001
        provider = {"active": {}}
        report_error = str(exc)
    active = provider.get("active") if isinstance(provider.get("active"), dict) else {}
    auth = active.get("auth") if isinstance(active.get("auth"), dict) else {}

    tools = tool_inventory(config)
    skills = skill_inventory(config)
    plugins = plugin_inventory()
    mcp_servers = config.get("mcp.servers", {}) or {}
    channels = list(config.get("gateway.channels", []) or [])

    state: dict[str, object] = {}
    try:
        from ..session import SessionStore
        state["sessions"] = len(SessionStore().list(limit=100000))
    except Exception:  # noqa: BLE001
        pass
    mem = cfg.sub("memories", "MEMORY.md")
    if os.path.exists(mem):
        state["memory_bytes"] = os.path.getsize(mem)
    tp = config.get("trajectory.path", "trajectories.jsonl")
    tp = tp if os.path.isabs(tp) else cfg.sub(tp)
    n_traj = sum(1 for _ in open(tp, encoding="utf-8")) if os.path.exists(tp) else 0
    state["trajectory"] = {
        "enabled": bool(config.get("trajectory.enabled", False)),
        "captured": n_traj,
        "path": str(tp),
    }
    try:
        from .. import usage_log
        r = usage_log.cost_report(30, config)
        state["cost_30d"] = {"usd": float(r["total_cost_usd"]), "calls": int(r["calls"])}
    except Exception:  # noqa: BLE001
        pass
    try:
        home = cfg.get_home()
        total = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _, fs in os.walk(home) for f in fs
                    if os.path.exists(os.path.join(dp, f)))
        state["disk_kb"] = int(total / 1024)
    except Exception:  # noqa: BLE001
        pass

    services: dict[str, str] = {}
    try:
        from ..daemon import status as daemon_status
        services = {str(unit): str(state) for unit, state in daemon_status().items()}
    except Exception as exc:  # noqa: BLE001
        services = {"unavailable": str(exc)}

    host = config.get("server.dashboard_host", "127.0.0.1")
    port = int(config.get("server.dashboard_port", 9119))
    token = config.get("server.dashboard_token")
    url = f"http://{host}:{port}/" + ("?token=[REDACTED]" if token else "")
    display = {
        "reasoning": config.get("display.reasoning", "off") or "off",
        "timestamps": bool(config.get("display.timestamps", False)),
        "status_footer": bool(config.get("display.status_footer", True)),
        "tool_progress": config.get("display.tool_progress", True),
        "tool_progress_grouping": bool(config.get("display.tool_progress_grouping", True)),
        "memory_notifications": bool(config.get("display.memory_notifications", True)),
        "theme": config.get("display.theme", "") or "(default)",
    }
    terminal = {
        "backend": config.get("tools.terminal_backend"),
        "exec_mode": config.get("tools.exec_mode"),
        "busy_mode": _busy_mode(config.get("gateway.busy_mode", "queue")),
        "subagent_backend": config.get("tools.subagent_terminal_backend") or "(inherit)",
        "allow_local_fallback": bool(config.get("tools.allow_local_fallback")),
        "timeout_seconds": config.get("tools.terminal_lifetime_seconds"),
    }
    surface = {
        "toolsets": list(tools.toolsets),
        "tools": {"enabled": tools.enabled_count, "total": tools.total_count},
        "skills": {"available": skills.available_count, "bundled": skills.bundled_count},
        "plugins": {
            "files": plugins.files_count,
            "tools": len(plugins.tools),
            "channels": len(plugins.channels),
            "providers": len(plugins.providers),
            "errors": len(plugins.errors),
        },
        "mcp_servers": len(mcp_servers),
        "channels": channels,
    }
    payload = {
        "ok": not report_error,
        "object": "aegis.status",
        "version": __version__,
        "paths": {
            "home": str(cfg.get_home()),
            "config": str(cfg.config_path()),
            "workspace": str(cfg.sub("workspace")),
        },
        "model": {
            "provider": active.get("name") or config.get("model.provider"),
            "model": active.get("model") or config.get("model.default"),
            "api_mode": active.get("api_mode") or "",
            "base_url": active.get("base_url") or config.get("model.base_url") or "",
            "context_length": active.get("context_length") or config.get("model.context_length") or 0,
            "max_output_tokens": active.get("max_output_tokens") or 0,
            "auth": auth,
            "capability_summary": active.get("capability_summary") or "",
            "capability_flags": active.get("capability_flags") or {},
            "error": report_error,
        },
        "surface": surface,
        "display": display,
        "terminal": terminal,
        "state": state,
        "services": services,
        "dashboard": {"url": url},
    }
    if getattr(args, "json", False):
        safe_payload = _redact_string_values(payload)
        _print(json.dumps(safe_payload, indent=2, sort_keys=True))
        return 0

    unicode_ui = _terminal_unicode_enabled()
    _print(_terminal_title("AEGIS Status", unicode=unicode_ui))
    _print()
    _print(_terminal_section("Paths", unicode=unicode_ui))
    _print(f"  Version:   {__version__}")
    _print(f"  Home:      {cfg.get_home()}")
    _print(f"  Config:    {cfg.config_path()}")
    _print(f"  Workspace: {cfg.sub('workspace')}")
    _print()
    _print(_terminal_section("Model", unicode=unicode_ui))
    _print(f"  Provider:  {payload['model']['provider']}")
    _print(f"  Model:     {payload['model']['model']}")
    _print(f"  Auth:      {auth.get('description', 'unknown')} ({'ready' if auth.get('available') else 'missing'})")
    _print(f"  API mode:  {payload['model']['api_mode'] or 'unknown'}")
    _print(f"  Context:   {payload['model']['context_length'] or 'unknown'}")
    _print(f"  Output:    {payload['model']['max_output_tokens'] or 'unknown'}")
    if payload["model"]["capability_summary"]:
        _print(f"  Caps:      {payload['model']['capability_summary']}")
    if report_error:
        _print(f"  Error:     {report_error}")
    _print()
    _print(_terminal_section("Surface", unicode=unicode_ui))
    _print(
        f"  Inventory: tools: {tools.enabled_count}/{tools.total_count}, "
        f"skills: {skills.available_count}, plugins: {plugins.files_count}"
    )
    _print(f"  Toolsets:  {', '.join(tools.toolsets) or 'none'}")
    _print(f"  Tools:     {tools.enabled_count}/{tools.total_count} model-visible")
    _print(f"  Skills:    {skills.available_count} available ({skills.bundled_count} bundled)")
    _print(f"  Plugins:   {plugins.files_count} file(s), {len(plugins.tools)} tool(s), "
           f"{len(plugins.channels)} channel(s), {len(plugins.providers)} provider(s)")
    if plugins.errors:
        _print(f"             {len(plugins.errors)} error(s); run `aegis plugins doctor`")
    _print(f"  MCP:       {len(mcp_servers)} server(s)")
    _print(f"  Channels:  {', '.join(channels) or 'none'}")
    _print()
    _print(_terminal_section("Display", unicode=unicode_ui))
    _print(f"  Reasoning:   {display['reasoning']}")
    _print(f"  Timestamps:  {display['timestamps']}")
    _print(f"  Status line: {display['status_footer']}")
    _print(f"  Tool progress: {display['tool_progress']}")
    _print(f"  Tool grouping: {display['tool_progress_grouping']}")
    _print(f"  Theme:       {display['theme']}")
    _print()
    _print(_terminal_section("Terminal", unicode=unicode_ui))
    _print(f"  Backend:     {terminal['backend']}")
    _print(f"  Exec mode:   {terminal['exec_mode']}")
    _print(f"  Input mode:  {_busy_mode_status(terminal['busy_mode'])}")
    _print(f"  Subagents:   {terminal['subagent_backend']}")
    _print(f"  Local fallback: {terminal['allow_local_fallback']}")
    _print(f"  Timeout:     {terminal['timeout_seconds']}s")
    _print("  Config edit: aegis config edit | aegis config edit --secrets")
    _print()
    _print(_terminal_section("State", unicode=unicode_ui))
    if "sessions" in state:
        _print(f"  Sessions:   {state['sessions']}")
    if "memory_bytes" in state:
        _print(f"  Memory:     {state['memory_bytes']:,} bytes")
    traj = state.get("trajectory", {})
    if isinstance(traj, dict):
        _print(f"  Trajectory: {'on' if traj.get('enabled') else 'off'} ({traj.get('captured', 0)} captured)")
    cost = state.get("cost_30d", {})
    if isinstance(cost, dict):
        _print(f"  Cost (30d): ~${float(cost.get('usd', 0.0)):.4f} over {cost.get('calls', 0)} call(s)")
    if "disk_kb" in state:
        _print(f"  Disk:       {state['disk_kb']} KB in {cfg.get_home()}")
    _print()
    _print(_terminal_section("Services", unicode=unicode_ui))
    for unit, service_state in services.items():
        _print(f"  {unit}: {service_state}")
    _print()
    _print(_terminal_section("Dashboard", unicode=unicode_ui))
    _print(f"  {url}")
    return 0


def cmd_memory(args, config: Config) -> int:
    from ..memory import MemoryStore

    store = MemoryStore()
    target = "user" if args.user else "memory"
    if args.action == "add":
        if not args.text:
            return _die("usage: aegis memory add <text> [--user]")
        _print(store.add(target, " ".join(args.text)))
        return 0
    if args.action == "replace":
        if not args.old_text or not args.text:
            return _die("usage: aegis memory replace --old-text <match> <new text> [--user]")
        _print(store.replace(target, args.old_text, " ".join(args.text)))
        return 0
    if args.action == "remove":
        match = args.old_text or " ".join(args.text)
        if not match:
            return _die("usage: aegis memory remove <match> [--user]")
        _print(store.remove(target, match))
        return 0
    if args.action == "clear":
        for e in store.entries(target):
            store.remove(target, e)
        _print(f"cleared {target}")
        return 0
    if args.action == "status":
        for tgt in ("memory", "user"):
            path = store._path(tgt)
            _print(f"{path.name}: {store.usage(tgt)}")
            _print(f"  path: {path}")
            _print(f"  entries: {len(store.entries(tgt))}")
        return 0
    _print("# MEMORY\n" + (store.raw("memory") or "(empty)"))
    _print("\n# USER\n" + (store.raw("user") or "(empty)"))
    return 0


def _config_default_values() -> dict[str, object]:
    from ..config import DEFAULT_CONFIG

    def flat(data: dict, prefix: str = "") -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in data.items():
            path = f"{prefix}{key}"
            out[path] = value
            if isinstance(value, dict):
                out.update(flat(value, path + "."))
        return out

    return flat(DEFAULT_CONFIG)


_CONFIG_SET_EXTENSION_ROOTS = {
    "auxiliary.approval",
    "auxiliary.architect",
    "auxiliary.compaction",
    "auxiliary.curator",
    "auxiliary.kanban_decomposer",
    "auxiliary.mcp",
    "auxiliary.session_summary",
    "auxiliary.trajectory_compression",
    "auxiliary.vision",
    "auxiliary.web_extract",
    "credential_pools",
    "display.platforms",
    "gateway.profiles",
    "hooks",
    "lsp.servers",
    "mcp.servers",
    "onboarding.seen",
    "skills.bundles",
}


_CONFIG_KEY_ALIASES = {
    "model.api_base": "model.base_url",
}


def _normalize_config_key(key: str) -> str:
    return _CONFIG_KEY_ALIASES.get(key, key)


def _config_set_secret_key(key: str) -> bool:
    low = key.lower()
    return "." not in key and (key.isupper() or any(low.endswith(s) for s in cfg.SECRET_SUFFIXES))


def _config_key_known_or_extension(key: str) -> bool:
    defaults = _config_default_values()
    if key in defaults:
        return True
    parts = key.split(".")
    for index in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:index])
        if isinstance(defaults.get(prefix), list):
            return True
    return any(key == root or key.startswith(root + ".") for root in _CONFIG_SET_EXTENSION_ROOTS)


def _parse_config_set_value(key: str, raw_parts: object) -> tuple[object, str]:
    from ..config import _coerce, config_enum_error

    if raw_parts is None:
        return None, "usage: aegis config set <key> <value>"
    if isinstance(raw_parts, list):
        if not raw_parts:
            return None, "usage: aegis config set <key> <value>"
        raw = " ".join(str(part) for part in raw_parts)
    else:
        raw = str(raw_parts)

    defaults = _config_default_values()
    expected = defaults.get(key)
    stripped = raw.strip()

    if isinstance(expected, list):
        if not stripped:
            return [], ""
        if stripped.startswith("["):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                return None, "value must be a JSON array or comma-separated list"
            return (value, "") if isinstance(value, list) else (None, "value must be a list")
        return ([part.strip() for part in raw.split(",") if part.strip()], "")

    if isinstance(expected, dict):
        if not stripped:
            return {}, ""
        if not stripped.startswith("{"):
            return None, "value must be a JSON object"
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return None, "value must be a JSON object"
        return (value, "") if isinstance(value, dict) else (None, "value must be an object")

    value = _coerce(raw)
    if expected is None or key not in defaults:
        return value, ""
    if isinstance(expected, bool) and not isinstance(value, bool):
        return None, "value must be a boolean"
    if isinstance(expected, int) and not isinstance(expected, bool) and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        return None, "value must be an integer"
    if isinstance(expected, float) and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        return None, "value must be a number"
    if isinstance(expected, str) and not isinstance(value, str):
        return None, "value must be a string"
    enum_error = config_enum_error(key, value)
    if enum_error:
        return None, enum_error
    return value, ""


def cmd_config(args, config: Config) -> int:
    json_mode = bool(getattr(args, "json", False))

    def provider_label(name: str, display_name: str | None = None) -> str:
        labels = {
            "anthropic": "Anthropic",
            "cerebras": "Cerebras",
            "dashscope": "DashScope",
            "deepseek": "DeepSeek",
            "fireworks": "Fireworks",
            "google": "Google",
            "groq": "Groq",
            "huggingface": "Hugging Face",
            "hyperbolic": "Hyperbolic",
            "kimi": "Kimi",
            "minimax": "MiniMax",
            "mistral": "Mistral",
            "novita": "Novita",
            "nvidia": "NVIDIA",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "perplexity": "Perplexity",
            "qwen": "Qwen",
            "sambanova": "SambaNova",
            "stepfun": "StepFun",
            "together": "Together",
            "xai": "xAI",
            "zai": "Z.ai",
        }
        key = str(name or "").strip().lower()
        if key in labels:
            return labels[key]
        raw = str(display_name or name or "").strip()
        return raw.replace("_", " ").replace("-", " ").title() if raw else "Provider"

    def api_key_specs() -> list[tuple[str, tuple[str, ...]]]:
        specs: list[tuple[str, tuple[str, ...]]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()

        def add(label: str, names) -> None:
            clean = tuple(str(name).strip() for name in names if str(name).strip())
            if not clean:
                return
            key = (str(label).strip().lower(), clean)
            if key in seen:
                return
            seen.add(key)
            specs.append((str(label).strip() or clean[0], clean))

        rows: dict[str, dict] = {}
        try:
            from ..providers import registry

            rows = {
                str(row.get("name") or ""): row
                for row in registry.oauth_catalog(config)
                if row.get("env_vars")
            }
        except Exception:  # noqa: BLE001
            rows = {}

        preferred = (
            "openrouter", "openai", "anthropic", "google", "groq", "deepseek",
            "xai", "mistral", "together",
        )
        for name in preferred:
            row = rows.pop(name, None)
            if row:
                add(provider_label(name, row.get("display_name")), row.get("env_vars") or [])
        for name, row in sorted(rows.items(), key=lambda item: provider_label(item[0], item[1].get("display_name")).lower()):
            add(provider_label(name, row.get("display_name")), row.get("env_vars") or [])

        for label, names in (
            ("OpenAI (STT/TTS)", ("VOICE_TOOLS_OPENAI_KEY",)),
            ("Exa", ("EXA_API_KEY",)),
            ("Parallel", ("PARALLEL_API_KEY",)),
            ("Firecrawl", ("FIRECRAWL_API_KEY",)),
            ("Tavily", ("TAVILY_API_KEY",)),
            ("Browserbase", ("BROWSERBASE_API_KEY",)),
            ("Browser Use", ("BROWSER_USE_API_KEY",)),
            ("FAL", ("FAL_KEY", "FAL_API_KEY")),
        ):
            add(label, names)
        return specs

    api_keys = api_key_specs()

    def masked_env_preview(value: str) -> str:
        return "*" * min(max(len(value), 4), 12)

    def configured_env(*names: str) -> str:
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                return f"(set, {len(value)} chars, {name}={masked_env_preview(value)})"
        return "(not set)"

    def env_status(*names: str) -> dict[str, object]:
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                return {
                    "set": True,
                    "name": name,
                    "source": name,
                    "chars": len(value),
                    "preview": masked_env_preview(value),
                }
        return {"set": False, "name": names[0] if names else "", "source": "", "chars": 0, "preview": ""}

    def collect_platform_statuses() -> tuple[dict[str, str], dict[str, dict[str, object]]]:
        try:
            from ..platforms.helpers import PLATFORM_METADATA, normalize_platform_name
        except Exception:  # noqa: BLE001
            PLATFORM_METADATA = {}

            def normalize_platform_name(value, *, default="webhook"):
                return str(value or default).strip().lower()

        gateway_channels = {
            normalize_platform_name(item, default="")
            for item in (config.get("gateway.channels", []) or [])
            if str(item or "").strip()
        }
        statuses: dict[str, str] = {}
        details: dict[str, dict[str, object]] = {}
        for platform, metadata in sorted(
            PLATFORM_METADATA.items(),
            key=lambda item: str(item[1].get("display_name") or item[0]).lower(),
        ):
            required = [str(name) for name in metadata.get("required_env", []) if str(name)]
            optional = [str(name) for name in metadata.get("optional_env", []) if str(name)]
            required_set = [name for name in required if os.environ.get(name, "").strip()]
            optional_set = [name for name in optional if os.environ.get(name, "").strip()]
            configured = platform in gateway_channels
            if required:
                configured = configured or len(required_set) == len(required)
            else:
                configured = configured or bool(optional_set)
            state = "configured" if configured else "not configured"
            statuses[platform] = state
            details[platform] = {
                "display_name": str(metadata.get("display_name") or platform),
                "status": state,
                "enabled_in_gateway": platform in gateway_channels,
                "required_env": required,
                "optional_env": optional,
                "required_set": required_set,
                "optional_set": optional_set,
                "transport": metadata.get("transport"),
            }
        return statuses, details

    def active_provider_credentials_status() -> str:
        provider = str(config.get("model.provider") or "").strip()
        if not provider:
            return "(no active provider)"
        try:
            from ..providers import registry

            spec = registry.get_spec(provider, config)
        except Exception:  # noqa: BLE001
            spec = None
        if spec is None:
            return f"{provider}: unknown provider"
        env_vars = [str(name) for name in (getattr(spec, "env_vars", None) or []) if str(name)]
        if not env_vars:
            return f"{provider}: not required"
        configured = [name for name in env_vars if os.environ.get(name, "").strip()]
        if configured:
            return f"{provider}: configured via {', '.join(configured)}"
        return f"{provider}: missing one of {', '.join(env_vars)}"

    def print_config_usage() -> None:
        _print("Usage: aegis config set <key> <value>")
        _print()
        _print("Examples:")
        _print("  aegis config set model.provider openai")
        _print("  aegis config set model gpt-5.5")
        _print("  aegis config set model.base_url http://localhost:8080/v1")
        _print("  aegis config set OPENAI_API_KEY sk-...")

    def editor_command(target: Path) -> list[str] | None:
        import shutil

        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if editor:
            return [*shlex.split(editor), str(target)]
        candidates = (
            ("notepad", "code", "vim", "vi", "nano")
            if sys.platform == "win32"
            else ("nano", "vim", "vi", "code")
        )
        for candidate in candidates:
            if shutil.which(candidate):
                return [candidate, str(target)]
        return None

    def edit_backup_path(target: Path) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        candidate = target.with_name(f"{target.name}.bak-{stamp}")
        index = 1
        while candidate.exists():
            candidate = target.with_name(f"{target.name}.bak-{stamp}-{index}")
            index += 1
        return candidate

    def validate_yaml_config_edit(target: Path) -> str:
        parsed, errors = cfg.parse_config_file(target)
        if not errors:
            errors.extend(cfg.config_type_errors(parsed))
        return "; ".join(errors)

    def redacted_value(key: str, value):
        return redact_secret_values({key: value}).get(key)

    def json_error(message: str, *, key: str = "") -> int:
        _print(json.dumps({
            "ok": False,
            "object": "aegis.config.result",
            "action": args.action,
            "key": key,
            "error": message,
        }, indent=2, sort_keys=True))
        return 1

    def show_summary() -> int:
        compression = config.get("agent.compression", {}) or {}
        platforms = config.get("display.platforms", {}) or {}
        file_errors = cfg.validate_config_file()
        type_errors = cfg.config_type_errors(config.data)
        platform_statuses, platform_details = collect_platform_statuses()
        active_profile = cfg.current_profile() or "default"
        model_view = {
            key: config.get(f"model.{key}")
            for key in ("provider", "default", "base_url", "api_mode", "context_length")
            if config.get(f"model.{key}") not in (None, "")
        }
        server_host = config.get("server.host") or "127.0.0.1"
        server_port = config.get("server.port") or 8790
        dashboard_host = config.get("server.dashboard_host") or server_host
        dashboard_port = config.get("server.dashboard_port") or 9119
        desktop_dir = Path(os.environ.get("AEGIS_DESKTOP_DIR") or cfg.get_home() / "desktop").expanduser()
        desktop_state = "not installed"
        if (desktop_dir / "package.json").exists():
            desktop_state = "source ready"
        if any(
            path.exists()
            for path in (
                desktop_dir / "release" / "linux-unpacked" / "AEGIS",
                desktop_dir / "release" / "win-unpacked" / "AEGIS.exe",
                desktop_dir / "release" / "mac" / "AEGIS.app" / "Contents" / "MacOS" / "AEGIS",
                desktop_dir / "release" / "mac-arm64" / "AEGIS.app" / "Contents" / "MacOS" / "AEGIS",
            )
        ):
            desktop_state = "packaged"
        timezone = config.get("timezone") or os.environ.get("TZ") or time.tzname[0] or "(server-local)"
        setup_descriptions = {
            "model": "Configure provider/model",
            "terminal": "Configure execution approval",
            "tools": "Configure model-visible toolsets",
            "gateway": "Configure messaging channels",
            "agent": "Configure agent behavior",
            "web": "Configure web/browser tools",
            "memory": "Configure memory backends",
            "dashboard": "Configure dashboard services",
            "services": "Install/start services",
        }
        setup_commands = [f"aegis setup {section}" for section in _SETUP_SECTIONS]
        config_setup_commands = [f"aegis config setup {section}" for section in _SETUP_SECTIONS]
        commands = [
            "aegis config view",
            "aegis config status",
            "aegis config paths",
            "aegis config edit",
            "aegis config edit --secrets",
            "aegis config get <key>",
            "aegis config set <key> <value>",
            "aegis config reset <key>",
            "aegis config doctor",
            "aegis config setup",
            "aegis tui",
            *config_setup_commands,
            *setup_commands,
            "aegis setup",
        ]
        if getattr(args, "json", False):
            payload = {
                "object": "aegis.config.status",
                "paths": {
                    "config": str(cfg.config_path()),
                    "secrets": str(cfg.env_path()),
                    "home": str(cfg.get_home()),
                    "workspace": str(cfg.workspace_dir()),
                    "profile": active_profile,
                    "install": str(Path(__file__).resolve().parents[2]),
                },
                "services": {
                    "api_adapter": f"http://{server_host}:{server_port}",
                    "dashboard": f"http://{dashboard_host}:{dashboard_port}",
                    "frontend": config.get("dashboard.frontend"),
                    "api_auth_configured": bool(config.get("server.api_key")),
                    "dashboard_auth_configured": bool(config.get("server.dashboard_token")),
                    "desktop": {"state": desktop_state, "path": str(desktop_dir)},
                },
                "api_keys": {
                    label: {
                        **env_status(*names),
                        "env": list(names),
                    }
                    for label, names in api_keys
                },
                "model": {
                    "active": model_view,
                    "provider": config.get("model.provider"),
                    "default": config.get("model.default"),
                    "base_url": config.get("model.base_url"),
                    "max_turns": config.get("agent.max_iterations"),
                },
                "display": {
                    "personality": config.get("agent.personality", "none") or "none",
                    "reasoning": config.get("display.reasoning", "off") or "off",
                    "model_effort": config.get("agent.reasoning_effort", "off") or "off",
                    "timestamps": config.get("display.timestamps", False),
                    "status_footer": config.get("display.status_footer", True),
                    "tool_progress": config.get("display.tool_progress", True),
                    "tool_progress_grouping": config.get("display.tool_progress_grouping", True),
                    "memory_notifications": config.get("display.memory_notifications", True),
                    "theme": config.get("display.theme", ""),
                    "platforms": sorted(platforms) if isinstance(platforms, dict) else [],
                },
                "terminal": {
                    "backend": config.get("tools.terminal_backend"),
                    "exec_mode": config.get("tools.exec_mode"),
                    "busy_mode": _busy_mode(config.get("gateway.busy_mode", "queue")),
                    "busy_mode_hint": _BUSY_MODE_HINTS[
                        _busy_mode(config.get("gateway.busy_mode", "queue"))
                    ],
                    "subagent_backend": config.get("tools.subagent_terminal_backend") or "(inherit)",
                    "allow_local_fallback": config.get("tools.allow_local_fallback"),
                    "working_dir": str(Path.cwd()),
                    "timeout_seconds": config.get("tools.terminal_lifetime_seconds"),
                },
                "timezone": timezone,
                "context_compression": {
                    "enabled": True,
                    "threshold_percent": int(float(compression.get("threshold", 0.5) or 0.5) * 100),
                    "target_ratio_percent": int(float(compression.get("tail_fraction", 0.25) or 0.25) * 100),
                    "protect_last": compression.get("preserve_last", 20),
                    "protect_first": compression.get("preserve_first", 3),
                },
                "messaging_platforms": platform_statuses,
                "messaging_platform_details": platform_details,
                "validation": {
                    "config_yaml": "ok" if not file_errors else "error",
                    "config_yaml_errors": file_errors,
                    "value_types": "ok" if not type_errors else "error",
                    "type_errors": type_errors,
                    "secrets_file": "present" if cfg.env_path().exists() else "not present",
                },
                "commands": commands,
            }
            _print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        unicode_ui = _terminal_unicode_enabled()
        _print(_terminal_title("AEGIS Configuration", unicode=unicode_ui))
        _print()
        _print(_terminal_section("Paths", unicode=unicode_ui))
        _print(f"  Config:       {cfg.config_path()}")
        _print(f"  Secrets:      {cfg.env_path()}")
        _print(f"  Home:         {cfg.get_home()}")
        _print(f"  Workspace:    {cfg.workspace_dir()}")
        _print(f"  Profile:      {active_profile}")
        _print(f"  Install:      {Path(__file__).resolve().parents[2]}")
        _print()
        _print(_terminal_section("Services", unicode=unicode_ui))
        _print(f"  API adapter:  http://{server_host}:{server_port}")
        _print(f"  Dashboard:    http://{dashboard_host}:{dashboard_port}")
        _print(f"  Frontend:     {config.get('dashboard.frontend')}")
        _print(f"  API auth:     {'configured' if config.get('server.api_key') else 'not configured'}")
        _print(f"  Dashboard auth: {'configured' if config.get('server.dashboard_token') else 'not configured'}")
        _print(f"  Desktop:      {desktop_state} ({desktop_dir})")
        _print()
        _print(_terminal_section("API Keys", unicode=unicode_ui))
        for label, names in api_keys:
            _print(f"  {label:<17} {configured_env(*names)}")
        _print()
        _print(_terminal_section("Model", unicode=unicode_ui))
        _print(f"  Active:  {model_view}")
        _print(f"  Provider: {config.get('model.provider')}")
        _print(f"  Model:    {config.get('model.default')}")
        if config.get("model.base_url"):
            _print(f"  Base URL: {config.get('model.base_url')}")
        _print(f"  Max turns: {config.get('agent.max_iterations')}")
        _print()
        _print(_terminal_section("Display", unicode=unicode_ui))
        _print(f"  Personality: {config.get('agent.personality', 'none') or 'none'}")
        _print(f"  Reasoning:   {config.get('display.reasoning', 'off') or 'off'}")
        _print(f"  Model effort: {config.get('agent.reasoning_effort', 'off') or 'off'}")
        _print(f"  Theme:       {config.get('display.theme') or '(default)'}")
        _print(f"  Timestamps:  {config.get('display.timestamps', False)}")
        _print(f"  Status line: {config.get('display.status_footer', True)}")
        _print(f"  Tool progress: {config.get('display.tool_progress', True)}")
        _print(f"  Tool grouping: {config.get('display.tool_progress_grouping', True)}")
        _print(f"  Memory notes: {config.get('display.memory_notifications', True)}")
        if platforms:
            _print(f"  Platforms:   {', '.join(sorted(platforms))}")
        _print()
        _print(_terminal_section("Terminal", unicode=unicode_ui))
        _print(f"  Backend:     {config.get('tools.terminal_backend')}")
        _print(f"  Exec mode:   {config.get('tools.exec_mode')}")
        _print(f"  Input mode:  {_busy_mode_status(config.get('gateway.busy_mode', 'queue'))}")
        _print(f"  Subagents:   {config.get('tools.subagent_terminal_backend') or '(inherit)'}")
        _print(f"  Local fallback: {config.get('tools.allow_local_fallback')}")
        _print(f"  Working dir: {Path.cwd()}")
        _print(f"  Timeout:     {config.get('tools.terminal_lifetime_seconds')}s")
        _print()
        _print(_terminal_section("Timezone", unicode=unicode_ui))
        _print(f"  Timezone:    {timezone}")
        _print()
        _print(_terminal_section("Context Compression", unicode=unicode_ui))
        _print("  Enabled:        yes")
        _print(f"  Threshold:      {int(float(compression.get('threshold', 0.5) or 0.5) * 100)}%")
        _print(f"  Target ratio:   {int(float(compression.get('tail_fraction', 0.25) or 0.25) * 100)}% preserved")
        _print(f"  Protect last:   {compression.get('preserve_last', 20)} messages")
        _print(f"  Protect first:  {compression.get('preserve_first', 3)} messages")
        _print()
        _print(_terminal_section("Messaging Platforms", unicode=unicode_ui))
        for platform, detail in platform_details.items():
            label = str(detail.get("display_name") or platform)
            _print(f"  {label + ':':<11} {platform_statuses.get(platform, 'not configured')}")
        _print()
        _print(_terminal_section("Validation", unicode=unicode_ui))
        _print(f"  Config YAML:  {'ok' if not file_errors else str(len(file_errors)) + ' error(s)'}")
        _print(f"  Value types:  {'ok' if not type_errors else str(len(type_errors)) + ' error(s)'}")
        _print(f"  Secrets file: {'present' if cfg.env_path().exists() else 'not present'}")
        _print()
        _print(_terminal_section("Commands", unicode=unicode_ui))
        _print("  aegis config view                 # Show this configuration screen")
        _print("  aegis config status               # Show this configuration screen")
        _print("  aegis config paths                # Show config/secrets/home/install paths")
        _print("  aegis config edit                 # Edit config file")
        _print("  aegis config edit --secrets       # Edit local .env secrets")
        _print("  aegis config get <key>            # Print a config value")
        _print("  aegis config set <key> <value>    # Update a config value")
        _print("  aegis config reset <key>          # Reset a config key or section to defaults")
        _print("  aegis config doctor               # Validate config and provider credentials")
        _print("  aegis config setup                # Run setup wizard from config")
        _print("  aegis tui                         # Open terminal cockpit with config/edit actions")
        for section in _SETUP_SECTIONS:
            note = setup_descriptions.get(section, f"Configure {section}")
            _print(f"  aegis config setup {section:<9} # {note}")
        for section in _SETUP_SECTIONS:
            note = setup_descriptions.get(section, f"Configure {section}")
            _print(f"  aegis setup {section:<16} # {note}")
        _print("  aegis setup                       # Run setup wizard")
        return 0

    if args.action == "path":
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.path",
                "path": str(cfg.config_path()),
            }, indent=2, sort_keys=True))
            return 0
        _print(str(cfg.config_path()))
        return 0
    if args.action == "env-path":
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.env_path",
                "path": str(cfg.env_path()),
            }, indent=2, sort_keys=True))
            return 0
        _print(str(cfg.env_path()))
        return 0
    if args.action == "paths":
        paths_payload = {
            "config": str(cfg.config_path()),
            "secrets": str(cfg.env_path()),
            "home": str(cfg.get_home()),
            "workspace": str(cfg.workspace_dir()),
            "profile": cfg.current_profile() or "default",
            "install": str(Path(__file__).resolve().parents[2]),
        }
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.paths",
                "paths": paths_payload,
            }, indent=2, sort_keys=True))
            return 0
        _print(f"Config:  {cfg.config_path()}")
        _print(f"Secrets: {cfg.env_path()}")
        _print(f"Home:    {cfg.get_home()}")
        _print(f"Workspace: {cfg.workspace_dir()}")
        _print(f"Profile: {cfg.current_profile() or 'default'}")
        _print(f"Install: {Path(__file__).resolve().parents[2]}")
        return 0
    if args.action in ("summary", "show", "status", "view"):
        return show_summary()
    if args.action == "setup":
        if getattr(args, "key", None):
            if getattr(args, "value", None):
                return _die("usage: aegis config setup [section] [setup flags]")
            args.section = str(args.key)
        return cmd_setup(args, config)
    if args.action == "edit":
        import shutil

        target = cfg.env_path() if getattr(args, "secrets", False) else cfg.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
        if getattr(args, "secrets", False):
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        command = editor_command(target)
        if not command:
            _print("No editor found. Config file is at:")
            _print(f"  {target}")
            return 1
        backup = edit_backup_path(target)
        shutil.copy2(target, backup)
        _print(f"Opening {target} in {' '.join(command[:-1])}...")
        try:
            code = subprocess.run(command).returncode
        except FileNotFoundError:
            return _die(f"editor not found: {command[0]}")
        if code != 0:
            _print(f"Editor exited with status {code}; backup kept at {backup}")
            return code
        if not getattr(args, "secrets", False):
            validation_error = validate_yaml_config_edit(target)
            if validation_error:
                shutil.copy2(backup, target)
                _print(f"Restored previous config from {backup}")
                return _die(f"config edit failed validation: {validation_error}")
            _print(f"Config OK. Backup: {backup}")
        else:
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            _print(f"Secrets saved. Backup: {backup}")
        return 0
    if args.action == "get":
        if not args.key:
            if json_mode:
                return json_error("usage: aegis config get <key>")
            return _die("usage: aegis config get <key>")
        key = _normalize_config_key(args.key)
        low = key.lower()
        if "." not in key and (key.isupper() or any(low.endswith(s) for s in cfg.SECRET_SUFFIXES)):
            env_name = key if key.isupper() else key.upper()
            value = os.environ.get(env_name, config.get(key))
            source = ".env"
        else:
            value = config.get(key)
            source = "config"
        value = redacted_value(key, value)
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.value",
                "key": key,
                "source": source,
                "value": value,
            }, indent=2, sort_keys=True))
            return 0
        _print(str(value))
        return 0
    if args.action == "set":
        key = _normalize_config_key(args.key or "")
        raw_value = getattr(args, "value", None)
        if key == "model":
            raw_text = " ".join(str(part) for part in raw_value or []).strip()
            if raw_text and not raw_text.startswith("{"):
                key = "model.default"
        if (
            key
            and not getattr(args, "force", False)
            and not _config_set_secret_key(key)
            and not _config_key_known_or_extension(key)
        ):
            message = f"unknown config key: {key} (use --force to create a custom key)"
            if json_mode:
                return json_error(message, key=key)
            return _die(message)
        value, value_error = _parse_config_set_value(key, raw_value)
        if not key or value_error:
            if json_mode:
                return json_error(value_error or "usage: aegis config set <key> <value>", key=key)
            print_config_usage()
            return _die(value_error) if value_error and args.key else 1
        try:
            where = config.set(key, value)
        except ValueError as exc:
            if json_mode:
                return json_error(str(exc), key=key)
            return _die(str(exc))
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.result",
                "action": "set",
                "key": key,
                "value": redacted_value(key, value),
                "where": where,
            }, indent=2, sort_keys=True))
            return 0
        _print(f"set {key} -> {where}")
        return 0
    if args.action == "reset":
        key = _normalize_config_key((args.key or "").strip())
        if not key:
            if json_mode:
                return json_error("usage: aegis config reset <key|section|all>")
            return _die("usage: aegis config reset <key|section|all>")
        import shutil

        target = cfg.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists():
            backup = edit_backup_path(target)
            shutil.copy2(target, backup)
        try:
            reset_key = config.reset(key)
        except ValueError as exc:
            if json_mode:
                return json_error(str(exc), key=key)
            return _die(str(exc))
        if json_mode:
            _print(json.dumps({
                "ok": True,
                "object": "aegis.config.result",
                "action": "reset",
                "key": reset_key,
                "backup": str(backup) if backup else "",
                "path": str(target),
            }, indent=2, sort_keys=True))
            return 0
        suffix = f" (backup: {backup})" if backup else ""
        _print(f"reset {reset_key} -> default{suffix}")
        return 0
    if args.action in ("check", "doctor", "migrate"):
        from ..config import DEFAULT_CONFIG, _deep_merge

        file_errors = cfg.validate_config_file()

        def flat(d, prefix=""):
            out = {}
            for k, v in d.items():
                key = f"{prefix}{k}"
                if isinstance(v, dict):
                    out.update(flat(v, key + "."))
                else:
                    out[key] = v
            return out

        defaults, current = flat(DEFAULT_CONFIG), flat(config.data)
        missing = [k for k in defaults if k not in current]
        unknown = [k for k in current if k not in defaults and k.split(".")[0] not in
                   ("custom_providers", "fallback_providers", "hooks", "mcp", "routing")]
        type_errors = cfg.config_type_errors(config.data)
        if args.action in ("check", "doctor"):
            if json_mode:
                _print(json.dumps({
                    "ok": not (file_errors or type_errors),
                    "object": "aegis.config.check",
                    "action": args.action,
                    "config_yaml_errors": file_errors,
                    "missing_default_keys": missing,
                    "unknown_keys": unknown,
                    "type_errors": type_errors,
                    "provider_credentials": active_provider_credentials_status(),
                }, indent=2, sort_keys=True))
                return 1 if file_errors or type_errors else 0
            _print(f"config file: {'invalid' if file_errors or type_errors else 'ok'}")
            for error in file_errors:
                _print(f"  - {error}")
            _print(f"missing default keys: {', '.join(missing) or '(none)'}")
            _print(f"unknown keys: {', '.join(unknown) or '(none)'}")
            _print(f"type mismatches: {', '.join(type_errors) or '(none)'}")
            _print(f"provider credentials: {active_provider_credentials_status()}")
            return 1 if file_errors or type_errors else 0
        if file_errors:
            return _die("config file failed validation: " + "; ".join(file_errors))
        if type_errors:
            return _die("config file failed type validation: " + "; ".join(type_errors))
        config.data = _deep_merge(DEFAULT_CONFIG, config.data)
        config.save()
        _print(f"migrated: added {len(missing)} missing default key(s).")
        return 0
    # dump
    import yaml
    _print(yaml.safe_dump(redact_secret_values(config.data), sort_keys=False))
    return 0


def cmd_secret(args, config: Config) -> int:
    from ..secret_capture import capture_secret_interactive, store_secret_value, validate_secret_key

    if args.action == "path":
        _print(str(cfg.env_path()))
        return 0
    if args.action == "set":
        if not args.key:
            return _die("usage: aegis secret set <ENV_KEY>")
        try:
            key = validate_secret_key(args.key)
        except ValueError as exc:
            return _die(str(exc))
        if getattr(args, "stdin", False):
            value = sys.stdin.read().rstrip("\n")
            result = store_secret_value(key, value)
        else:
            result = capture_secret_interactive(key, f"Enter {key}")
        if result.get("skipped"):
            _print(f"secret setup skipped for {key}")
        else:
            _print(f"secret stored as {key} -> {cfg.env_path()}")
        return 0
    return _die("usage: aegis secret set <ENV_KEY> | aegis secret path")


def cmd_sessions(args, config: Config) -> int:
    from ..session import SessionStore

    store = SessionStore()
    if args.action == "check":
        from ..session_checks import cross_session_integrity_report, repair_cross_session_integrity

        stale_running_arg = getattr(args, "stale_running_seconds", None)
        stale_resume_arg = getattr(args, "stale_resume_pending_seconds", None)
        session_limit_arg = getattr(args, "session_limit", None)
        run_limit_arg = getattr(args, "run_limit", None)
        stale_running = 21600.0 if stale_running_arg is None else float(stale_running_arg)
        stale_resume = 86400.0 if stale_resume_arg is None else float(stale_resume_arg)
        session_limit = 500 if session_limit_arg is None else int(session_limit_arg)
        run_limit = 500 if run_limit_arg is None else int(run_limit_arg)
        repair = None
        if getattr(args, "repair", False):
            repair = repair_cross_session_integrity(
                session_limit=session_limit,
                run_limit=run_limit,
                stale_running_seconds=stale_running,
                stale_resume_pending_seconds=stale_resume,
            )
        report = cross_session_integrity_report(
            session_limit=session_limit,
            run_limit=run_limit,
            stale_running_seconds=stale_running,
            stale_resume_pending_seconds=stale_resume,
        )
        if getattr(args, "json", False):
            payload = {"report": report}
            if repair is not None:
                payload["repair"] = repair
            _print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report.get("error_count", 0) == 0 else 1
        state = "ok" if report.get("ok") else "degraded"
        _print(f"Cross-session checks: {state}")
        _print(
            f"  sessions={report.get('counts', {}).get('sessions', 0)} "
            f"runs={report.get('counts', {}).get('runs', 0)} "
            f"errors={report.get('error_count', 0)} warnings={report.get('warning_count', 0)}"
        )
        for check in report.get("checks", []):
            marker = "ok" if check.get("ok") else "fail"
            _print(f"  [{marker}] {check.get('id')}: {check.get('detail')}")
        issues = report.get("issues", [])
        if issues:
            _print("Issues")
            for issue in issues[:12]:
                target = issue.get("session_id") or issue.get("run_id") or ""
                label = f" {target}" if target else ""
                _print(f"  {issue.get('severity')} {issue.get('code')}{label}: {issue.get('message')}")
            if len(issues) > 12:
                _print(f"  ... {len(issues) - 12} more issue(s)")
        if repair is not None:
            _print("Repair")
            _print(
                f"  interrupted stale runs: {repair.get('repaired_running_runs', 0)}; "
                f"duplicate runs cleaned: {repair.get('repaired_duplicate_running_runs', 0)}; "
                f"planned-stop markers cleared: {repair.get('cleared_planned_stop_resume_pending', 0)}; "
                f"resume-pending sessions marked: {repair.get('marked_resume_pending', 0)}; "
                f"skipped: {repair.get('skipped', 0)}"
            )
        return 0 if report.get("error_count", 0) == 0 else 1
    if args.action == "show":
        s = store.load(args.id) if args.id else None
        if not s:
            return _die("session not found")
        runtime = s.meta.get("runtime") or {}
        if runtime:
            _print(
                f"runtime: {runtime.get('provider', '')}/{runtime.get('model', '')} "
                f"({runtime.get('transport', '') or 'transport?'})"
            )
        if s.meta.get("trace_id"):
            _print(f"trace: {s.meta.get('trace_id')}")
        if s.meta.get("system_prompt_hash"):
            _print(
                f"prompt: {s.meta.get('system_prompt_hash')} · "
                f"{s.meta.get('system_prompt_tokens', 0):,} tokens · "
                f"{len(s.meta.get('prompt_parts') or [])} part(s)"
            )
            for p in (s.meta.get("prompt_parts") or [])[:12]:
                _print(
                    f"  {p.get('tier', ''):<8} {p.get('name', ''):<22} "
                    f"{p.get('tokens', 0):>6} tok  {p.get('hash', '')}"
                )
        for m in s.messages:
            if m.role in ("user", "assistant") and m.content:
                _print(f"[{m.role}] {m.content[:500]}")
        return 0
    if args.action == "rm":
        _print("removed" if store.delete(args.id) else "not found")
        return 0
    if args.action == "summarize":
        s = store.summarize(args.id, config=config) if args.id else None
        _print(s or "usage: aegis sessions summarize <id>")
        return 0
    if args.action == "search":
        for h in store.search_messages(args.id or ""):
            _print(f"  [{h['when']}] {h['title']} ({h['session'][:14]})\n    {h['role']}: {h['snippet']}")
        return 0
    for s in store.list(50):
        _print(f"  {s['id']}  {s['title'][:40]:<40}  {s['updated_at']}")
    return 0


# --------------------------------------------------------------------------- #
# gateway
# --------------------------------------------------------------------------- #
def cmd_gateway(args, config: Config) -> int:
    action = getattr(args, "action", None)
    def configured_channels() -> list[str]:
        channels = [str(ch).strip() for ch in (config.get("gateway.channels", []) or []) if str(ch).strip()]
        api_enabled = bool(config.get("gateway.api_server.enabled", False)) or _env_enabled("API_SERVER_ENABLED")
        if api_enabled and "api_server" not in {ch.lower() for ch in channels}:
            channels.append("api_server")
        return channels

    if action in ("install", "uninstall", "status", "start", "stop", "restart"):
        from ..gateway.service import cmd_gateway_service
        chans = args.channels or ",".join(configured_channels()) or "telegram"
        return cmd_gateway_service(action, chans)

    from .._log import setup_logging
    from ..gateway.channels import build_adapter
    from ..gateway.runner import GatewayRunner

    setup_logging(mode="gateway")
    runner = GatewayRunner(config)
    configured = ",".join(configured_channels())
    channels = (args.channels or configured or "cli").split(",")
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
    for mod, package, extra in (("playwright", "playwright", "browser"),
                                ("pyautogui", "PyAutoGUI", "computer"),
                                ("discord", "discord.py", "discord"),
                                ("slack_bolt", "slack-bolt", "slack")):
        try:
            __import__(mod)
            _print(f"  ✓ {mod}")
        except Exception as e:  # noqa: BLE001
            try:
                importlib_metadata.version(package)
                _print(f"  ✓ {mod} installed (import blocked: {type(e).__name__})")
            except importlib_metadata.PackageNotFoundError:
                _print(f"  – {mod} (pip install 'aegis-agent-harness[{extra}]')")
    tools = config.get("tools.toolsets", [])
    mcp_servers = config.get("mcp.servers", {}) or {}
    try:
        from ..surface import plugin_inventory, skill_inventory, tool_inventory
        tinv = tool_inventory(config)
        sinv = skill_inventory(config)
        pinv = plugin_inventory()
        _print(f"toolsets: {tools} · mcp servers: {len(mcp_servers)}")
        _print(f"tools: {tinv.enabled_count}/{tinv.total_count} model-visible")
        _print(f"skills: {sinv.available_count} available ({sinv.bundled_count} bundled)")
        _print(f"plugins: {pinv.files_count} file(s), {len(pinv.tools)} tool(s), "
               f"{len(pinv.errors)} error(s)")
        if sinv.names:
            _print("  examples: " + ", ".join(sinv.names[:8]))
    except Exception as e:  # noqa: BLE001
        _print(f"surface inventory: ERROR {e}")
    try:
        from ..providers import build_provider
        p = build_provider(config)
        _print(f"provider: {p.describe()}")
        _print(f"  auth available: {p.auth.available()}")
    except Exception as e:  # noqa: BLE001
        _print(f"provider: ERROR {e}")
    if getattr(args, "probe", False):
        from ..doctor import run_probes
        failures = run_probes(config, out=_print)
        if failures:
            _print(f"✗ {failures} probe(s) failed")
    if getattr(args, "release", False):
        from ..doctor import run_release_preflight
        failures = run_release_preflight(out=_print)
        if failures:
            _print("✗ desktop release preflight failed")
    if getattr(args, "fix", False):
        from ..util import ensure_dir
        for d in (cfg.memories_dir(), cfg.skills_dir(), cfg.workspace_dir(), cfg.logs_dir(),
                  cfg.sub("plugins")):
            ensure_dir(d)
        if cfg.auth_path().exists():
            try:
                os.chmod(cfg.auth_path(), 0o600)
            except OSError:
                pass
        if cfg.env_path().exists():
            try:
                os.chmod(cfg.env_path(), 0o600)
            except OSError:
                pass
        if not cfg.config_path().exists():
            config.save()
        _print("✓ fixed: ensured dirs, tightened secret perms (0600), wrote config if missing.")
    return 0


def cmd_update(args, config: Config) -> int:
    import aegis
    pkg_root = Path(aegis.__file__).resolve().parent.parent
    branch = getattr(args, "branch", None) or "main"
    is_git = (pkg_root / ".git").exists()

    if getattr(args, "check", False):
        if is_git:
            shallow = subprocess.run(
                ["git", "rev-parse", "--is-shallow-repository"],
                cwd=str(pkg_root),
                capture_output=True,
                text=True,
            ).stdout.strip().lower() == "true"
            fetch_cmd = ["git", "fetch", "-q"]
            if shallow:
                fetch_cmd += ["--depth", "1"]
            subprocess.run([*fetch_cmd, "origin", branch], cwd=str(pkg_root))
            if shallow:
                head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(pkg_root),
                                      capture_output=True, text=True).stdout.strip()
                remote = subprocess.run(["git", "rev-parse", f"origin/{branch}"], cwd=str(pkg_root),
                                        capture_output=True, text=True).stdout.strip()
                _print("up to date." if head and remote and head == remote
                       else f"update available on origin/{branch} (shallow checkout; commit count unavailable).")
            else:
                behind = subprocess.run(["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
                                        cwd=str(pkg_root), capture_output=True, text=True).stdout.strip()
                _print(f"{behind} commit(s) behind origin/{branch}" if behind not in ("", "0")
                       else "up to date.")
        else:
            _print("git/pip install — run `aegis update` to reinstall from "
                   "git+https://github.com/Alien0013/aegis.git")
        return 0

    try:                              # snapshot config/state so a bad update is recoverable
        from .. import backup
        snap = backup.make_snapshot("pre-update")
        backup.prune_snapshots(int(config.get("snapshots.keep", 10)))
        _print(f"  ▸ pre-update snapshot: {snap.name} (restore with `aegis snapshot restore`)")
    except Exception as e:  # noqa: BLE001
        _print(f"  ! snapshot before update failed (continuing): {e}")

    if is_git:
        _print(f"updating from git ({branch}) at {pkg_root}…")
        subprocess.run(["git", "fetch", "-q", "origin", branch], cwd=str(pkg_root))
        subprocess.run(["git", "checkout", "-q", branch], cwd=str(pkg_root))
        subprocess.run(["git", "pull", "--ff-only", "origin", branch], cwd=str(pkg_root))
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], cwd=str(pkg_root))
    else:
        _print("reinstalling from git…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade",
                        f"git+https://github.com/Alien0013/aegis.git@{branch}"])
    _print("✓ updated. Run `aegis doctor` to confirm.")
    from ..gateway.service import restart_after_update
    restart_after_update()        # bounce the gateway service if one is installed
    return 0


def cmd_uninstall(args, config: Config) -> int:
    import shutil
    home = cfg.get_home()
    bin_dir = Path(os.environ.get("AEGIS_BIN_DIR", str(Path.home() / ".local" / "bin"))).expanduser()
    launcher_candidates = {bin_dir / "aegis"}
    found_launcher = shutil.which("aegis")
    if found_launcher:
        launcher_candidates.add(Path(found_launcher).expanduser())
    venv = home / "venv"

    for unit in ("aegis-dashboard.service", "aegis-gateway.service", "aegis-cron.service"):
        if shutil.which("systemctl"):
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        for unit_path in (
            Path.home() / ".config" / "systemd" / "user" / unit,
            Path.home() / ".config" / "systemd" / "user" / "default.target.wants" / unit,
        ):
            try:
                unit_path.unlink()
                _print(f"removed {unit_path}")
            except FileNotFoundError:
                pass

    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    for launcher in sorted(launcher_candidates):
        try:
            launcher.unlink()
            _print(f"removed launcher {launcher}")
        except FileNotFoundError:
            pass

    if venv.exists():
        shutil.rmtree(venv)
        _print(f"removed venv {venv}")

    if getattr(args, "purge", False):
        if home.exists():
            shutil.rmtree(home)
        _print(f"purged {home}")
    else:
        _print(f"kept your data at {home} (pass --purge to delete config/sessions/memory/skills).")
    return 0


def cmd_batch(args, config: Config) -> int:
    """Run a prompt per line of a file (or '-' for stdin); print results."""
    from ..session import Session
    from . import repl
    src = args.file
    lines = (sys.stdin.read() if src == "-" else Path(src).expanduser().read_text(encoding="utf-8")).splitlines()
    prompts = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    _print(f"running {len(prompts)} prompt(s)…")
    for i, prompt in enumerate(prompts, 1):
        _print(f"\n=== [{i}/{len(prompts)}] {prompt[:70]} ===")
        repl.run_once(config, prompt, model=args.model, provider_name=args.provider,
                      session=Session.create(), auto=True, surface="batch",
                      meta={"batch_source": src, "batch_index": i,
                            "batch_total": len(prompts)})
    return 0


def cmd_logs(args, config: Config) -> int:  # noqa: ARG001
    import time

    from .. import config as cfg

    names = {
        "agent": "agent.log",
        "desktop": "desktop.log",
        "errors": "errors.log",
        "gateway": "gateway.log",
        "gui": "gui.log",
        "legacy": "aegis.log",
    }
    filename = names.get(args.name, names["agent"])
    path = cfg.logs_dir() / filename
    if not path.exists() and args.name == "agent":
        path = cfg.logs_dir() / "aegis.log"
    if not path.exists():
        _print(f"log file not found: {path}")
        return 1

    def tail_once(offset: int = 0) -> int:
        text = path.read_text(errors="replace")
        if offset:
            chunk = text[offset:]
            if chunk:
                _print(chunk.rstrip("\n"))
            return len(text)
        for line in text.splitlines()[-int(args.lines or 80):]:
            _print(line)
        return len(text)

    offset = tail_once()
    if getattr(args, "follow", False):
        try:
            while True:
                time.sleep(1)
                offset = tail_once(offset)
        except KeyboardInterrupt:
            return 0
    return 0


_CMDS = (
    "ab acp auth background backup batch bench budget chat checkpoints completion config cost cron "
    "curator daemon dashboard debug deksktop desktop doctor eval gateway gstack hooks import improve "
    "insights kanban learn logs mcp memory model models onboard pairing plugins profile profiles rpc "
    "secret secrets security serve sessions setup skills snapshot spec status tools trace trajectory tui ui "
    "uninstall update watch webhook"
)


def cmd_checkpoints(args, config: Config) -> int:
    from ..checkpoints import CheckpointStore
    store = CheckpointStore()
    if args.action == "rollback":
        restored = store.rollback(args.id)
        _print(f"rolled back {len(restored)} file(s): {', '.join(restored) or '(none)'}")
        return 0
    if args.action == "diff":
        d = store.diff(args.id)
        _print(d or "(no changes since checkpoint)")
        return 0
    if args.action == "clear":
        _print(f"cleared {store.clear()} checkpoint(s)")
        return 0
    for cp in store.list():
        _print(f"  {cp.id}  {cp.created_at}  [{cp.label}]  {len(cp.files)} file(s)")
    return 0


def cmd_background(args, config: Config) -> int:
    from ..background import get_manager
    for t in get_manager().list():
        _print(f"  {t['id']}  [{t['status']}]  {t['prompt']}")
    return 0


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


_SETUP_SECTIONS = ("model", "terminal", "tools", "gateway", "agent", "web", "memory", "dashboard", "services")


def _add_onboard_automation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--non-interactive", "--noninteractive", action="store_true",
                        help="configure without prompts")
    parser.add_argument("--accept-risk", action="store_true", help="required with --non-interactive")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable onboarding summary")
    parser.add_argument("--provider", help="provider for noninteractive onboarding")
    parser.add_argument("--auth", choices=["skip", "api-key", "local", "oauth", "codex"], default="skip",
                        help="credential mode for noninteractive onboarding")
    parser.add_argument("--model", help="model id for noninteractive onboarding")
    parser.add_argument("--web", default="auto", help="web search backend, or skip")
    parser.add_argument("--toolsets", help="comma list, e.g. core,browser,lsp,mcp")
    parser.add_argument("--channels", help="comma list, e.g. telegram,discord")
    parser.add_argument("--exec-mode", default="ask",
                        choices=["ask", "auto", "allowlist", "deny", "full", "smart"])
    parser.add_argument("--install-services", action="store_true",
                        help="install dashboard/gateway services in noninteractive mode")


def _add_setup_args(parser: argparse.ArgumentParser, *, include_section: bool = False) -> None:
    if include_section:
        parser.add_argument("section", nargs="?", choices=_SETUP_SECTIONS,
                            help="configure one section instead of running the full wizard")
    parser.add_argument("--quick", action="store_true", help="apply fast local defaults")
    parser.add_argument("--advanced", action="store_true", help="show advanced setup choices")
    parser.add_argument("--no-probe", action="store_true", help="skip provider connection test")
    parser.add_argument("--no-services", action="store_true", help="skip user systemd service setup")
    _add_onboard_automation_args(parser)


def _needs_first_run() -> bool:
    if os.environ.get("AEGIS_SKIP_FIRST_RUN", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return not cfg.config_path().exists()


def _handle_first_run(config: Config) -> int:
    if sys.stdin.isatty() and sys.stdout.isatty():
        from ..onboarding import run_onboarding

        return run_onboarding(config)
    _print("AEGIS is not configured yet.")
    _print("Run one of these first:")
    _print("  aegis setup")
    _print("  aegis setup --non-interactive --accept-risk --json")
    _print("Set AEGIS_SKIP_FIRST_RUN=1 to bypass this guard.")
    return 2


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
    c.add_argument("--image", action="append", help="attach an image for vision (repeatable)")
    c.add_argument("-s", "--skills", help="comma-sep skills or skill bundles to preload")
    c.set_defaults(func=cmd_chat)

    m = sub.add_parser("model", help="show/set the model")
    m.add_argument("action", nargs="?", choices=["list", "set", "doctor"])
    m.add_argument("provider", nargs="?")
    m.add_argument("model", nargs="?")
    m.set_defaults(func=cmd_model)

    a = sub.add_parser("auth", help="API-key / OAuth authentication")
    a.add_argument("action", choices=["status", "login", "logout", "import-claude", "pool"])
    a.add_argument("provider", nargs="?")
    a.add_argument("--manual", action="store_true", help="manual code-paste OAuth flow")
    a.set_defaults(func=cmd_auth)

    s = sub.add_parser("setup", help="interactive setup wizard")
    _add_setup_args(s, include_section=True)
    s.set_defaults(func=cmd_setup)

    ob = sub.add_parser("onboard", help="interactive setup wizard (alias of setup)")
    _add_setup_args(ob, include_section=True)
    ob.set_defaults(func=cmd_setup)

    up = sub.add_parser("update", help="update AEGIS to the latest version")
    up.add_argument("--check", action="store_true", help="report if an update is available, don't install")
    up.add_argument("--branch", help="update against a non-default branch")
    up.set_defaults(func=cmd_update)

    un = sub.add_parser("uninstall", help="remove AEGIS (--purge also deletes ~/.aegis)")
    un.add_argument("--purge", action="store_true")
    un.set_defaults(func=cmd_uninstall)

    st = sub.add_parser("status", help="show install/auth/tools/skills/plugins/service status")
    st.add_argument("--json", action="store_true", help="print machine-readable status")
    st.set_defaults(func=cmd_status)

    lg = sub.add_parser("logs", help="tail agent/desktop/errors/gateway/gui logs")
    lg.add_argument(
        "name",
        nargs="?",
        choices=["agent", "desktop", "errors", "gateway", "gui", "legacy"],
        default="agent",
    )
    lg.add_argument("-n", "--lines", type=int, default=80)
    lg.add_argument("-f", "--follow", action="store_true")
    lg.set_defaults(func=cmd_logs)

    ba = sub.add_parser("batch", help="run a prompt per line of a file (or - for stdin)")
    ba.add_argument("file")
    ba.add_argument("-m", "--model")
    ba.add_argument("--provider")
    ba.set_defaults(func=cmd_batch)

    cm = sub.add_parser("completion", help="output shell completion script")
    cm.add_argument("shell", choices=["bash", "zsh", "fish"])
    cm.set_defaults(func=cmd_completion)

    # --- parity subsystems (modules under aegis/) ---
    from .. import acp as _acp
    from .. import backup as _backup
    from .. import curator as _curator
    from .. import dashboard as _dash
    from .. import desktop as _desktop
    from .. import hooks as _hooks
    from .. import insights as _insights
    from .. import kanban as _kanban
    from .. import webhook as _webhook
    from . import tui as _tui

    bk = sub.add_parser("backup", help="back up ~/.aegis to a zip")
    bk.add_argument("--out")
    bk.add_argument("--quick", action="store_true")
    bk.set_defaults(func=_backup.cmd_backup)

    im = sub.add_parser("import", help="restore a backup zip")
    im.add_argument("path")
    im.set_defaults(func=_backup.cmd_import)

    snap = sub.add_parser("snapshot", help="config/state snapshots (auto before updates)")
    snap.add_argument("action", nargs="?", choices=["create", "restore", "prune", "list"],
                      default="list")
    snap.add_argument("label", nargs="?", help="label (create) | id (restore) | N (prune)")
    snap.set_defaults(func=_backup.cmd_snapshot)

    ins = sub.add_parser("insights", help="usage analytics over your history")
    ins.add_argument("--days", type=int, default=30)
    ins.add_argument("--source")
    ins.add_argument("--json", action="store_true")
    ins.set_defaults(func=_insights.cmd_insights)

    from .. import usage_log as _usage_log
    co = sub.add_parser("cost", help="estimated spend by model (token-aware, cache-discounted)")
    co.add_argument("--days", type=int, default=30)
    co.add_argument("--json", action="store_true")
    co.set_defaults(func=_usage_log.cmd_cost)

    from .. import model_meta as _model_meta
    mo = sub.add_parser("models", help="show/refresh model metadata (context window from models.dev)")
    mo.add_argument("action", nargs="?", choices=["show", "refresh"], default="show")
    mo.set_defaults(func=_model_meta.cmd_models)

    wh = sub.add_parser("webhook", help="event webhooks that trigger the agent")
    wh.add_argument("action", nargs="?", choices=["list", "add", "remove", "serve"], default="list")
    wh.add_argument("name", nargs="?")
    wh.add_argument("prompt", nargs="*")
    wh.add_argument("--secret")
    wh.add_argument("--host")
    wh.add_argument("--port", type=int)
    wh.add_argument("--deliver", help="comma-sep platform:chat_id targets, e.g. telegram:42")
    wh.add_argument("--deliver-only", action="store_true", help="deliver rendered payload without running the agent")
    wh.add_argument("--events", help="comma-sep X-GitHub-Event allowlist, e.g. pull_request,push")
    wh.add_argument("--skills", help="comma-sep skills to load before running")
    wh.set_defaults(func=_webhook.cmd_webhook)

    hk = sub.add_parser("hooks", help="lifecycle shell hooks")
    hk.add_argument("action", nargs="?", choices=["list", "test"], default="list")
    hk.add_argument("event", nargs="?")
    hk.set_defaults(func=_hooks.cmd_hooks)

    kb = sub.add_parser("kanban", help="multi-agent task board (dependency graph + workers)")
    kb.add_argument("action", nargs="?",
                    choices=["create", "list", "show", "claim", "complete", "assign", "block",
                             "unblock", "promote", "archive", "link", "runs", "heartbeat",
                             "stats", "dispatch", "decompose", "run"],
                    default="list")
    kb.add_argument("title", nargs="?")
    kb.add_argument("--id")
    kb.add_argument("--body")
    kb.add_argument("--priority", type=int)
    kb.add_argument("--status")
    kb.add_argument("--assignee")
    kb.add_argument("--worker")
    kb.add_argument("--parent", action="append", help="parent task id (repeatable)")
    kb.add_argument("--child", help="child task id (link)")
    kb.add_argument("--tenant", help="tenant namespace")
    kb.add_argument("--workspace", help="scratch | dir:<path> | worktree")
    kb.add_argument("--reason", help="reason text (block)")
    kb.add_argument("--summary", help="completion summary (complete)")
    kb.add_argument("--note", help="heartbeat note")
    kb.add_argument("--no-spawn", action="store_true", dest="no_spawn",
                    help="dispatch: reclaim + promote only, don't spawn workers")
    kb.set_defaults(func=_kanban.cmd_kanban)

    cu = sub.add_parser("curator", help="background skill maintenance")
    cu.add_argument("action", nargs="?",
                    choices=["status", "review", "prune", "archive", "restore",
                             "transitions", "pin", "unpin", "run", "backup", "rollback",
                             "list-archived"], default="status")
    cu.add_argument("name", nargs="?")
    cu.add_argument("--apply", action="store_true", help="apply changes (transitions/prune)")
    cu.add_argument("--dry-run", action="store_true", help="preview a run without mutating")
    cu.add_argument("--id", help="snapshot id (rollback)")
    cu.add_argument("--list", action="store_true", help="list snapshots (rollback)")
    cu.set_defaults(func=_curator.cmd_curator)

    for _name in ("dashboard", "ui"):       # `aegis ui` is the friendly alias
        db = sub.add_parser(_name, help="open the AEGIS control panel in your browser")
        db.add_argument("--host")
        db.add_argument("--port", type=int)
        db.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
        db.set_defaults(func=_dash.cmd_dashboard)

    tu = sub.add_parser("tui", help="terminal cockpit for sessions/runs/cron/kanban")
    tu.add_argument("--once", action="store_true", help="render one snapshot and exit")
    tu.add_argument("--watch", action="store_true", help="refresh until interrupted")
    tu.add_argument("--interval", type=float, default=5.0, help="watch refresh interval in seconds")
    tu.add_argument("--no-color", action="store_true", help="disable ANSI color")
    tu.set_defaults(func=_tui.cmd_tui)

    for _name in ("desktop", "deksktop"):
        help_text = "install/update and launch the native desktop app"
        if _name == "deksktop":
            help_text = argparse.SUPPRESS
        ds = sub.add_parser(_name, help=help_text)
        ds.add_argument("--status", action="store_true",
                        help="print desktop bootstrap status without syncing, installing, or launching")
        ds.add_argument("--doctor", action="store_true",
                        help="print desktop readiness diagnostics without changing files")
        ds.add_argument("--install-only", action="store_true",
                        help="install/update desktop dependencies without launching")
        ds.add_argument("--reinstall", action="store_true",
                        help="run npm install even if dependencies already exist")
        ds.add_argument("--sandbox", action="store_true",
                        help="opt into Electron's Chromium sandbox on Linux")
        ds.add_argument("--source", action="store_true",
                        help="launch the Electron source tree with npm start instead of the unpacked packaged app")
        ds.add_argument("--cwd",
                        help="workspace directory for desktop-launched backend sessions")
        ds.add_argument("--package", metavar="TARGET", nargs="?", const="auto",
                        help="build an installable app instead of launching "
                             "(auto = this OS; or linux/win/mac). Output in the app's release/ dir.")
        ds.set_defaults(func=_desktop.cmd_desktop)

    from ..daemon import cmd_daemon as _cmd_daemon
    dm = sub.add_parser("daemon", help="install/control user services")
    dm.add_argument("action", nargs="?", choices=["status", "install", "start", "stop", "restart", "remove"],
                    default="status")
    dm.add_argument("--channels", help="gateway channels for service install, e.g. telegram,discord")
    dm.add_argument("--no-start", action="store_true", help="write units but do not start them")
    dm.set_defaults(func=_cmd_daemon)

    ac = sub.add_parser("acp", help="run as an ACP stdio server for IDEs")
    ac.set_defaults(func=_acp.cmd_acp)

    from ..gateway.pairing import cmd_pairing as _cmd_pairing
    pr = sub.add_parser("pairing", help="approve/revoke gateway users")
    pr.add_argument("action", nargs="?", choices=["list", "approve", "revoke"], default="list")
    pr.add_argument("platform", nargs="?")
    pr.add_argument("code", nargs="?")
    pr.set_defaults(func=_cmd_pairing)

    ck = sub.add_parser("checkpoints", help="list/diff/rollback/clear file checkpoints")
    ck.add_argument("action", nargs="?", choices=["list", "diff", "rollback", "clear"], default="list")
    ck.add_argument("id", nargs="?")
    ck.set_defaults(func=cmd_checkpoints)

    bg = sub.add_parser("background", help="list background tasks")
    bg.set_defaults(func=cmd_background)

    from .. import ops as _ops
    secp = sub.add_parser("security", help="security audit of deps/MCP/plugins/skills")
    secp.add_argument("action", nargs="?", choices=["audit"], default="audit")
    secp.add_argument("--fail-on", dest="fail_on")
    secp.set_defaults(func=_ops.cmd_security_audit)

    dbg = sub.add_parser("debug", help="bundle a redacted debug report")
    dbg.add_argument("action", nargs="?", choices=["share"], default="share")
    dbg.set_defaults(func=_ops.cmd_debug)

    sec2 = sub.add_parser("secrets", help="sync secrets from a manager (bitwarden)")
    sec2.add_argument("provider", nargs="?", default="bitwarden")
    sec2.set_defaults(func=_ops.cmd_secrets)

    from .. import learn as _learn
    ln = sub.add_parser("learn", help="review sessions; promote learned memories/skills")
    ln.add_argument("action", nargs="?", choices=["review", "list", "apply", "reject"], default="list")
    ln.add_argument("id", nargs="?")
    ln.set_defaults(func=_learn.cmd_learn)

    from .. import trajectory as _traj
    tj = sub.add_parser("trajectory", help="record/export/compress session trajectories")
    tj.add_argument("action", nargs="?", choices=["stats", "export", "compress"], default="stats")
    tj.add_argument("--out")
    tj.add_argument("--format", choices=["aegis", "openai", "hf", "sharegpt", "toolxml"], default="aegis",
                    help="export format: native, OpenAI fine-tune, or HuggingFace/ShareGPT")
    tj.add_argument("--summarize", action="store_true", help="LLM-summarize long tool outputs when compressing")
    tj.set_defaults(func=_traj.cmd_trajectory)

    sk = sub.add_parser("skills", help="list/view/create/install/search/remove/uninstall skills")
    sk.add_argument("action", nargs="?",
                    choices=[
                        "list", "view", "new", "install", "search", "remove", "uninstall", "hub",
                        "bundles", "bundle", "unbundle",
                    ],
                    default="list")
    sk.add_argument("name", nargs="?", help="skill name, install source, or hub name")
    sk.add_argument("--force", action="store_true", help="install even if the security scan flags it")
    sk.add_argument("--members", help="comma-separated skill names for `aegis skills bundle`")
    sk.add_argument("--description", default="", help="description for `aegis skills bundle`")
    sk.add_argument("--instruction", default="", help="extra guidance injected by `aegis skills bundle`")
    sk.set_defaults(func=cmd_skills)

    pl = sub.add_parser("plugins", help="manage manifest and drop-in plugins")
    pl.add_argument("action", nargs="?",
                    choices=["list", "doctor", "path", "install", "enable", "disable", "remove"],
                    default="list")
    pl.add_argument("name", nargs="?")
    pl.add_argument("--force", action="store_true", help="replace an installed local plugin")
    pl.set_defaults(func=cmd_plugins)

    mc = sub.add_parser("mcp", help="manage MCP servers (or `serve` to be one)")
    mc.add_argument("action", nargs="?",
                    choices=["list", "add", "remove", "test", "serve", "catalog", "install", "tools"],
                    default="list")
    mc.add_argument("name", nargs="?")
    mc.add_argument("cmd", nargs="?", help='command line, e.g. "npx -y @modelcontextprotocol/server-filesystem /tmp"')
    mc.set_defaults(func=cmd_mcp)

    tr = sub.add_parser("trace", help="inspect/export session traces")
    tr.add_argument("action", nargs="?", choices=["list", "show", "export"], default="list")
    tr.add_argument("id", nargs="?", help="trace id for `show`/`export`")
    tr.add_argument("--session", help="filter spans by session id")
    tr.add_argument("--status", help="filter list/export/spans by status text")
    tr.add_argument("--limit", type=int, default=50)
    tr.add_argument("--spans", action="store_true", help="list spans instead of trace summaries")
    tr.add_argument("--out", help="write exported JSONL to a file")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=cmd_trace)

    ev = sub.add_parser("eval", help="run/list/show offline eval suites")
    ev.add_argument("action", nargs="?", choices=["list", "run", "show"], default="list")
    ev.add_argument("path", nargs="?", help="jsonl suite path for `run`, run id for `show`")
    ev.add_argument("--limit", type=int, default=20)
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(func=cmd_eval)

    from ..bench import cmd_bench as _cmd_bench
    bn = sub.add_parser("bench", help="run end-to-end task benchmarks (give a task, score pass/fail)")
    bn.add_argument("action", nargs="?", choices=["run", "list", "score"], default="run")
    bn.add_argument("--dir", help="benchmark directory (default: ./benchmarks)")
    bn.add_argument("--task", help="run only this task by name")
    bn.add_argument("--json", action="store_true")
    bn.set_defaults(func=_cmd_bench)

    from ..self_improve import cmd_improve as _cmd_improve
    ip = sub.add_parser("improve", help="verified self-improvement: keep curator edits only if the benchmark holds")
    ip.add_argument("action", nargs="?", choices=["run", "log"], default="log")
    ip.add_argument("--min-delta", type=float, default=0.0, dest="min_delta",
                    help="score must improve by at least this to keep the change")
    ip.add_argument("--limit", type=int, default=20)
    ip.set_defaults(func=_cmd_improve)

    from ..spec import cmd_spec as _cmd_spec
    sp = sub.add_parser("spec", help="spec-driven dev: list/show persistent requirements→design→tasks")
    sp.add_argument("action", nargs="?", choices=["list", "show"], default="list")
    sp.add_argument("slug", nargs="?")
    sp.set_defaults(func=_cmd_spec)

    from ..governor import cmd_budget as _cmd_budget
    bg2 = sub.add_parser("budget", help="cost & latency governor: spend vs caps, auto-downshift")
    bg2.add_argument("action", nargs="?", choices=["status"], default="status")
    bg2.set_defaults(func=_cmd_budget)

    from ..ab import cmd_ab as _cmd_ab
    abp = sub.add_parser("ab", help="replay a session on a different model and diff the result")
    abp.add_argument("session_id")
    abp.add_argument("--model", help="model for variant B")
    abp.add_argument("--provider", help="provider for variant B")
    abp.add_argument("--json", action="store_true")
    abp.set_defaults(func=_cmd_ab)

    from ..ambient import cmd_watch as _cmd_watch
    wp = sub.add_parser("watch", help="ambient mode: run the project's tests on every save")
    wp.add_argument("path", nargs="?", help="directory to watch (default: .)")
    wp.set_defaults(func=_cmd_watch)

    from ..gstack import cmd_gstack as _cmd_gstack
    gs = sub.add_parser("gstack", help="run a goal through a sprint of roles (think→plan→build→review→test→ship→reflect)")
    gs.add_argument("goal", nargs="*", help="the goal for the sprint")
    gs.add_argument("--phases", help="comma-separated subset, e.g. think,plan,build")
    gs.add_argument("--from", dest="from_phase", help="start from this phase (resume a sprint)")
    gs.add_argument("--dry", action="store_true", help="print the phase plan without running")
    gs.set_defaults(func=_cmd_gstack)

    sv = sub.add_parser("serve", help="run an OpenAI-compatible API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.set_defaults(func=cmd_serve)

    rp = sub.add_parser("rpc", help="run a local JSON-RPC agent server over stdio")
    rp.set_defaults(func=cmd_rpc)

    pf = sub.add_parser("profile", help="manage isolated runtime profiles")
    pf_sub = pf.add_subparsers(dest="profile_action")
    pf.set_defaults(func=cmd_profile)
    pf_list = pf_sub.add_parser("list", help="list profiles")
    pf_list.set_defaults(func=cmd_profile)
    pf_use = pf_sub.add_parser("use", help="set sticky default profile")
    pf_use.add_argument("profile_name", help="profile name, or 'default'")
    pf_use.set_defaults(func=cmd_profile)
    pf_create = pf_sub.add_parser("create", help="create a profile")
    pf_create.add_argument("profile_name")
    pf_create.add_argument("--clone", action="store_true",
                           help="copy config, secrets, identity files, memories, and skills from the active profile")
    pf_create.add_argument("--clone-all", action="store_true",
                           help="copy profile state, excluding session history and logs")
    pf_create.add_argument("--clone-from", metavar="SOURCE",
                           help="source profile to clone from; implies --clone")
    pf_create.set_defaults(func=cmd_profile)
    pf_clone = pf_sub.add_parser("clone", help="clone one profile to another")
    pf_clone.add_argument("source_profile")
    pf_clone.add_argument("profile_name")
    pf_clone.add_argument("--clone-all", action="store_true",
                          help="copy profile state, excluding session history and logs")
    pf_clone.set_defaults(func=cmd_profile)
    pf_show = pf_sub.add_parser("show", help="show profile details")
    pf_show.add_argument("profile_name", nargs="?", help="profile name; defaults to active")
    pf_show.set_defaults(func=cmd_profile)
    pf_export = pf_sub.add_parser("export", help="export a profile to tar.gz")
    pf_export.add_argument("profile_name", nargs="?", help="profile name; defaults to active")
    pf_export.add_argument("-o", "--out", help="archive path")
    pf_export.add_argument("--include-history", action="store_true",
                           help="include session DB, logs, and cron output history")
    pf_export.add_argument("--include-secrets", action="store_true",
                           help="include .env and auth.json in the archive")
    pf_export.set_defaults(func=cmd_profile)
    pf_import = pf_sub.add_parser("import", help="import a profile archive")
    pf_import.add_argument("archive")
    pf_import.add_argument("--name", help="profile name to import as")
    pf_import.set_defaults(func=cmd_profile)

    pfs = sub.add_parser("profiles", help="list isolated runtime profiles")
    pfs.set_defaults(func=cmd_profile, profile_action="list")

    cr = sub.add_parser("cron", help="schedule recurring agent tasks")
    cr.add_argument("action", nargs="?",
                    choices=["list", "add", "rm", "run", "install", "uninstall",
                             "status", "start", "stop", "restart"],
                    default="list")
    cr.add_argument("schedule", nargs="?", help='e.g. "30m", "@daily", or 5-field cron')
    cr.add_argument("prompt", nargs="*")
    cr.add_argument("--script", help="Python file to run first; its stdout is prepended as context")
    cr.add_argument("--skills", help="comma-sep skills to load before running")
    cr.add_argument("--context-from", help="comma-sep cron job ids/names whose latest output is prepended")
    cr.add_argument("--deliver", help="comma-sep platform:chat_id targets (supersedes single channel)")
    cr.add_argument("--no-agent", action="store_true", help="run script-only and deliver stdout")
    cr.add_argument("--no-start", action="store_true", help="write service unit but do not start it")
    cr.set_defaults(func=cmd_cron)

    t = sub.add_parser("tools", help="list tools; `doctor` for availability; `status` for backends")
    t.add_argument("action", nargs="?", choices=["list", "status", "doctor"], default="list")
    t.set_defaults(func=cmd_tools)

    mem = sub.add_parser("memory", help="show/add/replace/remove/status long-term memory")
    mem.add_argument("action", nargs="?", choices=["show", "add", "replace", "remove", "clear", "status"],
                     default="show")
    mem.add_argument("text", nargs="*")
    mem.add_argument("--old-text", help="unique substring to replace/remove")
    mem.add_argument("--user", action="store_true", help="target the user profile")
    mem.set_defaults(func=cmd_memory)

    cf = sub.add_parser("config", help="view, edit, get, or set configuration")
    cf.add_argument("action", nargs="?",
                    choices=[
                        "summary", "show", "status", "view", "edit", "get", "set", "path", "env-path", "paths",
                        "dump", "check", "doctor", "migrate", "setup", "reset",
                    ],
                    default="summary")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="*")
    cf.add_argument("--secrets", action="store_true", help="edit the local secrets .env file")
    cf.add_argument("--force", action="store_true", help="allow config set to create an unknown custom key")
    _add_setup_args(cf)
    cf.set_defaults(func=cmd_config)

    sc = sub.add_parser("secret", help="store a local secret in ~/.aegis/.env with hidden input")
    sc.add_argument("action", nargs="?", choices=["set", "path"], default="set")
    sc.add_argument("key", nargs="?")
    sc.add_argument("--stdin", action="store_true", help="read the value from stdin instead of prompting")
    sc.set_defaults(func=cmd_secret)

    se = sub.add_parser("sessions", help="list/show/remove/check sessions")
    se.add_argument("action", nargs="?", choices=["list", "show", "rm", "summarize", "search", "check"],
                    default="list")
    se.add_argument("id", nargs="?")
    se.add_argument("--json", action="store_true", help="print session check output as JSON")
    se.add_argument("--repair", action="store_true", help="repair stale running run records")
    se.add_argument("--session-limit", type=int, default=500)
    se.add_argument("--run-limit", type=int, default=500)
    se.add_argument("--stale-running-seconds", type=float, default=21600)
    se.add_argument("--stale-resume-pending-seconds", type=float, default=86400)
    se.set_defaults(func=cmd_sessions)

    g = sub.add_parser("gateway", help="run the multi-channel gateway")
    g.add_argument("action", nargs="?", choices=["run", "install", "uninstall", "status", "start", "stop", "restart"],
                   default="run", help="run, or install/control an OS service")
    g.add_argument("--channels", help="comma list: cli,telegram (default cli)")
    g.set_defaults(func=cmd_gateway)

    d = sub.add_parser("doctor", help="diagnose (and optionally repair) the installation")
    d.add_argument("--fix", action="store_true", help="create missing dirs + tighten secret perms")
    d.add_argument("--probe", action="store_true",
                   help="live checks: one-token provider call + channel token validation")
    d.add_argument("--release", action="store_true",
                   help="check GitHub/env signing and notarization inputs for desktop releases")
    d.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load(profile=args.profile)

    if not getattr(args, "command", None):
        if _needs_first_run():
            return _handle_first_run(config)
        # default: interactive chat
        from argparse import Namespace
        from ..session import SessionStore
        from . import repl
        store = SessionStore()
        repl.interactive(config, session=_terminal_session(Namespace(), store), store=store)
        return 0
    try:
        return args.func(args, config)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
