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
WATCH_MIN_INTERVAL_SECONDS = 15
WATCH_STRIKE_LIMIT = 3
WATCH_GLOBAL_MAX_PER_WINDOW = 15
WATCH_GLOBAL_WINDOW_SECONDS = 10
WATCH_GLOBAL_COOLDOWN_SECONDS = 30


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
    pty: bool = False
    pty_fallback: str = ""
    watch_patterns: list[str] = field(default_factory=list)
    _watch_hits: int = field(default=0, repr=False)
    _watch_suppressed: int = field(default=0, repr=False)
    _watch_disabled: bool = field(default=False, repr=False)
    _watch_last_emit_at: float = field(default=0.0, repr=False)
    _watch_cooldown_until: float = field(default=0.0, repr=False)
    _watch_strike_candidate: bool = field(default=False, repr=False)
    _watch_consecutive_strikes: int = field(default=0, repr=False)
    _pty: Any = field(default=None, repr=False)
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
        self._global_watch_lock = threading.Lock()
        self._global_watch_window_start: float = 0.0
        self._global_watch_window_hits: int = 0
        self._global_watch_tripped_until: float = 0.0
        self._global_watch_suppressed_during_trip: int = 0
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
        watch_patterns: list[str] | None = None,
        use_pty: bool = False,
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
            watch_patterns=_normalize_watch_patterns(watch_patterns),
        )
        shell = _find_shell()
        run_env = dict(os.environ)
        if env_vars:
            run_env.update({str(k): str(v) for k, v in env_vars.items()})
        run_env["PYTHONUNBUFFERED"] = "1"
        if task_id:
            run_env["AEGIS_TASK_ID"] = task_id

        if use_pty:
            try:
                pty_proc = _spawn_pty_process(
                    [shell, "-lic", f"set +m; {command}"],
                    cwd=session.cwd,
                    env=run_env,
                )
                session.pid = int(getattr(pty_proc, "pid", 0) or 0) or None
                session.pty = True
                session._pty = pty_proc
                with self._lock:
                    self._prune_if_needed()
                    self._running[session.id] = session
                reader = threading.Thread(
                    target=self._pty_reader_loop,
                    args=(session,),
                    daemon=True,
                    name=f"proc-pty-reader-{session.id}",
                )
                session._reader_thread = reader
                reader.start()
                self._write_checkpoint()
                return session
            except ImportError:
                session.pty_fallback = "ptyprocess is not installed; fell back to pipe mode"
            except Exception as e:  # noqa: BLE001
                session.pty_fallback = f"PTY spawn failed ({e}); fell back to pipe mode"

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
        watch_patterns: list[str] | None = None,
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
            watch_patterns=_normalize_watch_patterns(watch_patterns),
        )
        result = env.execute(command, cwd=session.cwd, timeout=10)
        session.output_buffer = str(result.get("output", ""))
        self._check_watch_patterns(session, session.output_buffer)
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
                self._check_watch_patterns(session, chunk)
        finally:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            with session._lock:
                session.exited = True
                session.exit_code = proc.returncode
            self._move_to_finished(session)

    def _pty_reader_loop(self, session: ProcessSession) -> None:
        pty = session._pty
        if pty is None:
            return
        try:
            while pty.isalive():
                try:
                    chunk = pty.read(4096)
                except EOFError:
                    break
                if not chunk:
                    continue
                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                with session._lock:
                    session.output_buffer += text
                    if len(session.output_buffer) > session.max_output_chars:
                        session.output_buffer = session.output_buffer[-session.max_output_chars:]
                self._check_watch_patterns(session, text)
        finally:
            try:
                pty.wait()
            except Exception:
                pass
            with session._lock:
                session.exited = True
                session.exit_code = int(getattr(pty, "exitstatus", -1) or 0)
            self._move_to_finished(session)

    def _move_to_finished(self, session: ProcessSession) -> None:
        with self._lock:
            was_running = self._running.pop(session.id, None) is not None
            self._finished[session.id] = session
        self._write_checkpoint()
        if was_running and session.notify_on_complete:
            self._queue_completion(session)

    def _check_watch_patterns(self, session: ProcessSession, new_text: str) -> None:
        """Scan freshly streamed output for Hermes-style watch pattern notifications."""
        if not session.watch_patterns or session._watch_disabled or session.exited:
            return
        matched_lines: list[str] = []
        matched_pattern = ""
        for line in new_text.splitlines():
            for pattern in session.watch_patterns:
                if pattern and pattern in line:
                    matched_lines.append(line.rstrip())
                    if not matched_pattern:
                        matched_pattern = pattern
                    break
        if not matched_lines:
            return

        now = time.time()
        return_early = False
        should_disable = False
        suppressed = 0
        with session._lock:
            if session._watch_cooldown_until and now < session._watch_cooldown_until:
                session._watch_suppressed += len(matched_lines)
                if not session._watch_strike_candidate:
                    session._watch_strike_candidate = True
                    session._watch_consecutive_strikes += 1
                    if session._watch_consecutive_strikes >= WATCH_STRIKE_LIMIT:
                        session._watch_disabled = True
                        session.notify_on_complete = True
                        should_disable = True
                        suppressed = session._watch_suppressed
                return_early = True
            else:
                if session._watch_cooldown_until and not session._watch_strike_candidate:
                    session._watch_consecutive_strikes = 0
                session._watch_strike_candidate = False
                session._watch_last_emit_at = now
                session._watch_cooldown_until = now + WATCH_MIN_INTERVAL_SECONDS
                session._watch_hits += 1
                suppressed = session._watch_suppressed
                session._watch_suppressed = 0

        if return_early:
            if should_disable:
                self._write_checkpoint()
                self._queue_watch_disabled(session, suppressed)
            return

        output = "\n".join(matched_lines[:20])
        if len(output) > 2000:
            output = output[:2000] + "\n...(truncated)"
        if self._global_watch_admit(now):
            self._queue_watch_match(session, matched_pattern, output, suppressed)

    def _global_watch_admit(self, now: float) -> bool:
        release_msg: dict[str, Any] | None = None
        trip_msg: dict[str, Any] | None = None
        with self._global_watch_lock:
            if self._global_watch_tripped_until and now >= self._global_watch_tripped_until:
                suppressed = self._global_watch_suppressed_during_trip
                self._global_watch_tripped_until = 0.0
                self._global_watch_suppressed_during_trip = 0
                self._global_watch_window_start = now
                self._global_watch_window_hits = 0
                if suppressed > 0:
                    release_msg = {
                        "type": "watch_overflow_released",
                        "session_id": "",
                        "session_key": "",
                        "command": "",
                        "suppressed": suppressed,
                        "message": (
                            "Watch-pattern notifications resumed. "
                            f"{suppressed} match event(s) were suppressed during the flood."
                        ),
                    }

            if self._global_watch_tripped_until and now < self._global_watch_tripped_until:
                self._global_watch_suppressed_during_trip += 1
                admit = False
            else:
                if now - self._global_watch_window_start >= WATCH_GLOBAL_WINDOW_SECONDS:
                    self._global_watch_window_start = now
                    self._global_watch_window_hits = 0
                if self._global_watch_window_hits >= WATCH_GLOBAL_MAX_PER_WINDOW:
                    self._global_watch_tripped_until = now + WATCH_GLOBAL_COOLDOWN_SECONDS
                    self._global_watch_suppressed_during_trip += 1
                    trip_msg = {
                        "type": "watch_overflow_tripped",
                        "session_id": "",
                        "session_key": "",
                        "command": "",
                        "message": (
                            f"Watch-pattern overflow: >{WATCH_GLOBAL_MAX_PER_WINDOW} "
                            f"notifications in {WATCH_GLOBAL_WINDOW_SECONDS}s across all processes. "
                            f"Suppressing further watch_match events for "
                            f"{WATCH_GLOBAL_COOLDOWN_SECONDS}s."
                        ),
                    }
                    admit = False
                else:
                    self._global_watch_window_hits += 1
                    admit = True

        if release_msg is not None:
            self.completion_queue.put(release_msg)
            self._queue_wakeup_event(release_msg)
        if trip_msg is not None:
            self.completion_queue.put(trip_msg)
            self._queue_wakeup_event(trip_msg)
        return admit

    def _queue_watch_match(
        self,
        session: ProcessSession,
        pattern: str,
        output: str,
        suppressed: int,
    ) -> None:
        event = {
            "type": "watch_match",
            "session_id": session.id,
            "session_key": session.session_key,
            "command": session.command,
            "pattern": pattern,
            "output": output,
            "suppressed": suppressed,
            "platform": session.watcher_platform,
            "chat_id": session.watcher_chat_id,
        }
        self.completion_queue.put(event)
        self._queue_wakeup_event(event)

    def _queue_watch_disabled(self, session: ProcessSession, suppressed: int) -> None:
        event = {
            "type": "watch_disabled",
            "session_id": session.id,
            "session_key": session.session_key,
            "command": session.command,
            "suppressed": suppressed,
            "platform": session.watcher_platform,
            "chat_id": session.watcher_chat_id,
            "message": (
                f"Watch patterns disabled for process {session.id} — "
                f"{WATCH_STRIKE_LIMIT} consecutive rate-limit windows triggered "
                f"(min spacing {WATCH_MIN_INTERVAL_SECONDS}s). Falling back to "
                "notify_on_complete semantics; you'll get exactly one notification "
                "when the process exits."
            ),
        }
        self.completion_queue.put(event)
        self._queue_wakeup_event(event)

    def _queue_wakeup_event(self, event: dict[str, Any]) -> None:
        text = format_process_notification(event)
        if not text:
            return
        try:
            from ..agent.wakeups import add_wakeup

            add_wakeup("process", text[:200], str(event.get("output") or event.get("message") or ""))
        except Exception:
            pass
        platform = str(event.get("platform") or "")
        chat_id = str(event.get("chat_id") or "")
        if platform and chat_id:
            try:
                from ..gateway.queue import DeliveryQueue

                DeliveryQueue().enqueue(platform, chat_id, text)
            except Exception:
                pass
        try:
            from ..eventbus import BUS

            BUS.publish({
                "type": event.get("type", "process"),
                "platform": platform or "cli",
                "text": text,
            })
        except Exception:
            pass

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
            text = format_process_notification(event)
            if text:
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
        if session.pty:
            result["pty"] = True
        if session.pty_fallback:
            result["pty_fallback"] = session.pty_fallback
        if session.watch_patterns:
            result["watch_patterns"] = session.watch_patterns
            result["watch_disabled"] = session._watch_disabled
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
            if session._pty is not None:
                try:
                    session._pty.terminate(force=True)
                except TypeError:
                    session._pty.terminate()
            elif session.process is not None and session.pid is not None:
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
        if session._pty is not None:
            try:
                pty_data = data if os.name == "nt" else data.encode("utf-8")
                session._pty.write(pty_data)
                return {"status": "ok", "bytes_written": len(data)}
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "error": str(e)}
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
        if session._pty is not None:
            try:
                session._pty.sendeof()
                return {"status": "ok", "message": "EOF sent"}
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "error": str(e)}
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
                "pty": session.pty,
                "pty_fallback": session.pty_fallback,
                "watch_patterns": session.watch_patterns,
                "watch_disabled": session._watch_disabled,
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
            "watch_patterns": session.watch_patterns,
            "pty": session.pty,
            "pty_fallback": session.pty_fallback,
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
            watch_patterns=_normalize_watch_patterns(entry.get("watch_patterns")),
            pty=bool(entry.get("pty", False)),
            pty_fallback=str(entry.get("pty_fallback") or ""),
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


