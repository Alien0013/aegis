"""`aegis` command-line entrypoint."""

from __future__ import annotations

import argparse
import importlib.metadata as importlib_metadata
import json
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
    from ..onboarding import run_onboarding, run_onboarding_noninteractive

    if getattr(args, "json", False) and not getattr(args, "non_interactive", False):
        return _die("--json requires --non-interactive")
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
    if args.action == "remove":
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
        job = store.add(prompt=prompt, schedule=args.schedule,
                        script=getattr(args, "script", "") or "", skills=skills,
                        deliver=getattr(args, "deliver", "") or "",
                        no_agent=bool(getattr(args, "no_agent", False)))
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
        _print(f"  {j.id}  [{j.schedule}]  {j.prompt[:60]}")
    return 0


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
        return 1 if unusable else 0
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
    from ..surface import plugin_inventory, skill_inventory, tool_inventory

    _print(f"AEGIS v{__version__}")
    _print(f"home:      {cfg.get_home()}")
    _print(f"config:    {cfg.config_path()}")
    _print(f"workspace: {cfg.sub('workspace')}")
    _print("")
    _print("Model")
    _print(f"  provider: {config.get('model.provider')}")
    _print(f"  model:    {config.get('model.default')}")
    try:
        from ..providers import build_provider
        p = build_provider(config)
        _print(f"  auth:     {p.auth.describe()} ({'ready' if p.auth.available() else 'missing'})")
        _print(f"  api mode: {p.api_mode.value}")
    except Exception as e:  # noqa: BLE001
        _print(f"  error:    {e}")

    tools = tool_inventory(config)
    skills = skill_inventory(config)
    plugins = plugin_inventory()
    mcp_servers = config.get("mcp.servers", {}) or {}
    channels = list(config.get("gateway.channels", []) or [])
    _print("")
    _print("Surface")
    _print(f"  toolsets: {', '.join(tools.toolsets)}")
    _print(f"  tools:    {tools.enabled_count}/{tools.total_count} model-visible")
    _print(f"  skills:   {skills.available_count} available ({skills.bundled_count} bundled)")
    _print(f"  plugins:  {plugins.files_count} file(s), {len(plugins.tools)} tool(s), "
           f"{len(plugins.channels)} channel(s), {len(plugins.providers)} provider(s)")
    if plugins.errors:
        _print(f"            {len(plugins.errors)} error(s); run `aegis plugins doctor`")
    _print(f"  mcp:      {len(mcp_servers)} server(s)")
    _print(f"  channels: {', '.join(channels) or 'none'}")

    # --- State: where the data lives and how much there is ---
    import os
    _print("")
    _print("State")
    try:
        from ..session import SessionStore
        _print(f"  sessions:   {len(SessionStore().list(limit=100000))}")
    except Exception:  # noqa: BLE001
        pass
    mem = cfg.sub("memories", "MEMORY.md")
    if os.path.exists(mem):
        _print(f"  memory:     {os.path.getsize(mem):,} bytes")
    tp = config.get("trajectory.path", "trajectories.jsonl")
    tp = tp if os.path.isabs(tp) else cfg.sub(tp)
    n_traj = sum(1 for _ in open(tp, encoding="utf-8")) if os.path.exists(tp) else 0
    _print(f"  trajectory: {'on' if config.get('trajectory.enabled', False) else 'off'} "
           f"({n_traj} captured)")
    try:
        from .. import usage_log
        r = usage_log.cost_report(30, config)
        _print(f"  cost (30d): ~${r['total_cost_usd']:.4f} over {r['calls']} call(s)")
    except Exception:  # noqa: BLE001
        pass
    try:
        home = cfg.get_home()
        total = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _, fs in os.walk(home) for f in fs
                    if os.path.exists(os.path.join(dp, f)))
        _print(f"  disk:       {total / 1024:.0f} KB in {home}")
    except Exception:  # noqa: BLE001
        pass

    _print("")
    _print("Services")
    try:
        from ..daemon import status as daemon_status
        for unit, state in daemon_status().items():
            _print(f"  {unit}: {state}")
    except Exception as e:  # noqa: BLE001
        _print(f"  unavailable: {e}")

    host = config.get("server.dashboard_host", "127.0.0.1")
    port = int(config.get("server.dashboard_port", 9119))
    token = config.get("server.dashboard_token")
    url = f"http://{host}:{port}/" + (f"?token={token}" if token else "")
    _print("")
    _print("Dashboard")
    _print(f"  {url}")
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
    if args.action in ("check", "migrate"):
        from ..config import DEFAULT_CONFIG, _deep_merge

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
        if args.action == "check":
            _print(f"missing default keys: {', '.join(missing) or '(none)'}")
            _print(f"unknown keys: {', '.join(unknown) or '(none)'}")
            return 0
        config.data = _deep_merge(DEFAULT_CONFIG, config.data)
        config.save()
        _print(f"migrated: added {len(missing)} missing default key(s).")
        return 0
    # dump
    import yaml
    _print(yaml.safe_dump(config.data, sort_keys=False))
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
    if action in ("install", "uninstall", "status"):
        from ..gateway.service import cmd_gateway_service
        chans = args.channels or ",".join(config.get("gateway.channels", []) or []) or "telegram"
        return cmd_gateway_service(action, chans)

    from ..gateway.channels import build_adapter
    from ..gateway.runner import GatewayRunner

    runner = GatewayRunner(config)
    configured = ",".join(config.get("gateway.channels", []) or [])
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
            subprocess.run(["git", "fetch", "-q", "origin", branch], cwd=str(pkg_root))
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


