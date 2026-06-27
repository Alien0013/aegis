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
    assert scripts["typecheck"] == "npm --prefix web run typecheck && npm --prefix aegis/tui_ink run typecheck"
    assert scripts["build:web"] == "npm --prefix web run build"
    assert scripts["test:desktop"] == "npm --prefix desktop run test:desktop"


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
