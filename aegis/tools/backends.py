"""Terminal execution backends for the bash tool.

A small abstraction over *where* a shell command actually runs. ``BashTool``
delegates to :func:`run_command`, which dispatches on a backend name:

    local   subprocess in the host shell (default)
    docker  a persistent per-task container, cwd bind-mounted at ``/work``;
            Linux capabilities dropped for a tighter sandbox
    ssh     ``ssh user@host`` running the command on a remote box
    modal   a Modal cloud sandbox via the Modal SDK
    daytona a Daytona cloud sandbox when the SDK and API key are configured

If a non-local backend is requested but its prerequisites are missing, the
call fails closed unless ``tools.allow_local_fallback`` is enabled; with
explicit fallback, AEGIS prepends a one-line note before running locally.

The backend is normally chosen via ``config.tools.terminal_backend`` and the
docker image via ``config.tools.docker_image``. SSH target is read from the
environment (``TERMINAL_SSH_HOST``, ``TERMINAL_SSH_USER``, ``TERMINAL_SSH_PORT``).
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import inspect
import importlib.util
import io
import json
import logging
import math
import os
import signal
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .environments import (
    DockerEnvironment,
    LocalEnvironment,
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
    "remote_backend_diagnostics",
    "remote_backend_live_proof",
    "resolve_backend_context",
    "run_command",
]

DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_DAYTONA_IMAGE = DEFAULT_DOCKER_IMAGE
DEFAULT_ENV_LIFETIME_SECONDS = 300

_active_environments: dict[tuple[str, str], Any] = {}
_last_activity: dict[tuple[str, str], float] = {}
_env_lock = threading.Lock()
_creation_locks: dict[tuple[str, str], threading.Lock] = {}
_creation_locks_lock = threading.Lock()
_interrupt_wrap_lock = threading.Lock()
_cleanup_thread: threading.Thread | None = None
_cleanup_running = False
_task_env_overrides: dict[str, dict[str, Any]] = {}
_INTERRUPTED_OUTPUT = "[interrupted by user]"
logger = logging.getLogger(__name__)


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


def resolve_backend_context(
    backend: str | None,
    cwd: str,
    task_id: str | None = None,
) -> dict[str, str]:
    """Return the effective backend and cwd after task-level overrides."""
    configured = (backend or "local").strip().lower()
    overrides = _task_overrides(task_id)
    return {
        "configured_backend": configured,
        "backend": effective_backend(configured, task_id),
        "cwd": str(overrides.get("cwd") or cwd or ""),
    }


def create_environment(
    backend: str | None,
    cwd: str,
    timeout: int,
    config: Any = None,
    task_id: str | None = None,
) -> tuple[Any | None, str, str]:
    """Create the environment object for ``backend`` without executing a command."""
    backend = effective_backend(backend, task_id)
    task_key = task_id or "default"
    if backend == "local":
        try:
            env = _get_or_create_local_environment(
                task_key,
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
            image = str(overrides.get("docker_image") or image)
            return _get_or_create_backend_environment(
                backend,
                task_key,
                cwd=str(overrides.get("cwd") or cwd),
                timeout=timeout,
                factory=lambda: DockerEnvironment(
                    image=image,
                    cwd=str(overrides.get("cwd") or cwd),
                    timeout=timeout,
                    task_id=task_key,
                    extra_args=_docker_extra_args(config, overrides),
                ),
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
        return _get_or_create_backend_environment(
            backend,
            task_key,
            cwd=str(overrides.get("cwd") or cwd),
            timeout=timeout,
            factory=lambda: SSHEnvironment(
                host=host,
                user=user,
                port=port,
                cwd=str(overrides.get("cwd") or cwd),
                timeout=timeout,
                task_id=task_key,
            ),
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
        image = str(overrides.get("singularity_image") or image)
        return _get_or_create_backend_environment(
            backend,
            task_key,
            cwd=str(overrides.get("cwd") or cwd),
            timeout=timeout,
            factory=lambda: SingularityEnvironment(
                binary=binp,
                image=image,
                cwd=str(overrides.get("cwd") or cwd),
                timeout=timeout,
                task_id=task_key,
            ),
        ), "", backend
    if backend == "modal":
        if error := _modal_prerequisite_error():
            return None, error, backend
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
            return _get_or_create_backend_environment(
                backend,
                task_key,
                cwd=str(_task_overrides(task_id).get("cwd") or cwd),
                timeout=timeout,
                factory=lambda: _ModalSandboxEnvironment(
                    app=app,
                    image=image,
                    cwd=str(_task_overrides(task_id).get("cwd") or cwd),
                    timeout=timeout,
                    task_id=task_key,
                ),
            ), "", backend
        except Exception as e:  # noqa: BLE001
            detail = _sanitize_remote_proof_text(str(e))
            return None, f"sandbox unavailable: modal sandbox error ({detail})", backend
    if backend == "daytona":
        if error := _daytona_prerequisite_error():
            return None, error, backend
        overrides = _task_overrides(task_id)
        image = str(
            overrides.get("daytona_image")
            or _config_value(config, "tools.daytona_image", None)
            or os.environ.get("TERMINAL_DAYTONA_IMAGE")
            or DEFAULT_DAYTONA_IMAGE
        )
        persistent = _boolish(
            overrides.get(
                "daytona_persistent",
                _config_value(config, "tools.daytona_persistent", None),
            ),
            default=_boolish(os.environ.get("TERMINAL_CONTAINER_PERSISTENT"), default=True),
        )
        cpu = int(_numberish(_config_value(config, "tools.daytona_cpu", None), default=1))
        memory = int(_numberish(
            _config_value(config, "tools.daytona_memory", None),
            default=_numberish(os.environ.get("TERMINAL_CONTAINER_MEMORY"), default=5120),
        ))
        disk = int(_numberish(
            _config_value(config, "tools.daytona_disk", None),
            default=_numberish(os.environ.get("TERMINAL_CONTAINER_DISK"), default=10240),
        ))
        try:
            return _get_or_create_backend_environment(
                backend,
                task_key,
                cwd=str(overrides.get("cwd") or cwd),
                timeout=timeout,
                factory=lambda: _DaytonaSandboxEnvironment(
                    image=image,
                    cwd=str(overrides.get("cwd") or cwd),
                    host_cwd=str(overrides.get("cwd") or cwd),
                    timeout=timeout,
                    task_id=task_key,
                    persistent_filesystem=persistent,
                    cpu=cpu,
                    memory=memory,
                    disk=disk,
                ),
            ), "", backend
        except Exception as e:  # noqa: BLE001
            detail = _sanitize_remote_proof_text(str(e))
            return None, f"sandbox unavailable: daytona sandbox error ({detail})", backend
    return None, f"unknown backend {backend!r}", backend


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    try:
        return config.get(key, default)
    except Exception:
        return default


def _boolish(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _numberish(value: Any, *, default: int | float) -> int | float:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def remote_backend_diagnostics() -> dict[str, dict[str, Any]]:
    """Return passive SDK/config readiness for live remote backends.

    This intentionally avoids SDK client constructors and sandbox creation. It
    only checks module importability plus local credential/config signals.
    """
    return {
        "modal": _modal_backend_diagnostics(),
        "daytona": _daytona_backend_diagnostics(),
    }


def remote_backend_live_proof(
    backend: str | None = None,
    *,
    config: Any = None,
    timeout: int = 30,
) -> dict[str, dict[str, Any]]:
    """Run a gated live proof for Modal/Daytona remote backends.

    The proof never starts a paid remote sandbox unless explicitly enabled via
    environment or config. When credentials or the SDK are missing, it returns a
    closed/blocked report with concrete evidence instead of falling back locally.
    """
    return {
        name: _remote_backend_live_proof_one(name, config=config, timeout=timeout)
        for name in _live_proof_backend_names(backend)
    }


def _live_proof_backend_names(backend: str | None) -> tuple[str, ...]:
    if backend is None or str(backend).strip().lower() in {"", "all"}:
        return ("modal", "daytona")
    name = str(backend).strip().lower()
    if name not in {"modal", "daytona"}:
        return (name,)
    return (name,)


def _remote_backend_live_proof_one(
    backend: str,
    *,
    config: Any,
    timeout: int,
) -> dict[str, Any]:
    if backend == "modal":
        diagnostics = _modal_backend_diagnostics()
    elif backend == "daytona":
        diagnostics = _daytona_backend_diagnostics()
    else:
        return {
            "backend": backend,
            "status": "invalid",
            "probe": "live",
            "live_sandbox_started": False,
            "failure_reason": f"unsupported live proof backend {backend!r}",
        }

    gate = _remote_live_proof_gate(backend, config)
    expected_output = f"aegis-live-proof:{backend}:{uuid.uuid4().hex[:12]}"
    command = f"printf %s {shlex.quote(expected_output)}"
    report: dict[str, Any] = {
        "backend": backend,
        "status": "blocked",
        "probe": "live",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_revision": _git_revision(),
        "diagnostics": diagnostics,
        "credential_evidence": _remote_credential_evidence(backend, diagnostics),
        "gate": gate,
        "command": command,
        "expected_output": expected_output,
        "live_sandbox_started": False,
    }

    if not diagnostics["ready"]:
        missing = ", ".join(diagnostics["missing"]) or "unknown prerequisite"
        report["failure_reason"] = f"missing prerequisites: {missing}"
        return report

    if not gate["enabled"]:
        report["failure_reason"] = (
            "live remote proof is not enabled; set "
            f"{gate['backend_env']}=1 or AEGIS_REMOTE_LIVE_PROOF=1, "
            "or enable tools.remote_live_proof/tools."
            f"{backend}_live_proof in config"
        )
        return report

    task_id = f"live-proof-{backend}-{uuid.uuid4().hex[:12]}"
    proof_config = _ConfigOverlay(
        config,
        {
            "tools.allow_local_fallback": False,
            "tools.daytona_persistent": False,
        },
    )

    try:
        env, error, _actual_backend = create_environment(
            backend,
            "/",
            int(timeout or 30),
            proof_config,
            task_id=task_id,
        )
        if env is None:
            report["status"] = "failed"
            report["failure_reason"] = _sanitize_remote_proof_text(
                error or f"{backend} environment unavailable"
            )
            return report

        report["live_sandbox_started"] = True
        result = env.execute(command, cwd="/", timeout=int(timeout or 30))
        output = _output_text(result.get("output", "") if isinstance(result, dict) else result)
        returncode = int(result.get("returncode", 1) if isinstance(result, dict) else 1)
        report["returncode"] = returncode
        report["output_excerpt"] = _proof_excerpt(output)
        if returncode == 0 and expected_output in output:
            report["status"] = "passed"
        else:
            report["status"] = "failed"
            report["failure_reason"] = "proof command did not return the expected marker"
        return report
    except Exception as e:  # noqa: BLE001
        report["status"] = "failed"
        report["failure_reason"] = _sanitize_remote_proof_text(
            f"{type(e).__name__}: {e}"
        )
        return report
    finally:
        cleanup_task_environment(task_id, backend=backend)


class _ConfigOverlay:
    def __init__(self, base: Any, overrides: dict[str, Any]) -> None:
        self._base = base
        self._overrides = dict(overrides)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        if self._base is None:
            return default
        try:
            return self._base.get(key, default)
        except Exception:
            return default


def _remote_live_proof_gate(backend: str, config: Any) -> dict[str, Any]:
    backend_env = f"AEGIS_{backend.upper()}_LIVE_PROOF"
    sources: list[str] = []

    if _boolish(os.environ.get(backend_env), default=False):
        sources.append(f"env:{backend_env}")

    if _boolish(os.environ.get("AEGIS_REMOTE_LIVE_PROOF"), default=False):
        selector = os.environ.get("AEGIS_REMOTE_LIVE_PROOF_BACKENDS", "")
        if _backend_selected(selector, backend, default=True):
            sources.append("env:AEGIS_REMOTE_LIVE_PROOF")

    if _boolish(_config_value(config, f"tools.{backend}_live_proof", None), default=False):
        sources.append(f"config:tools.{backend}_live_proof")

    if _boolish(_config_value(config, "tools.remote_live_proof", None), default=False):
        selector = _config_value(config, "tools.remote_live_proof_backends", None)
        if _backend_selected(selector, backend, default=True):
            sources.append("config:tools.remote_live_proof")

    return {
        "enabled": bool(sources),
        "sources": sources,
        "backend_env": backend_env,
        "global_env": "AEGIS_REMOTE_LIVE_PROOF",
        "backend_selector_env": "AEGIS_REMOTE_LIVE_PROOF_BACKENDS",
    }


def _backend_selected(value: Any, backend: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        selected = {item.strip().lower() for item in text.split(",") if item.strip()}
    elif isinstance(value, (list, tuple, set, frozenset)):
        selected = {str(item).strip().lower() for item in value if str(item).strip()}
    else:
        return default
    return "all" in selected or backend in selected


def _remote_credential_evidence(backend: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
    if backend == "modal":
        missing = []
        if not diagnostics["credentials_config_available"]:
            missing = [
                "env:MODAL_TOKEN_ID+MODAL_TOKEN_SECRET",
                "config:MODAL_CONFIG_PATH or ~/.modal.toml",
            ]
        return {
            "env": {
                "MODAL_TOKEN_ID": _env_set("MODAL_TOKEN_ID"),
                "MODAL_TOKEN_SECRET": _env_set("MODAL_TOKEN_SECRET"),
            },
            "credential_sources": diagnostics["credential_sources"],
            "config_sources": diagnostics["config_sources"],
            "missing": missing,
        }
    missing = []
    if not diagnostics["credentials_config_available"]:
        missing = [
            "env:DAYTONA_API_KEY",
            "config:DAYTONA_CONFIG_PATH or ~/.daytona/config.{toml,json,yaml,yml}",
        ]
    return {
        "env": {"DAYTONA_API_KEY": _env_set("DAYTONA_API_KEY")},
        "credential_sources": diagnostics["credential_sources"],
        "config_sources": diagnostics["config_sources"],
        "missing": missing,
    }


def _proof_excerpt(output: str, *, limit: int = 2000) -> str:
    text = _sanitize_remote_proof_text(output)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _sanitize_remote_proof_text(text: str) -> str:
    sanitized = str(text or "")
    for name in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "DAYTONA_API_KEY",
    ):
        value = os.environ.get(name, "")
        if value:
            sanitized = sanitized.replace(value, f"[redacted:{name}]")
    return sanitized


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=2,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _modal_prerequisite_error() -> str:
    state = _modal_backend_diagnostics()
    if not state["sdk_available"]:
        return "sandbox unavailable: modal SDK not installed"
    if not state["credentials_config_available"]:
        return "sandbox unavailable: modal credentials/config not found"
    if not state["sdk_api_compatible"]:
        return (
            "sandbox unavailable: modal SDK API incompatible "
            f"({_sdk_api_incompatibility_detail(state)})"
        )
    return ""


def _daytona_prerequisite_error() -> str:
    state = _daytona_backend_diagnostics()
    if not state["credentials_config_available"]:
        return (
            "daytona backend is not configured in AEGIS "
            "(DAYTONA_API_KEY not set and no Daytona config found)"
        )
    if not state["sdk_available"]:
        return (
            "daytona backend is not configured in AEGIS (daytona SDK not installed)"
        )
    if not state["sdk_api_compatible"]:
        return (
            "daytona backend is not configured in AEGIS "
            f"(daytona SDK API incompatible: {_sdk_api_incompatibility_detail(state)})"
        )
    return ""


def _sdk_api_incompatibility_detail(state: dict[str, Any]) -> str:
    shape = state.get("sdk_api_shape") or {}
    missing = shape.get("missing") or []
    detail = ", ".join(str(item) for item in missing if str(item).strip())
    return detail or "unknown SDK API mismatch"


def _modal_backend_diagnostics() -> dict[str, Any]:
    credential_sources: list[str] = []
    if _env_pair_set("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        credential_sources.append("env:MODAL_TOKEN_ID+MODAL_TOKEN_SECRET")

    sdk_api_shape = _modal_sdk_api_shape()
    return _backend_diagnostic(
        backend="modal",
        sdk_available=_module_available("modal"),
        sdk_api_shape=sdk_api_shape,
        credential_sources=credential_sources,
        config_sources=_modal_config_sources(),
        sdk_missing="modal SDK not installed",
        credentials_missing="Modal credentials/config not found",
    )


def _daytona_backend_diagnostics() -> dict[str, Any]:
    credential_sources: list[str] = []
    if _env_set("DAYTONA_API_KEY"):
        credential_sources.append("env:DAYTONA_API_KEY")

    sdk_api_shape = _daytona_sdk_api_shape()
    return _backend_diagnostic(
        backend="daytona",
        sdk_available=_module_available("daytona"),
        sdk_api_shape=sdk_api_shape,
        credential_sources=credential_sources,
        config_sources=_daytona_config_sources(),
        sdk_missing="daytona SDK not installed",
        credentials_missing="Daytona credentials/config not found",
    )


def _backend_diagnostic(
    *,
    backend: str,
    sdk_available: bool,
    sdk_api_shape: dict[str, Any],
    credential_sources: list[str],
    config_sources: list[str],
    sdk_missing: str,
    credentials_missing: str,
) -> dict[str, Any]:
    credentials_config_available = bool(credential_sources or config_sources)
    missing: list[str] = []
    if not sdk_available:
        missing.append(sdk_missing)
    elif not sdk_api_shape["compatible"]:
        detail = ", ".join(sdk_api_shape["missing"]) or "unknown SDK API mismatch"
        missing.append(f"{backend} SDK API incompatible: {detail}")
    if not credentials_config_available:
        missing.append(credentials_missing)
    ready = sdk_available and sdk_api_shape["compatible"] and credentials_config_available
    return {
        "backend": backend,
        "ready": ready,
        "status": "ready" if ready else "unavailable",
        "sdk_available": sdk_available,
        "sdk_api_compatible": sdk_api_shape["compatible"],
        "sdk_api_shape": sdk_api_shape,
        "credential_available": bool(credential_sources),
        "config_available": bool(config_sources),
        "credentials_config_available": credentials_config_available,
        "credential_sources": credential_sources,
        "config_sources": config_sources,
        "missing": missing,
        "probe": "passive",
        "live_sandbox_started": False,
    }


def _module_available(module_name: str) -> bool:
    if sys.modules.get(module_name) is not None:
        return True
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _modal_sdk_api_shape() -> dict[str, Any]:
    module, import_error = _import_optional_sdk_module("modal")
    required = (
        _SdkRequirement("modal.App.lookup", ("App", "lookup"), keywords=("create_if_missing",)),
        _SdkRequirement("modal.Image.debian_slim", ("Image", "debian_slim")),
        _SdkRequirement("modal.Image.from_id", ("Image", "from_id")),
        _SdkRequirement("modal.Sandbox.create", ("Sandbox", "create"), keywords=("app", "image", "timeout")),
    )
    shape = _sdk_api_shape(module, import_error=import_error, required=required)
    shape["optional"] = {
        "modal.Sandbox.exec": _dotted_callable(module, ("Sandbox", "exec")),
        "modal.Sandbox.terminate": _dotted_callable(module, ("Sandbox", "terminate")),
        "modal.Sandbox.snapshot_filesystem": _dotted_callable(module, ("Sandbox", "snapshot_filesystem")),
    }
    return shape


def _daytona_sdk_api_shape() -> dict[str, Any]:
    module, import_error = _import_optional_sdk_module("daytona")
    list_mode = "unavailable"
    if module is not None:
        daytona_cls = getattr(module, "Daytona", None)
        list_mode = _daytona_list_api_mode(getattr(daytona_cls, "list", None))
    required = (
        _SdkRequirement("daytona.Daytona", ("Daytona",), callable_required=True),
        _SdkRequirement("daytona.Daytona.create", ("Daytona", "create")),
        _SdkRequirement("daytona.Daytona.get", ("Daytona", "get")),
        _SdkRequirement("daytona.Daytona.list", ("Daytona", "list")),
        _SdkRequirement("daytona.Daytona.delete", ("Daytona", "delete")),
        _SdkRequirement(
            "daytona.CreateSandboxFromImageParams",
            ("CreateSandboxFromImageParams",),
            keywords=("image", "name", "labels", "auto_stop_interval", "resources"),
            callable_required=True,
        ),
        _SdkRequirement(
            "daytona.Resources",
            ("Resources",),
            keywords=("cpu", "memory", "disk"),
            callable_required=True,
        ),
        _SdkRequirement("daytona.DaytonaError", ("DaytonaError",)),
        _SdkRequirement("daytona.SandboxState", ("SandboxState",)),
    )
    shape = _sdk_api_shape(module, import_error=import_error, required=required)
    if module is not None and not _daytona_error_type_compatible(module):
        shape["missing"].append("daytona.DaytonaError must be an exception type")
    if module is not None and not _daytona_sandbox_state_compatible(module):
        shape["missing"].append("daytona.SandboxState.STOPPED/ARCHIVED")
    if list_mode == "query" and _daytona_list_query_class(module) is None:
        shape["missing"].append("daytona.ListSandboxesQuery")
    shape["compatible"] = not shape["missing"]
    shape["daytona_list_mode"] = list_mode
    shape["optional"] = {
        "daytona.common.filesystem.FileUpload": _daytona_file_upload_available(),
    }
    return shape


class _SdkRequirement:
    def __init__(
        self,
        name: str,
        path: tuple[str, ...],
        *,
        keywords: tuple[str, ...] = (),
        callable_required: bool = True,
    ) -> None:
        self.name = name
        self.path = path
        self.keywords = keywords
        self.callable_required = callable_required


def _import_optional_sdk_module(module_name: str) -> tuple[Any | None, str | None]:
    try:
        return __import__(module_name), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _sdk_api_shape(
    module: Any | None,
    *,
    import_error: str | None,
    required: tuple[_SdkRequirement, ...],
) -> dict[str, Any]:
    missing: list[str] = []
    present: list[str] = []
    if module is None:
        missing.extend(item.name for item in required)
    else:
        for item in required:
            value = _dotted_attr(module, item.path)
            if value is None:
                missing.append(item.name)
                continue
            if item.callable_required and not callable(value):
                missing.append(item.name)
                continue
            if item.keywords and not _callable_accepts_keywords(value, item.keywords):
                missing.append(f"{item.name}({', '.join(item.keywords)})")
                continue
            present.append(item.name)
    return {
        "compatible": not missing,
        "import_error": import_error,
        "version": str(getattr(module, "__version__", "") or "") if module is not None else "",
        "required": [item.name for item in required],
        "present": present,
        "missing": missing,
    }


def _dotted_attr(root: Any, path: tuple[str, ...]) -> Any:
    value = root
    for name in path:
        value = getattr(value, name, None)
        if value is None:
            return None
    return value


def _dotted_callable(root: Any, path: tuple[str, ...]) -> bool:
    return callable(_dotted_attr(root, path)) if root is not None else False


def _callable_accepts_keywords(fn: Any, keywords: tuple[str, ...]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return True
    return all(keyword in params for keyword in keywords)


def _daytona_error_type_compatible(module: Any) -> bool:
    error_type = getattr(module, "DaytonaError", None)
    try:
        return isinstance(error_type, type) and issubclass(error_type, BaseException)
    except TypeError:
        return False


def _daytona_sandbox_state_compatible(module: Any) -> bool:
    state = getattr(module, "SandboxState", None)
    return all(hasattr(state, name) for name in ("STOPPED", "ARCHIVED"))


def _daytona_list_api_mode(list_fn: Any) -> str:
    if not callable(list_fn):
        return "unavailable"
    try:
        params = inspect.signature(list_fn).parameters
    except (TypeError, ValueError):
        return "keywords"
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return "keywords"
    if "labels" in params or "limit" in params:
        return "keywords"
    if "query" in params:
        return "query"
    positional = [
        param
        for param in params.values()
        if param.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        and param.name != "self"
    ]
    return "query" if len(positional) <= 1 else "unknown"


def _daytona_list_query_class(module: Any | None = None) -> Any | None:
    if module is not None:
        query_cls = getattr(module, "ListSandboxesQuery", None)
        if callable(query_cls):
            return query_cls
    try:
        from daytona import ListSandboxesQuery  # type: ignore[import-not-found]

        return ListSandboxesQuery
    except Exception:
        pass
    try:
        from daytona.common.sandbox import ListSandboxesQuery  # type: ignore[import-not-found]
    except Exception:
        return None
    return ListSandboxesQuery


def _daytona_file_upload_available() -> bool:
    try:
        from daytona.common.filesystem import FileUpload  # type: ignore[import-not-found]
    except Exception:
        return False
    return callable(FileUpload)


def _env_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _env_pair_set(*names: str) -> bool:
    return all(_env_set(name) for name in names)


def _modal_config_sources() -> list[str]:
    return _config_sources_with_keys(
        _candidate_config_paths(
            env_var="MODAL_CONFIG_PATH",
            home_paths=(".modal.toml", ".modal/config.toml"),
            xdg_paths=("modal.toml", "modal/config.toml"),
        ),
        required_keys=("token_id", "token_secret"),
    )


def _daytona_config_sources() -> list[str]:
    return _config_sources_with_any_key(
        _candidate_config_paths(
            env_var="DAYTONA_CONFIG_PATH",
            home_paths=(
                ".daytona/config.toml",
                ".daytona/config.json",
                ".daytona/config.yaml",
                ".daytona/config.yml",
            ),
            xdg_paths=(
                "daytona/config.toml",
                "daytona/config.json",
                "daytona/config.yaml",
                "daytona/config.yml",
            ),
        ),
        keys=("api_key", "apikey", "apiKey"),
    )


def _candidate_config_paths(
    *,
    env_var: str,
    home_paths: tuple[str, ...],
    xdg_paths: tuple[str, ...],
) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    configured = os.environ.get(env_var, "").strip()
    if configured:
        paths.append((f"env:{env_var}", Path(configured).expanduser()))

    home = Path.home()
    for rel in home_paths:
        paths.append((f"file:~/{rel}", home / rel))

    xdg_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_home:
        xdg_root = Path(xdg_home).expanduser()
        for rel in xdg_paths:
            paths.append((f"file:$XDG_CONFIG_HOME/{rel}", xdg_root / rel))
    return paths


def _config_sources_with_keys(
    candidates: list[tuple[str, Path]],
    *,
    required_keys: tuple[str, ...],
) -> list[str]:
    sources: list[str] = []
    seen: set[Path] = set()
    for source, path in candidates:
        marker = _path_marker(path)
        if marker in seen:
            continue
        seen.add(marker)
        if _config_file_has_keys(path, required_keys):
            sources.append(source)
    return sources


def _config_sources_with_any_key(
    candidates: list[tuple[str, Path]],
    *,
    keys: tuple[str, ...],
) -> list[str]:
    sources: list[str] = []
    seen: set[Path] = set()
    for source, path in candidates:
        marker = _path_marker(path)
        if marker in seen:
            continue
        seen.add(marker)
        if any(_config_file_has_keys(path, (key,)) for key in keys):
            sources.append(source)
    return sources


def _path_marker(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _config_file_has_keys(path: Path, keys: tuple[str, ...]) -> bool:
    try:
        if not path.expanduser().is_file():
            return False
    except OSError:
        return False

    data = _load_toml_config(path)
    if data is not None:
        values = _flatten_config_values(data)
        return all(str(values.get(key.lower(), "")).strip() for key in keys)

    try:
        text = path.expanduser().read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lowered = text.lower()
    return all(key.lower() in lowered for key in keys)


def _load_toml_config(path: Path) -> dict[str, Any] | None:
    if path.suffix.lower() != ".toml":
        return None
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return None
    try:
        with path.expanduser().open("rb") as handle:
            data = tomllib.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _flatten_config_values(data: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not isinstance(data, dict):
        return values
    for key, value in data.items():
        if isinstance(value, dict):
            values.update(_flatten_config_values(value))
        else:
            values[str(key).lower()] = value
    return values


_REMOTE_WORKSPACE_ROOT = "/workspace"
_REMOTE_SYNC_INTERVAL_SECONDS = 2.0
_REMOTE_SYNC_MAX_FILE_BYTES = 16 * 1024 * 1024
_REMOTE_SYNC_BACK_MAX_BYTES = 512 * 1024 * 1024
_REMOTE_SYNC_BACK_RETRIES = 3
_REMOTE_SYNC_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_REMOTE_SYNC_EXCLUDED_FILES = {
    ".DS_Store",
    ".aegis",
    ".coverage",
    ".env",
    ".env.local",
}
_REMOTE_SYNC_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_MODAL_DIRECT_SNAPSHOT_NAMESPACE = "direct"


def _modal_snapshot_store_path() -> Path:
    try:
        from .. import config as cfg

        return cfg.get_home() / "modal_snapshots.json"
    except Exception:
        return Path(os.environ.get("AEGIS_HOME", str(Path.home() / ".aegis"))) / "modal_snapshots.json"


def _load_modal_snapshots() -> dict[str, str]:
    path = _modal_snapshot_store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, str) and value}


def _save_modal_snapshots(data: dict[str, str]) -> None:
    path = _modal_snapshot_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _modal_direct_snapshot_key(task_id: str) -> str:
    return f"{_MODAL_DIRECT_SNAPSHOT_NAMESPACE}:{task_id}"


def _modal_snapshot_restore_candidate(task_id: str) -> tuple[str | None, bool]:
    snapshots = _load_modal_snapshots()
    direct_key = _modal_direct_snapshot_key(task_id)
    snapshot_id = snapshots.get(direct_key)
    if snapshot_id:
        return snapshot_id, False
    legacy_snapshot_id = snapshots.get(task_id)
    if legacy_snapshot_id:
        return legacy_snapshot_id, True
    return None, False


def _store_modal_snapshot(task_id: str, snapshot_id: str) -> None:
    snapshots = _load_modal_snapshots()
    snapshots[_modal_direct_snapshot_key(task_id)] = snapshot_id
    snapshots.pop(task_id, None)
    _save_modal_snapshots(snapshots)


def _delete_modal_snapshot(task_id: str, snapshot_id: str | None = None) -> None:
    snapshots = _load_modal_snapshots()
    changed = False
    for key in (_modal_direct_snapshot_key(task_id), task_id):
        existing = snapshots.get(key)
        if existing is None:
            continue
        if snapshot_id is None or existing == snapshot_id:
            snapshots.pop(key, None)
            changed = True
    if changed:
        _save_modal_snapshots(snapshots)


def _resolve_remote_workspace(cwd: str, task_id: str) -> tuple[Path | None, str, str]:
    """Return ``(host_root, remote_root, cwd)`` for sync-aware remote backends."""
    del task_id  # Reserved for future per-task remote roots; current sandboxes are per-task.
    host_root = _local_sync_root(cwd)
    if host_root is None:
        return None, _REMOTE_WORKSPACE_ROOT, _remote_cwd(cwd)
    return host_root, _REMOTE_WORKSPACE_ROOT, _REMOTE_WORKSPACE_ROOT


def _local_sync_root(cwd: str) -> Path | None:
    if not cwd:
        return None
    try:
        path = Path(str(cwd)).expanduser().resolve()
    except OSError:
        return None
    try:
        home = Path.home().resolve()
    except OSError:
        home = Path.home()
    if path in {Path(path.anchor), home}:
        return None
    if not path.is_dir():
        return None
    return path


def _map_cwd_to_remote(cwd: str, host_root: Path | None, remote_root: str) -> str:
    if host_root is None or not cwd:
        return _remote_cwd(cwd)
    try:
        candidate = Path(str(cwd)).expanduser().resolve()
    except OSError:
        return _remote_cwd(cwd)
    try:
        rel = candidate.relative_to(host_root)
    except ValueError:
        return _remote_cwd(cwd)
    rel_text = rel.as_posix()
    if rel_text == ".":
        return remote_root
    return _remote_join(remote_root, rel_text)


def _remote_join(root: str, rel: str) -> str:
    rel = str(rel).strip("/")
    return root.rstrip("/") if not rel else f"{root.rstrip('/')}/{rel}"


def _remote_parent(path: str) -> str:
    path = str(path).rstrip("/")
    if "/" not in path:
        return "."
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def _quoted_mkdir_command(dirs: list[str]) -> str:
    clean = sorted({d for d in dirs if d})
    if not clean:
        return "true"
    return "mkdir -p " + " ".join(shlex.quote(d) for d in clean)


def _quoted_rm_command(paths: list[str]) -> str:
    clean = [p for p in paths if p]
    if not clean:
        return "true"
    return "rm -f " + " ".join(shlex.quote(p) for p in clean)


def _safe_tar_rel(name: str) -> str | None:
    rel = str(name).replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if not rel or rel == ".":
        return None
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return rel


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_sync_excluded_file(path: Path) -> bool:
    name = path.name
    lowered = name.lower()
    if name in _REMOTE_SYNC_EXCLUDED_FILES:
        return True
    if lowered.startswith(".env.") and lowered not in {".env.example", ".env.sample"}:
        return True
    if lowered.endswith(_REMOTE_SYNC_SECRET_SUFFIXES):
        return True
    return False


class _RemoteWorkspaceSync:
    """Incremental host-workspace sync for remote sandboxes."""

    def __init__(
        self,
        *,
        host_root: Path | None,
        remote_root: str,
        bulk_upload_fn: Callable[[list[tuple[str, str]]], None],
        bulk_download_fn: Callable[[Path], None],
        delete_fn: Callable[[list[str]], None],
        sync_interval: float = _REMOTE_SYNC_INTERVAL_SECONDS,
    ) -> None:
        self.host_root = host_root
        self.remote_root = remote_root.rstrip("/") or "/"
        self._bulk_upload_fn = bulk_upload_fn
        self._bulk_download_fn = bulk_download_fn
        self._delete_fn = delete_fn
        self._sync_interval = sync_interval
        self._last_sync_time = 0.0
        self._synced_files: dict[str, tuple[float, int]] = {}
        self._pushed_hashes: dict[str, str] = {}

    def sync_to_remote(self, *, force: bool = False) -> None:
        if self.host_root is None:
            return
        if not force and time.monotonic() - self._last_sync_time < self._sync_interval:
            return

        current = self._scan_host_files()
        current_rels = set(current)
        to_upload = [
            (str(item["host_path"]), str(item["remote_path"]))
            for rel, item in current.items()
            if self._synced_files.get(rel) != item["key"]
        ]
        to_delete = [
            _remote_join(self.remote_root, rel)
            for rel in self._synced_files
            if rel not in current_rels
        ]
        if not to_upload and not to_delete:
            self._last_sync_time = time.monotonic()
            return

        previous_files = dict(self._synced_files)
        previous_hashes = dict(self._pushed_hashes)
        try:
            if to_upload:
                self._bulk_upload_fn(to_upload)
            if to_delete:
                self._delete_fn(to_delete)

            self._synced_files = {
                rel: item["key"] for rel, item in current.items()
            }
            for host_path, remote_path in to_upload:
                rel = self._rel_from_remote(remote_path)
                if rel is not None:
                    self._pushed_hashes[rel] = _sha256_file(Path(host_path))
            for remote_path in to_delete:
                rel = self._rel_from_remote(remote_path)
                if rel is not None:
                    self._pushed_hashes.pop(rel, None)
            self._last_sync_time = time.monotonic()
        except Exception:
            self._synced_files = previous_files
            self._pushed_hashes = previous_hashes
            self._last_sync_time = time.monotonic()
            raise

    def sync_back(self) -> None:
        if self.host_root is None:
            return
        if not self._synced_files and not self._pushed_hashes:
            return
        last_exc: Exception | None = None
        for attempt in range(_REMOTE_SYNC_BACK_RETRIES):
            try:
                self._sync_back_once()
                return
            except Exception as exc:
                last_exc = exc
                if attempt + 1 >= _REMOTE_SYNC_BACK_RETRIES:
                    break
                logger.warning(
                    "remote sync-back attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                time.sleep(0.1 * (attempt + 1))
        logger.warning(
            "remote sync-back failed after %d attempts: %s",
            _REMOTE_SYNC_BACK_RETRIES,
            last_exc,
        )

    def _scan_host_files(self) -> dict[str, dict[str, Any]]:
        assert self.host_root is not None
        files: dict[str, dict[str, Any]] = {}
        for dirpath, dirnames, filenames in os.walk(self.host_root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _REMOTE_SYNC_EXCLUDED_DIRS
            ]
            base = Path(dirpath)
            for filename in filenames:
                host_path = base / filename
                if _remote_sync_excluded_file(host_path):
                    continue
                try:
                    stat = host_path.stat()
                except OSError:
                    continue
                if not host_path.is_file() or host_path.is_symlink():
                    continue
                if stat.st_size > _REMOTE_SYNC_MAX_FILE_BYTES:
                    continue
                try:
                    rel = host_path.relative_to(self.host_root).as_posix()
                except ValueError:
                    continue
                if _safe_tar_rel(rel) != rel:
                    continue
                files[rel] = {
                    "host_path": host_path,
                    "remote_path": _remote_join(self.remote_root, rel),
                    "key": (stat.st_mtime, stat.st_size),
                }
        return files

    def _sync_back_once(self) -> None:
        assert self.host_root is not None
        self.host_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".tar.gz") as tmp:
            self._bulk_download_fn(Path(tmp.name))
            try:
                tar_size = os.path.getsize(tmp.name)
                if tar_size > _REMOTE_SYNC_BACK_MAX_BYTES:
                    logger.warning(
                        "remote sync-back tar exceeded size cap (%d > %d bytes); skipping",
                        tar_size,
                        _REMOTE_SYNC_BACK_MAX_BYTES,
                    )
                    return
            except OSError:
                logger.warning("remote sync-back could not stat downloaded tar; skipping")
                return
            with tempfile.TemporaryDirectory(prefix="aegis-sync-back-") as staging_dir:
                staging = Path(staging_dir)
                self._extract_safe_tar(Path(tmp.name), staging)
                for staged in staging.rglob("*"):
                    if not staged.is_file():
                        continue
                    rel = staged.relative_to(staging).as_posix()
                    pushed_hash = self._pushed_hashes.get(rel)
                    remote_hash = _sha256_file(staged)
                    if pushed_hash is not None and remote_hash == pushed_hash:
                        continue
                    host_path = self.host_root / rel
                    host_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(staged, host_path)
                    try:
                        stat = host_path.stat()
                        self._synced_files[rel] = (stat.st_mtime, stat.st_size)
                    except OSError:
                        pass
                    self._pushed_hashes[rel] = remote_hash

    def _extract_safe_tar(self, tar_path: Path, staging: Path) -> None:
        with tarfile.open(tar_path, "r:*") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                rel = _safe_tar_rel(member.name)
                if rel is None:
                    continue
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                with target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)

    def _rel_from_remote(self, remote_path: str) -> str | None:
        prefix = self.remote_root.rstrip("/") + "/"
        if not remote_path.startswith(prefix):
            return None
        rel = remote_path[len(prefix):]
        return rel if _safe_tar_rel(rel) == rel else None


class _ModalSandboxEnvironment:
    """Persistent Modal sandbox with AEGIS workspace sync and remote result handoff."""

    _STDIN_CHUNK_SIZE = 1024 * 1024

    def __init__(
        self,
        *,
        app: Any,
        image: Any,
        cwd: str,
        timeout: int,
        task_id: str,
    ) -> None:
        import modal

        self.app = app
        self.image = image
        self.timeout = timeout
        self.task_id = task_id or "default"
        self._modal = modal
        self._lock = threading.RLock()
        self._sandbox = None
        self._sync = None
        try:
            self._sandbox = self._create_sandbox(timeout)
            self._host_root, self._remote_root, self.cwd = _resolve_remote_workspace(cwd, self.task_id)
            self._sync = _RemoteWorkspaceSync(
                host_root=self._host_root,
                remote_root=self._remote_root,
                bulk_upload_fn=self._modal_bulk_upload,
                bulk_download_fn=self._modal_bulk_download,
                delete_fn=self._modal_delete,
            )
            self._sync.sync_to_remote(force=True)
        except Exception:
            self._cleanup_failed_init()
            raise

    def get_temp_dir(self) -> str:
        return "/tmp"

    def set_cwd(self, cwd: str) -> None:
        self.cwd = _map_cwd_to_remote(cwd, self._host_root, self._remote_root)

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync.sync_to_remote()
            effective_cwd = _map_cwd_to_remote(cwd, self._host_root, self._remote_root) if cwd else self.cwd
            shell = (
                f"export AEGIS_TASK_ID={shlex.quote(self.task_id)}; "
                f"cd {shlex.quote(effective_cwd)} 2>/dev/null || true; "
                f"bash -lc {shlex.quote(command)}"
            )
            stdout, stderr, code = self._modal_exec(
                shell,
                timeout=int(timeout or self.timeout),
                stdin_data=stdin_data,
            )
            return {
                "output": _merge_streams(_output_text(stdout), _output_text(stderr)),
                "returncode": code,
            }

    def cancel(self) -> None:
        with self._lock:
            terminate = getattr(self._sandbox, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                except Exception:
                    pass

    def cleanup(self) -> None:
        with self._lock:
            sandbox = self._sandbox
            if sandbox is None:
                return
            try:
                self._sync.sync_back()
            finally:
                snapshot_id = self._snapshot_filesystem(sandbox)
                if snapshot_id:
                    _store_modal_snapshot(self.task_id, snapshot_id)
                try:
                    sandbox.terminate()
                except Exception:
                    pass
                self._sandbox = None

    def _cleanup_failed_init(self) -> None:
        sandbox = getattr(self, "_sandbox", None)
        if sandbox is None:
            return
        try:
            terminate = getattr(sandbox, "terminate", None)
            if callable(terminate):
                terminate()
        except Exception:
            pass
        finally:
            self._sandbox = None

    def _create_sandbox(self, timeout: int) -> Any:
        snapshot_id, from_legacy_key = _modal_snapshot_restore_candidate(self.task_id)
        if snapshot_id:
            try:
                image_from_id = getattr(getattr(self._modal, "Image", None), "from_id")
                sandbox = self._create_sandbox_with_image(image_from_id(snapshot_id), timeout)
            except Exception:
                _delete_modal_snapshot(self.task_id, snapshot_id)
            else:
                if from_legacy_key:
                    _store_modal_snapshot(self.task_id, snapshot_id)
                return sandbox
        return self._create_sandbox_with_image(self.image, timeout)

    def _create_sandbox_with_image(self, image: Any, timeout: int) -> Any:
        create = self._modal.Sandbox.create
        try:
            return create(
                "sleep",
                "infinity",
                app=self.app,
                image=image,
                timeout=max(int(timeout or 0), 3600),
            )
        except TypeError:
            return create(app=self.app, image=image, timeout=timeout)

    @staticmethod
    def _snapshot_filesystem(sandbox: Any) -> str | None:
        snapshot = getattr(sandbox, "snapshot_filesystem", None)
        if not callable(snapshot):
            return None
        try:
            image = snapshot()
        except Exception:
            return None
        snapshot_id = getattr(image, "object_id", None) or getattr(image, "id", None)
        if isinstance(snapshot_id, str) and snapshot_id:
            return snapshot_id
        if isinstance(image, str) and image:
            return image
        return None

    def _modal_exec(
        self,
        command: str,
        *,
        timeout: int,
        stdin_data: str | None = None,
    ) -> tuple[Any, Any, int]:
        try:
            proc = self._sandbox.exec("bash", "-lc", command, timeout=timeout)
        except TypeError:
            proc = self._sandbox.exec("bash", "-lc", command)
        if stdin_data is not None:
            stdin = getattr(proc, "stdin", None)
            if stdin is None:
                raise RuntimeError("modal process does not expose stdin")
            self._write_modal_stdin(stdin, stdin_data)
        stdout = self._read_process_stream(getattr(proc, "stdout", None))
        stderr = self._read_process_stream(getattr(proc, "stderr", None))
        wait = getattr(proc, "wait", None)
        code = wait() if callable(wait) else getattr(proc, "returncode", 0)
        return stdout, stderr, int(code or 0)

    def _write_modal_stdin(self, stdin: Any, data: str) -> None:
        for offset in range(0, len(data), self._STDIN_CHUNK_SIZE):
            stdin.write(data[offset:offset + self._STDIN_CHUNK_SIZE])
            self._drain_modal_stdin(stdin)
        write_eof = getattr(stdin, "write_eof", None)
        if callable(write_eof):
            write_eof()
            return
        close = getattr(stdin, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _drain_modal_stdin(stdin: Any) -> None:
        drain = getattr(stdin, "drain", None)
        if callable(drain):
            drain()
            return
        drain_aio = getattr(drain, "aio", None)
        if callable(drain_aio):
            _run_sync_awaitable(drain_aio())

    @staticmethod
    def _read_process_stream(stream: Any) -> Any:
        read = getattr(stream, "read", None)
        if callable(read):
            return read()
        return ""

    def _modal_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        if not files:
            return
        payload = _build_upload_payload(files)
        parents = [_remote_parent(remote_path) for _host_path, remote_path in files]
        command = f"{_quoted_mkdir_command([self._remote_root, *parents])} && base64 -d | tar xzf - -C /"
        _stdout, stderr, code = self._modal_exec(command, timeout=120, stdin_data=payload)
        if code != 0:
            raise RuntimeError(f"modal bulk upload failed: {_output_text(stderr)}")

    def _modal_bulk_download(self, dest: Path) -> None:
        root = shlex.quote(self._remote_root)
        command = (
            f"if [ -d {root} ]; then tar czf - -C {root} .; "
            "else tar czf - -T /dev/null; fi"
        )
        stdout, stderr, code = self._modal_exec(command, timeout=120)
        if code != 0:
            raise RuntimeError(f"modal bulk download failed: {_output_text(stderr)}")
        if isinstance(stdout, bytes):
            dest.write_bytes(stdout)
        else:
            dest.write_bytes(str(stdout or "").encode("utf-8"))

    def _modal_delete(self, remote_paths: list[str]) -> None:
        _stdout, _stderr, code = self._modal_exec(_quoted_rm_command(remote_paths), timeout=30)
        if code != 0:
            raise RuntimeError("modal remote delete failed")


def _run_sync_awaitable(value: Any) -> Any:
    from .async_bridge import run_sync_awaitable

    return run_sync_awaitable(value)


def _build_upload_payload(files: list[tuple[str, str]]) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for host_path, remote_path in files:
            tar.add(host_path, arcname=remote_path.lstrip("/"))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _update_environment_cwd(env: Any, cwd: str) -> None:
    set_cwd = getattr(env, "set_cwd", None)
    if callable(set_cwd):
        set_cwd(cwd)
        return
    if cwd and getattr(env, "cwd", None) is not None:
        env.cwd = cwd


def _daytona_list_sandboxes(client: Any, labels: dict[str, str], *, limit: int):
    list_fn = getattr(client, "list")
    if _daytona_list_api_mode(list_fn) == "query":
        query_cls = _daytona_list_query_class()
        if query_cls is not None:
            return list_fn(query_cls(labels=labels, limit=limit))
    try:
        return list_fn(labels=labels, limit=limit)
    except TypeError:
        query_cls = _daytona_list_query_class()
        if query_cls is None:
            raise
        return list_fn(query_cls(labels=labels, limit=limit))


class _DaytonaSandboxEnvironment:
    """Minimal Daytona SDK adapter with persistent resume semantics."""

    def __init__(
        self,
        *,
        image: str,
        cwd: str,
        host_cwd: str | None = None,
        timeout: int,
        task_id: str,
        persistent_filesystem: bool,
        cpu: int,
        memory: int,
        disk: int,
    ) -> None:
        from daytona import (
            CreateSandboxFromImageParams,
            Daytona,
            DaytonaError,
            Resources,
            SandboxState,
        )

        self.image = image
        self._host_root, self._remote_root, self.cwd = _resolve_remote_workspace(host_cwd or cwd, task_id)
        self.timeout = timeout
        self.task_id = task_id or "default"
        self._persistent = bool(persistent_filesystem)
        self._client = None
        self._DaytonaError = DaytonaError
        self._SandboxState = SandboxState
        self._sandbox = None
        self._lock = threading.RLock()
        self._sandbox_name = f"aegis-{_path_safe_id(self.task_id)}"
        self._labels = {"aegis_task_id": self.task_id}
        self._sync = None

        try:
            self._client = Daytona()
            self._sandbox = self._resume_sandbox()
            if self._sandbox is None:
                memory_gib = max(1, math.ceil(int(memory or 5120) / 1024))
                disk_gib = max(1, min(10, math.ceil(int(disk or 10240) / 1024)))
                self._sandbox = self._client.create(
                    CreateSandboxFromImageParams(
                        image=image,
                        name=self._sandbox_name,
                        labels=self._labels,
                        auto_stop_interval=0,
                        resources=Resources(cpu=int(cpu or 1), memory=memory_gib, disk=disk_gib),
                    )
                )
            self._detect_remote_home()
            self._sync = _RemoteWorkspaceSync(
                host_root=self._host_root,
                remote_root=self._remote_root,
                bulk_upload_fn=self._daytona_bulk_upload,
                bulk_download_fn=self._daytona_bulk_download,
                delete_fn=self._daytona_delete,
            )
            self._sync.sync_to_remote(force=True)
        except Exception:
            self._cleanup_failed_init()
            raise

    def get_temp_dir(self) -> str:
        return "/tmp"

    def set_cwd(self, cwd: str) -> None:
        self.cwd = _map_cwd_to_remote(cwd, self._host_root, self._remote_root)

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            sandbox = self._ensure_sandbox_ready()
            self._sync.sync_to_remote()
            effective_timeout = int(timeout or self.timeout)
            effective_cwd = _map_cwd_to_remote(cwd, self._host_root, self._remote_root) if cwd else self.cwd
            shell_command = self._shell_command(command, effective_cwd, stdin_data)
            response = sandbox.process.exec(shell_command, timeout=effective_timeout)
            return {
                "output": str(getattr(response, "result", "") or ""),
                "returncode": int(getattr(response, "exit_code", 0) or 0),
            }

    def cancel(self) -> None:
        with self._lock:
            sandbox = self._sandbox
            if sandbox is None:
                return
            try:
                sandbox.stop()
            except Exception:
                pass

    def cleanup(self) -> None:
        with self._lock:
            sandbox = self._sandbox
            if sandbox is None:
                return
            try:
                self._sync.sync_back()
            except Exception:
                pass
            try:
                if self._persistent:
                    sandbox.stop()
                else:
                    delete = getattr(self._client, "delete", None)
                    if callable(delete):
                        delete(sandbox)
                    else:
                        sandbox.delete()
            except Exception:
                pass
            finally:
                self._sandbox = None

    def _cleanup_failed_init(self) -> None:
        sandbox = getattr(self, "_sandbox", None)
        if sandbox is None:
            return
        try:
            if self._persistent:
                stop = getattr(sandbox, "stop", None)
                if callable(stop):
                    stop()
            else:
                delete = getattr(getattr(self, "_client", None), "delete", None)
                if callable(delete):
                    delete(sandbox)
                else:
                    sandbox_delete = getattr(sandbox, "delete", None)
                    if callable(sandbox_delete):
                        sandbox_delete()
        except Exception:
            pass
        finally:
            self._sandbox = None

    def _daytona_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        if not files:
            return
        parents = [_remote_parent(remote_path) for _host_path, remote_path in files]
        mkdir_response = self._sandbox.process.exec(
            _quoted_mkdir_command([self._remote_root, *parents]),
            timeout=30,
        )
        if int(getattr(mkdir_response, "exit_code", 0) or 0) != 0:
            output = str(getattr(mkdir_response, "result", "") or "mkdir failed")
            raise RuntimeError(f"daytona bulk upload failed: {output}")
        fs = getattr(self._sandbox, "fs", None)
        upload_files = getattr(fs, "upload_files", None)
        upload_file = getattr(fs, "upload_file", None)
        if callable(upload_files):
            try:
                upload_files([self._daytona_upload_entry(host, remote) for host, remote in files])
                return
            except Exception as exc:
                if not callable(upload_file):
                    raise RuntimeError(f"daytona bulk upload failed: {exc}") from exc
                logger.debug("daytona bulk upload_files failed; falling back to upload_file: %s", exc)
        if not callable(upload_file):
            payload = _build_upload_payload(files)
            command = f"base64 -d <<'__AEGIS_SYNC_TAR__' | tar xzf - -C /\n{payload}\n__AEGIS_SYNC_TAR__"
            response = self._sandbox.process.exec(command, timeout=120)
            if int(getattr(response, "exit_code", 0) or 0) != 0:
                output = str(getattr(response, "result", "") or "daytona tar upload failed")
                raise RuntimeError(f"daytona bulk upload failed: {output}")
            return
        try:
            for host_path, remote_path in files:
                upload_file(host_path, remote_path)
        except Exception as exc:
            raise RuntimeError(f"daytona bulk upload failed: {exc}") from exc

    @staticmethod
    def _daytona_upload_entry(host_path: str, remote_path: str) -> Any:
        try:
            from daytona.common.filesystem import FileUpload

            return FileUpload(source=host_path, destination=remote_path)
        except Exception:
            class _FileUpload:
                def __init__(self, source: str, destination: str) -> None:
                    self.source = source
                    self.destination = destination

            return _FileUpload(source=host_path, destination=remote_path)

    def _daytona_bulk_download(self, dest: Path) -> None:
        remote_tar = f"/tmp/aegis-sync-{os.getpid()}-{uuid.uuid4().hex}.tar.gz"
        root = shlex.quote(self._remote_root)
        command = (
            f"if [ -d {root} ]; then tar czf {shlex.quote(remote_tar)} -C {root} .; "
            f"else tar czf {shlex.quote(remote_tar)} -T /dev/null; fi"
        )
        response = self._sandbox.process.exec(command, timeout=120)
        if int(getattr(response, "exit_code", 0) or 0) != 0:
            raise RuntimeError(str(getattr(response, "result", "") or "daytona tar failed"))
        fs = getattr(self._sandbox, "fs", None)
        download_file = getattr(fs, "download_file", None)
        if not callable(download_file):
            raise RuntimeError("daytona sandbox does not expose fs.download_file")
        download_file(remote_tar, str(dest))
        try:
            self._sandbox.process.exec(f"rm -f {shlex.quote(remote_tar)}", timeout=30)
        except Exception:
            pass

    def _daytona_delete(self, remote_paths: list[str]) -> None:
        self._sandbox.process.exec(_quoted_rm_command(remote_paths), timeout=30)

    def _resume_sandbox(self) -> Any | None:
        if not self._persistent:
            return None
        try:
            sandbox = self._client.get(self._sandbox_name)
            sandbox.start()
            return sandbox
        except self._DaytonaError:
            pass
        except Exception:
            pass
        try:
            sandboxes = self._list_daytona_sandboxes_by_label()
            sandbox = next(iter(sandboxes), None)
            if sandbox is not None:
                sandbox.start()
                return sandbox
        except Exception:
            pass
        return None

    def _list_daytona_sandboxes_by_label(self):
        return _daytona_list_sandboxes(self._client, self._labels, limit=1)

    def _ensure_sandbox_ready(self) -> Any:
        sandbox = self._sandbox
        if sandbox is None:
            raise RuntimeError("daytona sandbox is not available")
        refresh = getattr(sandbox, "refresh_data", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
        state = getattr(sandbox, "state", "")
        stopped = {
            getattr(self._SandboxState, "STOPPED", "stopped"),
            getattr(self._SandboxState, "ARCHIVED", "archived"),
            "stopped",
            "archived",
        }
        if state in stopped:
            sandbox.start()
        return sandbox

    def _detect_remote_home(self) -> None:
        self._remote_home = "/home/daytona"
        try:
            response = self._sandbox.process.exec("echo $HOME", timeout=30)
            home = str(getattr(response, "result", "") or "").strip()
            if home:
                self._remote_home = home
                if self.cwd in {"~", "/home/daytona"}:
                    self.cwd = home
        except Exception:
            pass

    def _shell_command(self, command: str, cwd: str, stdin_data: str | None) -> str:
        prefix = (
            f"export AEGIS_TASK_ID={shlex.quote(self.task_id)}; "
            f"cd {shlex.quote(_remote_cwd(cwd))} 2>/dev/null || true; "
        )
        if stdin_data is None:
            return f"{prefix}bash -lc {shlex.quote(command)}"

        marker = f"__AEGIS_STDIN_{uuid.uuid4().hex}__"
        payload = base64.b64encode(str(stdin_data).encode("utf-8")).decode("ascii")
        return "\n".join([
            "__aegis_stdin=$(mktemp /tmp/aegis-stdin.XXXXXX)",
            "trap 'rm -f \"$__aegis_stdin\"' EXIT",
            f"base64 -d > \"$__aegis_stdin\" <<'{marker}'",
            payload,
            marker,
            f"{prefix}bash -lc {shlex.quote(command)} < \"$__aegis_stdin\"",
        ])


def _remote_cwd(cwd: str) -> str:
    if not cwd:
        return "/home/daytona"
    text = str(cwd)
    if text.startswith(("/home/", "/Users/", "C:\\", "C:/")):
        return "/home/daytona"
    return text if text.startswith("/") or text.startswith("~") else "/home/daytona"


def _get_or_create_backend_environment(
    backend: str,
    task_id: str,
    *,
    cwd: str,
    timeout: int,
    factory,
):
    """Return a cached non-local environment for result-storage handoff."""
    key = (backend, task_id)
    stale_env = None
    with _env_lock:
        env = _active_environments.get(key)
        if env is not None:
            _last_activity[key] = time.time()
            _update_environment_cwd(env, cwd)
            return _ensure_interruptible_environment(env)
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
        with _env_lock:
            env = _active_environments.get(key)
            if env is not None:
                _last_activity[key] = time.time()
                _update_environment_cwd(env, cwd)
                return _ensure_interruptible_environment(env)
        env = factory()
        env = _ensure_interruptible_environment(env)
        with _env_lock:
            _active_environments[key] = env
            _last_activity[key] = time.time()
        return env


class _LiveBackendExecution:
    """Best-effort live cancellation state for one environment command."""

    def __init__(self, env: Any) -> None:
        self.env = env
        self.cancelled = False
        self._lock = threading.Lock()
        self._processes: list[Any] = []

    def track_process(self, proc: Any) -> Any:
        should_kill = False
        with self._lock:
            self._processes.append(proc)
            should_kill = self.cancelled
        if should_kill:
            self._kill_process(proc)
        return proc

    def cancel(self) -> None:
        with self._lock:
            first_cancel = not self.cancelled
            self.cancelled = True
            processes = list(self._processes)
        for proc in processes:
            self._kill_process(proc)
        if first_cancel:
            self._call_environment_cancel_hook()

    def _kill_process(self, proc: Any) -> None:
        kill = getattr(self.env, "_kill_process", None)
        if callable(kill):
            try:
                kill(proc)
                return
            except Exception:
                pass
        if _kill_process_group(proc):
            return
        for name in ("terminate", "kill"):
            method = getattr(proc, name, None)
            if callable(method):
                try:
                    method()
                    return
                except Exception:
                    pass

    def _call_environment_cancel_hook(self) -> None:
        for name in ("interrupt", "cancel", "terminate", "kill"):
            hook = getattr(self.env, name, None)
            if not callable(hook):
                continue
            try:
                hook()
                return
            except Exception:
                pass


def _ensure_interruptible_environment(env: Any) -> Any:
    execute = getattr(env, "execute", None)
    if not callable(execute) or getattr(env, "_aegis_interrupt_wrapped", False):
        return env

    def _wrapped_execute(*args: Any, **kwargs: Any) -> Any:
        return _execute_with_interrupt(env, execute, *args, **kwargs)

    try:
        setattr(env, "_aegis_original_execute", execute)
        setattr(env, "execute", _wrapped_execute)
        setattr(env, "_aegis_interrupt_wrapped", True)
    except Exception:
        return env
    return env


def _environment_execute_lock(env: Any) -> threading.RLock:
    lock = getattr(env, "_aegis_execute_lock", None)
    if lock is not None:
        return lock
    with _interrupt_wrap_lock:
        lock = getattr(env, "_aegis_execute_lock", None)
        if lock is None:
            lock = threading.RLock()
            try:
                setattr(env, "_aegis_execute_lock", lock)
            except Exception:
                pass
        return lock


def _execute_with_interrupt(env: Any, execute: Any, *args: Any, **kwargs: Any) -> Any:
    from .interrupt import is_interrupted, register_interrupt_hook

    if is_interrupted():
        return _interrupted_backend_result()

    done = threading.Event()
    result_box: dict[str, Any] = {}
    state = _LiveBackendExecution(env)
    unregister = register_interrupt_hook(state.cancel)

    def _run_execute() -> None:
        try:
            lock = _environment_execute_lock(env)
            with lock:
                if state.cancelled:
                    result_box["result"] = _interrupted_backend_result()
                    return
                _install_process_capture(env, state)
                try:
                    result_box["result"] = _execute_backend_call(
                        env,
                        execute,
                        state,
                        *args,
                        **kwargs,
                    )
                finally:
                    _remove_process_capture(env)
        except BaseException as e:  # noqa: BLE001
            result_box["error"] = e
        finally:
            done.set()

    worker = threading.Thread(
        target=_run_execute,
        daemon=True,
        name=f"aegis-backend-exec-{getattr(env, 'task_id', 'default')}",
    )
    worker.start()
    try:
        while not done.wait(0.05):
            if is_interrupted():
                state.cancel()
                if not done.wait(2.0):
                    return _interrupted_backend_result()
                break
    except KeyboardInterrupt:
        state.cancel()
        if not done.wait(2.0):
            return _interrupted_backend_result()
    finally:
        unregister()

    error = result_box.get("error")
    if error is not None:
        if isinstance(error, KeyboardInterrupt):
            return _interrupted_backend_result()
        raise error
    result = result_box.get("result", {})
    if state.cancelled or is_interrupted():
        return _interrupted_backend_result(result)
    return result


def _execute_backend_call(
    env: Any,
    execute: Any,
    state: _LiveBackendExecution,
    *args: Any,
    **kwargs: Any,
) -> Any:
    env_type = type(env).__name__
    if env_type == "SSHEnvironment":
        return _execute_ssh_with_tracking(env, state, *args, **kwargs)
    if env_type == "SingularityEnvironment":
        return _execute_singularity_with_tracking(env, state, *args, **kwargs)
    return execute(*args, **kwargs)


def _parse_execute_call(env: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str, int, str | None]:
    command = str(args[0] if args else kwargs.get("command", ""))
    cwd = str(args[1] if len(args) > 1 else kwargs.get("cwd", "") or "")
    timeout = kwargs.get("timeout", None)
    if timeout is None:
        timeout = getattr(env, "timeout", 120)
    stdin_data = kwargs.get("stdin_data", None)
    return command, cwd, int(timeout or 120), stdin_data


def _execute_ssh_with_tracking(
    env: Any,
    state: _LiveBackendExecution,
    *args: Any,
    **kwargs: Any,
) -> dict[str, int | str]:
    command, cwd, timeout, stdin_data = _parse_execute_call(env, args, kwargs)
    effective_cwd = cwd or str(getattr(env, "cwd", "") or "")
    remote = (
        f"export AEGIS_TASK_ID={shlex.quote(str(getattr(env, 'task_id', 'default')))}; "
        f"cd {shlex.quote(effective_cwd)} 2>/dev/null; "
        f"bash -c {shlex.quote(command)}"
    )
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    port = str(getattr(env, "port", "") or "")
    if port:
        argv += ["-p", port]
    target = getattr(env, "target", "")
    argv += [str(target), remote]
    proc = _popen_tracked(state, argv, stdin_data=stdin_data)
    return _communicate_tracked(
        state,
        proc,
        timeout=timeout,
        stdin_data=stdin_data,
        timeout_message=f"ssh command timed out after {timeout}s",
    )


def _execute_singularity_with_tracking(
    env: Any,
    state: _LiveBackendExecution,
    *args: Any,
    **kwargs: Any,
) -> dict[str, int | str]:
    command, cwd, timeout, stdin_data = _parse_execute_call(env, args, kwargs)
    effective_cwd = cwd or str(getattr(env, "cwd", "") or "")
    argv = [
        str(getattr(env, "binary", "singularity")),
        "exec",
        "--containall",
        "--writable-tmpfs",
        "--env",
        f"AEGIS_TASK_ID={getattr(env, 'task_id', 'default')}",
        "--bind",
        f"{effective_cwd}:/work",
        "--pwd",
        "/work",
        str(getattr(env, "image", "")),
        "bash",
        "-c",
        command,
    ]
    proc = _popen_tracked(state, argv, stdin_data=stdin_data)
    return _communicate_tracked(
        state,
        proc,
        timeout=timeout,
        stdin_data=stdin_data,
        timeout_message=f"singularity command timed out after {timeout}s",
    )


def _popen_tracked(
    state: _LiveBackendExecution,
    argv: list[str],
    *,
    stdin_data: str | None = None,
) -> subprocess.Popen:
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    if os.name != "nt":
        try:
            proc._aegis_pgid = os.getpgid(proc.pid)
        except Exception:
            pass
    return state.track_process(proc)


def _communicate_tracked(
    state: _LiveBackendExecution,
    proc: subprocess.Popen,
    *,
    timeout: int,
    stdin_data: str | None,
    timeout_message: str,
) -> dict[str, int | str]:
    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        state._kill_process(proc)
        stdout = _output_text(getattr(e, "output", "") or "")
        stderr = _output_text(getattr(e, "stderr", "") or "")
        try:
            out_tail, err_tail = proc.communicate(timeout=2)
            stdout = _output_text(out_tail) or stdout
            stderr = _output_text(err_tail) or stderr
        except Exception:
            pass
        output = _merge_streams(stdout, stderr)
        output = output + f"\n{timeout_message}" if output else timeout_message
        return {"output": output, "returncode": 124}
    except (KeyboardInterrupt, SystemExit):
        state._kill_process(proc)
        raise
    return {
        "output": _merge_streams(_output_text(stdout), _output_text(stderr)),
        "returncode": int(proc.returncode or 0),
    }


def _output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _merge_streams(stdout: str, stderr: str) -> str:
    out = stdout or ""
    if stderr:
        out += ("\n[stderr]\n" + stderr) if out else stderr
    return out


def _kill_process_group(proc: Any) -> bool:
    if os.name == "nt":
        return False
    pgid = getattr(proc, "_aegis_pgid", None)
    if pgid is None:
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    deadline = time.monotonic() + 1.0
    poll = getattr(proc, "poll", None)
    while callable(poll) and time.monotonic() < deadline:
        try:
            if poll() is not None:
                return True
        except Exception:
            break
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    return True


def _install_process_capture(env: Any, state: _LiveBackendExecution) -> None:
    run_bash = getattr(env, "_run_bash", None)
    if not callable(run_bash):
        return
    env_dict = getattr(env, "__dict__", {})
    had_instance_attr = "_run_bash" in env_dict
    previous = env_dict.get("_run_bash") if had_instance_attr else None

    def _wrapped_run_bash(*args: Any, **kwargs: Any) -> Any:
        return state.track_process(run_bash(*args, **kwargs))

    try:
        setattr(env, "_aegis_run_bash_capture", (had_instance_attr, previous))
        setattr(env, "_run_bash", _wrapped_run_bash)
    except Exception:
        pass


def _remove_process_capture(env: Any) -> None:
    sentinel = getattr(env, "_aegis_run_bash_capture", None)
    if sentinel is None:
        return
    had_instance_attr, previous = sentinel
    try:
        if had_instance_attr:
            setattr(env, "_run_bash", previous)
        else:
            delattr(env, "_run_bash")
    except Exception:
        pass
    try:
        delattr(env, "_aegis_run_bash_capture")
    except Exception:
        pass


def _interrupted_backend_result(result: Any | None = None) -> dict[str, int | str]:
    output = ""
    if isinstance(result, dict):
        output = str(result.get("output", "") or "")
    if _INTERRUPTED_OUTPUT.lower() not in output.lower():
        output = (output.rstrip() + "\n" if output else "") + _INTERRUPTED_OUTPUT
    return {"output": output, "returncode": 130}


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
                return _ensure_interruptible_environment(env)
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
                    return _ensure_interruptible_environment(env)
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
        env = _ensure_interruptible_environment(env)
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
    env, error, _backend = create_environment("docker", cwd, timeout, config, task_id)
    if env is None:
        return _degraded(config, error or "docker environment unavailable", command, cwd, timeout, task_id)
    try:
        result = env.execute(command, timeout=timeout)
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
    env, error, _backend = create_environment("ssh", cwd, timeout, config, task_id)
    if env is None:
        return _degraded(config, error or "ssh environment unavailable", command, cwd, timeout, task_id)
    try:
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
    env, error, _backend = create_environment("singularity", cwd, timeout, config, task_id)
    if env is None:
        return _degraded(config, error or "singularity environment unavailable", command, cwd, timeout, task_id)
    try:
        result = env.execute(command, timeout=timeout)
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
    env, error, _backend = create_environment("modal", cwd, timeout, config, task_id)
    if env is None:
        return _degraded(config, error or "modal environment unavailable", command, cwd, timeout, task_id)
    try:
        result = env.execute(command, cwd=cwd, timeout=timeout)
        return str(result.get("output", "")), int(result.get("returncode", 0) or 0)
    except Exception as e:  # noqa: BLE001
        return _degraded(config, f"modal sandbox error ({e})", command, cwd, timeout, task_id)


def _run_daytona(command: str, cwd: str, timeout: int, config: Any,
                 task_id: str | None = None) -> tuple[str, int]:
    env, error, _backend = create_environment("daytona", cwd, timeout, config, task_id)
    if env is None:
        return _degraded(config, error or "daytona environment unavailable", command, cwd, timeout, task_id)
    try:
        result = env.execute(command, cwd=cwd, timeout=timeout)
        return str(result.get("output", "")), int(result.get("returncode", 0) or 0)
    except Exception as e:  # noqa: BLE001
        return _degraded(config, f"daytona sandbox error ({e})", command, cwd, timeout, task_id)


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
