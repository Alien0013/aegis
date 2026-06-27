"""Guards that the things users rely on at install time actually ship + resolve."""

from __future__ import annotations

import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_root_package_json_exposes_frontend_workspaces_and_scripts():
    package_path = ROOT / "package.json"
    assert package_path.exists(), "root package.json should expose AEGIS npm surfaces"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    workspaces = package.get("workspaces") or []
    for workspace in ("web", "desktop", "aegis/tui_ink", "site-next"):
        assert workspace in workspaces
        assert (ROOT / workspace / "package.json").exists()
    names = []
    for workspace in workspaces:
        manifest = json.loads((ROOT / workspace / "package.json").read_text(encoding="utf-8"))
        names.append(manifest["name"])
    assert len(names) == len(set(names))
    scripts = package.get("scripts") or {}
    assert scripts["typecheck"] == "npm --prefix apps/desktop run typecheck && npm --prefix apps/shared run typecheck && npm --prefix web run typecheck && npm --prefix aegis/tui_ink run typecheck"
    assert scripts["build:web"] == "npm --prefix web run build"
    assert scripts["test:desktop"] == "npm --prefix desktop run test:desktop"


def test_package_wrapper_roots_delegate_to_native_aegis_packages():
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    workspaces = set(root_package.get("workspaces") or [])
    expected = {
        "apps/desktop": ("aegis-app-desktop", "npm --prefix ../../desktop run test:desktop"),
        "apps/bootstrap-installer": ("@aegis/bootstrap-installer", "node ./scripts/verify-installer-surface.mjs"),
        "apps/shared": ("@aegis/shared", "node ../../web/node_modules/typescript/bin/tsc -p . --noEmit"),
        "scripts/whatsapp-bridge": ("@aegis/whatsapp-bridge", "node ./scripts/verify-bridge-surface.mjs"),
        "website": ("aegis-website", "npm --prefix ../site-next run build"),
        "ui-tui": ("aegis-ui-tui", "npm --prefix ../aegis/tui_ink run build"),
    }
    for workspace, (name, build_or_test_script) in expected.items():
        assert workspace in workspaces
        manifest = json.loads((ROOT / workspace / "package.json").read_text(encoding="utf-8"))
        assert manifest["name"] == name
        assert manifest["private"] is True
        scripts = manifest.get("scripts") or {}
        assert build_or_test_script in scripts.values()


def test_desktop_wrapper_maps_install_update_surfaces_to_native_desktop():
    root = ROOT / "apps" / "desktop"
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    verifier = root / "scripts" / "verify-desktop-surface.mjs"

    assert manifest["name"] == "aegis-app-desktop"
    assert manifest["scripts"]["typecheck"] == "node ./scripts/verify-desktop-surface.mjs && node --test electron/*.test.cjs"
    assert verifier.is_file()
    native = manifest["aegis"]["native_install_update_surfaces"]
    assert native["desktop-uninstall"] == "../../desktop/electron/desktop-uninstall.cjs"
    assert native["update-status"] == "../../desktop/electron/updater-status.cjs"
    assert native["gateway-update-coordination"] == "../../desktop/electron/gateway-update-coordination.cjs"
    assert native["build-stamp"] == "../../desktop/scripts/write-build-stamp.cjs"
    assert native["stage-backend"] == "../../desktop/scripts/stage-backend.cjs"
    assert native["stage-uninstall"] == "../../desktop/scripts/stage-uninstall.cjs"


def test_desktop_uninstall_app_path_delegates_to_native_desktop_module():
    wrapper = ROOT / "apps" / "desktop" / "electron" / "desktop-uninstall.cjs"
    wrapper_test = ROOT / "apps" / "desktop" / "electron" / "desktop-uninstall.test.cjs"
    native = ROOT / "desktop" / "electron" / "desktop-uninstall.cjs"

    assert wrapper.is_file()
    assert wrapper_test.is_file()
    text = wrapper.read_text(encoding="utf-8")
    assert "../../../desktop/electron/desktop-uninstall.cjs" in text
    assert "desktopUninstallPlan" in native.read_text(encoding="utf-8")


def test_desktop_update_count_app_path_exposes_shallow_clone_helper():
    helper = ROOT / "apps" / "desktop" / "electron" / "update-count.cjs"
    helper_test = ROOT / "apps" / "desktop" / "electron" / "update-count.test.cjs"

    assert helper.is_file()
    assert helper_test.is_file()
    source = helper.read_text(encoding="utf-8")
    assert "shouldCountCommits" in source
    assert "resolveBehindCount" in source


