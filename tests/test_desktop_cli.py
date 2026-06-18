from __future__ import annotations

import json
import re
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from aegis import desktop
from aegis.cli.main import build_parser


def _write_desktop_template(root: Path) -> Path:
    root.mkdir()
    for name in desktop.DESKTOP_FILES:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{name}\n", encoding="utf-8")
    return root


def test_desktop_install_only_syncs_and_installs(monkeypatch, tmp_path):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    calls: list[dict] = []

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    def fake_run(cmd, cwd, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        (cwd / "node_modules" / "electron").mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)

    args = Namespace(install_only=True, reinstall=False, sandbox=False)
    assert desktop.cmd_desktop(args, object()) == 0

    assert calls == [{"cmd": ["/usr/bin/npm", "ci"], "cwd": target, "env": None}]
    for name in desktop.DESKTOP_FILES:
        assert (target / name).read_text(encoding="utf-8") == f"{name}\n"


def test_desktop_status_reports_bootstrap_without_running_npm(monkeypatch, tmp_path, capsys):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    calls: list[dict] = []

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: calls.append({"args": a, "kwargs": k}))

    args = Namespace(status=True, install_only=False, reinstall=False, sandbox=False)
    assert desktop.cmd_desktop(args, object()) == 0

    body = json.loads(capsys.readouterr().out)
    assert calls == []
    assert body["ok"] is True
    assert body["target"] == str(target)
    assert body["dependencies_installed"] is False
    assert body["package_lock"] is False
    assert body["install_command"] == ["/usr/bin/npm", "ci"]


def test_desktop_npm_install_command_uses_lockfile(tmp_path):
    assert desktop._npm_install_command("/usr/bin/npm", tmp_path) == ["/usr/bin/npm", "install"]
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    assert desktop._npm_install_command("/usr/bin/npm", tmp_path) == ["/usr/bin/npm", "ci"]


def test_desktop_launch_skips_install_when_dependencies_exist(monkeypatch, tmp_path):
    from aegis import config as cfg

    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    desktop._sync_desktop_app(source, target)
    (target / "node_modules" / "electron").mkdir(parents=True)
    calls: list[dict] = []

    def fake_which(name):
        return {("npm"): "/usr/bin/npm", ("aegis"): "/usr/local/bin/aegis"}.get(name)

    def fake_run(cmd, cwd, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        if cmd == ["/usr/bin/npm", "run", "pack"]:
            exe = desktop._unpacked_executable(target)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.delenv("AEGIS_BIN", raising=False)
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", fake_which)
    monkeypatch.setattr(desktop.sys, "argv", ["aegis"])
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    monkeypatch.chdir(project)

    args = Namespace(install_only=False, reinstall=False, sandbox=False, source=False, cwd=None)
    assert desktop.cmd_desktop(args, object()) == 0

    assert [call["cmd"] for call in calls] == [
        ["/usr/bin/npm", "run", "pack"],
        desktop._packaged_launch_command(target),
    ]
    assert calls[0]["cwd"] == target
    assert calls[1]["cwd"] == target
    assert calls[1]["env"]["AEGIS_BIN"] == "/usr/local/bin/aegis"
    assert calls[1]["env"]["AEGIS_HOME"] == str(cfg.get_home())
    assert calls[1]["env"]["TERMINAL_CWD"] == str(project)


def test_desktop_source_launch_accepts_explicit_cwd(monkeypatch, tmp_path):
    from aegis import config as cfg

    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    desktop._sync_desktop_app(source, target)
    (target / "node_modules" / "electron").mkdir(parents=True)
    calls: list[dict] = []

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else "/usr/local/bin/aegis")
    monkeypatch.setattr(desktop.subprocess, "run", lambda cmd, cwd, env=None: calls.append({
        "cmd": cmd,
        "cwd": cwd,
        "env": env,
    }) or SimpleNamespace(returncode=0))

    args = Namespace(install_only=False, reinstall=False, sandbox=False, source=True, cwd=str(project))
    assert desktop.cmd_desktop(args, object()) == 0

    assert calls[0]["cmd"] == ["/usr/bin/npm", "start"]
    assert calls[0]["env"]["AEGIS_HOME"] == str(cfg.get_home())
    assert calls[0]["env"]["TERMINAL_CWD"] == str(project)


def test_desktop_launch_preserves_explicit_aegis_home(monkeypatch, tmp_path):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    project = tmp_path / "project"
    explicit_home = tmp_path / "explicit-home"
    project.mkdir()
    desktop._sync_desktop_app(source, target)
    (target / "node_modules" / "electron").mkdir(parents=True)
    calls: list[dict] = []

    def fake_run(cmd, cwd, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        if cmd == ["/usr/bin/npm", "run", "pack"]:
            exe = desktop._unpacked_executable(target)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.setenv("AEGIS_HOME", str(explicit_home))
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else "/usr/local/bin/aegis")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    monkeypatch.chdir(project)

    args = Namespace(install_only=False, reinstall=False, sandbox=False, source=False, cwd=None)
    assert desktop.cmd_desktop(args, object()) == 0

    assert calls[-1]["env"]["AEGIS_HOME"] == str(explicit_home)
    assert calls[-1]["env"]["TERMINAL_CWD"] == str(project)


