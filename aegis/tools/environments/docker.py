"""Docker execution environment."""

from __future__ import annotations

import subprocess
from typing import Any


class DockerEnvironment:
    """Run one command in a throwaway Docker container."""

    def __init__(
        self,
        *,
        image: str,
        cwd: str,
        timeout: int,
        task_id: str = "default",
    ) -> None:
        self.image = image
        self.cwd = cwd
        self.timeout = timeout
        self.task_id = task_id or "default"

    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        effective_cwd = cwd or self.cwd
        effective_timeout = timeout or self.timeout
        argv = [
            "docker", "run", "--rm",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "-e", f"AEGIS_TASK_ID={self.task_id}",
            "-v", f"{effective_cwd}:/work",
            "-w", "/work",
            self.image,
            "bash",
            "-c",
            command,
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "output": f"docker command timed out after {effective_timeout}s",
                "returncode": 124,
            }
        return {"output": _merge(proc.stdout, proc.stderr), "returncode": proc.returncode}

    def cleanup(self) -> None:
        return None


def _merge(stdout: str, stderr: str) -> str:
    out = stdout or ""
    if stderr:
        out += ("\n[stderr]\n" + stderr) if out else stderr
    return out
