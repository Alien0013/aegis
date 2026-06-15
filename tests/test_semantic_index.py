"""Semantic code index: embedding-backed code_search, incremental indexing, and the
graceful fallback to the structural repo map when no embeddings provider is set."""

import subprocess

import pytest

from aegis import semantic_index as si
from aegis.config import Config
from aegis.tools.base import ToolContext
from aegis.tools.code_search_tool import CodeSearchTool


def _fake_embed(texts, config):
    """Deterministic 4-dim embedder keyed on a few keywords, so cosine is predictable."""
    out = []
    for t in texts:
        tl = t.lower()
        out.append([
            1.0 if ("auth" in tl or "token" in tl) else 0.0,
            1.0 if ("parse" in tl or "json" in tl) else 0.0,
            1.0 if ("deploy" in tl or "ship" in tl) else 0.0,
            0.1,
        ])
    return out


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")          # embeddings_available -> True
    monkeypatch.setattr(si, "_embed", _fake_embed)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "auth.py").write_text("def validate_token(tok):\n    return tok == SECRET  # auth token\n")
    (root / "parse.py").write_text("import json\ndef parse_json(s):\n    return json.loads(s)\n")
    (root / "deploy.py").write_text("def ship():\n    deploy_to_prod()\n")
    subprocess.run(["git", "init", "-q", str(root)], check=False)
    return root


def test_build_and_semantic_search(repo):
    cfg = Config.load()
    res = si.build(repo, cfg, force=True)
    assert res["ok"] and res["indexed"] == 3 and res["chunks"] >= 3
    hits = si.search(repo, "where are auth tokens validated", cfg, k=2)
    assert hits and hits[0]["path"] == "auth.py"
    assert si.search(repo, "json parsing", cfg, k=1)[0]["path"] == "parse.py"


def test_incremental_skips_unchanged(repo):
    cfg = Config.load()
    si.build(repo, cfg, force=True)
    second = si.build(repo, cfg)                # nothing changed
    assert second["indexed"] == 0 and second["skipped"] >= 3


def test_unavailable_without_key(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    cfg = Config.load()
    assert si.embeddings_available(cfg) is False
    assert si.search(repo, "anything", cfg) == []


def test_tool_uses_semantic_when_available(repo):
    cfg = Config.load()
    out = CodeSearchTool().run({"action": "search", "query": "validate auth tokens"},
                               ToolContext(cwd=repo, config=cfg))
    assert not out.is_error and "auth.py" in out.content and "semantic" in out.display


def test_tool_falls_back_to_repo_map_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    out = CodeSearchTool().run({"action": "search", "query": "anything semantic"},
                               ToolContext(cwd=tmp_path, config=Config.load()))
    assert not out.is_error and "fallback" in out.display
