"""Local execution environment for AEGIS terminal commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .base import BaseEnvironment, _pipe_stdin


_SANE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_SECRET_ENV_NAMES = {
    "AEGIS_DASHBOARD_SESSION_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIRECRAWL_API_KEY",
    "FIREWORKS_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "HELICONE_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENROUTER_API_KEY",
    "PARALLEL_API_KEY",
    "PERPLEXITY_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
}


def _append_missing_sane_path_entries(existing_path: str) -> str:
    sane_entries = [entry for entry in _SANE_PATH.split(":") if entry]
    if not existing_path:
        return ":".join(sane_entries)
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in existing_path.split(":"):
        if not entry or entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    for entry in sane_entries:
        if entry not in seen:
            ordered.append(entry)
    return ":".join(ordered)


def _make_run_env(extra_env: dict[str, str], task_id: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("_AEGIS_FORCE_"):
            env[key[len("_AEGIS_FORCE_"):]] = value
        elif key not in _SECRET_ENV_NAMES:
            env[key] = value
    for key, value in extra_env.items():
        if key.startswith("_AEGIS_FORCE_"):
            env[key[len("_AEGIS_FORCE_"):]] = value
        elif key not in _SECRET_ENV_NAMES:
            env[key] = value
    env["PATH"] = _append_missing_sane_path_entries(env.get("PATH", ""))
    env["AEGIS_TASK_ID"] = task_id
    return env


def _find_bash() -> str:
    return (
        shutil.which("bash")
        or ("/usr/bin/bash" if os.path.isfile("/usr/bin/bash") else "")
        or ("/bin/bash" if os.path.isfile("/bin/bash") else "")
        or os.environ.get("SHELL")
        or "/bin/sh"
    )


def _resolve_safe_cwd(cwd: str) -> str:
    if cwd and os.path.isdir(cwd):
        return cwd
    parent = os.path.dirname(cwd) if cwd else ""
    while parent:
        if os.path.isdir(parent):
            return parent
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent
    return tempfile.gettempdir()


def _resolve_shell_init_files() -> list[str]:
    candidates = ["~/.profile", "~/.bash_profile", "~/.bashrc"]
    resolved: list[str] = []
    for raw in candidates:
        try:
            path = os.path.expandvars(os.path.expanduser(raw))
        except Exception:
            continue
        if path and os.path.isfile(path):
            resolved.append(path)
    return resolved


def _prepend_shell_init(command: str, files: list[str]) -> str:
    if not files:
        return command
    prelude = ["set +e"]
    for path in files:
        safe = path.replace("'", "'\\''")
        prelude.append(f"[ -r '{safe}' ] && . '{safe}' 2>/dev/null || true")
    return "\n".join(prelude) + "\n" + command


class LocalEnvironment(BaseEnvironment):
    """Run commands on the host with Hermes-style per-task shell state."""

    def __init__(
        self,
        *,
        cwd: str = "",
        timeout: int = 120,
        task_id: str = "default",
        env: dict[str, str] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        super().__init__(
            cwd=cwd or os.getcwd(),
            timeout=timeout,
            task_id=task_id,
            env=env,
            state_dir=state_dir,
        )
        self.init_session()

    def get_temp_dir(self) -> str:
        for key in ("TMPDIR", "TMP", "TEMP"):
            candidate = self.env.get(key) or os.environ.get(key)
            if candidate and candidate.startswith("/"):
                return candidate.rstrip("/") or "/"
        if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK):
            return "/tmp"
        candidate = tempfile.gettempdir()
        return candidate.rstrip("/") if candidate.startswith("/") else "/tmp"

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        if login:
            cmd_string = _prepend_shell_init(cmd_string, _resolve_shell_init_files())
        args = [_find_bash(), "-l", "-c", cmd_string] if login else [_find_bash(), "-c", cmd_string]
        self.cwd = _resolve_safe_cwd(self.cwd)
        proc = subprocess.Popen(
            args,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_make_run_env(self.env, self.task_id),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            preexec_fn=os.setsid if os.name != "nt" else None,
            cwd=self.cwd,
        )
        if os.name != "nt":
            try:
                proc._aegis_pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pass
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    def _kill_process(self, proc) -> None:
        if os.name == "nt":
            try:
                proc.terminate()
            except Exception:
                pass
            return

        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = getattr(proc, "_aegis_pgid", None)
        except Exception:
            pgid = None
        if pgid is None:
            try:
                proc.kill()
            except Exception:
                pass
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                proc.poll()
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except Exception:
                break
            time.sleep(0.05)

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _update_cwd(self, result: dict[str, int | str]) -> None:
        try:
            cwd_path = Path(self._cwd_file).read_text(encoding="utf-8").strip()
            if cwd_path and os.path.isdir(cwd_path):
                self.cwd = cwd_path
        except (OSError, FileNotFoundError):
            pass
        self._extract_cwd_from_output(result)
        if not os.path.isdir(self.cwd):
            self.cwd = _resolve_safe_cwd(self.cwd)

    def cleanup(self) -> None:
        for path in (self._snapshot_path, self._cwd_file):
            try:
                os.unlink(path)
            except OSError:
                pass
