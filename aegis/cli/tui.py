"""Terminal cockpit for AEGIS.

This is intentionally store-backed instead of server-backed: it works even when
the dashboard backend is down, and it gives the CLI a real ``aegis tui`` surface
instead of a stale advertised command.
"""

from __future__ import annotations

import sys
import time
import webbrowser
from argparse import Namespace
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import Config


def _safe(label: str, errors: list[str], fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label}: {exc}")
        return default


def _clip(value: Any, limit: int = 72) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 1e12 else raw
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


_BUSY_MODE_HINTS = {
    "queue": "new input waits",
    "steer": "new input guides active runs",
    "interrupt": "new input stops active runs",
}


def _busy_mode(value: Any) -> str:
    mode = str(value or "queue").strip().lower()
    return mode if mode in _BUSY_MODE_HINTS else "queue"


def _busy_mode_hint(value: Any) -> str:
    mode = _busy_mode(value)
    return f"{mode} - {_BUSY_MODE_HINTS[mode]}"


def _dashboard_url(config: Config, *, redact: bool = False) -> str:
    host = config.get("server.dashboard_host", "127.0.0.1")
    port = int(config.get("server.dashboard_port", 9119))
    token = config.get("server.dashboard_token", "")
    if token and redact:
        token = "[REDACTED]"
    return f"http://{host}:{port}/" + (f"?token={token}" if token else "")


def collect_snapshot(config: Config) -> dict[str, Any]:
    errors: list[str] = []
    from .. import config as cfg

    def provider_report() -> dict[str, Any]:
        from ..providers import registry

        return registry.provider_report(config)

    def surface() -> dict[str, Any]:
        from ..surface import plugin_inventory, skill_inventory, tool_inventory

        return {
            "tools": tool_inventory(config),
            "skills": skill_inventory(config),
            "plugins": plugin_inventory(),
        }

    def sessions() -> list[dict[str, Any]]:
        from ..session import SessionStore

        return SessionStore().list(limit=8)

    def runs() -> list[dict[str, Any]]:
        from ..runs import RunStore

        return RunStore().list(limit=8)

    def active_runs() -> list[dict[str, Any]]:
        from ..runs import RunStore

        return RunStore().list(limit=20, status="running")

    def background() -> dict[str, Any]:
        from ..background import get_manager

        manager = get_manager()
        tasks = manager.list()
        return {
            "tasks": tasks,
            "capacity": manager.capacity(config),
        }

    def processes() -> list[dict[str, Any]]:
        from ..tools.process_registry import process_registry

        return process_registry.list_sessions()[:20]

    def cron_jobs() -> list[dict[str, Any]]:
        from ..cron import CronStore

        return [job.__dict__ for job in CronStore().list()]

    def kanban() -> dict[str, Any]:
        from ..kanban import KanbanStore

        store = KanbanStore()
        tasks = store.list()
        return {"stats": store.stats(), "tasks": tasks[:8]}

    def cross_session() -> dict[str, Any]:
        from ..session_checks import cross_session_integrity_report

        def threshold(key: str, default: float) -> float:
            raw = config.get(key, default)
            return default if raw is None else float(raw)

        stale_running = threshold("server.stale_run_health_seconds", 6 * 60 * 60)
        stale_resume = threshold("server.stale_resume_pending_health_seconds", 24 * 60 * 60)
        return cross_session_integrity_report(
            session_limit=100,
            run_limit=500,
            stale_running_seconds=stale_running,
            stale_resume_pending_seconds=stale_resume,
        )

    def services() -> dict[str, str]:
        from ..daemon import status

        return status()

    provider = _safe("provider", errors, provider_report, {})
    surface_data = _safe("surface", errors, surface, {})
    return {
        "provider": provider,
        "surface": surface_data,
        "sessions": _safe("sessions", errors, sessions, []),
        "runs": _safe("runs", errors, runs, []),
        "active_runs": _safe("active-runs", errors, active_runs, []),
        "background": _safe("background", errors, background, {"tasks": [], "capacity": {}}),
        "processes": _safe("processes", errors, processes, []),
        "cron": _safe("cron", errors, cron_jobs, []),
        "kanban": _safe("kanban", errors, kanban, {"stats": {}, "tasks": []}),
        "cross_session": _safe("cross-session", errors, cross_session, {}),
        "services": _safe("services", errors, services, {}),
        "mcp_servers": dict(config.get("mcp.servers", {}) or {}),
        "channels": list(config.get("gateway.channels", []) or []),
        "config": {
            "profile": cfg.current_profile() or "default",
            "config_path": str(cfg.config_path()),
            "secrets_path": str(cfg.env_path()),
            "reasoning": config.get("display.reasoning", "summary"),
            "timestamps": bool(config.get("display.timestamps", False)),
            "status_footer": bool(config.get("display.status_footer", True)),
            "tool_progress": config.get("display.tool_progress", "compact"),
            "busy_mode": _busy_mode(config.get("gateway.busy_mode", "queue")),
            "exec_mode": config.get("tools.exec_mode", "auto"),
        },
        "dashboard_url": _dashboard_url(config, redact=True),
        "errors": errors,
    }


