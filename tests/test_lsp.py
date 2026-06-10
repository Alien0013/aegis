"""LSP package: range-shift delta math, workspace gating, server registry, service delta."""

from __future__ import annotations

import subprocess


def _diag(line, msg, sev=1, code="E1"):
    return {"severity": sev, "code": code, "source": "t", "message": msg,
            "range": {"start": {"line": line, "character": 0},
                      "end": {"line": line, "character": 5}}}


# --- range shift -------------------------------------------------------------
def test_line_shift_insert_delete_and_identity():
    from aegis.lsp.range_shift import build_line_shift

    pre = "a\nb\nc\nd"
    # insert one line above c -> c moves from 2 to 3
    shift = build_line_shift(pre, "a\nb\nX\nc\nd")
    assert shift(0) == 0 and shift(2) == 3 and shift(3) == 4
    # delete b -> line 1 has no counterpart
    shift = build_line_shift(pre, "a\nc\nd")
    assert shift(1) is None and shift(2) == 1
    # identity
    assert build_line_shift(pre, pre)(3) == 3


def test_shift_baseline_drops_deleted_lines():
    from aegis.lsp.range_shift import build_line_shift, shift_baseline

    shift = build_line_shift("a\nbad\nc", "a\nc")
    out = shift_baseline([_diag(1, "bad thing"), _diag(2, "still here")], shift)
    assert len(out) == 1
    assert out[0]["message"] == "still here"
    assert out[0]["range"]["start"]["line"] == 1     # c moved up


# --- workspace gate ----------------------------------------------------------
def test_workspace_gate_requires_git(tmp_path):
    from aegis.lsp import workspace

    workspace.clear_cache()
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "f.py").write_text("x = 1")
    assert workspace.resolve_workspace(str(plain / "f.py")) is None    # not a project

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    f = repo / "src" / "f.py"
    f.write_text("x = 1")
    assert workspace.resolve_workspace(str(f)) == str(repo)
    workspace.clear_cache()


def test_nearest_root_finds_marker(tmp_path):
    from aegis.lsp.workspace import nearest_root

    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "pyproject.toml").write_text("")
    found = nearest_root(str(tmp_path / "pkg" / "sub" / "x.py"), ["pyproject.toml"])
    assert found == str(tmp_path / "pkg")


# --- server registry ---------------------------------------------------------
def test_find_server_by_extension_and_override():
    from aegis.lsp.servers import find_server

    assert find_server("a/b.py").id == "pyright"
    assert find_server("a/b.tsx").language_for(".tsx") == "typescriptreact"
    assert find_server("Dockerfile").id == "docker"
    assert find_server("a/b.unknownext") is None

    class Cfg:
        def get(self, k, d=None):
            return {".py": "pylsp --stdio"} if k == "lsp.servers" else d
    sd = find_server("a/b.py", Cfg())
    assert sd.command == ["pylsp", "--stdio"] and sd.language_id == "python"


# --- service delta with a fake client ---------------------------------------
def test_service_delta_reports_only_new_diags(tmp_path, monkeypatch):
    from aegis.lsp.service import LSPService

    f = tmp_path / "m.py"
    f.write_text("a\nbad\nc")

    class FakeClient:
        alive = True

        def __init__(self):
            self.published = [_diag(1, "pre-existing")]

        def clear_diag_event(self, uri): pass
        def sync_doc(self, path, text, lang): return "uri"
        def wait_diagnostics(self, uri, timeout=5.0): return list(self.published)

    fake = FakeClient()
    svc = LSPService()
    monkeypatch.setattr(svc, "_client_for", lambda *a, **k: (fake, type(
        "SD", (), {"language_for": staticmethod(lambda e: "python")})()))

    svc.snapshot(str(f))
    # edit: insert a line above, introduce one NEW diagnostic; old one shifts down
    f.write_text("a\nnew\nbad\nc")
    fake.published = [_diag(2, "pre-existing"), _diag(1, "fresh problem")]
    new = svc.delta(str(f))
    assert [d["message"] for d in new] == ["fresh problem"]


def test_format_diags_orders_errors_first():
    from aegis.lsp.service import format_diags

    out = format_diags([_diag(5, "warn", sev=2), _diag(9, "err", sev=1)])
    lines = out.splitlines()
    assert "err" in lines[0] and "warn" in lines[1]
