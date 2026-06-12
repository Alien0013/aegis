"""SSH execution environment."""

from __future__ import annotations

import subprocess
from typing import Any


class SSHEnvironment:
    """Run commands on a configured SSH target."""

    def __init__(
        self,
        *,
        host: str,
        user: str = "",
        port: str = "",
        cwd: str,
        timeout: int,
        task_id: str = "default",
    ) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.cwd = cwd
        self.timeout = timeout
        self.task_id = task_id or "default"

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        effective_cwd = cwd or self.cwd
        effective_timeout = timeout or self.timeout
        remote = (
            f"export AEGIS_TASK_ID={_sh_quote(self.task_id)}; "
            f"cd {_sh_quote(effective_cwd)} 2>/dev/null; bash -c {_sh_quote(command)}"
        )
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if self.port:
            argv += ["-p", self.port]
        argv += [self.target, remote]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "output": f"ssh command timed out after {effective_timeout}s",
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


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