def _kv_table(rows: list[tuple[str, Any]]) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for key, value in rows:
        table.add_row(key, str(value))
    return table


def _model_panel(snapshot: dict[str, Any]) -> Panel:
    active = (snapshot.get("provider") or {}).get("active") or {}
    rows = [
        ("provider", active.get("name") or active.get("provider") or ""),
        ("model", active.get("model") or ""),
        ("mode", active.get("api_mode") or ""),
        ("context", f"{int(active.get('context_length') or 0):,}"),
    ]
    if active.get("capability_summary"):
        rows.append(("caps", active.get("capability_summary")))
    if active.get("error"):
        rows.append(("error", _clip(active.get("error"), 64)))
    return Panel(_kv_table(rows), title="Model", border_style="cyan")


def _surface_panel(snapshot: dict[str, Any]) -> Panel:
    surface = snapshot.get("surface") or {}
    tools = surface.get("tools")
    skills = surface.get("skills")
    plugins = surface.get("plugins")
    rows = [
        ("tools", f"{getattr(tools, 'enabled_count', 0)}/{getattr(tools, 'total_count', 0)}"),
        ("toolsets", ", ".join(getattr(tools, "toolsets", []) or [])),
        ("skills", f"{getattr(skills, 'available_count', 0)} available"),
        ("plugins", (
            f"{getattr(plugins, 'files_count', 0)} files, "
            f"{len(getattr(plugins, 'tools', []) or [])} tools"
        )),
        ("mcp", f"{len(snapshot.get('mcp_servers') or {})} servers"),
        ("channels", ", ".join(snapshot.get("channels") or []) or "none"),
    ]
    return Panel(_kv_table(rows), title="Surface", border_style="green")


def _status_panel(snapshot: dict[str, Any]) -> Panel:
    active = snapshot.get("active_runs") or []
    background = snapshot.get("background") or {}
    bg_tasks = background.get("tasks") or []
    capacity = background.get("capacity") or {}
    processes = snapshot.get("processes") or []
    cron_jobs = snapshot.get("cron") or []
    running_bg = sum(1 for task in bg_tasks if task.get("status") == "running")
    done_bg = sum(1 for task in bg_tasks if task.get("status") in {"done", "error"})
    running_proc = sum(1 for proc in processes if not proc.get("exited"))
    oldest = 0.0
    now = datetime.now(timezone.utc).timestamp()
    for run in active:
        started = _parse_time(run.get("started_at"))
        if started and (oldest == 0.0 or started < oldest):
            oldest = started
    oldest_age = _duration(now - oldest) if oldest else "none"
    rows = [
        ("active runs", f"{len(active)} (oldest {oldest_age})"),
        ("background", f"{running_bg}/{capacity.get('max', '?')} running, {done_bg} retained"),
        ("processes", f"{running_proc} running / {len(processes)} tracked"),
        ("cron", f"{sum(1 for job in cron_jobs if job.get('enabled', True))}/{len(cron_jobs)} enabled"),
        ("sessions", f"{len(snapshot.get('sessions') or [])} recent"),
    ]
    if active:
        run = active[0]
        started = _parse_time(run.get("started_at"))
        elapsed = _duration(now - started) if started else "running"
        label = run.get("title") or run.get("prompt_preview") or run.get("id")
        rows.insert(1, ("active now", f"{elapsed} · {_clip(label, 42)}"))
    return Panel(_kv_table(rows), title="Status", border_style="bright_green")


