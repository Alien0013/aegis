"""Terminal execution backends for the bash tool.

A small abstraction over *where* a shell command actually runs. ``BashTool``
delegates to :func:`run_command`, which dispatches on a backend name:

    local   subprocess in the host shell (default)
    docker  a persistent per-task container, cwd bind-mounted at ``/work``;
            Linux capabilities dropped for a tighter sandbox
    ssh     ``ssh user@host`` running the command on a remote box

If the docker or ssh backend is requested but its prerequisites are missing
(no ``docker``/``ssh`` binary, or ``TERMINAL_SSH_HOST`` unset), the call
fails closed unless ``tools.allow_local_fallback`` is enabled; with explicit
fallback, AEGIS prepends a one-line note before running locally.

The backend is normally chosen via ``config.tools.terminal_backend`` and the
docker image via ``config.tools.docker_image``. SSH target is read from the
environment (``TERMINAL_SSH_HOST``, ``TERMINAL_SSH_USER``, ``TERMINAL_SSH_PORT``).
"""

from __future__ import annotations

import atexit
import os
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .environments import (
    DockerEnvironment,
    LocalEnvironment,
    ModalEnvironment,
    SSHEnvironment,
    SingularityEnvironment,
)
from .command_utils import rewrite_compound_background, validate_command

__all__ = [
    "cleanup_all_environments",
    "cleanup_task_environment",
    "clear_task_env_overrides",
    "create_environment",
    "effective_backend",
    "get_active_environment",
    "register_task_env_overrides",
    "run_command",
]

DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_ENV_LIFETIME_SECONDS = 300

_active_environments: dict[tuple[str, str], Any] = {}
_last_activity: dict[tuple[str, str], float] = {}
_env_lock = threading.Lock()
_creation_locks: dict[tuple[str, str], threading.Lock] = {}
_creation_locks_lock = threading.Lock()
_cleanup_thread: threading.Thread | None = None
_cleanup_running = False
_task_env_overrides: dict[str, dict[str, Any]] = {}


def run_command(
    command: str,
    cwd: str,
    timeout: int,
    backend: str = "local",
    config: Any = None,
    task_id: str | None = None,
) -> tuple[str, int]:
    """Run ``command`` via the named backend.

    Returns ``(combined_output, returncode)``. stderr is merged into the
    output (labelled) so callers get a single text blob. On timeout the
    returncode is ``124`` (the conventional ``timeout(1)`` code). Unknown or
    unavailable backends fall back to ``local`` with an explanatory note.
    """
    command, error = validate_command(command)
    if error:
        return error, -1
    command = rewrite_compound_background(command)
    backend = effective_backend(backend, task_id)
    if backend == "docker":
        return _run_docker(command, cwd, timeout, config, task_id)
    if backend == "ssh":
        return _run_ssh(command, cwd, timeout, config, task_id)
    if backend in ("singularity", "apptainer"):
        return _run_singularity(command, cwd, timeout, config, task_id)
    if backend == "modal":
        return _run_modal(command, cwd, timeout, config, task_id)
    if backend == "daytona":
        return _run_daytona(command, cwd, timeout, config, task_id)
    if backend != "local":
        out, code = _run_local(command, cwd, timeout, config, task_id)
        return _note(f"unknown backend {backend!r}; ran locally", out), code
    return _run_local(command, cwd, timeout, config, task_id)


def effective_backend(backend: str | None, task_id: str | None = None) -> str:
    """Return the backend after applying any per-task override."""
    override = _task_overrides(task_id).get("terminal_backend")
    if isinstance(override, str) and override.strip():
        return override.strip().lower()
    return (backend or "local").strip().lower()


