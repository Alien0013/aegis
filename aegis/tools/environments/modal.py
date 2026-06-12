"""Modal execution environment."""

from __future__ import annotations

from typing import Any


class ModalEnvironment:
    """Run one command in a Modal sandbox."""

    def __init__(
        self,
        *,
        app: Any,
        image: Any,
        timeout: int,
        task_id: str = "default",
    ) -> None:
        self.app = app
        self.image = image
        self.timeout = timeout
        self.task_id = task_id or "default"

    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        effective_timeout = timeout or self.timeout
        sandbox = self._modal().Sandbox.create(
            app=self.app,
            image=self.image,
            timeout=effective_timeout,
        )
        try:
            prefix = f"export AEGIS_TASK_ID={_sh_quote(self.task_id)}; "
            if cwd:
                prefix += f"cd {_sh_quote(cwd)} 2>/dev/null; "
            proc = sandbox.exec("bash", "-c", prefix + command)
            out = proc.stdout.read()
            err = proc.stderr.read()
            code = proc.wait()
        finally:
            sandbox.terminate()
        return {"output": _merge(out, err), "returncode": code}

    def cleanup(self) -> None:
        return None

    @staticmethod
    def _modal() -> Any:
        import modal

        return modal


def _merge(stdout: str, stderr: str) -> str:
    out = stdout or ""
    if stderr:
        out += ("\n[stderr]\n" + stderr) if out else stderr
    return out


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
