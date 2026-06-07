"""Terminal execution backends for the bash tool.

A small abstraction over *where* a shell command actually runs. ``BashTool``
delegates to :func:`run_command`, which dispatches on a backend name:

    local   subprocess in the host shell (default)
    docker  ``docker run --rm`` against a throwaway container, cwd bind-mounted
            at ``/work``; Linux capabilities dropped for a tighter sandbox
    ssh     ``ssh user@host`` running the command on a remote box

If the docker or ssh backend is requested but its prerequisites are missing
(no ``docker``/``ssh`` binary, or ``TERMINAL_SSH_HOST`` unset), the call
transparently degrades to ``local`` and prepends a one-line note explaining
the fallback, so the agent always gets *some* output rather than an opaque
error.

The backend is normally chosen via ``config.tools.terminal_backend`` and the
docker image via ``config.tools.docker_image``. SSH target is read from the
environment (``TERMINAL_SSH_HOST``, ``TERMINAL_SSH_USER``, ``TERMINAL_SSH_PORT``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

__all__ = ["run_command"]

DEFAULT_DOCKER_IMAGE = "python:3.12-slim"


def run_command(
    command: str,
    cwd: str,
    timeout: int,
    backend: str = "local",
    config: Any = None,
) -> tuple[str, int]:
    """Run ``command`` via the named backend.

    Returns ``(combined_output, returncode)``. stderr is merged into the
    output (labelled) so callers get a single text blob. On timeout the
    returncode is ``124`` (the conventional ``timeout(1)`` code). Unknown or
    unavailable backends fall back to ``local`` with an explanatory note.
    """
    backend = (backend or "local").strip().lower()
    if backend == "docker":
        return _run_docker(command, cwd, timeout, config)
    if backend == "ssh":
        return _run_ssh(command, cwd, timeout)
    if backend != "local":
        out, code = _run_local(command, cwd, timeout)
        return _note(f"unknown backend {backend!r}; ran locally", out), code
    return _run_local(command, cwd, timeout)


# --------------------------------------------------------------------------- #
# local
# --------------------------------------------------------------------------- #
def _run_local(command: str, cwd: str, timeout: int) -> tuple[str, int]:
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"command timed out after {timeout}s", 124
    return _merge(proc.stdout, proc.stderr), proc.returncode


# --------------------------------------------------------------------------- #
# docker
# --------------------------------------------------------------------------- #
def _run_docker(command: str, cwd: str, timeout: int, config: Any) -> tuple[str, int]:
    if shutil.which("docker") is None:
        out, code = _run_local(command, cwd, timeout)
        return _note("docker not found on PATH; ran locally", out), code

    image = DEFAULT_DOCKER_IMAGE
    if config is not None:
        try:
            image = config.get("tools.docker_image", DEFAULT_DOCKER_IMAGE) or DEFAULT_DOCKER_IMAGE
        except Exception:  # noqa: BLE001 — config may be a bare dict or None
            image = DEFAULT_DOCKER_IMAGE

    argv = [
        "docker", "run", "--rm",
        "--network", "none",                 # no outbound network by default
        "--cap-drop", "ALL",                 # drop all Linux capabilities
        "--security-opt", "no-new-privileges",
        "--pids-limit", "256",
        "-v", f"{cwd}:/work",
        "-w", "/work",
        image, "bash", "-c", command,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"docker command timed out after {timeout}s", 124
    except OSError as e:
        out, code = _run_local(command, cwd, timeout)
        return _note(f"docker failed to start ({e}); ran locally", out), code

    # Distinguish "image missing / daemon down" from the program's own failure.
    out = _merge(proc.stdout, proc.stderr)
    if proc.returncode == 125 and "Unable to find image" not in out and "/work" not in out:
        # 125 = docker run itself failed (bad image, daemon, perms). Fall back.
        local_out, code = _run_local(command, cwd, timeout)
        return _note(f"docker run failed: {out.strip() or 'unknown error'}; ran locally", local_out), code
    return out, proc.returncode


# --------------------------------------------------------------------------- #
# ssh
# --------------------------------------------------------------------------- #
def _run_ssh(command: str, cwd: str, timeout: int) -> tuple[str, int]:
    host = os.environ.get("TERMINAL_SSH_HOST", "").strip()
    user = os.environ.get("TERMINAL_SSH_USER", "").strip()
    port = os.environ.get("TERMINAL_SSH_PORT", "").strip()

    if shutil.which("ssh") is None:
        out, code = _run_local(command, cwd, timeout)
        return _note("ssh not found on PATH; ran locally", out), code
    if not host:
        out, code = _run_local(command, cwd, timeout)
        return _note("TERMINAL_SSH_HOST not set; ran locally", out), code

    target = f"{user}@{host}" if user else host
    # `cd` into cwd on the remote when present, then run the command in bash.
    remote = f"cd {_sh_quote(cwd)} 2>/dev/null; bash -c {_sh_quote(command)}"
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if port:
        argv += ["-p", port]
    argv += [target, remote]

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"ssh command timed out after {timeout}s", 124
    except OSError as e:
        out, code = _run_local(command, cwd, timeout)
        return _note(f"ssh failed to start ({e}); ran locally", out), code

    out = _merge(proc.stdout, proc.stderr)
    # 255 is ssh's own "connection failed" code (auth/network), not the remote
    # program's exit. Degrade to local so the agent isn't left empty-handed.
    if proc.returncode == 255:
        local_out, code = _run_local(command, cwd, timeout)
        return _note(f"ssh connection to {target} failed: {out.strip() or 'unknown error'}; ran locally",
                     local_out), code
    return out, proc.returncode


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _merge(stdout: str, stderr: str) -> str:
    out = stdout or ""
    if stderr:
        out += ("\n[stderr]\n" + stderr) if out else stderr
    return out


def _note(message: str, output: str) -> str:
    prefix = f"[backend: {message}]"
    return f"{prefix}\n{output}" if output else prefix


def _sh_quote(s: str) -> str:
    """POSIX single-quote a string for safe embedding in a remote shell line."""
    return "'" + s.replace("'", "'\\''") + "'"