def test_desktop_update_marker_app_path_exposes_aegis_marker_helper():
    helper = ROOT / "apps" / "desktop" / "electron" / "update-marker.cjs"
    helper_test = ROOT / "apps" / "desktop" / "electron" / "update-marker.test.cjs"

    assert helper.is_file()
    assert helper_test.is_file()
    source = helper.read_text(encoding="utf-8")
    assert ".aegis-update-in-progress" in source
    assert "readLiveUpdateMarker" in source


def test_desktop_update_remote_app_path_exposes_aegis_remote_helper():
    helper = ROOT / "apps" / "desktop" / "electron" / "update-remote.cjs"
    helper_test = ROOT / "apps" / "desktop" / "electron" / "update-remote.test.cjs"

    assert helper.is_file()
    assert helper_test.is_file()
    source = helper.read_text(encoding="utf-8")
    assert "github.com/alien0013/aegis" in source.lower()
    assert "isOfficialSshRemote" in source


def test_desktop_update_rebuild_app_path_exposes_retry_helper():
    helper = ROOT / "apps" / "desktop" / "electron" / "update-rebuild.cjs"
    helper_test = ROOT / "apps" / "desktop" / "electron" / "update-rebuild.test.cjs"

    assert helper.is_file()
    assert helper_test.is_file()
    source = helper.read_text(encoding="utf-8")
    assert "shouldRetryRebuild" in source
    assert "runRebuildWithRetry" in source


def test_desktop_update_relaunch_app_path_exposes_relaunch_decision_helper():
    helper = ROOT / "apps" / "desktop" / "electron" / "update-relaunch.cjs"
    helper_test = ROOT / "apps" / "desktop" / "electron" / "update-relaunch.test.cjs"

    assert helper.is_file()
    assert helper_test.is_file()
    source = helper.read_text(encoding="utf-8")
    assert "resolveUnpackedRelease" in source
    assert "decideRelaunchOutcome" in source
    assert "AEGIS_HOME" in source


def test_shared_package_exports_aegis_runtime_contract():
    manifest = json.loads((ROOT / "apps" / "shared" / "package.json").read_text(encoding="utf-8"))
    source = (ROOT / "apps" / "shared" / "src" / "index.ts").read_text(encoding="utf-8")
    assert manifest["exports"] == {".": "./src/index.ts"}
    assert manifest["types"] == "./src/index.ts"
    assert "AEGIS_PRODUCT_NAME" in source
    assert "AEGIS_PROTOCOL_SCHEME" in source
    assert "aegis" in source


def test_bootstrap_installer_package_wraps_native_install_scripts():
    root = ROOT / "apps" / "bootstrap-installer"
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    verifier = root / "scripts" / "verify-installer-surface.mjs"
    assert manifest["name"] == "@aegis/bootstrap-installer"
    assert manifest["private"] is True
    assert manifest["aegis"]["installer_scripts"] == ["../../install.sh", "../../install.ps1"]
    assert manifest["aegis"]["ui_routes"] == ["welcome", "progress", "success", "failure"]
    assert manifest["scripts"]["typecheck"] == "node ./scripts/verify-installer-surface.mjs"
    assert verifier.is_file()

    required = [
        ".gitignore",
        "index.html",
        "src/app.tsx",
        "src/main.tsx",
        "src/store.ts",
        "src/styles.css",
        "src/lib/utils.ts",
        "src/components/button.tsx",
        "src/routes/welcome.tsx",
        "src/routes/progress.tsx",
        "src/routes/success.tsx",
        "src/routes/failure.tsx",
        "src/vite-env.d.ts",
        "src-tauri/Cargo.toml",
        "src-tauri/build.rs",
        "src-tauri/tauri.conf.json",
        "src-tauri/capabilities/default.json",
        "src-tauri/src/bootstrap.rs",
        "src-tauri/src/events.rs",
        "src-tauri/src/install_script.rs",
        "src-tauri/src/lib.rs",
        "src-tauri/src/main.rs",
        "src-tauri/src/paths.rs",
        "src-tauri/src/powershell.rs",
        "src-tauri/src/update.rs",
        "tsconfig.json",
        "tsconfig.node.json",
        "vite.config.ts",
    ]
    for rel in required:
        assert (root / rel).is_file(), rel
    assert "AEGIS Bootstrap Installer" in (root / "index.html").read_text(encoding="utf-8")
    assert "Install AEGIS" in (root / "src" / "routes" / "welcome.tsx").read_text(encoding="utf-8")
    assert "install.sh" in (root / "src-tauri" / "src" / "install_script.rs").read_text(encoding="utf-8")
    assert "install.ps1" in (root / "src-tauri" / "src" / "powershell.rs").read_text(encoding="utf-8")