def create_environment(
    backend: str | None,
    cwd: str,
    timeout: int,
    config: Any = None,
    task_id: str | None = None,
) -> tuple[Any | None, str, str]:
    """Create the environment object for ``backend`` without executing a command."""
    backend = effective_backend(backend, task_id)
    if backend == "local":
        try:
            env = _get_or_create_local_environment(
                task_id or "default",
                cwd=str(_task_overrides(task_id).get("cwd") or cwd),
                timeout=timeout,
                config=config,
            )
            return env, "", backend
        except Exception as e:  # noqa: BLE001
            failure = _outer_sandbox_failure(str(e))
            if failure:
                return None, failure[0], backend
            return None, f"local command failed to start ({type(e).__name__}: {e})", backend
    if backend == "docker":
        if shutil.which("docker") is None:
            return None, "sandbox unavailable: docker not found on PATH", backend
        overrides = _task_overrides(task_id)
        image = DEFAULT_DOCKER_IMAGE
        if config is not None:
            try:
                image = config.get("tools.docker_image", DEFAULT_DOCKER_IMAGE) or DEFAULT_DOCKER_IMAGE
            except Exception:  # noqa: BLE001
                pass
        try:
            return DockerEnvironment(
                image=str(overrides.get("docker_image") or image),
                cwd=str(overrides.get("cwd") or cwd),
                timeout=timeout,
                task_id=task_id or "default",
            ), "", backend
        except Exception as e:  # noqa: BLE001
            return None, f"sandbox unavailable: docker environment error ({e})", backend
    if backend == "ssh":
        host = os.environ.get("TERMINAL_SSH_HOST", "").strip()
        user = os.environ.get("TERMINAL_SSH_USER", "").strip()
        port = os.environ.get("TERMINAL_SSH_PORT", "").strip()
        if shutil.which("ssh") is None:
            return None, "sandbox unavailable: ssh not found on PATH", backend
        if not host:
            return None, "sandbox unavailable: TERMINAL_SSH_HOST not set", backend
        overrides = _task_overrides(task_id)
        return SSHEnvironment(
            host=host,
            user=user,
            port=port,
            cwd=str(overrides.get("cwd") or cwd),
            timeout=timeout,
            task_id=task_id or "default",
        ), "", backend
    if backend in ("singularity", "apptainer"):
        binp = shutil.which("apptainer") or shutil.which("singularity")
        if binp is None:
            return None, "sandbox unavailable: apptainer/singularity not found", backend
        overrides = _task_overrides(task_id)
        image = "docker://python:3.12-slim"
        if config is not None:
            try:
                image = config.get("tools.singularity_image", image) or image
            except Exception:  # noqa: BLE001
                pass
        return SingularityEnvironment(
            binary=binp,
            image=str(overrides.get("singularity_image") or image),
            cwd=str(overrides.get("cwd") or cwd),
            timeout=timeout,
            task_id=task_id or "default",
        ), "", backend
    if backend == "modal":
        try:
            import modal
        except ImportError:
            return None, "sandbox unavailable: modal SDK not installed", backend
        try:
            app = modal.App.lookup("aegis-sandbox", create_if_missing=True)
            image = modal.Image.debian_slim()
            pkgs = []
            if config is not None:
                try:
                    pkgs = _task_overrides(task_id).get("modal_pip") or config.get("tools.modal_pip", []) or []
                except Exception:  # noqa: BLE001
                    pkgs = []
            if pkgs:
                image = image.pip_install(*pkgs)
            return ModalEnvironment(
                app=app,
                image=image,
                timeout=timeout,
                task_id=task_id or "default",
            ), "", backend
        except Exception as e:  # noqa: BLE001
            return None, f"sandbox unavailable: modal sandbox error ({e})", backend
    if backend == "daytona":
        return None, "sandbox unavailable: daytona backend is not configured in AEGIS", backend
    return None, f"unknown backend {backend!r}", backend


# --------------------------------------------------------------------------- #
# local
# --------------------------------------------------------------------------- #
def _run_local(command: str, cwd: str, timeout: int, config: Any = None,
               task_id: str | None = None) -> tuple[str, int]:
    task_key = task_id or "default"
    overrides = _task_overrides(task_key)
    cwd = str(overrides.get("cwd") or cwd)
    _start_cleanup_thread(config)
    try:
        env = _get_or_create_local_environment(
            task_key,
            cwd=cwd,
            timeout=timeout,
            config=config,
        )
        result = env.execute(command, timeout=timeout)
    except OSError as e:
        return _outer_sandbox_failure(str(e)) or (f"local command failed to start ({e})", 126)
    except Exception as e:  # noqa: BLE001
        return _outer_sandbox_failure(str(e)) or (f"local command failed ({type(e).__name__}: {e})", 126)
    out = str(result.get("output", ""))
    code = int(result.get("returncode", 0) or 0)
    return _outer_sandbox_failure(out) or (out, code)


