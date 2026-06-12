from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from aegis import desktop
from aegis.cli.main import build_parser


def _write_desktop_template(root: Path) -> Path:
    root.mkdir()
    for name in desktop.DESKTOP_FILES:
        (root / name).write_text(f"{name}\n", encoding="utf-8")
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

    assert calls == [{"cmd": ["/usr/bin/npm", "install"], "cwd": target, "env": None}]
    for name in desktop.DESKTOP_FILES:
        assert (target / name).read_text(encoding="utf-8") == f"{name}\n"


def test_desktop_launch_skips_install_when_dependencies_exist(monkeypatch, tmp_path):
    source = _write_desktop_template(tmp_path / "source")
    target = tmp_path / "runtime"
    desktop._sync_desktop_app(source, target)
    (target / "node_modules" / "electron").mkdir(parents=True)
    calls: list[dict] = []

    def fake_which(name):
        return {("npm"): "/usr/bin/npm", ("aegis"): "/usr/local/bin/aegis"}.get(name)

    def fake_run(cmd, cwd, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("AEGIS_DESKTOP_DIR", str(target))
    monkeypatch.delenv("AEGIS_BIN", raising=False)
    monkeypatch.setattr(desktop, "_desktop_source", lambda: source)
    monkeypatch.setattr(desktop.shutil, "which", fake_which)
    monkeypatch.setattr(desktop.sys, "argv", ["aegis"])
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)

    args = Namespace(install_only=False, reinstall=False, sandbox=False)
    assert desktop.cmd_desktop(args, object()) == 0

    assert len(calls) == 1
    assert calls[0]["cmd"] == ["/usr/bin/npm", "start"]
    assert calls[0]["cwd"] == target
    assert calls[0]["env"]["AEGIS_BIN"] == "/usr/local/bin/aegis"


def test_desktop_parser_and_typo_alias():
    parser = build_parser()
    args = parser.parse_args(["desktop", "--install-only"])
    assert args.func is desktop.cmd_desktop
    assert args.install_only is True

    typo_args = parser.parse_args(["deksktop", "--install-only"])
    assert typo_args.func is desktop.cmd_desktop


def test_bundled_desktop_template_matches_source():
    root = Path(__file__).resolve().parents[1]
    for name in desktop.DESKTOP_FILES:
        assert (root / "aegis" / "desktop_app" / name).read_bytes() == (
            root / "desktop" / name
        ).read_bytes()
