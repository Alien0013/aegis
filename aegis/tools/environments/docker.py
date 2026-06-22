"""Docker execution environment."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import BaseEnvironment, ProcessHandle, _pipe_stdin


class DockerEnvironment(BaseEnvironment):
    """Run commands in one long-lived Docker container for this task."""

    def __init__(
        self,
        *,
        image: str,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        persist_across_processes: bool = True,
        extra_args: list[str] | None = None,
    ) -> None:
        self.image = image
        self.host_cwd = str(Path(cwd).expanduser().resolve()) if cwd else os.getcwd()
        self.persist_across_processes = bool(persist_across_processes)
        self.extra_args = list(extra_args or [])
        self._container_name = _container_name(task_id or "default", self.host_cwd)
        self._container_id = ""
        super().__init__(cwd="/work", timeout=timeout, task_id=task_id)
        self._ensure_container()
        self.init_session()

    def get_temp_dir(self) -> str:
        return "/tmp"

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        return super().execute(
            command,
            cwd=self._container_cwd(cwd),
            timeout=timeout,
            stdin_data=stdin_data,
        )

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> ProcessHandle:
        self._ensure_container()
        shell_arg = "-lc" if login else "-c"
        argv = [
            "docker", "exec", "-i",
            "-w", "/work",
            "-e", f"AEGIS_TASK_ID={self.task_id}",
            self._container_name,
            "bash",
            shell_arg,
            cmd_string,
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    def cleanup(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self._container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self._container_id = ""

    def _ensure_container(self) -> None:
        if self._container_id and self._is_running():
            return
        inspected = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self._container_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if inspected.returncode == 0:
            running = inspected.stdout.strip().lower() == "true"
            if not running:
                started = subprocess.run(
                    ["docker", "start", self._container_name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if started.returncode != 0:
                    self._remove_container()
                else:
                    self._container_id = self._container_name
                    return
            else:
                self._container_id = self._container_name
                return

        argv = [
            "docker", "run", "-d",
            "--name", self._container_name,
            "--label", "aegis-agent=1",
            "--label", f"aegis-task={_safe_label(self.task_id)}",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "-e", f"AEGIS_TASK_ID={self.task_id}",
            "-v", f"{self.host_cwd}:/work",
            "-w", "/work",
            *self.extra_args,
            self.image,
            "bash",
            "-lc",
            "while true; do sleep 3600; done",
        ]
        created = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if created.returncode != 0:
            raise RuntimeError((created.stderr or created.stdout or "docker run failed").strip())
        self._container_id = created.stdout.strip() or self._container_name

    def _is_running(self) -> bool:
        inspected = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self._container_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return inspected.returncode == 0 and inspected.stdout.strip().lower() == "true"

    def _remove_container(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self._container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self._container_id = ""

    def _container_cwd(self, cwd: str) -> str:
        if not cwd:
            return self.cwd
        if cwd.startswith("/work"):
            return cwd
        try:
            rel = Path(cwd).expanduser().resolve().relative_to(Path(self.host_cwd))
        except Exception:
            return self.cwd
        return str(Path("/work") / rel) if str(rel) != "." else "/work"


def _container_name(task_id: str, host_cwd: str) -> str:
    digest = hashlib.sha1(f"{task_id}:{host_cwd}".encode("utf-8")).hexdigest()[:12]
    return f"aegis-{_safe_label(task_id)[:36]}-{digest}"


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "default"))
    return cleaned.strip("-") or "default"
