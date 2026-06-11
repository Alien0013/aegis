"""Long-running background process management (start/list/logs/stop)."""

from __future__ import annotations

import json
import signal
import subprocess

from .. import config as cfg
from ..types import new_id
from ..util import atomic_write, ensure_dir, read_text, truncate
from .base import Tool, ToolContext, ToolResult


def _dir():
    return ensure_dir(cfg.sub("processes"))


def _registry() -> dict:
    raw = read_text(_dir() / "registry.json")
    return json.loads(raw) if raw.strip() else {}


def _save_registry(data: dict) -> None:
    atomic_write(_dir() / "registry.json", json.dumps(data, indent=2))


class ProcessTool(Tool):
    name = "process"
    description = ("Manage long-running background processes (dev servers, watchers). "
                  "actions: start(command) | list | logs(id) | stop(id).")
    groups = ["runtime"]
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "list", "logs", "stop"]},
            "command": {"type": "string"},
            "id": {"type": "string"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        action = args["action"]
        reg = _registry()
        if action == "start":
            if not args.get("command"):
                return ToolResult.error("start needs a command")
            pid_id = new_id("proc")
            log = _dir() / f"{pid_id}.log"
            with open(log, "w") as fh:
                proc = subprocess.Popen(args["command"], shell=True, cwd=str(ctx.cwd),
                                        stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            reg[pid_id] = {"pid": proc.pid, "command": args["command"], "log": str(log)}
            _save_registry(reg)
            _watch_completion(pid_id, proc, args["command"], log, ctx)
            return ToolResult.ok(
                f"started {pid_id} (pid {proc.pid}): {args['command']} — "
                "you'll be notified on your next turn when it exits.",
                display=f"started {pid_id}")
        if action == "list":
            if not reg:
                return ToolResult.ok("(no background processes)")
            rows = []
            for pid_id, info in reg.items():
                alive = _alive(info["pid"])
                rows.append(f"{pid_id}  pid={info['pid']}  {'running' if alive else 'exited'}  {info['command'][:50]}")
            return ToolResult.ok("\n".join(rows), display=f"{len(reg)} process(es)")
        if action == "logs":
            info = reg.get(args.get("id", ""))
            if not info:
                return ToolResult.error("unknown process id")
            return ToolResult.ok(truncate(read_text(info["log"]), 20_000), display="process logs")
        if action == "stop":
            info = reg.get(args.get("id", ""))
            if not info:
                return ToolResult.error("unknown process id")
            try:
                import os
                os.killpg(os.getpgid(info["pid"]), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
            del reg[args["id"]]
            _save_registry(reg)
            return ToolResult.ok(f"stopped {args['id']}", display="stopped")
        return ToolResult.error(f"unknown action {action}")


def _watch_completion(pid_id: str, proc, command: str, log, ctx: ToolContext) -> None:
    """Daemon thread: when the process exits, queue a wakeup for the next agent turn
    and (in a gateway chat) announce into the chat so a fresh turn fires immediately."""
    import threading
    platform = getattr(getattr(ctx, "agent", None), "platform", None)
    chat_id = getattr(getattr(ctx, "agent", None), "chat_id", None)

    def _wait():
        code = proc.wait()
        tail = "\n".join(read_text(log).splitlines()[-15:])
        title = f"{pid_id} exited (code {code}): {command[:80]}"
        from ..agent.wakeups import add_wakeup
        add_wakeup("process", title, tail)
        if platform and chat_id:
            try:
                from ..gateway.queue import DeliveryQueue
                DeliveryQueue().enqueue(platform, chat_id,
                                        f"⏺ background process finished — {title}")
            except Exception:  # noqa: BLE001
                pass
        try:
            from ..eventbus import BUS
            BUS.publish({"type": "process_done", "platform": platform or "cli",
                         "text": title})
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_wait, daemon=True, name=f"watch-{pid_id}").start()


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_tools() -> list[Tool]:
    return [ProcessTool()]
