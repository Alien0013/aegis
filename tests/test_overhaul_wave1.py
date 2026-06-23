"""Overhaul wave 1: curator LLM pass, skill telemetry counters, prompt-cache TTL,
aux-model tools (vision_analyze/web_extract), and per-task auxiliary slots."""

from __future__ import annotations

from aegis.config import Config


# --- curator LLM consolidation pass ---------------------------------------
def test_curator_llm_review_noops_without_provider(monkeypatch):
    from aegis import curator, config as cfg
    # one curatable agent-created skill so the early "no skills" branch doesn't hide the path
    from aegis import provenance
    d = cfg.skills_dir() / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: demo\ndescription: x\n---\n")
    provenance.record("demo", "agent")
    # hermetic env has no usable model -> llm_review must degrade gracefully and never raise.
    r = curator.llm_review(Config.load(), dry_run=False)
    assert isinstance(r, dict) and "ran" in r


def test_curator_llm_review_dry_run_lists_candidates():
    from aegis import curator, config as cfg, provenance
    d = cfg.skills_dir() / "demo2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: demo2\ndescription: y\n---\n")
    provenance.record("demo2", "agent")
    r = curator.llm_review(Config.load(), dry_run=True)
    assert r["ran"] is False and "demo2" in r.get("candidates", [])


def test_curator_run_includes_llm_review_key(monkeypatch):
    from aegis import curator
    c = Config.load()
    c.data["curator"]["llm_review"] = True
    curator._save_state({"last_run_at": "2000-01-01T00:00:00+00:00"})
    monkeypatch.setattr(curator, "_idle_hours", lambda now=None: 999.0)
    r = curator.maybe_run(c)
    assert r is not None and "llm_review" in r


# --- skill telemetry counters ---------------------------------------------
def test_skill_telemetry_view_use_patch(tmp_path):
    from aegis.skills import SkillsLoader
    loader = SkillsLoader(Config.load(), cwd=tmp_path)
    loader.record_use("s")
    loader.record_view("s")
    loader.record_view("s")
    loader.record_patch("s")
    entry = loader.usage()["s"]
    assert entry["count"] == 1
    assert entry["view_count"] == 2
    assert entry["patch_count"] == 1
    assert entry["last_used"] and entry["last_viewed_at"] and entry["last_patched_at"]


# --- aux-model tools registered -------------------------------------------
def test_aux_tools_registered():
    from aegis.tools.registry import default_registry
    names = {t.name for t in default_registry(include_plugins=False).all()}
    assert {"vision_analyze", "web_extract"} <= names


# --- config: new keys ------------------------------------------------------
def test_config_has_overhaul_wave1_keys():
    c = Config.load()
    assert c.get("prompt_caching.cache_ttl") == "5m"
    assert c.get("curator.llm_review") is True
    # per-task auxiliary slots exist (empty = inherit)
    for slot in ("curator", "background_review", "vision", "web_extract", "approval", "kanban_decomposer"):
        assert isinstance(c.get(f"auxiliary.{slot}"), dict)


# --- prompt cache marker honors TTL ---------------------------------------
def test_cache_marker_honors_ttl(monkeypatch):
    from aegis.providers import anthropic
    monkeypatch.setattr(anthropic, "_CACHE_TTL", "1h")
    assert anthropic._cache_marker() == {"type": "ephemeral", "ttl": "1h"}
    monkeypatch.setattr(anthropic, "_CACHE_TTL", "5m")
    assert anthropic._cache_marker() == {"type": "ephemeral"}