def test_whatsapp_bridge_package_wraps_native_webhook_bridge():
    manifest = json.loads((ROOT / "scripts" / "whatsapp-bridge" / "package.json").read_text(encoding="utf-8"))
    verifier = ROOT / "scripts" / "whatsapp-bridge" / "scripts" / "verify-bridge-surface.mjs"
    assert manifest["name"] == "@aegis/whatsapp-bridge"
    assert manifest["private"] is True
    assert manifest["aegis"]["native_channel"] == "whatsapp"
    assert manifest["aegis"]["bridge_env_prefix"] == "WHATSAPP_CHANNEL"
    assert manifest["scripts"]["typecheck"] == "node ./scripts/verify-bridge-surface.mjs"
    assert verifier.is_file()


def test_pyproject_extras_expose_native_compatibility_aliases():
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    for name in ("cli", "cron", "pty", "web", "messaging", "gateway", "computer-use"):
        assert name in extras
    assert extras["cli"] == []
    assert extras["cron"] == []
    assert extras["pty"] == []
    assert extras["web"] == []
    assert extras["computer-use"] == extras["computer"]
    for name in ("dingtalk", "feishu", "homeassistant", "sms", "wecom"):
        assert name in extras
        assert extras[name] == []
    for dep in extras["discord"] + extras["slack"] + extras["matrix"]:
        assert dep in extras["messaging"]
        assert dep in extras["gateway"]
    unsupported = {"anthropic", "google", "youtube", "mcp", "acp"}
    assert unsupported.isdisjoint(extras)


def test_pyproject_packages_bundled_skills():
    """The wheel must include SKILL.md data files, not just .py modules."""
    txt = pathlib.Path("pyproject.toml").read_text()
    assert "[tool.setuptools.package-data]" in txt
    assert "builtin_skills/**/*" in txt, "package-data glob missing — skills won't ship in the wheel"
    assert "desktop_app/*" in txt, "desktop app template won't ship in the wheel"


def test_bundled_skills_resolve_and_discover():
    """The loader must find the bundled skills via the package path (works pip-installed)."""
    from aegis.config import Config
    from aegis.skills import SkillsLoader, _bundled_dir
    d = _bundled_dir()
    on_disk = list(d.glob("*/SKILL.md"))
    assert len(on_disk) >= 20, f"expected the bundled skill set, found {len(on_disk)}"
    discovered = SkillsLoader(Config.load()).discover()
    assert len(discovered) >= 20


def test_install_hints_use_correct_distribution_name():
    """Copy-pasteable hints must use the real PyPI name, not the taken 'aegis-agent'."""
    import re
    for p in pathlib.Path("aegis").rglob("*.py"):
        src = p.read_text()
        # any aegis-agent[extra] hint must be the -harness distribution
        for m in re.finditer(r"aegis-agent(-harness)?\[", src):
            assert m.group(1) == "-harness", f"{p}: stale 'aegis-agent[' install hint"


def _desktop_manifest(root: pathlib.Path) -> dict[pathlib.Path, bytes]:
    ignored_dirs = {"node_modules", "dist", "out"}
    ignored_files = {pathlib.Path("README.md"), pathlib.Path("build/install-stamp.json")}
    files: dict[pathlib.Path, bytes] = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in ignored_dirs for part in rel.parts) or rel in ignored_files:
            continue
        if path.is_file():
            files[rel] = path.read_bytes()
    return files


def test_desktop_source_and_packaged_copy_stay_in_sync():
    """The editable Electron app and the wheel-bundled copy must not drift."""
    source = _desktop_manifest(ROOT / "desktop")
    packaged = _desktop_manifest(ROOT / "aegis" / "desktop_app")

    assert set(source) == set(packaged)
    for rel in sorted(source):
        assert source[rel] == packaged[rel], f"desktop copy drifted: {rel}"


def test_desktop_lockfiles_cover_runtime_dependencies():
    """npm ci must be able to install every runtime dependency declared by the desktop app."""
    for root in (ROOT / "desktop", ROOT / "aegis" / "desktop_app"):
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
        locked_root = lock.get("packages", {}).get("", {})
        locked_deps = locked_root.get("dependencies", {})
        for name, spec in (package.get("dependencies") or {}).items():
            assert locked_deps.get(name) == spec, f"{root}: lockfile missing dependency {name}"
            assert f"node_modules/{name}" in lock.get("packages", {}), (
                f"{root}: lockfile missing package node_modules/{name}"
            )