def _get_or_create_local_environment(
    task_id: str,
    *,
    cwd: str,
    timeout: int,
    config: Any,
) -> LocalEnvironment:
    key = ("local", task_id)
    env_dir = _environment_state_dir(config, backend="local", task_id=task_id)
    stale_env = None
    with _env_lock:
        env = _active_environments.get(key)
        if env is not None:
            if _environment_matches_state_dir(env, env_dir):
                _last_activity[key] = time.time()
                return env
            stale_env = _active_environments.pop(key, None)
            _last_activity.pop(key, None)
    if stale_env is not None:
        _cleanup_environment(stale_env)

    with _creation_locks_lock:
        lock = _creation_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _creation_locks[key] = lock

    with lock:
        stale_env = None
        with _env_lock:
            env = _active_environments.get(key)
            if env is not None:
                if _environment_matches_state_dir(env, env_dir):
                    _last_activity[key] = time.time()
                    return env
                stale_env = _active_environments.pop(key, None)
                _last_activity.pop(key, None)
        if stale_env is not None:
            _cleanup_environment(stale_env)

        env = LocalEnvironment(
            cwd=cwd,
            timeout=timeout,
            task_id=task_id,
            state_dir=env_dir,
        )
        with _env_lock:
            _active_environments[key] = env
            _last_activity[key] = time.time()
        return env


def _environment_matches_state_dir(env: Any, state_dir: Path) -> bool:
    snapshot = getattr(env, "_snapshot_path", "")
    if not snapshot:
        return True
    try:
        return Path(str(snapshot)).parent == state_dir
    except Exception:
        return True


def register_task_env_overrides(task_id: str, overrides: dict[str, Any]) -> None:
    """Register per-task terminal environment overrides.

    Supported keys are intentionally small:
    ``cwd``, ``terminal_backend``, ``docker_image``, ``singularity_image``,
    and ``modal_pip``.
    """
    if not task_id:
        return
    _task_env_overrides[task_id] = dict(overrides or {})
    new_cwd = _task_env_overrides[task_id].get("cwd")
    if isinstance(new_cwd, str) and new_cwd.strip():
        with _env_lock:
            env = _active_environments.get(("local", task_id))
        if env is not None and getattr(env, "cwd", None) is not None:
            env.cwd = new_cwd


def clear_task_env_overrides(task_id: str) -> None:
    if task_id:
        _task_env_overrides.pop(task_id, None)


def get_active_environment(task_id: str, backend: str = "local") -> Any:
    with _env_lock:
        return _active_environments.get((backend, task_id))


def _task_overrides(task_id: str | None) -> dict[str, Any]:
    if not task_id:
        return {}
    return dict(_task_env_overrides.get(task_id) or {})


