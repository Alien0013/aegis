"""Guards that the things users rely on at install time actually ship + resolve."""

from __future__ import annotations

import pathlib


def test_pyproject_packages_bundled_skills():
    """The wheel must include SKILL.md data files, not just .py modules."""
    txt = pathlib.Path("pyproject.toml").read_text()
    assert "[tool.setuptools.package-data]" in txt
    assert "builtin_skills/**/*" in txt, "package-data glob missing — skills won't ship in the wheel"


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
