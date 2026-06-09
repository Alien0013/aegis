"""SSRF guard, fuzzy edit recovery, and nearest-AGENTS.md (monorepo) upgrades."""

from __future__ import annotations

from pathlib import Path


# --- SSRF guard (offline: literal IPs / locally-resolvable hosts) ----------
def test_ssrf_blocks_metadata_and_private():
    from aegis.net_safety import is_safe_url
    assert is_safe_url("http://169.254.169.254/latest/meta-data/")[0] is False   # AWS creds
    assert is_safe_url("http://metadata.google.internal/")[0] is False           # GCP host
    assert is_safe_url("http://127.0.0.1/")[0] is False                          # loopback
    assert is_safe_url("http://10.0.0.5/x")[0] is False                          # private
    assert is_safe_url("http://192.168.1.1/")[0] is False                        # private
    assert is_safe_url("file:///etc/passwd")[0] is False                         # bad scheme
    assert is_safe_url("http://100.100.100.200/")[0] is False                    # Alibaba metadata


def test_ssrf_metadata_blocked_even_when_private_allowed():
    from aegis.net_safety import is_safe_url

    class Cfg:
        def get(self, k, d=None): return True       # allow_private_urls = True
    # private becomes allowed, but cloud metadata stays blocked
    assert is_safe_url("http://10.0.0.5/", Cfg())[0] is True
    assert is_safe_url("http://169.254.169.254/", Cfg())[0] is False


def test_web_fetch_tool_refuses_metadata():
    from aegis.tools.builtin import WebFetchTool
    from aegis.tools.base import ToolContext
    r = WebFetchTool().run({"url": "http://169.254.169.254/"}, ToolContext())
    assert r.is_error and "blocked for safety" in r.content


# --- fuzzy edit recovery ----------------------------------------------------
def test_edit_file_fuzzy_hint(tmp_path):
    from aegis.tools.builtin import EditFileTool
    from aegis.tools.base import ToolContext
    (tmp_path / "f.py").write_text("def hello():\n    return  42\n")   # two spaces
    r = EditFileTool().run(
        {"path": "f.py", "old_string": "return 42", "new_string": "return 43"},
        ToolContext(cwd=tmp_path))
    assert r.is_error
    assert "Closest match" in r.content and "return  42" in r.content   # surfaces the real text


# --- nearest AGENTS.md (monorepo) ------------------------------------------
def test_nearest_agents_md_walks_up(tmp_path):
    from aegis.config import Workspace
    (tmp_path / "AGENTS.md").write_text("ROOT RULES")
    sub = tmp_path / "packages" / "foo"
    sub.mkdir(parents=True)
    assert "ROOT RULES" in Workspace(cwd=sub).rules()
    # a closer rule file wins
    (sub / "AGENTS.md").write_text("SUBPKG RULES")
    assert "SUBPKG RULES" in Workspace(cwd=sub).rules()


# --- model metadata ---------------------------------------------------------
def test_model_metadata_resolves_current_models():
    from aegis.model_meta import context_window
    assert context_window("claude-sonnet-4-6") == 1_000_000
    assert context_window("gpt-5.5") == 400_000
    assert context_window("gpt-4o") == 128_000
    assert context_window("gemini-2.5-pro") == 1_048_576
    assert context_window("totally-unknown-model") is None     # falls back to preset


def test_provider_uses_model_metadata_for_context():
    from aegis.config import Config
    from aegis.providers import build_provider
    c = Config.load()
    c.set("model.provider", "openai")
    c.set("model.default", "gpt-4o")        # smaller than the openai preset default
    assert build_provider(c).context_length == 128_000   # the real gpt-4o window, not the preset


# --- schema sanitizer -------------------------------------------------------
def test_schema_sanitizer_strips_annotations_keeps_structure():
    from aegis.providers.schema import sanitize
    out = sanitize({"type": ["string", "null"], "$schema": "x", "examples": [1],
                    "properties": {"a": {"type": "integer", "readOnly": True}}, "required": ["a"]})
    assert out["type"] == "string"                    # union normalized
    assert "$schema" not in out and "examples" not in out
    assert "readOnly" not in out["properties"]["a"]   # nested annotation dropped
    assert out["required"] == ["a"]                   # structure preserved
