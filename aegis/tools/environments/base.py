"""Base classes for AEGIS execution environments.

AEGIS follows Hermes' terminal model here: each command gets a fresh shell
process, while per-task state lives in a small shell snapshot and cwd marker.
"""

from __future__ import annotations

import codecs
import os
import select
import shlex
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Protocol


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...

    @property
    def stdout(self) -> IO[str] | None: ...

    @property
    def returncode(self) -> int | None: ...


def _pipe_stdin(proc: subprocess.Popen, data: str) -> None:
    """Write stdin asynchronously so large input cannot deadlock the child."""

    def _write() -> None:
        try:
            raw = data.encode("utf-8") if isinstance(data, str) else data
            target = getattr(proc.stdin, "buffer", proc.stdin)
            target.write(raw)
            target.close()
        except (BrokenPipeError, OSError, AttributeError):
            pass

    threading.Thread(target=_write, daemon=True).start()


def _cwd_marker(session_id: str) -> str:
    return f"__AEGIS_CWD_{session_id}__"


class BaseEnvironment(ABC):
    """Common foreground execution flow for task-aware terminal backends."""

    _snapshot_timeout = 30

    def __init__(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        env: dict[str, str] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser()) if cwd else os.getcwd()
        self.timeout = timeout
        self.task_id = task_id or "default"
        self.env = dict(env or {})

        session_id = uuid.uuid4().hex[:12]
        self._session_id = session_id
        root = state_dir or Path(self.get_temp_dir())
        root.mkdir(parents=True, exist_ok=True)
        self._snapshot_path = str(root / f"aegis-snap-{session_id}.sh")
        self._cwd_file = str(root / f"aegis-cwd-{session_id}.txt")
        self._cwd_marker = _cwd_marker(session_id)
        self._snapshot_ready = False

    def get_temp_dir(self) -> str:
        return "/tmp"

    @abstractmethod
    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> ProcessHandle:
        """Spawn a shell process for ``cmd_string``."""

    @abstractmethod
    def cleanup(self) -> None:
        """Release backend resources."""

    def init_session(self) -> None:
        """Capture login-shell state for later foreground commands."""
        quoted_snap = shlex.quote(self._snapshot_path)
        quoted_cwd_file = shlex.quote(self._cwd_file)
        quoted_cwd = shlex.quote(self.cwd)
        bootstrap = "\n".join([
            f"export -p > {quoted_snap}",
            f"declare -f | grep -vE '^_[^_]' >> {quoted_snap} 2>/dev/null || true",
            f"alias -p >> {quoted_snap} 2>/dev/null || true",
            f"echo 'shopt -s expand_aliases' >> {quoted_snap}",
            f"echo 'set +e' >> {quoted_snap}",
            f"echo 'set +u' >> {quoted_snap}",
            f"builtin cd -- {quoted_cwd} 2>/dev/null || true",
            f"pwd -P > {quoted_cwd_file} 2>/dev/null || true",
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"",
        ])
        try:
            proc = self._run_bash(
                bootstrap,
                login=True,
                timeout=self._snapshot_timeout,
            )
            result = self._wait_for_process(proc, timeout=self._snapshot_timeout)
            self._snapshot_ready = True
            self._update_cwd(result)
        except Exception:
            self._snapshot_ready = False

    @staticmethod
    def _quote_cwd_for_cd(cwd: str) -> str:
        if cwd == "~":
            return cwd
        if cwd == "~/":
            return "$HOME"
        if cwd.startswith("~/"):
            return f"$HOME/{shlex.quote(cwd[2:])}"
        return shlex.quote(cwd)

    @staticmethod
    def _escape_eval(command: str) -> str:
        return command.replace("'", "'\\''")

    def _wrap_command(self, command: str, cwd: str) -> str:
        quoted_snap = shlex.quote(self._snapshot_path)
        quoted_cwd_file = shlex.quote(self._cwd_file)
        parts: list[str] = []
        if self._snapshot_ready:
            parts.append(f"source {quoted_snap} >/dev/null 2>&1 || true")
        parts.append(f"builtin cd -- {self._quote_cwd_for_cd(cwd)} || exit 126")
        parts.append(f"export AEGIS_TASK_ID={shlex.quote(self.task_id)}")
        parts.append(f"eval '{self._escape_eval(command)}'")
        parts.append("__aegis_ec=$?")
        if self._snapshot_ready:
            parts.append(f"export -p > {quoted_snap} 2>/dev/null || true")
        parts.append(f"pwd -P > {quoted_cwd_file} 2>/dev/null || true")
        parts.append(f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"")
        parts.append("exit $__aegis_ec")
        return "\n".join(parts)

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, int | str]:
        effective_timeout = int(timeout or self.timeout)
        effective_cwd = cwd or self.cwd
        wrapped = self._wrap_command(command, effective_cwd)
        proc = self._run_bash(
            wrapped,
            login=not self._snapshot_ready,
            timeout=effective_timeout,
            stdin_data=stdin_data,
        )
        result = self._wait_for_process(proc, timeout=effective_timeout)
        self._update_cwd(result)
        return result

    def _wait_for_process(self, proc: ProcessHandle, timeout: int = 120) -> dict[str, int | str]:
        output_chunks: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def _drain_iterable(stream) -> None:
            try:
                for piece in stream:
                    if isinstance(piece, bytes):
                        output_chunks.append(decoder.decode(piece))
                    elif piece is not None:
                        output_chunks.append(str(piece))
            except Exception:
                pass
            finally:
                tail = decoder.decode(b"", final=True)
                if tail:
                    output_chunks.append(tail)

        def _drain() -> None:
            stream = proc.stdout
            if stream is None:
                return
            try:
                fd = stream.fileno()
            except Exception:
                _drain_iterable(stream)
                return
            if os.name == "nt":
                try:
                    while True:
                        chunk = os.read(fd, 4096)
                        if not chunk:
                            break
                        output_chunks.append(decoder.decode(chunk))
                except OSError:
                    pass
                finally:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        output_chunks.append(tail)
                return

            idle_after_exit = 0
            try:
                while True:
                    try:
                        ready, _, _ = select.select([fd], [], [], 0.1)
                    except (OSError, ValueError):
                        break
                    if ready:
                        try:
                            chunk = os.read(fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output_chunks.append(decoder.decode(chunk))
                        idle_after_exit = 0
                    elif proc.poll() is not None:
                        idle_after_exit += 1
                        if idle_after_exit >= 3:
                            break
            finally:
                tail = decoder.decode(b"", final=True)
                if tail:
                    output_chunks.append(tail)

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()
        deadline = time.monotonic() + timeout
        poll_sleep = 0.005
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    self._kill_process(proc)
                    drain_thread.join(timeout=2)
                    partial = "".join(output_chunks)
                    message = f"\n[Command timed out after {timeout}s]"
                    return {
                        "output": partial + message if partial else message.lstrip(),
                        "returncode": 124,
                    }
                time.sleep(poll_sleep)
                if poll_sleep < 0.2:
                    poll_sleep = min(poll_sleep * 1.5, 0.2)
        except (KeyboardInterrupt, SystemExit):
            self._kill_process(proc)
            drain_thread.join(timeout=2)
            raise

        drain_thread.join(timeout=2)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        return {"output": "".join(output_chunks), "returncode": proc.returncode or 0}

    def _kill_process(self, proc: ProcessHandle) -> None:
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _update_cwd(self, result: dict[str, int | str]) -> None:
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict[str, int | str]) -> None:
        output = str(result.get("output", ""))
        marker = self._cwd_marker
        last = output.rfind(marker)
        if last == -1:
            return
        first = output.rfind(marker, max(0, last - 4096), last)
        if first == -1 or first == last:
            return
        cwd_path = output[first + len(marker):last].strip()
        if cwd_path:
            self.cwd = cwd_path
        line_start = output.rfind("\n", 0, first)
        if line_start == -1:
            line_start = first
        line_end = output.find("\n", last + len(marker))
        line_end = line_end + 1 if line_end != -1 else len(output)
        result["output"] = output[:line_start] + output[line_end:]

    def stop(self) -> None:
        self.cleanup()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass
