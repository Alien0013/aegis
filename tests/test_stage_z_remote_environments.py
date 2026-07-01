"""Stage Z remote sandbox backend lifecycle behavior."""

from __future__ import annotations

import base64
import io
import json
import logging
import random
import sys
import tarfile
import types
from pathlib import Path

import pytest


class _Config:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default=None):
        return self.values.get(key, default)


class _ExecResponse:
    def __init__(self, result: str = "", exit_code: int = 0) -> None:
        self.result = result
        self.exit_code = exit_code


class _FakeFS:
    def __init__(self, sandbox: "_FakeSandbox") -> None:
        self.sandbox = sandbox
        self.upload_calls: list[list[object]] = []
        self.download_calls: list[tuple[str, str]] = []

    def upload_files(self, uploads: list[object]) -> None:
        self.upload_calls.append(list(uploads))
        for upload in uploads:
            source = Path(str(upload.source))
            destination = str(upload.destination)
            self.sandbox.files[destination] = source.read_bytes()

    def upload_file(self, source: str, destination: str) -> None:
        self.upload_files([types.SimpleNamespace(source=source, destination=destination)])

    def download_file(self, remote_path: str, destination: str) -> None:
        self.download_calls.append((remote_path, destination))
        _write_tar(self.sandbox.download_files, Path(destination))


class _FakeProcess:
    def __init__(self, sandbox: "_FakeSandbox") -> None:
        self.sandbox = sandbox

    def exec(self, command: str, timeout: int | None = None):
        self.sandbox.exec_calls.append({"command": command, "timeout": timeout})
        for needle, response in self.sandbox.fail_exec_contains.items():
            if needle in command:
                return response
        if command == "echo $HOME":
            return _ExecResponse("/home/daytona\n", 0)
        if command.startswith("rm -f "):
            for remote_path in command.removeprefix("rm -f ").split():
                self.sandbox.files.pop(remote_path, None)
        return _ExecResponse(f"ran:{command}", 0)


class _FakeSandbox:
    def __init__(self, sandbox_id: str, state: str = "started") -> None:
        self.id = sandbox_id
        self.state = state
        self.process = _FakeProcess(self)
        self.fs = _FakeFS(self)
        self.exec_calls: list[dict[str, object]] = []
        self.files: dict[str, bytes] = {}
        self.download_files: dict[str, bytes] = {}
        self.fail_exec_contains: dict[str, _ExecResponse] = {}
        self.start_calls = 0
        self.stop_calls = 0
        self.refresh_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self.state = "started"

    def stop(self) -> None:
        self.stop_calls += 1
        self.state = "stopped"

    def refresh_data(self) -> None:
        self.refresh_calls += 1


class _FakeClient:
    def __init__(self, module: types.ModuleType) -> None:
        self.module = module
        self.created: list[object] = []
        self.deleted: list[_FakeSandbox] = []
        self.get_calls: list[str] = []
        self.list_calls: list[dict[str, object]] = []
        self.keyword_list_raises_typeerror = False
        self.get_result: _FakeSandbox | None = None
        self.list_result: list[_FakeSandbox] = []

    def get(self, name: str):
        self.get_calls.append(name)
        if self.get_result is None:
            raise self.module.DaytonaError("not found")
        return self.get_result

    def list(self, *args, labels: dict[str, str] | None = None, limit: int | None = None):
        if args:
            query = args[0]
            labels = getattr(query, "labels", None)
            limit = getattr(query, "limit", None)
            self.list_calls.append({
                "labels": labels,
                "limit": limit,
                "query_type": type(query).__name__,
            })
            return iter(self.list_result)
        if self.keyword_list_raises_typeerror:
            raise TypeError("Daytona.list() got an unexpected keyword argument 'labels'")
        self.list_calls.append({"labels": labels, "limit": limit})
        return iter(self.list_result)

    def create(self, params):
        self.created.append(params)
        sandbox = _FakeSandbox(f"created-{len(self.created)}")
        return sandbox

    def delete(self, sandbox: _FakeSandbox) -> None:
        self.deleted.append(sandbox)


def _install_fake_daytona(monkeypatch):
    module = types.ModuleType("daytona")

    class DaytonaError(Exception):
        pass

    class SandboxState:
        STARTED = "started"
        STOPPED = "stopped"
        ARCHIVED = "archived"

    class Resources:
        def __init__(self, *, cpu: int, memory: int, disk: int) -> None:
            self.cpu = cpu
            self.memory = memory
            self.disk = disk

    class CreateSandboxFromImageParams:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class ListSandboxesQuery:
        def __init__(self, *, labels: dict[str, str] | None = None, limit: int | None = None) -> None:
            self.labels = labels
            self.limit = limit

    module.DaytonaError = DaytonaError
    module.SandboxState = SandboxState
    module.Resources = Resources
    module.CreateSandboxFromImageParams = CreateSandboxFromImageParams
    module.ListSandboxesQuery = ListSandboxesQuery
    client = _FakeClient(module)

    class Daytona:
        def __new__(cls):
            return client

        def create(self, params):
            raise AssertionError("class API stub should not be called")

        def get(self, sandbox_id_or_name: str):
            raise AssertionError("class API stub should not be called")

        def list(self, *, labels: dict[str, str], limit: int):
            raise AssertionError("class API stub should not be called")

        def delete(self, sandbox):
            raise AssertionError("class API stub should not be called")

    module.Daytona = Daytona
    monkeypatch.setitem(sys.modules, "daytona", module)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-key")
    return client


def _write_tar(files: dict[str, bytes], destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


class _FakeModalImage:
    def __init__(self, image_id: str | None = None) -> None:
        self.image_id = image_id
        self.packages = ()

    def pip_install(self, *packages):
        self.packages = packages
        return self


class _FakeModalStream:
    def __init__(self, value=b"") -> None:
        self.value = value

    def read(self):
        return self.value


class _FakeModalStdin:
    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.closed = False

    def write(self, data: str) -> None:
        self.chunks.append(data)

    def write_eof(self) -> None:
        self.closed = True


class _FakeModalProcess:
    def __init__(self, sandbox: "_FakeModalSandbox", command: str) -> None:
        self.sandbox = sandbox
        self.command = command
        self.exit_code = 0
        self.stdin = _FakeModalStdin()
        self.stderr = _FakeModalStream("")
        for needle, response in self.sandbox.fail_exec_contains.items():
            if needle in command:
                self.exit_code, stderr = response
                self.stderr.value = stderr
        self.stdout = _FakeModalStream(self._stdout_for_command(command))

    def _stdout_for_command(self, command: str):
        if "aegis-live-proof:" in command:
            return command
        if "cat /tmp/aegis-results/out.txt" in command:
            return self.sandbox.files.get("/tmp/aegis-results/out.txt", b"")
        if "tar czf - -C /workspace ." in command:
            data = {
                path.removeprefix("/workspace/"): content
                for path, content in self.sandbox.files.items()
                if path.startswith("/workspace/")
            }
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                for name, content in data.items():
                    info = tarfile.TarInfo(name=name)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
            return buffer.getvalue()
        return ""

    def wait(self):
        if "base64 -d | tar xzf - -C /" in self.command:
            payload = "".join(self.stdin.chunks)
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz") as tar:
                for member in tar:
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        self.sandbox.files["/" + member.name] = extracted.read()
        if "cat > /tmp/aegis-results/out.txt" in self.command:
            self.sandbox.files["/tmp/aegis-results/out.txt"] = "".join(self.stdin.chunks).encode()
        return self.exit_code


class _FakeModalSandbox:
    def __init__(
        self,
        create_args: tuple[object, ...],
        create_kwargs: dict[str, object],
        snapshot_id: str,
    ) -> None:
        self.create_args = create_args
        self.create_kwargs = create_kwargs
        self.exec_calls: list[dict[str, object]] = []
        self.processes: list[_FakeModalProcess] = []
        self.files: dict[str, bytes] = {}
        self.fail_exec_contains: dict[str, tuple[int, str]] = {}
        self.terminate_calls = 0
        self.snapshot_id = snapshot_id
        self.snapshot_calls = 0

    def snapshot_filesystem(self):
        self.snapshot_calls += 1
        return types.SimpleNamespace(object_id=self.snapshot_id)

    def exec(self, *args, timeout=None):
        command = str(args[-1])
        self.exec_calls.append({"args": args, "timeout": timeout, "command": command})
        process = _FakeModalProcess(self, command)
        self.processes.append(process)
        return process

    def terminate(self) -> None:
        self.terminate_calls += 1


def _install_fake_modal(
    monkeypatch,
    *,
    fail_snapshot_ids: set[str] | None = None,
    snapshot_id: str = "im-aegis-fresh",
):
    module = types.ModuleType("modal")
    module._last_sandbox = None
    module._create_attempts = []
    module._from_id_calls = []
    module.App = types.SimpleNamespace(
        lookup=lambda *args, **kwargs: types.SimpleNamespace(args=args, kwargs=kwargs)
    )

    def _from_id(image_id: str):
        module._from_id_calls.append(image_id)
        return _FakeModalImage(image_id=image_id)

    module.Image = types.SimpleNamespace(
        debian_slim=lambda: _FakeModalImage(),
        from_id=_from_id,
    )

    class Sandbox:
        @staticmethod
        def create(*args, **kwargs):
            module._create_attempts.append({"args": args, "kwargs": kwargs})
            image = kwargs.get("image")
            image_id = getattr(image, "image_id", None)
            if fail_snapshot_ids and image_id in fail_snapshot_ids:
                raise RuntimeError(f"cannot restore {image_id}")
            sandbox = _FakeModalSandbox(args, kwargs, snapshot_id)
            module._last_sandbox = sandbox
            return sandbox

    module.Sandbox = Sandbox
    monkeypatch.setitem(sys.modules, "modal", module)
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-token-secret")
    return module


def test_remote_backend_diagnostics_report_sdk_credentials_without_live_calls(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-token-secret")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

    calls: list[str] = []
    modal_module = types.ModuleType("modal")
    modal_module.App = types.SimpleNamespace(
        lookup=lambda *args, **kwargs: calls.append("modal.App.lookup")
    )
    modal_module.Image = types.SimpleNamespace(
        debian_slim=lambda: object(),
        from_id=lambda _image_id: object(),
    )
    modal_module.Sandbox = types.SimpleNamespace(
        create=lambda *args, **kwargs: calls.append("modal.Sandbox.create")
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    daytona_config = tmp_path / "daytona.toml"
    daytona_config.write_text('api_key = "configured"\n', encoding="utf-8")
    monkeypatch.setenv("DAYTONA_CONFIG_PATH", str(daytona_config))
    daytona_module = types.ModuleType("daytona")

    class DaytonaError(Exception):
        pass

    class SandboxState:
        STOPPED = "stopped"
        ARCHIVED = "archived"

    class Resources:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class CreateSandboxFromImageParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Daytona:
        def __new__(cls):
            calls.append("daytona.Daytona")
            return object()

        def create(self, params):
            raise AssertionError("not called")

        def get(self, sandbox_id_or_name: str):
            raise AssertionError("not called")

        def list(self, *, labels: dict[str, str], limit: int):
            raise AssertionError("not called")

        def delete(self, sandbox):
            raise AssertionError("not called")

    daytona_module.Daytona = Daytona
    daytona_module.DaytonaError = DaytonaError
    daytona_module.SandboxState = SandboxState
    daytona_module.Resources = Resources
    daytona_module.CreateSandboxFromImageParams = CreateSandboxFromImageParams
    monkeypatch.setitem(sys.modules, "daytona", daytona_module)

    diagnostics = backends.remote_backend_diagnostics()

    assert diagnostics["modal"]["ready"] is True
    assert diagnostics["modal"]["sdk_api_compatible"] is True
    assert diagnostics["modal"]["credential_sources"] == [
        "env:MODAL_TOKEN_ID+MODAL_TOKEN_SECRET"
    ]
    assert diagnostics["modal"]["live_sandbox_started"] is False
    assert diagnostics["daytona"]["ready"] is True
    assert diagnostics["daytona"]["sdk_api_compatible"] is True
    assert diagnostics["daytona"]["credential_available"] is False
    assert diagnostics["daytona"]["config_sources"] == ["env:DAYTONA_CONFIG_PATH"]
    assert diagnostics["daytona"]["live_sandbox_started"] is False
    assert calls == []


def test_installed_remote_sdks_report_credentials_only_blocker_without_live_calls(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("modal")
    pytest.importorskip("daytona")
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_CONFIG_PATH",
        "DAYTONA_API_KEY",
        "DAYTONA_CONFIG_PATH",
        "AEGIS_MODAL_LIVE_PROOF",
        "AEGIS_DAYTONA_LIVE_PROOF",
        "AEGIS_REMOTE_LIVE_PROOF",
        "AEGIS_REMOTE_LIVE_PROOF_BACKENDS",
    ):
        monkeypatch.delenv(name, raising=False)

    diagnostics = backends.remote_backend_diagnostics()
    proof = backends.remote_backend_live_proof(config=_Config({}))

    assert diagnostics["modal"]["sdk_available"] is True
    assert diagnostics["modal"]["ready"] is False
    assert diagnostics["modal"]["missing"] == ["Modal credentials/config not found"]
    assert diagnostics["modal"]["live_sandbox_started"] is False
    assert diagnostics["daytona"]["sdk_available"] is True
    assert diagnostics["daytona"]["ready"] is False
    assert diagnostics["daytona"]["missing"] == ["Daytona credentials/config not found"]
    assert diagnostics["daytona"]["live_sandbox_started"] is False
    assert proof["modal"]["status"] == "blocked"
    assert proof["modal"]["live_sandbox_started"] is False
    assert proof["modal"]["failure_reason"] == (
        "missing prerequisites: Modal credentials/config not found"
    )
    assert proof["daytona"]["status"] == "blocked"
    assert proof["daytona"]["live_sandbox_started"] is False
    assert proof["daytona"]["failure_reason"] == (
        "missing prerequisites: Daytona credentials/config not found"
    )


def test_remote_backend_live_proof_reports_missing_credentials_without_live_calls(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("AEGIS_REMOTE_LIVE_PROOF", raising=False)

    calls: list[str] = []
    modal_module = types.ModuleType("modal")
    modal_module.App = types.SimpleNamespace(
        lookup=lambda *args, **kwargs: calls.append("modal.App.lookup")
    )
    modal_module.Image = types.SimpleNamespace(debian_slim=lambda: object())
    modal_module.Sandbox = types.SimpleNamespace(
        create=lambda *args, **kwargs: calls.append("modal.Sandbox.create")
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    daytona_module = types.ModuleType("daytona")
    daytona_module.Daytona = lambda: calls.append("daytona.Daytona")
    monkeypatch.setitem(sys.modules, "daytona", daytona_module)

    proof = backends.remote_backend_live_proof()

    assert proof["modal"]["status"] == "blocked"
    assert proof["modal"]["live_sandbox_started"] is False
    assert "env:MODAL_TOKEN_ID+MODAL_TOKEN_SECRET" in proof["modal"]["credential_evidence"]["missing"]
    assert proof["daytona"]["status"] == "blocked"
    assert proof["daytona"]["live_sandbox_started"] is False
    assert "env:DAYTONA_API_KEY" in proof["daytona"]["credential_evidence"]["missing"]
    assert calls == []


def test_remote_backend_live_proof_requires_explicit_gate_when_ready(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    monkeypatch.delenv("AEGIS_REMOTE_LIVE_PROOF", raising=False)
    monkeypatch.delenv("AEGIS_MODAL_LIVE_PROOF", raising=False)
    monkeypatch.delenv("AEGIS_DAYTONA_LIVE_PROOF", raising=False)
    modal_module = _install_fake_modal(monkeypatch)
    daytona_client = _install_fake_daytona(monkeypatch)

    proof = backends.remote_backend_live_proof()

    assert proof["modal"]["status"] == "blocked"
    assert "live remote proof is not enabled" in proof["modal"]["failure_reason"]
    assert proof["modal"]["gate"]["enabled"] is False
    assert proof["modal"]["live_sandbox_started"] is False
    assert modal_module._create_attempts == []
    assert proof["daytona"]["status"] == "blocked"
    assert proof["daytona"]["gate"]["enabled"] is False
    assert proof["daytona"]["live_sandbox_started"] is False
    assert daytona_client.created == []


def test_real_remote_sdk_api_shape_is_passive_without_credentials(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("modal")
    pytest.importorskip("daytona")
    from aegis.tools import backends

    home = tmp_path / "home"
    xdg_home = tmp_path / "xdg"
    home.mkdir()
    xdg_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    for name in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_CONFIG_PATH",
        "DAYTONA_API_KEY",
        "DAYTONA_CONFIG_PATH",
        "AEGIS_REMOTE_LIVE_PROOF",
        "AEGIS_MODAL_LIVE_PROOF",
        "AEGIS_DAYTONA_LIVE_PROOF",
    ):
        monkeypatch.delenv(name, raising=False)

    diagnostics = backends.remote_backend_diagnostics()

    assert diagnostics["modal"]["sdk_available"] is True
    assert diagnostics["modal"]["sdk_api_compatible"] is True
    assert "modal.Sandbox.create" in diagnostics["modal"]["sdk_api_shape"]["present"]
    assert diagnostics["modal"]["ready"] is False
    assert diagnostics["modal"]["live_sandbox_started"] is False
    assert diagnostics["daytona"]["sdk_available"] is True
    assert diagnostics["daytona"]["sdk_api_compatible"] is True
    assert diagnostics["daytona"]["sdk_api_shape"]["daytona_list_mode"] in {
        "keywords",
        "query",
    }
    assert diagnostics["daytona"]["ready"] is False
    assert diagnostics["daytona"]["live_sandbox_started"] is False

    def fail_create_environment(*args, **kwargs):
        raise AssertionError("live sandbox creation should not run")

    monkeypatch.setattr(backends, "create_environment", fail_create_environment)
    proof = backends.remote_backend_live_proof()

    assert proof["modal"]["status"] == "blocked"
    assert proof["modal"]["live_sandbox_started"] is False
    assert "Modal credentials/config not found" in proof["modal"]["failure_reason"]
    assert proof["daytona"]["status"] == "blocked"
    assert proof["daytona"]["live_sandbox_started"] is False
    assert "Daytona credentials/config not found" in proof["daytona"]["failure_reason"]


def test_real_remote_sdk_live_proof_requires_gate_with_config_credentials(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("modal")
    pytest.importorskip("daytona")
    from aegis.tools import backends

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text(
        'token_id = "fake-token-id"\ntoken_secret = "fake-token-secret"\n',
        encoding="utf-8",
    )
    daytona_config = tmp_path / "daytona.toml"
    daytona_config.write_text('api_key = "fake-daytona-key"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODAL_CONFIG_PATH", str(modal_config))
    monkeypatch.setenv("DAYTONA_CONFIG_PATH", str(daytona_config))
    for name in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "DAYTONA_API_KEY",
        "AEGIS_REMOTE_LIVE_PROOF",
        "AEGIS_MODAL_LIVE_PROOF",
        "AEGIS_DAYTONA_LIVE_PROOF",
    ):
        monkeypatch.delenv(name, raising=False)

    diagnostics = backends.remote_backend_diagnostics()
    assert diagnostics["modal"]["ready"] is True
    assert diagnostics["daytona"]["ready"] is True

    def fail_create_environment(*args, **kwargs):
        raise AssertionError("live sandbox creation should not run")

    monkeypatch.setattr(backends, "create_environment", fail_create_environment)
    proof = backends.remote_backend_live_proof()

    assert proof["modal"]["status"] == "blocked"
    assert "live remote proof is not enabled" in proof["modal"]["failure_reason"]
    assert proof["modal"]["live_sandbox_started"] is False
    assert proof["daytona"]["status"] == "blocked"
    assert "live remote proof is not enabled" in proof["daytona"]["failure_reason"]
    assert proof["daytona"]["live_sandbox_started"] is False


def test_remote_backend_live_proof_runs_only_when_gated(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    monkeypatch.setenv("AEGIS_REMOTE_LIVE_PROOF", "1")
    modal_module = _install_fake_modal(monkeypatch)
    daytona_client = _install_fake_daytona(monkeypatch)

    proof = backends.remote_backend_live_proof(timeout=5)

    assert proof["modal"]["status"] == "passed"
    assert proof["modal"]["live_sandbox_started"] is True
    assert "aegis-live-proof:modal:" in proof["modal"]["output_excerpt"]
    assert modal_module._last_sandbox.terminate_calls == 1
    assert proof["daytona"]["status"] == "passed"
    assert proof["daytona"]["live_sandbox_started"] is True
    assert "aegis-live-proof:daytona:" in proof["daytona"]["output_excerpt"]
    assert len(daytona_client.deleted) == 1


def test_remote_backend_live_proof_cleans_up_modal_after_failed_initial_sync(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    monkeypatch.setenv("AEGIS_MODAL_LIVE_PROOF", "1")
    modal_module = _install_fake_modal(monkeypatch)

    def fail_sync_to_remote(self, *, force: bool = False):
        raise RuntimeError("initial sync rejected test-token-secret")

    monkeypatch.setattr(backends._RemoteWorkspaceSync, "sync_to_remote", fail_sync_to_remote)

    proof = backends.remote_backend_live_proof("modal", timeout=5)

    assert proof["modal"]["status"] == "failed"
    assert proof["modal"]["live_sandbox_started"] is False
    assert "[redacted:MODAL_TOKEN_SECRET]" in proof["modal"]["failure_reason"]
    assert "test-token-secret" not in proof["modal"]["failure_reason"]
    assert modal_module._last_sandbox.terminate_calls == 1


def test_remote_backend_live_proof_cleans_up_daytona_after_failed_initial_sync(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    monkeypatch.setenv("AEGIS_DAYTONA_LIVE_PROOF", "1")
    daytona_client = _install_fake_daytona(monkeypatch)

    def fail_sync_to_remote(self, *, force: bool = False):
        raise RuntimeError("initial sync rejected test-key")

    monkeypatch.setattr(backends._RemoteWorkspaceSync, "sync_to_remote", fail_sync_to_remote)

    proof = backends.remote_backend_live_proof("daytona", timeout=5)

    assert proof["daytona"]["status"] == "failed"
    assert proof["daytona"]["live_sandbox_started"] is False
    assert "[redacted:DAYTONA_API_KEY]" in proof["daytona"]["failure_reason"]
    assert "test-key" not in proof["daytona"]["failure_reason"]
    assert len(daytona_client.deleted) == 1


def test_modal_backend_fails_closed_without_credentials_before_live_lookup(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MODAL_CONFIG_PATH", raising=False)

    calls: list[str] = []
    modal_module = types.ModuleType("modal")
    modal_module.App = types.SimpleNamespace(
        lookup=lambda *args, **kwargs: calls.append("modal.App.lookup")
    )
    modal_module.Image = types.SimpleNamespace(debian_slim=lambda: object())
    modal_module.Sandbox = types.SimpleNamespace(
        create=lambda *args, **kwargs: calls.append("modal.Sandbox.create")
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    env, error, backend = backends.create_environment(
        "modal",
        str(tmp_path),
        30,
        _Config(),
        task_id="stage_z_modal_missing_credentials",
    )

    assert env is None
    assert backend == "modal"
    assert "modal credentials/config not found" in error
    assert calls == []


def test_modal_backend_fails_closed_on_incompatible_sdk_before_live_lookup(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-token-secret")

    calls: list[str] = []
    modal_module = types.ModuleType("modal")
    modal_module.App = types.SimpleNamespace(
        lookup=lambda *args, **kwargs: calls.append("modal.App.lookup")
    )
    modal_module.Image = types.SimpleNamespace(debian_slim=lambda: object())
    modal_module.Sandbox = types.SimpleNamespace(
        create=lambda *args, **kwargs: calls.append("modal.Sandbox.create")
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    env, error, backend = backends.create_environment(
        "modal",
        str(tmp_path),
        30,
        _Config(),
        task_id="stage_z_modal_bad_sdk_shape",
    )

    assert env is None
    assert backend == "modal"
    assert "modal SDK API incompatible" in error
    assert "modal.Image.from_id" in error
    assert calls == []


def test_modal_backend_redacts_creation_error_without_local_fallback(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-token-secret")

    modal_module = types.ModuleType("modal")

    def lookup(*args, **kwargs):
        raise RuntimeError("Modal auth rejected test-token-secret")

    modal_module.App = types.SimpleNamespace(lookup=lookup)
    modal_module.Image = types.SimpleNamespace(
        debian_slim=lambda: object(),
        from_id=lambda _image_id: object(),
    )
    modal_module.Sandbox = types.SimpleNamespace(
        create=lambda *args, **kwargs: object()
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    out, code = backends.run_command(
        "printf local-ran",
        str(tmp_path),
        10,
        "modal",
        _Config(),
        task_id="stage_z_modal_creation_error_redacted",
    )

    assert code == 126
    assert "modal sandbox error" in out
    assert "[redacted:MODAL_TOKEN_SECRET]" in out
    assert "test-token-secret" not in out
    assert "local-ran" not in out
    assert "Refusing to run on the host" in out


def test_daytona_backend_fails_closed_without_api_key(tmp_path, monkeypatch):
    from aegis.tools import backends

    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "daytona", raising=False)

    out, code = backends.run_command(
        "echo hi",
        str(tmp_path),
        10,
        "daytona",
        _Config(),
        task_id="stage_z_daytona_missing_key",
    )

    assert code == 126
    assert "daytona backend is not configured in AEGIS" in out
    assert "DAYTONA_API_KEY not set" in out
    assert "Refusing to run on the host" in out


def test_daytona_backend_fails_closed_on_incompatible_sdk_before_client_creation(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("DAYTONA_API_KEY", "test-key")
    calls: list[str] = []
    daytona_module = types.ModuleType("daytona")

    class DaytonaError(Exception):
        pass

    class SandboxState:
        STOPPED = "stopped"
        ARCHIVED = "archived"

    class Resources:
        def __init__(self, *, cpu: int, memory: int, disk: int) -> None:
            self.cpu = cpu
            self.memory = memory
            self.disk = disk

    class Daytona:
        def __new__(cls):
            calls.append("daytona.Daytona")
            return object()

        def create(self, params):
            raise AssertionError("not called")

        def get(self, sandbox_id_or_name: str):
            raise AssertionError("not called")

        def list(self, *, labels: dict[str, str], limit: int):
            raise AssertionError("not called")

        def delete(self, sandbox):
            raise AssertionError("not called")

    daytona_module.Daytona = Daytona
    daytona_module.DaytonaError = DaytonaError
    daytona_module.SandboxState = SandboxState
    daytona_module.Resources = Resources
    monkeypatch.setitem(sys.modules, "daytona", daytona_module)

    env, error, backend = backends.create_environment(
        "daytona",
        str(tmp_path),
        30,
        _Config(),
        task_id="stage_z_daytona_bad_sdk_shape",
    )

    assert env is None
    assert backend == "daytona"
    assert "daytona SDK API incompatible" in error
    assert "daytona.CreateSandboxFromImageParams" in error
    assert calls == []


def test_daytona_backend_reports_creation_error_without_local_fallback(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("DAYTONA_API_KEY", "daytona-secret-key")
    daytona_module = types.ModuleType("daytona")

    class DaytonaError(Exception):
        pass

    class SandboxState:
        STOPPED = "stopped"
        ARCHIVED = "archived"

    class Resources:
        def __init__(self, *, cpu: int, memory: int, disk: int) -> None:
            self.cpu = cpu
            self.memory = memory
            self.disk = disk

    class CreateSandboxFromImageParams:
        def __init__(
            self,
            *,
            image: str,
            name: str,
            labels: dict[str, str],
            auto_stop_interval: int,
            resources: Resources,
        ) -> None:
            self.kwargs = {
                "image": image,
                "name": name,
                "labels": labels,
                "auto_stop_interval": auto_stop_interval,
                "resources": resources,
            }

    class Daytona:
        def __init__(self) -> None:
            raise RuntimeError("Daytona auth rejected daytona-secret-key")

        def create(self, params):
            raise AssertionError("not called")

        def get(self, sandbox_id_or_name: str):
            raise AssertionError("not called")

        def list(self, *, labels: dict[str, str], limit: int):
            raise AssertionError("not called")

        def delete(self, sandbox):
            raise AssertionError("not called")

    daytona_module.Daytona = Daytona
    daytona_module.DaytonaError = DaytonaError
    daytona_module.SandboxState = SandboxState
    daytona_module.Resources = Resources
    daytona_module.CreateSandboxFromImageParams = CreateSandboxFromImageParams
    monkeypatch.setitem(sys.modules, "daytona", daytona_module)

    out, code = backends.run_command(
        "printf local-ran",
        str(tmp_path),
        10,
        "daytona",
        _Config(),
        task_id="stage_z_daytona_creation_error_redacted",
    )

    assert code == 126
    assert "daytona sandbox error" in out
    assert "[redacted:DAYTONA_API_KEY]" in out
    assert "daytona-secret-key" not in out
    assert "local-ran" not in out
    assert "Refusing to run on the host" in out


def test_daytona_persistent_backend_resumes_named_sandbox_and_stops_on_cleanup(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    client = _install_fake_daytona(monkeypatch)
    existing = _FakeSandbox("existing-1", state="stopped")
    client.get_result = existing
    task_id = "stage_z_daytona_resume"

    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        again, again_error, _ = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )

        assert error == ""
        assert again_error == ""
        assert backend == "daytona"
        assert env is again
        assert client.get_calls == ["aegis-stage_z_daytona_resume"]
        assert client.created == []
        assert existing.start_calls == 1
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")

    assert existing.stop_calls == 1
    assert client.deleted == []


def test_daytona_persistent_backend_resumes_legacy_labelled_sandbox(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    client = _install_fake_daytona(monkeypatch)
    legacy = _FakeSandbox("legacy-1", state="stopped")
    client.list_result = [legacy]
    task_id = "stage_z_daytona_legacy"

    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )

        assert env is not None
        assert error == ""
        assert backend == "daytona"
        assert client.created == []
        assert client.list_calls == [
            {"labels": {"aegis_task_id": task_id}, "limit": 1}
        ]
        assert legacy.start_calls == 1
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_legacy_resume_supports_query_object_list_signature(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    client = _install_fake_daytona(monkeypatch)
    client.keyword_list_raises_typeerror = True
    legacy = _FakeSandbox("legacy-query-1", state="stopped")
    client.list_result = [legacy]
    task_id = "stage_z_daytona_query_list"

    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )

        assert env is not None
        assert error == ""
        assert backend == "daytona"
        assert client.created == []
        assert client.list_calls == [
            {
                "labels": {"aegis_task_id": task_id},
                "limit": 1,
                "query_type": "ListSandboxesQuery",
            }
        ]
        assert legacy.start_calls == 1
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_persistent_backend_resumes_legacy_labelled_sandbox_with_query_list_api(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    client = _install_fake_daytona(monkeypatch)
    client.keyword_list_raises_typeerror = True
    legacy = _FakeSandbox("legacy-query-1", state="stopped")
    client.list_result = [legacy]
    task_id = "stage_z_daytona_legacy_query"

    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )

        assert env is not None
        assert error == ""
        assert backend == "daytona"
        assert client.created == []
        assert client.list_calls == [
            {
                "labels": {"aegis_task_id": task_id},
                "limit": 1,
                "query_type": "ListSandboxesQuery",
            }
        ]
        assert legacy.start_calls == 1
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_nonpersistent_backend_creates_and_deletes_sandbox(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    client = _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_ephemeral"

    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config({"tools.daytona_persistent": False}),
            task_id=task_id,
        )

        assert env is not None
        assert error == ""
        assert backend == "daytona"
        assert client.get_calls == []
        assert client.list_calls == []
        assert len(client.created) == 1
        params = client.created[0]
        assert params.kwargs["name"] == "aegis-stage_z_daytona_ephemeral"
        assert params.kwargs["labels"] == {"aegis_task_id": task_id}
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")

    assert len(client.deleted) == 1
    assert client.deleted[0].id == "created-1"


def test_daytona_execute_restarts_stopped_sandbox_and_wraps_stdin(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_execute"

    try:
        env, error, _ = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        sandbox = env._sandbox
        sandbox.state = "stopped"

        result = env.execute("cat > /tmp/payload.txt", stdin_data="secret payload")

        assert result["returncode"] == 0
        assert sandbox.refresh_calls == 1
        assert sandbox.start_calls == 1
        command = sandbox.exec_calls[-1]["command"]
        assert "base64 -d" in command
        assert "cat > /tmp/payload.txt" in command
        assert "secret payload" not in command
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_workspace_sync_uploads_and_syncs_remote_changes_back(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    (tmp_path / "input.txt").write_text("local v1", encoding="utf-8")
    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_workspace_sync"

    env = None
    try:
        env, error, backend = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )

        sandbox = env._sandbox
        assert error == ""
        assert backend == "daytona"
        assert env.cwd == "/workspace"
        assert sandbox.files["/workspace/input.txt"] == b"local v1"
        assert sandbox.fs.upload_calls

        sandbox.download_files = {
            "input.txt": b"remote v2",
            "nested/new.txt": b"created remotely",
        }
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")

    assert (tmp_path / "input.txt").read_text(encoding="utf-8") == "remote v2"
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "created remotely"


def test_daytona_workspace_sync_deletes_remote_file_removed_locally(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    target = tmp_path / "obsolete.txt"
    target.write_text("remove me", encoding="utf-8")
    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_workspace_delete"

    try:
        env, error, _ = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        assert "/workspace/obsolete.txt" in env._sandbox.files

        target.unlink()
        env._sync._sync_interval = 0
        env.execute("pwd", cwd=str(tmp_path))

        assert any(
            "rm -f /workspace/obsolete.txt" in str(call["command"])
            for call in env._sandbox.exec_calls
        )
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_bulk_upload_process_fallback_surfaces_tar_failure(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    host_file = tmp_path / "payload.txt"
    host_file.write_text("payload", encoding="utf-8")
    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_upload_fallback_error"

    try:
        env, error, _ = backends.create_environment(
            "daytona",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        sandbox = env._sandbox
        sandbox.fs.upload_files = None
        sandbox.fs.upload_file = None
        sandbox.fail_exec_contains["base64 -d"] = _ExecResponse("tar exploded", 2)

        with pytest.raises(RuntimeError, match="daytona bulk upload failed: tar exploded"):
            env._daytona_bulk_upload([(str(host_file), "/workspace/payload.txt")])
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_bulk_upload_surfaces_mkdir_failure(tmp_path, monkeypatch):
    from aegis.tools import backends

    host_file = tmp_path / "payload.txt"
    host_file.write_text("payload", encoding="utf-8")
    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_upload_mkdir_error"

    try:
        env, error, _ = backends.create_environment(
            "daytona",
            "/",
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        env._sandbox.fail_exec_contains["mkdir -p"] = _ExecResponse("mkdir denied", 1)

        with pytest.raises(RuntimeError, match="daytona bulk upload failed: mkdir denied"):
            env._daytona_bulk_upload([(str(host_file), "/workspace/payload.txt")])
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_daytona_bulk_upload_falls_back_when_batch_api_raises(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    host_file = tmp_path / "payload.txt"
    host_file.write_text("payload", encoding="utf-8")
    _install_fake_daytona(monkeypatch)
    task_id = "stage_z_daytona_upload_batch_fallback"

    try:
        env, error, _ = backends.create_environment(
            "daytona",
            "/",
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        sandbox = env._sandbox
        sandbox.fs.upload_files = lambda _uploads: (_ for _ in ()).throw(RuntimeError("batch failed"))
        sandbox.fs.upload_file = lambda source, dest: sandbox.files.__setitem__(
            dest,
            Path(source).read_bytes(),
        )

        env._daytona_bulk_upload([(str(host_file), "/workspace/payload.txt")])

        assert sandbox.files["/workspace/payload.txt"] == b"payload"
    finally:
        backends.cleanup_task_environment(task_id, backend="daytona")


def test_remote_workspace_sync_logs_sync_back_retry_failure(
    tmp_path,
    caplog,
):
    from aegis.tools import backends

    source = tmp_path / "source.txt"
    source.write_text("local", encoding="utf-8")
    attempts = 0

    def _download(_dest: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"download failed {attempts}")

    sync = backends._RemoteWorkspaceSync(
        host_root=tmp_path,
        remote_root="/workspace",
        bulk_upload_fn=lambda _files: None,
        bulk_download_fn=_download,
        delete_fn=lambda _paths: None,
    )
    sync.sync_to_remote(force=True)

    with caplog.at_level(logging.WARNING, logger="aegis.tools.backends"):
        sync.sync_back()

    assert attempts == 3
    assert any("remote sync-back attempt 1 failed" in record.message for record in caplog.records)
    assert any("remote sync-back failed after 3 attempts" in record.message for record in caplog.records)


def test_modal_workspace_sync_and_persisted_remote_output_handoff(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    (tmp_path / "script.py").write_text("print('hello')", encoding="utf-8")
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    module = _install_fake_modal(monkeypatch)
    task_id = "stage_z_modal_workspace_sync"

    try:
        env, error, backend = backends.create_environment(
            "modal",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        sandbox = module._last_sandbox

        assert error == ""
        assert backend == "modal"
        assert sandbox.create_args[:2] == ("sleep", "infinity")
        assert env.cwd == "/workspace"
        assert sandbox.files["/workspace/script.py"] == b"print('hello')"

        write = env.execute(
            "mkdir -p /tmp/aegis-results && cat > /tmp/aegis-results/out.txt",
            cwd=str(tmp_path),
            stdin_data="full remote output",
        )
        read = env.execute("cat /tmp/aegis-results/out.txt", cwd=str(tmp_path))

        assert write["returncode"] == 0
        assert read["output"] == "full remote output"
        assert sandbox.terminate_calls == 0
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")

    assert sandbox.terminate_calls == 1


def test_modal_bulk_upload_streams_chunked_payload_outside_command(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    large_file = tmp_path / "large.bin"
    large_content = random.Random(0).randbytes(backends._ModalSandboxEnvironment._STDIN_CHUNK_SIZE + 512 * 1024)
    large_file.write_bytes(large_content)
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    module = _install_fake_modal(monkeypatch)
    task_id = "stage_z_modal_bulk_upload_chunks"

    try:
        env, error, backend = backends.create_environment(
            "modal",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        sandbox = module._last_sandbox

        assert error == ""
        assert backend == "modal"
        assert env is not None
        upload_processes = [
            process
            for process in sandbox.processes
            if "base64 -d | tar xzf - -C /" in process.command
        ]
        assert upload_processes
        process = upload_processes[0]
        payload = "".join(process.stdin.chunks)

        assert len(process.stdin.chunks) >= 2
        assert process.stdin.closed
        assert "echo" not in process.command
        assert payload not in process.command
        assert sandbox.files["/workspace/large.bin"] == large_content
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")


def test_modal_bulk_upload_supports_drain_aio_without_embedding_payload(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    host_file = tmp_path / "payload.txt"
    host_file.write_text("payload", encoding="utf-8")
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    module = _install_fake_modal(monkeypatch)
    task_id = "stage_z_modal_bulk_upload_drain_aio"

    class _DrainOnly:
        def __init__(self) -> None:
            self.calls = 0

        async def _aio(self) -> None:
            self.calls += 1

        def aio(self):
            return self._aio()

    try:
        env, error, _ = backends.create_environment(
            "modal",
            "/",
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        sandbox = module._last_sandbox

        env._modal_bulk_upload([(str(host_file), "/workspace/payload.txt")])
        process = [
            p for p in sandbox.processes if "base64 -d | tar xzf - -C /" in p.command
        ][0]
        drain = _DrainOnly()
        process.stdin.drain = drain

        env._write_modal_stdin(process.stdin, "x" * (env._STDIN_CHUNK_SIZE + 1))

        assert drain.calls == 2
        assert process.stdin.closed
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")


def test_modal_bulk_upload_surfaces_remote_stderr_on_failure(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    host_file = tmp_path / "payload.txt"
    host_file.write_text("payload", encoding="utf-8")
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    module = _install_fake_modal(monkeypatch)
    task_id = "stage_z_modal_bulk_upload_error"

    try:
        env, error, _ = backends.create_environment(
            "modal",
            "/",
            30,
            _Config(),
            task_id=task_id,
        )
        assert error == ""
        module._last_sandbox.fail_exec_contains["base64 -d | tar xzf"] = (
            2,
            "tar: bad archive",
        )

        with pytest.raises(RuntimeError, match="modal bulk upload failed: tar: bad archive"):
            env._modal_bulk_upload([(str(host_file), "/workspace/payload.txt")])
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")


def test_modal_snapshot_restore_migrates_legacy_key_and_saves_cleanup_snapshot(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    store = Path(tmp_path / "aegis-home" / "modal_snapshots.json")
    store.parent.mkdir(parents=True, exist_ok=True)
    task_id = "stage_z_modal_snapshot_restore"
    store.write_text(json.dumps({task_id: "im-legacy123"}), encoding="utf-8")
    module = _install_fake_modal(monkeypatch, snapshot_id="im-cleanup456")

    try:
        env, error, backend = backends.create_environment(
            "modal",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        sandbox = module._last_sandbox

        assert error == ""
        assert backend == "modal"
        assert env is not None
        assert module._from_id_calls == ["im-legacy123"]
        assert sandbox.create_kwargs["image"].image_id == "im-legacy123"
        assert json.loads(store.read_text(encoding="utf-8")) == {
            f"direct:{task_id}": "im-legacy123"
        }
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")

    assert sandbox.snapshot_calls == 1
    assert sandbox.terminate_calls == 1
    assert json.loads(store.read_text(encoding="utf-8")) == {
        f"direct:{task_id}": "im-cleanup456"
    }


def test_modal_stale_snapshot_is_pruned_and_base_image_is_used(
    tmp_path,
    monkeypatch,
):
    from aegis.tools import backends

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    store = Path(tmp_path / "aegis-home" / "modal_snapshots.json")
    store.parent.mkdir(parents=True, exist_ok=True)
    task_id = "stage_z_modal_stale_snapshot"
    store.write_text(json.dumps({f"direct:{task_id}": "im-stale123"}), encoding="utf-8")
    module = _install_fake_modal(monkeypatch, fail_snapshot_ids={"im-stale123"})

    try:
        env, error, backend = backends.create_environment(
            "modal",
            str(tmp_path),
            30,
            _Config(),
            task_id=task_id,
        )
        sandbox = module._last_sandbox

        assert error == ""
        assert backend == "modal"
        assert env is not None
        assert module._from_id_calls == ["im-stale123"]
        assert [getattr(attempt["kwargs"]["image"], "image_id", None) for attempt in module._create_attempts] == [
            "im-stale123",
            None,
        ]
        assert json.loads(store.read_text(encoding="utf-8")) == {}
        assert sandbox.create_kwargs["image"].image_id is None
    finally:
        backends.cleanup_task_environment(task_id, backend="modal")