def _status_strip(snapshot: dict[str, Any]) -> Panel:
    active = (snapshot.get("provider") or {}).get("active") or {}
    config = snapshot.get("config") or {}
    background = snapshot.get("background") or {}
    bg_tasks = background.get("tasks") or []
    processes = snapshot.get("processes") or []
    live = [
        f"profile {config.get('profile') or 'default'}",
        f"model {active.get('model') or '-'}",
        f"runs {len(snapshot.get('active_runs') or [])}",
        f"subagents {sum(1 for task in bg_tasks if task.get('status') == 'running')}",
        f"processes {sum(1 for proc in processes if not proc.get('exited') and proc.get('status') != 'exited')}",
        f"busy {_busy_mode(config.get('busy_mode'))}",
    ]
    if config.get("tool_progress"):
        live.append(f"tools {config.get('tool_progress')}")
    return Panel("  •  ".join(live), border_style="bright_cyan", padding=(0, 1))


def _live_work_panel(snapshot: dict[str, Any]) -> Panel:
    table = Table(expand=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("ID", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Elapsed", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    now = datetime.now(timezone.utc).timestamp()
    added = 0
    for run in snapshot.get("active_runs") or []:
        started = _parse_time(run.get("started_at"))
        table.add_row(
            "run",
            _clip(run.get("id"), 18),
            _clip(run.get("status") or "running", 12),
            _duration(now - started) if started else "",
            _clip(run.get("title") or run.get("prompt_preview") or "", 78),
        )
        added += 1
    for task in (snapshot.get("background") or {}).get("tasks") or []:
        started = float(task.get("started_at") or task.get("created_at") or 0)
        elapsed = _duration(time.time() - started) if started else ""
        status = str(task.get("status") or "")
        style = "red" if status == "error" else "green" if status == "done" else ""
        table.add_row(
            "subagent",
            _clip(task.get("id"), 18),
            Text(status or "running", style=style),
            elapsed,
            _clip(task.get("prompt") or task.get("result_preview") or task.get("parent_session_id") or "", 78),
        )
        added += 1
    for proc in snapshot.get("processes") or []:
        status = str(proc.get("status") or ("exited" if proc.get("exited") else "running"))
        if status == "exited" and added >= 8:
            continue
        elapsed = _duration(float(proc.get("uptime_seconds") or 0))
        detail = proc.get("command") or proc.get("output_preview") or proc.get("cwd") or ""
        table.add_row(
            "process",
            _clip(proc.get("session_id"), 18),
            status,
            elapsed,
            _clip(detail, 78),
        )
        added += 1
    if not added:
        table.add_row("idle", "-", "-", "-", "No live runs, subagents, or background processes")
    return Panel(table, title="Live Work", border_style="bright_magenta")


def _config_panel(snapshot: dict[str, Any]) -> Panel:
    config = snapshot.get("config") or {}
    rows = [
        ("profile", config.get("profile") or "default"),
        ("display", (
            f"reasoning {config.get('reasoning')}; "
            f"footer {'on' if config.get('status_footer') else 'off'}; "
            f"tools {config.get('tool_progress')}"
        )),
        ("input", _busy_mode_hint(config.get("busy_mode"))),
        ("exec", config.get("exec_mode") or "auto"),
        ("edit", "e config / s secrets"),
    ]
    return Panel(_kv_table(rows), title="Config", border_style="bright_cyan")


def _sessions_panel(snapshot: dict[str, Any]) -> Panel:
    table = Table(expand=True)
    table.add_column("Session", overflow="fold")
    table.add_column("Updated", no_wrap=True)
    for row in snapshot.get("sessions") or []:
        table.add_row(_clip(row.get("title") or row.get("id"), 50), _clip(row.get("updated_at"), 19))
    if not table.rows:
        table.add_row("No sessions yet", "")
    return Panel(table, title="Sessions", border_style="magenta")


def _runs_panel(snapshot: dict[str, Any]) -> Panel:
    table = Table(expand=True)
    table.add_column("Run", overflow="fold")
    table.add_column("Surface", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    for row in snapshot.get("runs") or []:
        table.add_row(
            _clip(row.get("title") or row.get("prompt_preview") or row.get("id"), 42),
            _clip(row.get("surface"), 16),
            _clip(row.get("status"), 16),
        )
    if not table.rows:
        table.add_row("No runs yet", "", "")
    return Panel(table, title="Runs", border_style="yellow")


def _cron_panel(snapshot: dict[str, Any]) -> Panel:
    jobs = snapshot.get("cron") or []
    enabled = sum(1 for job in jobs if job.get("enabled", True))
    recent = [_clip(job.get("name") or job.get("prompt") or job.get("id"), 52) for job in jobs[:5]]
    body = Text()
    body.append(f"{enabled}/{len(jobs)} enabled\n", style="bold")
    body.append("\n".join(f"- {item}" for item in recent) if recent else "No scheduled jobs")
    return Panel(body, title="Cron", border_style="blue")


def _kanban_panel(snapshot: dict[str, Any]) -> Panel:
    board = snapshot.get("kanban") or {}
    stats = (board.get("stats") or {}).get("by_status") or {}
    tasks = board.get("tasks") or []
    body = Text()
    body.append(", ".join(f"{k}:{v}" for k, v in sorted(stats.items())) or "No tasks")
    for task in tasks[:5]:
        body.append(f"\n- {_clip(getattr(task, 'title', ''), 52)}")
    return Panel(body, title="Kanban", border_style="bright_blue")


def _integrity_panel(snapshot: dict[str, Any]) -> Panel:
    report = snapshot.get("cross_session") or {}
    ok = bool(report.get("ok", False)) if report else False
    status = str(report.get("status") or ("ok" if ok else "unknown"))
    counts = report.get("counts") or {}
    issues = report.get("issues") or []
    border = "green" if ok else "red" if report else "yellow"
    body = Text()
    body.append(status, style="bold green" if ok else "bold red")
    body.append(
        f"\nerrors {int(report.get('error_count') or 0)}"
        f" / warnings {int(report.get('warning_count') or 0)}"
    )
    body.append(
        f"\nsessions {int(counts.get('sessions') or 0)}"
        f" / runs {int(counts.get('runs') or 0)}"
        f" / running {int(counts.get('running_runs') or 0)}"
    )
    resume_pending = int(counts.get("resume_pending_sessions") or 0)
    if resume_pending:
        body.append(f"\nresume pending {resume_pending}")
    if issues:
        for issue in issues[:3]:
            target = issue.get("session_id") or issue.get("run_id") or ""
            suffix = f" {target}" if target else ""
            body.append(f"\n- {issue.get('severity')} {issue.get('code')}{suffix}")
        if len(issues) > 3:
            body.append(f"\n... {len(issues) - 3} more")
    else:
        body.append("\nNo integrity issues")
    return Panel(body, title="Integrity", border_style=border)


def _services_panel(snapshot: dict[str, Any]) -> Panel:
    services = snapshot.get("services") or {}
    rows = [(name, state) for name, state in sorted(services.items())]
    if not rows:
        rows = [("services", "unavailable")]
    rows.append(("dashboard", snapshot.get("dashboard_url", "")))
    return Panel(_kv_table(rows), title="Ops", border_style="white")


def build_renderable(snapshot: dict[str, Any]) -> Group:
    top = Columns(
        [_model_panel(snapshot), _surface_panel(snapshot), _config_panel(snapshot)],
        equal=True,
        expand=True,
    )
    middle = Columns([_sessions_panel(snapshot), _runs_panel(snapshot)], equal=True, expand=True)
    lower = Columns([
        _cron_panel(snapshot),
        _kanban_panel(snapshot),
        _integrity_panel(snapshot),
        _services_panel(snapshot),
    ], expand=True)
    footer_items = ["r refresh", "c chat", "d dashboard", "e config/edit", "s secrets/edit", "q quit"]
    if snapshot.get("errors"):
        footer_items.append(f"{len(snapshot['errors'])} warning(s)")
    footer = Panel("  ".join(footer_items), title="Actions", border_style="dim")
    return Group(
        Panel.fit("[bold]AEGIS Terminal Cockpit[/bold]", border_style="cyan"),
        _status_strip(snapshot),
        _status_panel(snapshot),
        _live_work_panel(snapshot),
        top,
        middle,
        lower,
        footer,
    )


def render_dashboard(config: Config, *, console: Console | None = None) -> dict[str, Any]:
    console = console or Console()
    snapshot = collect_snapshot(config)
    console.print(build_renderable(snapshot))
    if snapshot.get("errors"):
        warning_table = Table(title="Warnings", expand=True)
        warning_table.add_column("Source")
        warning_table.add_column("Detail")
        for error in snapshot["errors"]:
            source, _, detail = error.partition(":")
            warning_table.add_row(source, detail.strip())
        console.print(warning_table)
    return snapshot


def _open_dashboard(config: Config) -> None:
    webbrowser.open(_dashboard_url(config))


def _open_chat(config: Config) -> int:
    from ..session import Session
    from ..session import SessionStore
    from . import repl

    store = SessionStore()
    repl.interactive(config, session=store.latest() or Session.create(), store=store)
    return 0


def _edit_config(config: Config, *, secrets: bool = False) -> int:
    from .main import cmd_config

    return cmd_config(Namespace(action="edit", key=None, value=None, secrets=secrets), config)


def _handle_choice(choice: str, config: Config, console: Console) -> int | None:
    choice = choice.strip().lower()
    if choice in {"q", "quit", "exit"}:
        return 0
    if choice in {"", "r", "refresh"}:
        return None
    if choice in {"d", "dashboard", "ui"}:
        _open_dashboard(config)
        return None
    if choice in {"c", "chat"}:
        return _open_chat(config)
    if choice in {"e", "edit", "config"}:
        code = _edit_config(config, secrets=False)
        if code:
            console.print(f"Config editor exited with status {code}.", style="yellow")
        return None
    if choice in {"s", "secret", "secrets", "env"}:
        code = _edit_config(config, secrets=True)
        if code:
            console.print(f"Secrets editor exited with status {code}.", style="yellow")
        return None
    console.print("Unknown action. Use r, c, d, e, s, or q.", style="yellow")
    time.sleep(1.0)
    return None


def _prompt_choice() -> str:
    try:
        from prompt_toolkit import prompt

        return prompt("aegis tui> ")
    except Exception:  # noqa: BLE001
        return input("aegis tui> ")


def cmd_tui(args: Namespace, config: Config) -> int:
    console = Console(no_color=getattr(args, "no_color", False))
    if getattr(args, "watch", False):
        interval = max(0.5, float(getattr(args, "interval", 5.0) or 5.0))
        try:
            while True:
                console.clear()
                render_dashboard(config, console=console)
                time.sleep(interval)
        except KeyboardInterrupt:
            return 0

    if getattr(args, "once", False) or not (sys.stdin.isatty() and sys.stdout.isatty()):
        render_dashboard(config, console=console)
        return 0

    while True:
        console.clear()
        render_dashboard(config, console=console)
        result = _handle_choice(_prompt_choice(), config, console)
        if result is not None:
            return result
