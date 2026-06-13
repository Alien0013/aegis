"""PTY bridge used by the FastAPI dashboard WebSocket."""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys
from typing import Sequence


class PtyUnavailableError(RuntimeError):
    """Raised when the host cannot provide a POSIX pseudo-terminal."""


class PtyBridge:
    """Small byte-oriented wrapper around ptyprocess."""

    def __init__(self, proc) -> None:
        self._proc = proc
        self._fd = proc.fd
        self._closed = False

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 100,
        rows: int = 30,
    ) -> "PtyBridge":
        if sys.platform.startswith("win"):
            raise PtyUnavailableError("dashboard terminal PTY is unavailable on native Windows")
        try:
            from ptyprocess import PtyProcess
        except Exception as exc:  # noqa: BLE001
            raise PtyUnavailableError("ptyprocess is not installed") from exc
        spawn_env = os.environ.copy() if env is None else dict(env)
        spawn_env.setdefault("TERM", "xterm-256color")
        proc = PtyProcess.spawn(
            list(argv),
            cwd=cwd,
            env=spawn_env,
            dimensions=(max(1, int(rows)), max(1, int(cols))),
        )
        return cls(proc)

    def read(self, timeout: float = 0.2) -> bytes | None:
        if self._closed:
            return None
        try:
            readable, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not readable:
            return b""
        try:
            data = os.read(self._fd, 65536)
        except OSError:
            return None
        return data or None

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        view = memoryview(data)
        while view:
            try:
                written = os.write(self._fd, view)
            except OSError:
                return
            if written <= 0:
                return
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        cols = min(max(1, int(cols)), 2000)
        rows = min(max(1, int(rows)), 1000)
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            pgid = os.getpgid(self._proc.pid)
        except Exception:  # noqa: BLE001
            pgid = None
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            try:
                if pgid is not None:
                    os.killpg(pgid, sig)
                else:
                    os.kill(self._proc.pid, sig)
            except Exception:  # noqa: BLE001
                pass
            try:
                if not self._proc.isalive():
                    break
            except Exception:  # noqa: BLE001
                break
        try:
            self._proc.close(force=True)
        except Exception:  # noqa: BLE001
            pass


def dashboard_tui_argv(resume: str | None = None) -> list[str]:
    """Return the command used for the embedded dashboard terminal."""
    exe = os.environ.get("AEGIS_BIN") or shutil.which("aegis")
    argv = [exe, "tui"] if exe else [sys.executable, "-m", "aegis.cli.main", "tui"]
    if resume:
        argv.extend(["--resume", resume])
    return argv