def test_desktop_launch_rejects_invalid_cwd(monkeypatch, tmp_path, capsys):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    desktop._sync_desktop_app(source, target)
    (target / "node_modules" / "electron").mkdir(parents=True)

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else "/usr/local/bin/aegis")

    args = Namespace(install_only=False, reinstall=False, sandbox=False, cwd=str(tmp_path / "missing"))
    assert desktop.cmd_desktop(args, object()) == 1
    assert "desktop cwd not found" in capsys.readouterr().err


def test_desktop_sync_cleans_only_previous_managed_files(tmp_path):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    (target / "electron").mkdir(parents=True)
    (target / "electron" / "old-main.js").write_text("stale", encoding="utf-8")
    (target / "user-note.txt").write_text("keep me", encoding="utf-8")
    (target / "node_modules" / "electron").mkdir(parents=True)
    (target / desktop.DESKTOP_MANIFEST).write_text(
        json.dumps({"schema_version": 1, "files": ["electron/old-main.js", "package.json"]}),
        encoding="utf-8",
    )

    changed = desktop._sync_desktop_app(source, target)

    assert changed is True
    assert not (target / "electron" / "old-main.js").exists()
    assert (target / "user-note.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "node_modules" / "electron").is_dir()
    manifest = json.loads((target / desktop.DESKTOP_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["files"] == sorted(desktop.DESKTOP_FILES)


def test_desktop_parser_and_typo_alias():
    parser = build_parser()
    args = parser.parse_args(["desktop", "--install-only"])
    assert args.func is desktop.cmd_desktop
    assert args.install_only is True

    status_args = parser.parse_args(["desktop", "--status"])
    assert status_args.func is desktop.cmd_desktop
    assert status_args.status is True

    cwd_args = parser.parse_args(["desktop", "--source", "--cwd", "/tmp/project"])
    assert cwd_args.func is desktop.cmd_desktop
    assert cwd_args.source is True
    assert cwd_args.cwd == "/tmp/project"

    typo_args = parser.parse_args(["deksktop", "--install-only", "--status"])
    assert typo_args.func is desktop.cmd_desktop
    assert typo_args.status is True


def test_bundled_desktop_template_matches_source():
    root = Path(__file__).resolve().parents[1]
    for name in desktop.DESKTOP_FILES:
        assert (root / "aegis" / "desktop_app" / name).read_bytes() == (
            root / "desktop" / name
        ).read_bytes()


def test_desktop_sync_manifest_tracks_main_cjs_requires():
    root = Path(__file__).resolve().parents[1]
    main_js = (root / "desktop" / "electron" / "main.js").read_text(encoding="utf-8")
    required = {
        f"electron/{match}"
        for match in re.findall(r"""require\(["']\./([^"']+\.cjs)["']\)""", main_js)
    }

    assert required <= set(desktop.DESKTOP_FILES)


def test_desktop_builder_config_matches_release_parity():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "desktop" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
    bundled = json.loads((root / "aegis" / "desktop_app" / "package.json").read_text(encoding="utf-8"))

    assert bundled == package
    build = package["build"]
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    for section in ("dependencies", "devDependencies"):
        for dep_name, spec in package.get(section, {}).items():
            assert spec == lock["packages"][f"node_modules/{dep_name}"]["version"]
    assert package["scripts"]["pack"].endswith("npm run builder -- --dir")
    assert build["executableName"] == "AEGIS"
    assert build["electronVersion"] == "33.4.11"
    assert package["devDependencies"]["electron"] == build["electronVersion"]
    assert build["protocols"] == [{"name": "AEGIS Protocol", "schemes": ["aegis"]}]
    assert build["beforeBuild"] == "scripts/before-build.cjs"
    assert "electron/api-proxy.cjs" in desktop.DESKTOP_FILES
    assert "electron/api-proxy.test.cjs" in desktop.DESKTOP_FILES
    assert "scripts/write-build-stamp.cjs" in desktop.DESKTOP_FILES
    assert "scripts/stage-backend.cjs" in desktop.DESKTOP_FILES
    assert "electron/desktop-status.cjs" in desktop.DESKTOP_FILES
    assert "electron/updater-status.cjs" in desktop.DESKTOP_FILES
    assert "electron/updater-status.test.cjs" in desktop.DESKTOP_FILES
    assert "build/icon.ico" in desktop.DESKTOP_FILES
    resources = {entry["to"]: entry["from"] for entry in build["extraResources"]}
    assert resources["install-stamp.json"] == "build/install-stamp.json"
    assert resources["backend-manifest.json"] == "build/backend-manifest.json"
    assert resources["backend"] == "build/backend"
    assert "msi" in build["win"]["target"]
    assert build["win"]["signAndEditExecutable"] is False
    assert build["nsis"]["warningsAsErrors"] is False
    assert "rpm" in build["linux"]["target"]
    assert package["scripts"]["build:prepare"] == "npm run build:stamp && npm run build:backend"
    assert "build:prepare" in package["scripts"]["dist:win"]
    assert "msi" in package["scripts"]["dist:win"]
    assert "build:prepare" in package["scripts"]["dist:linux"]
    assert "rpm" in package["scripts"]["dist:linux"]


def test_release_workflow_builds_linux_desktop_artifacts():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "desktop-linux:" in workflow
    assert "cache-dependency-path: desktop/package-lock.json" in workflow
    assert "working-directory: desktop" in workflow
    assert "run: npm ci" in workflow
    assert "run: npm run dist:linux" in workflow
    assert 'AEGIS_RELEASE: "1"' in workflow
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in workflow
    assert "desktop/release/*" in workflow
    assert "softprops/action-gh-release@v2" in workflow