_CMDS = ("chat desktop model auth setup onboard status update skills plugins mcp serve rpc cron tools memory "
         "config sessions gateway doctor completion backup import insights webhook "
         "hooks kanban curator dashboard daemon acp pairing checkpoints background trace eval")


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
    s.add_argument("--quick", action="store_true", help="apply fast local defaults")
    s.add_argument("--advanced", action="store_true", help="show advanced setup choices")
    s.add_argument("--no-probe", action="store_true", help="skip provider connection test")
    s.add_argument("--no-services", action="store_true", help="skip user systemd service setup")
    _add_onboard_automation_args(s)
    s.set_defaults(func=cmd_setup)

    ob = sub.add_parser("onboard", help="interactive setup wizard (alias of setup)")
    ob.add_argument("--quick", action="store_true", help="apply fast local defaults")
    ob.add_argument("--advanced", action="store_true", help="show advanced setup choices")
    ob.add_argument("--no-probe", action="store_true", help="skip provider connection test")
    ob.add_argument("--no-services", action="store_true", help="skip user systemd service setup")
    _add_onboard_automation_args(ob)
    ob.set_defaults(func=cmd_setup)

    up = sub.add_parser("update", help="update AEGIS to the latest version")
    up.add_argument("--check", action="store_true", help="report if an update is available, don't install")
    up.add_argument("--branch", help="update against a non-default branch")
    up.set_defaults(func=cmd_update)

    un = sub.add_parser("uninstall", help="remove AEGIS (--purge also deletes ~/.aegis)")
    un.add_argument("--purge", action="store_true")
    un.set_defaults(func=cmd_uninstall)

    st = sub.add_parser("status", help="show install/auth/tools/skills/plugins/service status")
    st.set_defaults(func=cmd_status)

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
    wh.add_argument("--events", help="comma-sep X-GitHub-Event allowlist, e.g. pull_request,push")
    wh.add_argument("--skills", help="comma-sep skills to load before running")
    wh.set_defaults(func=_webhook.cmd_webhook)

    hk = sub.add_parser("hooks", help="lifecycle shell hooks")
    hk.add_argument("action", nargs="?", choices=["list", "test"], default="list")
    hk.add_argument("event", nargs="?")
    hk.set_defaults(func=_hooks.cmd_hooks)

    kb = sub.add_parser("kanban", help="multi-agent task board")
    kb.add_argument("action", nargs="?",
                    choices=["create", "list", "show", "claim", "complete", "assign", "dispatch",
                             "decompose", "run"],
                    default="list")
    kb.add_argument("title", nargs="?")
    kb.add_argument("--id")
    kb.add_argument("--body")
    kb.add_argument("--priority", type=int)
    kb.add_argument("--status")
    kb.add_argument("--assignee")
    kb.add_argument("--worker")
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

    for _name in ("desktop", "deksktop"):
        help_text = "install/update and launch the native desktop app"
        if _name == "deksktop":
            help_text = argparse.SUPPRESS
        ds = sub.add_parser(_name, help=help_text)
        ds.add_argument("--install-only", action="store_true",
                        help="install/update desktop dependencies without launching")
        ds.add_argument("--reinstall", action="store_true",
                        help="run npm install even if dependencies already exist")
        ds.add_argument("--sandbox", action="store_true",
                        help="opt into Electron's Chromium sandbox on Linux")
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

    sk = sub.add_parser("skills", help="list/view/create/install/search/remove skills")
    sk.add_argument("action", nargs="?",
                    choices=["list", "view", "new", "install", "search", "remove", "hub"], default="list")
    sk.add_argument("name", nargs="?", help="skill name, install source, or hub name")
    sk.add_argument("--force", action="store_true", help="install even if the security scan flags it")
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

    sv = sub.add_parser("serve", help="run an OpenAI-compatible API server")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.set_defaults(func=cmd_serve)

    rp = sub.add_parser("rpc", help="run a local JSON-RPC agent server over stdio")
    rp.set_defaults(func=cmd_rpc)

    cr = sub.add_parser("cron", help="schedule recurring agent tasks")
    cr.add_argument("action", nargs="?",
                    choices=["list", "add", "rm", "run", "install", "uninstall",
                             "status", "start", "stop", "restart"],
                    default="list")
    cr.add_argument("schedule", nargs="?", help='e.g. "30m", "@daily", or 5-field cron')
    cr.add_argument("prompt", nargs="*")
    cr.add_argument("--script", help="Python file to run first; its stdout is prepended as context")
    cr.add_argument("--skills", help="comma-sep skills to load before running")
    cr.add_argument("--deliver", help="comma-sep platform:chat_id targets (supersedes single channel)")
    cr.add_argument("--no-agent", action="store_true", help="run script-only and deliver stdout")
    cr.add_argument("--no-start", action="store_true", help="write service unit but do not start it")
    cr.set_defaults(func=cmd_cron)

    t = sub.add_parser("tools", help="list tools; `doctor` for availability; `status` for backends")
    t.add_argument("action", nargs="?", choices=["list", "status", "doctor"], default="list")
    t.set_defaults(func=cmd_tools)

    mem = sub.add_parser("memory", help="show/add long-term memory")
    mem.add_argument("action", nargs="?", choices=["show", "add", "clear"], default="show")
    mem.add_argument("text", nargs="*")
    mem.add_argument("--user", action="store_true", help="target the user profile")
    mem.set_defaults(func=cmd_memory)

    cf = sub.add_parser("config", help="get/set configuration")
    cf.add_argument("action", nargs="?", choices=["get", "set", "path", "dump", "check", "migrate"],
                    default="dump")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.set_defaults(func=cmd_config)

    sc = sub.add_parser("secret", help="store a local secret in ~/.aegis/.env with hidden input")
    sc.add_argument("action", nargs="?", choices=["set", "path"], default="set")
    sc.add_argument("key", nargs="?")
    sc.add_argument("--stdin", action="store_true", help="read the value from stdin instead of prompting")
    sc.set_defaults(func=cmd_secret)

    se = sub.add_parser("sessions", help="list/show/remove sessions")
    se.add_argument("action", nargs="?", choices=["list", "show", "rm", "summarize", "search"],
                    default="list")
    se.add_argument("id", nargs="?")
    se.set_defaults(func=cmd_sessions)

    g = sub.add_parser("gateway", help="run the multi-channel gateway")
    g.add_argument("action", nargs="?", choices=["run", "install", "uninstall", "status"],
                   default="run", help="run, or install/uninstall/status as an OS service")
    g.add_argument("--channels", help="comma list: cli,telegram (default cli)")
    g.set_defaults(func=cmd_gateway)

    d = sub.add_parser("doctor", help="diagnose (and optionally repair) the installation")
    d.add_argument("--fix", action="store_true", help="create missing dirs + tighten secret perms")
    d.add_argument("--probe", action="store_true",
                   help="live checks: one-token provider call + channel token validation")
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
