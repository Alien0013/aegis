"""Task-aware background process registry.

This is the AEGIS narrow waist for long-running commands, modeled after the
Hermes process registry: tools spawn managed ``ProcessSession`` objects, then
poll/read/wait/kill them through one registry instead of each tool owning a
private subprocess table.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as cfg
from ..types import new_id
from ..util import atomic_write, read_text, truncate

MAX_OUTPUT_CHARS = 200_000
FINISHED_TTL_SECONDS = 1800
MAX_PROCESSES = 64


@dataclass
class ProcessSession:
    id: str
    command: str
    task_id: str = ""
    session_key: str = ""
    pid: int | None = None
    process: subprocess.Popen | None = None
    env_ref: Any = None
    cwd: str = ""
    started_at: float = 0.0
    exited: bool = False
    exit_code: int | None = None
    output_buffer: str = ""
    max_output_chars: int = MAX_OUTPUT_CHARS
    notify_on_complete: bool = False
    watcher_platform: str = ""
    watcher_chat_id: str = ""
    detached: bool = False
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class ProcessRegistry:
    """Thread-safe registry of running and recently finished processes."""

    def __init__(self) -> None:
        self._running: dict[str, ProcessSession] = {}
        self._finished: dict[str, ProcessSession] = {}
        self._lock = threading.Lock()
        self.completion_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._completion_consumed: set[str] = set()
        self._load_checkpoint()

    def spawn_local(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        task_id: str = "",
        session_key: str = "",
        env_vars: dict[str, str] | None = None,
        notify_on_complete: bool = False,
        watcher_platform: str = "",
        watcher_chat_id: str = "",
    ) -> ProcessSession:
        session = ProcessSession(
            id=new_id("proc"),
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=str(Path(cwd).expanduser()) if cwd else os.getcwd(),
            started_at=time.time(),
            notify_on_complete=notify_on_complete,
            watcher_platform=watcher_platform,
            watcher_chat_id=watcher_chat_id,
        )
        shell = _find_shell()
        run_env = dict(os.environ)
        if env_vars:
            run_env.update({str(k): str(v) for k, v in env_vars.items()})
        run_env["PYTHONUNBUFFERED"] = "1"
        if task_id:
            run_env["AEGIS_TASK_ID"] = task_id

        proc = subprocess.Popen(
            [shell, "-lc", f"set +m; {command}"],
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=session.cwd,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            start_new_session=True,
        )
        session.process = proc
        session.pid = proc.pid
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            daemon=True,
            name=f"proc-reader-{session.id}",
        )
        session._reader_thread = reader
        reader.start()
        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session
        self._write_checkpoint()
        return session

    def spawn_via_env(
        self,
        env: Any,
        command: str,
        *,
        cwd: str | None = None,
        task_id: str = "",
        session_key: str = "",
        notify_on_complete: bool = False,
    ) -> ProcessSession:
        session = ProcessSession(
            id=new_id("proc"),
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd or getattr(env, "cwd", "") or "",
            started_at=time.time(),
            env_ref=env,
            notify_on_complete=notify_on_complete,
        )
        result = env.execute(command, cwd=session.cwd, timeout=10)
        session.output_buffer = str(result.get("output", ""))
        session.exit_code = int(result.get("returncode", 0) or 0)
        session.exited = True
        with self._lock:
            self._prune_if_needed()
            self._finished[session.id] = session
        self._write_checkpoint()
        if notify_on_complete:
            self._queue_completion(session)
        return session

    def _reader_loop(self, session: ProcessSession) -> None:
        proc = session.process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                with session._lock:
                    session.output_buffer += chunk
                    if len(session.output_buffer) > session.max_output_chars:
                        session.output_buffer = session.output_buffer[-session.max_output_chars:]
        finally:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            with session._lock:
                session.exited = True
                session.exit_code = proc.returncode
            self._move_to_finished(session)

    def _move_to_finished(self, session: ProcessSession) -> None:
        with self._lock:
            was_running = self._running.pop(session.id, None) is not None
            self._finished[session.id] = session
        self._write_checkpoint()
        if was_running and session.notify_on_complete:
            self._queue_completion(session)

    def _queue_completion(self, session: ProcessSession) -> None:
        title = f"{session.id} exited (code {session.exit_code}): {session.command[:80]}"
        output_tail = truncate(session.output_buffer[-2000:], 2000)
        try:
            from ..agent.wakeups import add_wakeup

            add_wakeup("process", title, output_tail)
        except Exception:
            pass
        if session.watcher_platform and session.watcher_chat_id:
            try:
                from ..gateway.queue import DeliveryQueue

                DeliveryQueue().enqueue(
                    session.watcher_platform,
                    session.watcher_chat_id,
                    f"background process finished: {title}",
                )
            except Exception:
                pass
        try:
            from ..eventbus import BUS

            BUS.publish({
                "type": "process_done",
                "platform": session.watcher_platform or "cli",
                "text": title,
            })
        except Exception:
            pass
        self.completion_queue.put({
            "type": "completion",
            "session_id": session.id,
            "session_key": session.session_key,
            "command": session.command,
            "exit_code": session.exit_code,
            "output": output_tail,
            "platform": session.watcher_platform,
            "chat_id": session.watcher_chat_id,
        })

    def drain_notifications(self) -> list[tuple[dict[str, Any], str]]:
        out: list[tuple[dict[str, Any], str]] = []
        while not self.completion_queue.empty():
            try:
                event = self.completion_queue.get_nowait()
            except Exception:
                break
            session_id = str(event.get("session_id", ""))
            if event.get("type") == "completion" and session_id in self._completion_consumed:
                continue
            text = (
                f"background process {session_id} exited "
                f"(code {event.get('exit_code')}): {event.get('command', '')}"
            )
            out.append((event, text))
        return out

    def get(self, session_id: str) -> ProcessSession | None:
        with self._lock:
            session = self._running.get(session_id) or self._finished.get(session_id)
            if session is None:
                for candidate in [*self._running.values(), *self._finished.values()]:
                    if candidate.id.startswith(session_id):
                        session = candidate
                        break
        if session is not None:
            self._refresh_detached_session(session)
            self._reconcile_local_exit(session)
        return session

    def poll(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        with session._lock:
            output_preview = session.output_buffer[-1000:]
            exited = session.exited
            exit_code = session.exit_code
        result: dict[str, Any] = {
            "session_id": session.id,
            "command": session.command,
            "status": "exited" if exited else "running",
            "pid": session.pid,
            "uptime_seconds": int(time.time() - session.started_at),
            "output_preview": output_preview,
        }
        if exited:
            result["exit_code"] = exit_code
            self._completion_consumed.add(session.id)
        if session.detached:
            result["detached"] = True
            result["note"] = "Process recovered after restart; output history may be incomplete"
        return result

    def read_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        with session._lock:
            output = session.output_buffer
            exited = session.exited
        lines = output.splitlines()
        selected = lines[-limit:] if offset == 0 and limit > 0 else lines[offset:offset + limit]
        if exited:
            self._completion_consumed.add(session.id)
        return {
            "session_id": session.id,
            "status": "exited" if exited else "running",
            "output": "\n".join(selected),
            "total_lines": len(lines),
            "showing": f"{len(selected)} lines",
        }

    def wait(self, session_id: str, timeout: int | None = None) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        deadline = time.monotonic() + int(timeout or 180)
        while time.monotonic() < deadline:
            self._reconcile_local_exit(session)
            if session.exited:
                self._completion_consumed.add(session.id)
                return {
                    "status": "exited",
                    "exit_code": session.exit_code,
                    "output": truncate(session.output_buffer[-2000:], 2000),
                }
            time.sleep(0.1)
        return {
            "status": "timeout",
            "output": truncate(session.output_buffer[-1000:], 1000),
        }

    def kill_process(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "exit_code": session.exit_code}
        try:
            if session.process is not None and session.pid is not None:
                if os.name == "nt":
                    session.process.terminate()
                else:
                    os.killpg(os.getpgid(session.pid), signal.SIGTERM)
            elif session.detached and session.pid is not None:
                if os.name == "nt":
                    os.kill(session.pid, signal.SIGTERM)
                else:
                    try:
                        os.killpg(os.getpgid(session.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        os.kill(session.pid, signal.SIGTERM)
            elif session.env_ref is not None and session.pid:
                session.env_ref.execute(f"kill {session.pid} 2>/dev/null", timeout=5)
            else:
                return {"status": "error", "error": "process handle is unavailable"}
            with session._lock:
                session.exited = True
                session.exit_code = -15
            self._move_to_finished(session)
            self._write_checkpoint()
            return {"status": "killed", "session_id": session.id}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}

    def write_stdin(self, session_id: str, data: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        proc = session.process
        if proc is None or proc.stdin is None:
            return {"status": "error", "error": "Process stdin is unavailable"}
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
            return {"status": "ok", "bytes_written": len(data)}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}

    def submit_stdin(self, session_id: str, data: str = "") -> dict[str, Any]:
        return self.write_stdin(session_id, data + "\n")

    def close_stdin(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        proc = session.process
        if proc is None or proc.stdin is None:
            return {"status": "error", "error": "Process stdin is unavailable"}
        try:
            proc.stdin.close()
            return {"status": "ok", "message": "stdin closed"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}

    def list_sessions(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sessions = [*self._running.values(), *self._finished.values()]
        for session in sessions:
            self._refresh_detached_session(session)
        if task_id:
            sessions = [s for s in sessions if s.task_id == task_id]
        out = []
        for session in sessions:
            self._refresh_detached_session(session)
            self._reconcile_local_exit(session)
            out.append({
                "session_id": session.id,
                "command": session.command[:200],
                "cwd": session.cwd,
                "pid": session.pid,
                "uptime_seconds": int(time.time() - session.started_at),
                "status": "exited" if session.exited else "running",
                "output_preview": session.output_buffer[-200:],
                "exit_code": session.exit_code if session.exited else None,
            })
        return out

    def has_active_processes(self, task_id: str) -> bool:
        with self._lock:
            sessions = list(self._running.values())
        for session in sessions:
            self._reconcile_local_exit(session)
        with self._lock:
            return any(s.task_id == task_id and not s.exited for s in self._running.values())

    def kill_all(self, task_id: str | None = None) -> int:
        with self._lock:
            sessions = [
                s for s in self._running.values()
                if task_id is None or s.task_id == task_id
            ]
        count = 0
        for session in sessions:
            result = self.kill_process(session.id)
            if result.get("status") in {"killed", "already_exited"}:
                count += 1
        return count

    def _reconcile_local_exit(self, session: ProcessSession) -> None:
        if session.exited or session.process is None:
            return
        try:
            code = session.process.poll()
        except Exception:
            return
        if code is None:
            return
        with session._lock:
            session.exited = True
            session.exit_code = code
        self._move_to_finished(session)

    def _refresh_detached_session(self, session: ProcessSession) -> None:
        if session.exited or not session.detached or session.pid is None:
            return
        if _pid_alive(session.pid):
            return
        with session._lock:
            session.exited = True
            session.exit_code = None
        self._move_to_finished(session)

    def _prune_if_needed(self) -> None:
        now = time.time()
        expired = [
            sid for sid, session in self._finished.items()
            if now - session.started_at > FINISHED_TTL_SECONDS
        ]
        for sid in expired:
            self._finished.pop(sid, None)
            self._completion_consumed.discard(sid)
        total = len(self._running) + len(self._finished)
        while total >= MAX_PROCESSES and self._finished:
            oldest = min(self._finished, key=lambda sid: self._finished[sid].started_at)
            self._finished.pop(oldest, None)
            self._completion_consumed.discard(oldest)
            total = len(self._running) + len(self._finished)
        self._write_checkpoint()

    def _write_checkpoint(self) -> None:
        try:
            data = {
                "running": [self._session_to_json(s) for s in self._running.values()],
                "finished": [self._session_to_json(s) for s in self._finished.values()],
            }
            path = _checkpoint_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_checkpoint(self) -> None:
        try:
            raw = read_text(_checkpoint_path())
            data = json.loads(raw) if raw.strip() else {}
        except Exception:
            return
        now = time.time()
        running = data.get("running") if isinstance(data, dict) else []
        finished = data.get("finished") if isinstance(data, dict) else []
        for entry in running or []:
            session = self._session_from_json(entry, detached=True)
            if session is None:
                continue
            if session.pid and _pid_alive(session.pid):
                self._running[session.id] = session
            else:
                session.exited = True
                session.exit_code = None
                self._finished[session.id] = session
        for entry in finished or []:
            session = self._session_from_json(entry, detached=True)
            if session is None:
                continue
            if now - session.started_at <= FINISHED_TTL_SECONDS:
                self._finished[session.id] = session

    @staticmethod
    def _session_to_json(session: ProcessSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "command": session.command,
            "task_id": session.task_id,
            "session_key": session.session_key,
            "pid": session.pid,
            "cwd": session.cwd,
            "started_at": session.started_at,
            "exited": session.exited,
            "exit_code": session.exit_code,
            "output_buffer": session.output_buffer[-MAX_OUTPUT_CHARS:],
            "notify_on_complete": session.notify_on_complete,
            "watcher_platform": session.watcher_platform,
            "watcher_chat_id": session.watcher_chat_id,
        }

    @staticmethod
    def _session_from_json(entry: Any, *, detached: bool) -> ProcessSession | None:
        if not isinstance(entry, dict) or not entry.get("id"):
            return None
        return ProcessSession(
            id=str(entry.get("id") or ""),
            command=str(entry.get("command") or ""),
            task_id=str(entry.get("task_id") or ""),
            session_key=str(entry.get("session_key") or ""),
            pid=int(entry["pid"]) if entry.get("pid") else None,
            cwd=str(entry.get("cwd") or ""),
            started_at=float(entry.get("started_at") or time.time()),
            exited=bool(entry.get("exited", False)),
            exit_code=entry.get("exit_code"),
            output_buffer=str(entry.get("output_buffer") or ""),
            notify_on_complete=bool(entry.get("notify_on_complete", False)),
            watcher_platform=str(entry.get("watcher_platform") or ""),
            watcher_chat_id=str(entry.get("watcher_chat_id") or ""),
            detached=detached,
        )


def _find_shell() -> str:
    import shutil

    return shutil.which("bash") or os.environ.get("SHELL") or "/bin/sh"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _checkpoint_path() -> Path:
    return cfg.sub("processes.json")


process_registry = ProcessRegistry()