def _environment_state_dir(config: Any, *, backend: str, task_id: str) -> Path:
    safe_task_id = _path_safe_id(task_id)
    try:
        from .. import config as cfg

        root = cfg.sub("terminal", backend, safe_task_id)
    except Exception:
        root = (
            Path(os.environ.get("AEGIS_HOME", str(Path.home() / ".aegis")))
            / "terminal"
            / backend
            / safe_task_id
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_safe_id(task_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in task_id)
    return safe or "default"


def _terminal_lifetime(config: Any) -> int:
    if config is not None:
        try:
            return int(config.get("tools.terminal_lifetime_seconds", DEFAULT_ENV_LIFETIME_SECONDS))
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_ENV_LIFETIME_SECONDS


def _cleanup_inactive_envs(lifetime_seconds: int = DEFAULT_ENV_LIFETIME_SECONDS) -> int:
    current_time = time.time()
    envs_to_stop: list[tuple[tuple[str, str], Any]] = []
    with _env_lock:
        for key, last_time in list(_last_activity.items()):
            if current_time - last_time > lifetime_seconds:
                env = _active_environments.pop(key, None)
                _last_activity.pop(key, None)
                if env is not None:
                    envs_to_stop.append((key, env))
        with _creation_locks_lock:
            for key, _env in envs_to_stop:
                _creation_locks.pop(key, None)

    for _key, env in envs_to_stop:
        _cleanup_environment(env)
    return len(envs_to_stop)


def _cleanup_thread_worker(config: Any) -> None:
    while _cleanup_running:
        try:
            _cleanup_inactive_envs(_terminal_lifetime(config))
        except Exception:
            pass
        for _ in range(60):
            if not _cleanup_running:
                break
            time.sleep(1)


def _start_cleanup_thread(config: Any = None) -> None:
    global _cleanup_running, _cleanup_thread
    with _env_lock:
        if _cleanup_thread is not None and _cleanup_thread.is_alive():
            return
        _cleanup_running = True
        _cleanup_thread = threading.Thread(
            target=_cleanup_thread_worker,
            args=(config,),
            daemon=True,
        )
        _cleanup_thread.start()


def _stop_cleanup_thread() -> None:
    global _cleanup_running
    _cleanup_running = False
    thread = _cleanup_thread
    if thread is not None:
        try:
            thread.join(timeout=5)
        except (KeyboardInterrupt, SystemExit):
            pass


def cleanup_task_environment(task_id: str, backend: str | None = None) -> int:
    """Clean up cached environments for one task id."""
    envs_to_stop: list[Any] = []
    with _env_lock:
        for key in list(_active_environments):
            key_backend, key_task = key
            if key_task != task_id or (backend is not None and key_backend != backend):
                continue
            env = _active_environments.pop(key, None)
            _last_activity.pop(key, None)
            if env is not None:
                envs_to_stop.append(env)
        with _creation_locks_lock:
            for key in list(_creation_locks):
                key_backend, key_task = key
                if key_task == task_id and (backend is None or key_backend == backend):
                    _creation_locks.pop(key, None)
    for env in envs_to_stop:
        _cleanup_environment(env)
    return len(envs_to_stop)


def cleanup_all_environments() -> int:
    """Clean up all cached terminal environments."""
    envs_to_stop: list[Any]
    with _env_lock:
        envs_to_stop = list(_active_environments.values())
        _active_environments.clear()
        _last_activity.clear()
    with _creation_locks_lock:
        _creation_locks.clear()
    for env in envs_to_stop:
        _cleanup_environment(env)
    return len(envs_to_stop)


def _cleanup_environment(env: Any) -> None:
    try:
        cleanup = getattr(env, "cleanup", None) or getattr(env, "stop", None)
        if callable(cleanup):
            cleanup()
    except Exception:
        pass


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


def _degraded(config: Any, reason: str, command: str, cwd: str, timeout: int,
              task_id: str | None = None) -> tuple[str, int]:
    """Fail closed: only run locally if the user explicitly opted in."""
    if _allow_fallback(config):
        out, code = _run_local(command, cwd, timeout, config, task_id)
        return _note(reason + "; ran locally (allow_local_fallback=true)", out), code
    return (f"⛔ sandbox unavailable: {reason}. Refusing to run on the host. "
            f"Set tools.allow_local_fallback: true to permit a local fallback.", 126)


def _run_docker(command: str, cwd: str, timeout: int, config: Any,
                task_id: str | None = None) -> tuple[str, int]:
    if shutil.which("docker") is None:
        return _degraded(config, "docker not found on PATH", command, cwd, timeout, task_id)

    overrides = _task_overrides(task_id)
    cwd = str(overrides.get("cwd") or cwd)
    image = DEFAULT_DOCKER_IMAGE
    if config is not None:
        try:
            image = config.get("tools.docker_image", DEFAULT_DOCKER_IMAGE) or DEFAULT_DOCKER_IMAGE
        except Exception:  # noqa: BLE001 — config may be a bare dict or None
            image = DEFAULT_DOCKER_IMAGE
    image = str(overrides.get("docker_image") or image)
    extra_args = _docker_extra_args(config, overrides)

    try:
        result = DockerEnvironment(
            image=image,
            cwd=cwd,
            timeout=timeout,
            task_id=task_id or "default",
            extra_args=extra_args,
        ).execute(command, timeout=timeout)
    except OSError as e:
        return _degraded(config, f"docker failed to start ({e})", command, cwd, timeout, task_id)
    except Exception as e:  # noqa: BLE001
        return _degraded(config, f"docker environment error ({e})", command, cwd, timeout, task_id)

    # Distinguish "image missing / daemon down" from the program's own failure.
    out = str(result.get("output", ""))
    code = int(result.get("returncode", 0) or 0)
    if failure := _outer_sandbox_failure(out):
        return failure
    if code == 125 and "Unable to find image" not in out and "/work" not in out:
        return _degraded(config, f"docker run failed: {out.strip() or 'unknown error'}",
                         command, cwd, timeout, task_id)
    return out, code


def _docker_extra_args(config: Any, overrides: dict[str, Any] | None = None) -> list[str]:
    def extend_from(value: Any, out: list[str]) -> None:
        if value is None:
            return
        if isinstance(value, str):
            out.extend(shlex.split(value))
            return
        if isinstance(value, (list, tuple)):
            out.extend(str(item) for item in value if str(item))

    args: list[str] = []
    if config is not None:
        try:
            extend_from(config.get("tools.docker_extra_args", []), args)
        except Exception:  # noqa: BLE001
            pass
    if overrides:
        extend_from(overrides.get("docker_extra_args"), args)
    extend_from(os.environ.get("TERMINAL_DOCKER_EXTRA_ARGS"), args)
    return args


# --------------------------------------------------------------------------- #
# ssh
# --------------------------------------------------------------------------- #
def _run_ssh(command: str, cwd: str, timeout: int, config: Any = None,
             task_id: str | None = None) -> tuple[str, int]:
    overrides = _task_overrides(task_id)
    cwd = str(overrides.get("cwd") or cwd)
    host = os.environ.get("TERMINAL_SSH_HOST", "").strip()
    user = os.environ.get("TERMINAL_SSH_USER", "").strip()
    port = os.environ.get("TERMINAL_SSH_PORT", "").strip()

    if shutil.which("ssh") is None:
        return _degraded(config, "ssh not found on PATH", command, cwd, timeout, task_id)
    if not host:
        return _degraded(config, "TERMINAL_SSH_HOST not set", command, cwd, timeout, task_id)

    try:
        env = SSHEnvironment(
            host=host,
            user=user,
            port=port,
            cwd=cwd,
            timeout=timeout,
            task_id=task_id or "default",
        )
        result = env.execute(command, timeout=timeout)
    except OSError as e:
        return _degraded(config, f"ssh failed to start ({e})", command, cwd, timeout, task_id)

    out = str(result.get("output", ""))
    code = int(result.get("returncode", 0) or 0)
    if failure := _outer_sandbox_failure(out):
        return failure
    # 255 is ssh's own "connection failed" code (auth/network), not the remote program's exit.
    if code == 255:
        return _degraded(config, f"ssh connection to {env.target} failed: {out.strip() or 'unknown error'}",
                         command, cwd, timeout, task_id)
    return out, code


# --------------------------------------------------------------------------- #
# singularity / apptainer (HPC-style container)
# --------------------------------------------------------------------------- #
def _run_singularity(command: str, cwd: str, timeout: int, config: Any,
                     task_id: str | None = None) -> tuple[str, int]:
    binp = shutil.which("apptainer") or shutil.which("singularity")
    if binp is None:
        return _degraded(config, "apptainer/singularity not found", command, cwd, timeout, task_id)
    overrides = _task_overrides(task_id)
    cwd = str(overrides.get("cwd") or cwd)
    image = "docker://python:3.12-slim"
    if config is not None:
        try:
            image = config.get("tools.singularity_image", image) or image
        except Exception:  # noqa: BLE001
            pass
    image = str(overrides.get("singularity_image") or image)
    try:
        result = SingularityEnvironment(
            binary=binp,
            image=image,
            cwd=cwd,
            timeout=timeout,
            task_id=task_id or "default",
        ).execute(command, timeout=timeout)
    except OSError as e:
        return _degraded(config, f"singularity failed to start ({e})", command, cwd, timeout, task_id)
    out = str(result.get("output", ""))
    code = int(result.get("returncode", 0) or 0)
    if failure := _outer_sandbox_failure(out):
        return failure
    if code == 255 and "/work" not in out:
        return _degraded(config, f"singularity run failed: {out.strip() or 'unknown'}", command, cwd, timeout, task_id)
    return out, code


# --------------------------------------------------------------------------- #
# modal (cloud sandbox via the modal SDK)
# --------------------------------------------------------------------------- #
def _run_modal(command: str, cwd: str, timeout: int, config: Any,
               task_id: str | None = None) -> tuple[str, int]:
    overrides = _task_overrides(task_id)
    cwd = str(overrides.get("cwd") or cwd)
    try:
        import modal
    except ImportError:
        return _degraded(config, "modal SDK not installed (pip install modal; modal token set)",
                         command, cwd, timeout, task_id)
    try:
        app = modal.App.lookup("aegis-sandbox", create_if_missing=True)
        image = modal.Image.debian_slim()
        if config is not None:
            try:
                pkgs = overrides.get("modal_pip") or config.get("tools.modal_pip", []) or []
                if pkgs:
                    image = image.pip_install(*pkgs)
            except Exception:  # noqa: BLE001
                pass
        result = ModalEnvironment(
            app=app,
            image=image,
            timeout=timeout,
            task_id=task_id or "default",
        ).execute(command, cwd=cwd, timeout=timeout)
        return str(result.get("output", "")), int(result.get("returncode", 0) or 0)
    except Exception as e:  # noqa: BLE001
        return _degraded(config, f"modal sandbox error ({e})", command, cwd, timeout, task_id)


def _run_daytona(command: str, cwd: str, timeout: int, config: Any,
                 task_id: str | None = None) -> tuple[str, int]:
    return _degraded(
        config,
        "daytona backend is not configured in AEGIS",
        command,
        cwd,
        timeout,
        task_id,
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
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


atexit.register(lambda: (_stop_cleanup_thread(), cleanup_all_environments()))
