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
        return _run_ssh(command, cwd, timeout, config)
    if backend in ("singularity", "apptainer"):
        return _run_singularity(command, cwd, timeout, config)
    if backend == "modal":
        return _run_modal(command, cwd, timeout, config)
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
    except OSError as e:
        return _outer_sandbox_failure(str(e)) or (f"local command failed to start ({e})", 126)
    out = _merge(proc.stdout, proc.stderr)
    return _outer_sandbox_failure(out) or (out, proc.returncode)


# --------------------------------------------------------------------------- #
# docker
# --------------------------------------------------------------------------- #
def _allow_fallback(config: Any) -> bool:
    if config is None:
        return False
    try:
        return bool(config.get("tools.allow_local_fallback", False))
    except Exception:  # noqa: BLE001
        return False


def _degraded(config: Any, reason: str, command: str, cwd: str, timeout: int) -> tuple[str, int]:
    """Fail closed: only run locally if the user explicitly opted in."""
    if _allow_fallback(config):
        out, code = _run_local(command, cwd, timeout)
        return _note(reason + "; ran locally (allow_local_fallback=true)", out), code
    return (f"⛔ sandbox unavailable: {reason}. Refusing to run on the host. "
            f"Set tools.allow_local_fallback: true to permit a local fallback.", 126)


def _run_docker(command: str, cwd: str, timeout: int, config: Any) -> tuple[str, int]:
    if shutil.which("docker") is None:
        return _degraded(config, "docker not found on PATH", command, cwd, timeout)

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
        return _degraded(config, f"docker failed to start ({e})", command, cwd, timeout)

    # Distinguish "image missing / daemon down" from the program's own failure.
    out = _merge(proc.stdout, proc.stderr)
    if failure := _outer_sandbox_failure(out):
        return failure
    if proc.returncode == 125 and "Unable to find image" not in out and "/work" not in out:
        return _degraded(config, f"docker run failed: {out.strip() or 'unknown error'}",
                         command, cwd, timeout)
    return out, proc.returncode


# --------------------------------------------------------------------------- #
# ssh
# --------------------------------------------------------------------------- #
def _run_ssh(command: str, cwd: str, timeout: int, config: Any = None) -> tuple[str, int]:
    host = os.environ.get("TERMINAL_SSH_HOST", "").strip()
    user = os.environ.get("TERMINAL_SSH_USER", "").strip()
    port = os.environ.get("TERMINAL_SSH_PORT", "").strip()

    if shutil.which("ssh") is None:
        return _degraded(config, "ssh not found on PATH", command, cwd, timeout)
    if not host:
        return _degraded(config, "TERMINAL_SSH_HOST not set", command, cwd, timeout)

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
        return _degraded(config, f"ssh failed to start ({e})", command, cwd, timeout)

    out = _merge(proc.stdout, proc.stderr)
    if failure := _outer_sandbox_failure(out):
        return failure
    # 255 is ssh's own "connection failed" code (auth/network), not the remote program's exit.
    if proc.returncode == 255:
        return _degraded(config, f"ssh connection to {target} failed: {out.strip() or 'unknown error'}",
                         command, cwd, timeout)
    return out, proc.returncode


# --------------------------------------------------------------------------- #
# singularity / apptainer (HPC-style container)
# --------------------------------------------------------------------------- #
def _run_singularity(command: str, cwd: str, timeout: int, config: Any) -> tuple[str, int]:
    binp = shutil.which("apptainer") or shutil.which("singularity")
    if binp is None:
        return _degraded(config, "apptainer/singularity not found", command, cwd, timeout)
    image = "docker://python:3.12-slim"
    if config is not None:
        try:
            image = config.get("tools.singularity_image", image) or image
        except Exception:  # noqa: BLE001
            pass
    argv = [binp, "exec", "--containall", "--writable-tmpfs",
            "--bind", f"{cwd}:/work", "--pwd", "/work", image, "bash", "-c", command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"singularity command timed out after {timeout}s", 124
    except OSError as e:
        return _degraded(config, f"singularity failed to start ({e})", command, cwd, timeout)
    out = _merge(proc.stdout, proc.stderr)
    if failure := _outer_sandbox_failure(out):
        return failure
    if proc.returncode == 255 and "/work" not in out:
        return _degraded(config, f"singularity run failed: {out.strip() or 'unknown'}", command, cwd, timeout)
    return out, proc.returncode


# --------------------------------------------------------------------------- #
# modal (cloud sandbox via the modal SDK)
# --------------------------------------------------------------------------- #
def _run_modal(command: str, cwd: str, timeout: int, config: Any) -> tuple[str, int]:
    try:
        import modal
    except ImportError:
        return _degraded(config, "modal SDK not installed (pip install modal; modal token set)",
                         command, cwd, timeout)
    try:
        app = modal.App.lookup("aegis-sandbox", create_if_missing=True)
        image = modal.Image.debian_slim()
        if config is not None:
            try:
                pkgs = config.get("tools.modal_pip", []) or []
                if pkgs:
                    image = image.pip_install(*pkgs)
            except Exception:  # noqa: BLE001
                pass
        sb = modal.Sandbox.create(app=app, image=image, timeout=timeout)
        try:
            p = sb.exec("bash", "-c", command)
            out = p.stdout.read()
            err = p.stderr.read()
            code = p.wait()
        finally:
            sb.terminate()
        return _merge(out, err), code
    except Exception as e:  # noqa: BLE001
        return _degraded(config, f"modal sandbox error ({e})", command, cwd, timeout)


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


def _outer_sandbox_failure(output: str) -> tuple[str, int] | None:
    """Detect host-level sandbox bootstrap failures that happen before the command runs."""
    summary = " ".join((output or "").strip().split())
    lowered = summary.lower()
    if "bwrap:" not in lowered:
        return None
    if "rtm_newaddr" not in lowered or "operation not permitted" not in lowered:
        return None
    return (
        "sandbox unavailable: the host bubblewrap wrapper failed before the command ran "
        f"({summary}). This usually means the environment blocks loopback/network namespace "
        "setup. Subagents can still spawn, but bash/process tools will fail until AEGIS is "
        "run in an environment that permits that sandbox setup, or a remote/container backend "
        "is configured from a host that can start subprocesses.",
        126,
    )


def _sh_quote(s: str) -> str:
    """POSIX single-quote a string for safe embedding in a remote shell line."""
    return "'" + s.replace("'", "'\\''") + "'"
