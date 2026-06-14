"""The repo map: symbol extraction (Python + regex langs), reference-weighted ranking,
character budgeting, symbol lookup, and the repo_map tool wrapper."""

from pathlib import Path

from aegis import repomap
from aegis.tools.base import ToolContext
from aegis.tools.repomap_tool import RepoMapTool


def _write(root: Path) -> None:
    (root / "pkg").mkdir()
    # core.py defines a symbol referenced everywhere → should rank first.
    (root / "pkg" / "core.py").write_text(
        "class Engine:\n"
        "    def run(self):\n"
        "        return 1\n"
        "    def _private(self):\n"
        "        return 2\n"
        "def helper():\n"
        "    return Engine()\n"
    )
    (root / "pkg" / "a.py").write_text("from .core import Engine\n\ndef a():\n    return Engine().run()\n")
    (root / "pkg" / "b.py").write_text("from .core import Engine\n\ndef b():\n    return Engine()\n")
    (root / "app.js").write_text(
        "export function main() {}\n"
        "class Widget {}\n"
        "export const build = async () => {}\n"
    )


def test_python_symbol_extraction_skips_private_methods(tmp_path):
    _write(tmp_path)
    syms = repomap.extract_symbols(tmp_path / "pkg" / "core.py",
                                   (tmp_path / "pkg" / "core.py").read_text())
    names = {s.name for s in syms}
    assert "Engine" in names and "helper" in names and "Engine.run" in names
    assert "Engine._private" not in names           # leading-underscore methods are dropped


def test_js_regex_extraction(tmp_path):
    _write(tmp_path)
    syms = repomap.extract_symbols(tmp_path / "app.js", (tmp_path / "app.js").read_text())
    names = {s.name for s in syms}
    assert {"main", "Widget", "build"} <= names


def test_ranking_promotes_referenced_files(tmp_path):
    _write(tmp_path)
    entries = repomap.build_index(tmp_path)
    rels = [e.rel for e in entries]
    # core.py defines Engine, referenced by a.py and b.py → ranks above them.
    assert rels[0].endswith("core.py")


def test_render_map_respects_char_budget(tmp_path):
    _write(tmp_path)
    out = repomap.render_map(tmp_path, max_chars=120)
    assert len(out) <= 400                          # budget + the trailing "more files" line
    assert "Repo map" in out


def test_find_symbol(tmp_path):
    _write(tmp_path)
    hits = repomap.find_symbol(tmp_path, "Engine")
    assert any(rel.endswith("core.py") and kind == "class" for rel, _line, kind in hits)


def test_repo_map_tool_actions(tmp_path):
    _write(tmp_path)
    tool = RepoMapTool()
    ctx = ToolContext(cwd=tmp_path)
    mapped = tool.run({"action": "map"}, ctx)
    assert not mapped.is_error and "core.py" in mapped.content
    found = tool.run({"action": "find", "name": "helper"}, ctx)
    assert not found.is_error and "core.py" in found.content
    missing = tool.run({"action": "find", "name": "does_not_exist"}, ctx)
    assert "no definition" in missing.content.lower()