def _spawn_pty_process(argv: list[str], *, cwd: str, env: dict[str, str]) -> Any:
    if os.name == "nt":
        from winpty import PtyProcess as PtyProcess
    else:
        from ptyprocess import PtyProcess

    return PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(30, 120))


def _checkpoint_path() -> Path:
    return cfg.sub("processes.json")


def _normalize_watch_patterns(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return []
    patterns: list[str] = []
    for value in values:
        pattern = str(value)
        if pattern:
            patterns.append(pattern)
    return patterns


def format_process_notification(event: dict[str, Any]) -> str | None:
    evt_type = event.get("type", "completion")
    sid = event.get("session_id", "unknown")
    command = event.get("command", "unknown")

    if evt_type == "watch_disabled":
        return f"[IMPORTANT: {event.get('message', '')}]"

    if evt_type == "watch_match":
        pattern = event.get("pattern", "?")
        output = event.get("output", "")
        suppressed = int(event.get("suppressed", 0) or 0)
        text = (
            f"[IMPORTANT: Background process {sid} matched "
            f"watch pattern \"{pattern}\".\n"
            f"Command: {command}\n"
            f"Matched output:\n{output}"
        )
        if suppressed:
            text += f"\n({suppressed} earlier matches were suppressed by rate limit)"
        text += "]"
        return text

    if evt_type in {"watch_overflow_released", "watch_overflow_tripped"}:
        return f"[IMPORTANT: {event.get('message', '')}]"

    exit_code = event.get("exit_code", "?")
    output = event.get("output", "")
    return (
        f"[IMPORTANT: Background process {sid} completed "
        f"(exit code {exit_code}).\n"
        f"Command: {command}\n"
        f"Output:\n{output}]"
    )


process_registry = ProcessRegistry()
